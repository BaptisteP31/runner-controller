import ipaddress
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

from .proxmox import ProxmoxError

LOG = logging.getLogger("runner_controller")


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class RunnerService:
    def __init__(self, config, store, proxmox):
        self.config, self.store, self.pve = config, store, proxmox

    def _audit(self, action, result, runner=None, vmid=None, upid=None):
        self.store.audit(action, result, runner, vmid, upid)

    def _owned(self, runner):
        record = self.store.get_runner(runner)
        if not record:
            raise ApiError(404, "RUNNER_NOT_FOUND", "Runner not found")
        return record

    def _ensure_in_pool(self, vmid):
        try:
            if vmid not in self.pve.pool_vmids():
                raise ApiError(404, "RUNNER_NOT_FOUND", "Runner not found in authorized pool")
        except ApiError:
            raise
        except ProxmoxError:
            raise ApiError(502, "PROXMOX_ERROR", "Unable to verify runner pool") from None

    def create(self, body):
        if not isinstance(body, dict) or set(body) - {"memory_mb", "ttl_seconds"}:
            raise ApiError(400, "INVALID_REQUEST", "Only memory_mb and ttl_seconds are accepted")
        memory = body.get("memory_mb", self.config.memory_default_mb)
        ttl = body.get("ttl_seconds", self.config.ttl_default_seconds)
        if type(memory) is not int or not self.config.memory_min_mb <= memory <= self.config.memory_max_mb:
            raise ApiError(400, "INVALID_MEMORY", "memory_mb is outside the allowed range")
        if type(ttl) is not int or not self.config.ttl_min_seconds <= ttl <= self.config.ttl_max_seconds:
            raise ApiError(400, "INVALID_TTL", "ttl_seconds is outside the allowed range")
        runner = "runner-" + secrets.token_hex(4)
        expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
        try:
            occupied = self.pve.vmids()
            candidates = (i for i in range(self.config.vmid_min, self.config.vmid_max + 1)
                          if i not in occupied)
            vmid = self.store.reserve_available(runner, candidates, expires, memory,
                                                self.config.max_runners)
            if vmid is None:
                raise ApiError(503, "VMID_RANGE_EXHAUSTED", "No VMID is available")
        except RuntimeError:
            raise ApiError(429, "RUNNER_LIMIT_REACHED", "Maximum number of active runners reached") from None
        except ProxmoxError:
            raise ApiError(502, "PROXMOX_ERROR", "Unable to inspect Proxmox resources") from None

        upid = None
        try:
            upid = self.pve.clone(vmid)
            self._audit("create_clone", "started", runner, vmid, upid)
            self.pve.wait_task(upid)
            self._ensure_in_pool(vmid)
            self.pve.configure_network(vmid)
            upid = self.pve.start(vmid)
            self._audit("start", "started", runner, vmid, upid)
            self.pve.wait_task(upid)
            deadline = time.monotonic() + self.config.guest_agent_timeout_seconds
            ipv4 = None
            while time.monotonic() < deadline:
                try:
                    ipv4 = self.pve.guest_ipv4(vmid)
                except ProxmoxError:
                    pass
                if ipv4:
                    break
                time.sleep(2)
            if not ipv4 or ipaddress.ip_address(ipv4).version != 4:
                raise ApiError(504, "GUEST_AGENT_TIMEOUT", "Runner did not report an IPv4 address")
            self.store.update_runner(runner, status="running", ipv4=ipv4)
            self._audit("create", "succeeded", runner, vmid, upid)
            return self._public(self.store.get_runner(runner))
        except Exception as exc:
            self._audit("create", "failed", runner, vmid, upid)
            LOG.warning("runner creation failed runner_id=%s vmid=%s error_type=%s", runner, vmid, type(exc).__name__)
            try:
                # Best effort cleanup is limited to the reserved VMID and authorized pool.
                if vmid in self.pve.pool_vmids():
                    status = self.pve.vm_status(vmid)
                    if status.get("status") == "running":
                        cleanup_upid = self.pve.shutdown(vmid)
                        self.pve.wait_task(cleanup_upid)
                    cleanup_upid = self.pve.delete(vmid)
                    self.pve.wait_task(cleanup_upid)
                self.store.remove_runner(runner)
            except Exception:
                self.store.update_runner(runner, status="error")
            if isinstance(exc, ApiError):
                raise
            raise ApiError(502, "RUNNER_CREATE_FAILED", "Runner creation failed") from None

    @staticmethod
    def _public(record):
        return {key: record[key] for key in ("id", "vmid", "status", "ipv4", "created_at", "expires_at")}

    def list(self):
        result = []
        for record in self.store.list_runners():
            item = self._public(record)
            try:
                self._ensure_in_pool(record["vmid"])
                status = self.pve.vm_status(record["vmid"])
                item["status"] = status.get("status", item["status"])
            except (ApiError, ProxmoxError):
                item["status"] = "unknown"
            result.append(item)
        return {"runners": result}

    def get(self, runner):
        record = self._owned(runner)
        self._ensure_in_pool(record["vmid"])
        try:
            status = self.pve.vm_status(record["vmid"])
            self.store.update_runner(runner, status=status.get("status", record["status"]))
            record = self.store.get_runner(runner)
        except ProxmoxError:
            raise ApiError(502, "PROXMOX_ERROR", "Unable to read runner status") from None
        return self._public(record)

    def shutdown(self, runner):
        record = self._owned(runner)
        self._ensure_in_pool(record["vmid"])
        try:
            status = self.pve.vm_status(record["vmid"])
            if status.get("status") == "running":
                upid = self.pve.shutdown(record["vmid"])
                self._audit("shutdown", "started", runner, record["vmid"], upid)
                self.pve.wait_task(upid)
            self.store.update_runner(runner, status="stopped")
            self._audit("shutdown", "succeeded", runner, record["vmid"])
            return self._public(self.store.get_runner(runner))
        except ProxmoxError:
            self._audit("shutdown", "failed", runner, record["vmid"])
            raise ApiError(502, "PROXMOX_ERROR", "Unable to shut down runner") from None

    def delete(self, runner):
        record = self.store.get_runner(runner)
        if not record:
            return
        vmid = record["vmid"]
        try:
            self._ensure_in_pool(vmid)
            status = self.pve.vm_status(vmid)
            if status.get("status") == "running":
                upid = self.pve.shutdown(vmid)
                self.pve.wait_task(upid)
            upid = self.pve.delete(vmid)
            self._audit("delete", "started", runner, vmid, upid)
            self.pve.wait_task(upid)
        except ApiError as exc:
            if exc.status == 404:
                pass
            else:
                self._audit("delete", "failed", runner, vmid)
                raise
        except ProxmoxError as exc:
            if exc.status != 404:
                self._audit("delete", "failed", runner, vmid)
                raise ApiError(502, "PROXMOX_ERROR", "Unable to delete runner") from None
        self.store.remove_runner(runner)
        self._audit("delete", "succeeded", runner, vmid)

    def cleanup_expired(self):
        for record in self.store.expired():
            try:
                self.delete(record["id"])
            except ApiError:
                LOG.warning("expired runner cleanup failed runner_id=%s vmid=%s", record["id"], record["vmid"])
                self._audit("ttl_cleanup", "failed", record["id"], record["vmid"])
            else:
                self._audit("ttl_cleanup", "succeeded", record["id"], record["vmid"])
