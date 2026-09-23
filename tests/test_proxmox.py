import ssl
import unittest
from unittest.mock import Mock, patch

from app.config import Config
from app.proxmox import ProxmoxClient


class GuestIpv4Tests(unittest.TestCase):
    def setUp(self):
        self.client = ProxmoxClient(Config("https://pve", "pve-lab", "user!token", "secret"))

    def test_guest_interfaces_use_get(self):
        self.client._request = Mock(return_value={"result": []})
        self.assertIsNone(self.client.guest_ipv4(923))
        self.client._request.assert_called_once_with(
            "GET", "/nodes/pve-lab/qemu/923/agent/network-get-interfaces")

    def test_returns_runner_network_address_instead_of_docker_or_other_addresses(self):
        self.client._request = Mock(return_value={"result": [
            {"name": "lo", "ip-addresses": [
                {"ip-address-type": "ipv4", "ip-address": "127.0.0.1"}]},
            {"name": "docker0", "ip-addresses": [
                {"ip-address-type": "ipv4", "ip-address": "172.17.0.1"}]},
            {"name": "eth0", "ip-addresses": [
                {"ip-address-type": "ipv4", "ip-address": "192.168.1.20"},
                {"ip-address-type": "ipv4", "ip-address": "10.20.40.23"}]},
        ]})
        self.assertEqual(self.client.guest_ipv4(923), "10.20.40.23")

    def test_does_not_fall_back_to_docker_address_when_runner_network_is_absent(self):
        self.client._request = Mock(return_value={"result": [
            {"name": "docker0", "ip-addresses": [
                {"ip-address-type": "ipv4", "ip-address": "172.17.0.1"}]},
        ]})
        self.assertIsNone(self.client.guest_ipv4(923))

    @patch("app.proxmox.urlopen")
    def test_rest_request_keeps_default_tls_verification(self, urlopen):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"data": {}}'
        urlopen.return_value = response

        self.client._request("GET", "/version")

        self.assertIsInstance(urlopen.call_args.kwargs["context"], ssl.SSLContext)
        self.assertTrue(urlopen.call_args.kwargs["context"].check_hostname)
        self.assertEqual(urlopen.call_args.kwargs["context"].verify_mode, ssl.CERT_REQUIRED)


if __name__ == "__main__":
    unittest.main()
