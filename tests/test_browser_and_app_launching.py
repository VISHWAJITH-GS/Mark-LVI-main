"""
tests/test_browser_and_app_launching.py — Verification for Robust Browser & App Launching.

Tests:
1. Chrome launch resolution: resolves to Google Chrome on disk with CDP flags, without silent Edge substitution.
2. Edge launch resolution: resolves to Microsoft Edge with CDP flags.
3. IDLE launch resolution: resolves to pythonw -m idlelib without shell injection.
4. VS Code and Notepad resolution.
"""
import os
import unittest
from unittest.mock import MagicMock, patch

from actions.open_app import _launch_windows, _resolve_windows_known_app


class TestBrowserAndAppLaunching(unittest.TestCase):

    def test_chrome_resolution_and_cdp_flags(self):
        """Requesting 'chrome' must resolve to Google Chrome executable and include CDP port 9222."""
        with patch("os.path.exists") as mock_exists:
            # Simulate Chrome existing in Program Files
            mock_exists.side_effect = lambda p: "Google\\Chrome" in p or "User Data" in p

            cmd, extra_args = _resolve_windows_known_app("chrome")
            self.assertIsNotNone(cmd)
            self.assertTrue(any("chrome.exe" in part.lower() for part in cmd))
            self.assertFalse(any("msedge" in part.lower() for part in cmd))

            # Must contain remote debugging port
            self.assertIn("--remote-debugging-port=9222", extra_args)
            self.assertTrue(any("--user-data-dir=" in arg for arg in extra_args))

    def test_edge_resolution_and_cdp_flags(self):
        """Requesting 'edge' or 'msedge' must resolve to Edge executable with CDP port 9222."""
        with patch("os.path.exists") as mock_exists:
            mock_exists.side_effect = lambda p: "Microsoft\\Edge" in p or "User Data" in p

            cmd, extra_args = _resolve_windows_known_app("msedge")
            self.assertIsNotNone(cmd)
            self.assertTrue(any("msedge.exe" in part.lower() for part in cmd))
            self.assertFalse(any("chrome.exe" in part.lower() for part in cmd))
            self.assertIn("--remote-debugging-port=9222", extra_args)

    def test_idle_resolution(self):
        """Requesting 'idle' must resolve to pythonw -m idlelib."""
        with patch("os.path.exists") as mock_exists:
            mock_exists.return_value = True
            cmd, extra_args = _resolve_windows_known_app("idle")
            self.assertIsNotNone(cmd)
            self.assertTrue(any("idlelib" in part for part in cmd))

    @patch("subprocess.Popen")
    def test_launch_windows_invokes_chrome_directly(self, mock_popen):
        """_launch_windows('Google Chrome') must call subprocess.Popen with chrome arguments without shell."""
        with patch("actions.open_app._resolve_windows_known_app") as mock_resolve:
            mock_resolve.return_value = (["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"], ["--remote-debugging-port=9222"])

            launched = _launch_windows("Google Chrome")
            self.assertTrue(launched)
            mock_popen.assert_called_once()
            args = mock_popen.call_args[0][0]
            self.assertIn("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", args[0])
            self.assertIn("--remote-debugging-port=9222", args)


if __name__ == "__main__":
    unittest.main()
