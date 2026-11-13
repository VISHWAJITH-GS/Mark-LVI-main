"""
tests/test_wifi_bluetooth_system.py — Tests for Module 1 & Module 2 Wi-Fi & Bluetooth Management:
- Wi-Fi status detection (enabled, connected, SSID, IP, internet availability)
- Wi-Fi ON/OFF detection
- Available network enumeration (SSID listing & fallback)
- Wi-Fi network switching & verification
- Bluetooth status detection (enabled, adapter present, connected devices)
- Bluetooth ON/OFF detection
- Bluetooth connected & paired device enumeration
- Bluetooth device switching & audio endpoint verification
- Confirmation workflow & voice resolution
"""

import unittest
from unittest.mock import patch, MagicMock
import actions.windows_control as wc
import actions.computer_settings as cs
import core.confirm as confirm


class TestWiFiManagement(unittest.TestCase):
    def test_wifi_status_structure(self):
        """Verify get_wifi_status returns all required fields without crashing."""
        st = wc.get_wifi_status()
        self.assertIn("enabled", st)
        self.assertIn("state", st)
        self.assertIn("adapter_present", st)
        self.assertIn("adapter_name", st)
        self.assertIn("connected", st)
        self.assertIn("connected_ssid", st)
        self.assertIn("ip_address", st)
        self.assertIn("has_internet", st)
        self.assertIn("saved_profiles", st)

    def test_list_available_wifi_networks(self):
        """Verify list_available_wifi_networks lists visible and saved profiles."""
        res = wc.list_available_wifi_networks()
        self.assertTrue(res.get("success"))
        self.assertIsInstance(res.get("networks"), list)
        self.assertIn("count", res)

    @patch("actions.windows_control._native_wlan_connect", return_value=True)
    @patch("actions.windows_control.get_wifi_status")
    def test_connect_wifi_mocked(self, mock_status, mock_native):
        """Verify connect_wifi initiates connection and verifies state."""
        mock_status.return_value = {
            "enabled": True,
            "connected": True,
            "connected_ssid": "Test_Network",
            "has_internet": True
        }
        res = wc.connect_wifi("Test_Network")
        self.assertTrue(res.get("verified"))
        self.assertEqual(res.get("ssid"), "Test_Network")

    @patch("actions.windows_control._native_wlan_disconnect", return_value=True)
    @patch("actions.windows_control.get_wifi_status")
    def test_disconnect_wifi_mocked(self, mock_status, mock_native_disc):
        """Verify disconnect_wifi initiates disconnect and verifies state."""
        mock_status.return_value = {"connected": False}
        res = wc.disconnect_wifi()
        self.assertTrue(res.get("verified"))


class TestBluetoothManagement(unittest.TestCase):
    def test_bluetooth_status_structure(self):
        """Verify get_bluetooth_status returns all required fields."""
        st = wc.get_bluetooth_status()
        self.assertIn("enabled", st)
        self.assertIn("state", st)
        self.assertIn("adapter_available", st)
        self.assertIn("devices", st)
        self.assertIn("connected_devices", st)

    def test_list_bluetooth_devices(self):
        """Verify list_bluetooth_devices returns a list of devices."""
        devs = wc.list_bluetooth_devices()
        self.assertIsInstance(devs, list)

    @patch("actions.windows_control._run_ps", return_value=(0, "", ""))
    @patch("actions.windows_control.get_bluetooth_status")
    def test_connect_bluetooth_device_verified(self, mock_status, mock_ps):
        """Verify connect_bluetooth_device verifies connection."""
        mock_status.return_value = {
            "enabled": True,
            "connected_devices": [{"Name": "OPPO Enco Buds2", "IsConnected": True}]
        }
        res = wc.connect_bluetooth_device("OPPO Enco Buds2")
        self.assertTrue(res.get("verified"))
        self.assertEqual(res.get("device"), "OPPO Enco Buds2")

    @patch("actions.windows_control._run_ps", return_value=(0, "", ""))
    @patch("actions.windows_control.get_bluetooth_status")
    def test_disconnect_bluetooth_device(self, mock_status, mock_ps):
        """Verify disconnect_bluetooth_device verifies disconnection."""
        mock_status.return_value = {
            "connected_devices": []
        }
        res = wc.disconnect_bluetooth_device("OPPO Enco Buds2")
        self.assertTrue(res.get("verified"))


class TestVoiceConfirmationWorkflow(unittest.TestCase):
    def test_confirm_resolution(self):
        """Verify confirm resolves pending action without screen interaction."""
        executed = []
        confirm.bind(MagicMock(), MagicMock(), MagicMock())
        msg = confirm.request("switch_wifi", "Switch Wi-Fi", "Connecting to new network", lambda: (executed.append(True) or "Done"))
        self.assertIn("[CONFIRMATION_PENDING]", msg)
        self.assertEqual(confirm.pending_title(), "Switch Wi-Fi")

        # User says 'confirm'
        res = confirm.resolve_by_voice("confirm")
        self.assertTrue(res)
        self.assertEqual(confirm.pending_title(), "")

    def test_cancel_resolution(self):
        """Verify cancel rejects pending action."""
        executed = []
        confirm.bind(MagicMock(), MagicMock(), MagicMock())
        confirm.request("switch_bt", "Switch Bluetooth", "Connecting to headphones", lambda: (executed.append(True) or "Done"))
        res = confirm.resolve_by_voice("cancel")
        self.assertFalse(res)
        self.assertEqual(confirm.pending_title(), "")
        self.assertEqual(len(executed), 0)


if __name__ == "__main__":
    unittest.main()

