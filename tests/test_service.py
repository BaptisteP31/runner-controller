import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.config import Config
from app.proxmox import ProxmoxError
from app.service import ApiError, RunnerService
from app.store import Store


class RunnerServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config("https://pve", "pve-lab", "user!token", "secret",
                             database_path=str(Path(self.temp.name) / "db.sqlite"),
                             guest_agent_timeout_seconds=1)
        self.store = Store(self.config.database_path)
        self.pve = Mock()
        self.pve.vmids.return_value = set()
        self.pve.pool_vmids.return_value = {920}
        self.pve.clone.return_value = "UPID:clone"
        self.pve.start.return_value = "UPID:start"
        self.pve.guest_ipv4.return_value = "10.0.0.2"
        self.service = RunnerService(self.config, self.store, self.pve)

    def tearDown(self):
        self.temp.cleanup()

    def test_create_records_runner_and_audit(self):
        runner = self.service.create({})
        self.assertEqual(runner["vmid"], 920)
        self.assertEqual(runner["ipv4"], "10.0.0.2")
        with self.store._connect() as db:
            rows = db.execute("SELECT action,result,upid FROM actions ORDER BY id").fetchall()
        self.assertIn(("create", "succeeded", "UPID:start"),
                      [(r["action"], r["result"], r["upid"]) for r in rows])
        self.pve.clone.assert_called_once_with(920)
        self.pve.configure_network.assert_called_once_with(920)

    def test_create_rejects_client_selected_infrastructure(self):
        with self.assertRaises(ApiError) as ctx:
            self.service.create({"template": 901})
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")
        self.pve.clone.assert_not_called()

    def test_create_enforces_memory_limit(self):
        with self.assertRaises(ApiError) as ctx:
            self.service.create({"memory_mb": 6145})
        self.assertEqual(ctx.exception.code, "INVALID_MEMORY")

    def test_capacity_is_limited(self):
        self.store.reserve("runner-11111111", 920, "2099-01-01T00:00:00+00:00", 4096, 2)
        self.store.reserve("runner-22222222", 921, "2099-01-01T00:00:00+00:00", 4096, 2)
        with self.assertRaises(ApiError) as ctx:
            self.service.create({})
        self.assertEqual(ctx.exception.code, "RUNNER_LIMIT_REACHED")

    def test_delete_is_idempotent_for_unknown_runner(self):
        self.service.delete("runner-deadbeef")
        self.pve.delete.assert_not_called()

    def test_shutdown_waits_for_proxmox_task(self):
        self.store.reserve("runner-11111111", 920, "2099-01-01T00:00:00+00:00", 4096, 2)
        self.pve.vm_status.return_value = {"status": "running"}
        self.pve.shutdown.return_value = "UPID:shutdown"
        result = self.service.shutdown("runner-11111111")
        self.pve.wait_task.assert_called_once_with("UPID:shutdown")
        self.assertEqual(result["status"], "stopped")

    def test_delete_never_touches_vm_outside_pool(self):
        self.store.reserve("runner-11111111", 920, "2099-01-01T00:00:00+00:00", 4096, 2)
        self.pve.pool_vmids.return_value = set()
        self.service.delete("runner-11111111")
        self.pve.delete.assert_not_called()
        self.assertIsNone(self.store.get_runner("runner-11111111"))


if __name__ == "__main__":
    unittest.main()
