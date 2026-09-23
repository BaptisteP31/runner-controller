import json
import ipaddress
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class ProxmoxError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class ProxmoxClient:
    def __init__(self, config):
        self.config = config

    def _request(self, method, path, data=None):
        url = self.config.pve_host + "/api2/json" + path
        body = urlencode(data or {}).encode() if data is not None else None
        req = Request(url, data=body, method=method, headers={
            "Authorization": f"PVEAPIToken={self.config.pve_token_id}={self.config.pve_token_secret}",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        try:
            with urlopen(req, timeout=self.config.request_timeout_seconds,
                         context=ssl.create_default_context()) as response:
                payload = json.loads(response.read() or b"{}")
                return payload.get("data")
        except HTTPError as exc:
            # Do not surface Proxmox response bodies: they may contain sensitive details.
            raise ProxmoxError("Proxmox API request failed", exc.code) from None
        except (URLError, TimeoutError, ValueError):
            raise ProxmoxError("Proxmox API unavailable") from None

    def vmids(self):
        resources = self._request("GET", "/cluster/resources?type=vm") or []
        return {int(item["vmid"]) for item in resources if "vmid" in item}

    def clone(self, vmid):
        return self._request("POST", f"/nodes/{quote(self.config.pve_node)}/qemu/{self.config.template_id}/clone", {
            "newid": vmid, "name": f"runner-{vmid}", "full": 1,
            "storage": self.config.storage, "pool": self.config.pool,
        })

    def configure_network(self, vmid):
        self._request("PUT", f"/nodes/{quote(self.config.pve_node)}/qemu/{vmid}/config",
                      {"net0": f"virtio,bridge={self.config.vnet}"})

    def start(self, vmid):
        return self._request("POST", f"/nodes/{quote(self.config.pve_node)}/qemu/{vmid}/status/start", {})

    def shutdown(self, vmid):
        return self._request("POST", f"/nodes/{quote(self.config.pve_node)}/qemu/{vmid}/status/shutdown", {})

    def delete(self, vmid):
        return self._request("DELETE", f"/nodes/{quote(self.config.pve_node)}/qemu/{vmid}?purge=1")

    def pool_vmids(self):
        result = self._request("GET", f"/pools/{quote(self.config.pool)}") or {}
        return {int(item["vmid"]) for item in result.get("members", [])
                if item.get("type") == "qemu" and "vmid" in item}

    def vm_status(self, vmid):
        return self._request("GET", f"/nodes/{quote(self.config.pve_node)}/qemu/{vmid}/status/current")

    def guest_ipv4(self, vmid):
        data = self._request("GET", f"/nodes/{quote(self.config.pve_node)}/qemu/{vmid}/agent/network-get-interfaces") or {}
        runner_network = ipaddress.ip_network("10.20.40.0/24")
        for interface in data.get("result", []):
            for addr in interface.get("ip-addresses", []):
                if addr.get("ip-address-type") != "ipv4":
                    continue
                try:
                    ip = ipaddress.ip_address(addr.get("ip-address", ""))
                except ValueError:
                    continue
                if ip in runner_network:
                    return str(ip)
        return None

    def wait_task(self, upid):
        if not upid:
            return
        deadline = time.monotonic() + self.config.task_timeout_seconds
        task_path = quote(upid, safe="")
        while time.monotonic() < deadline:
            status = self._request("GET", f"/nodes/{quote(self.config.pve_node)}/tasks/{task_path}/status") or {}
            if status.get("status") == "stopped":
                if status.get("exitstatus") != "OK":
                    raise ProxmoxError("Proxmox task failed")
                return
            time.sleep(1)
        raise ProxmoxError("Proxmox task timed out")
