import unittest
from unittest.mock import patch, MagicMock
from actions import computer_settings, open_app, windows_control


class TestShellSafety(unittest.TestCase):

    def test_xrandr_brightness_rejects_malicious_output_names(self):
        # Mock xrandr output with command injection in output name
        malicious_xrandr_stdout = (
            "eDP-1; calc.exe connected primary 1920x1080+0+0\n"
            "  Brightness: 0.8\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=malicious_xrandr_stdout)
            computer_settings._adjust_xrandr_brightness(0.1)

            # mock_run was called for `xrandr --verbose`, but must NOT execute the injection command
            for call_item in mock_run.call_args_list:
                args, kwargs = call_item
                self.assertFalse(kwargs.get("shell", False), "shell=True must not be used")
                cmd = args[0]
                self.assertNotIn("calc.exe", cmd)

    def test_open_app_does_not_use_shell_for_malicious_names(self):
        malicious_app = "notepad.exe & calc.exe"
        with patch("subprocess.Popen") as mock_popen, \
             patch("shutil.which", return_value="C:\\Windows\\System32\\notepad.exe"):
            open_app._launch_windows(malicious_app)

            if mock_popen.called:
                args, kwargs = mock_popen.call_args
                self.assertFalse(kwargs.get("shell", False), "shell=True must not be used")
                # Command must be a list containing the resolved executable only
                self.assertIsInstance(args[0], list)
                self.assertEqual(args[0], ["C:\\Windows\\System32\\notepad.exe"])

    def test_bluetooth_connect_sanitizes_script_injection(self):
        malicious_device = 'MyHeadphones" ; calc.exe ; #'
        with patch("actions.windows_control.list_bluetooth_devices", return_value=[]), \
             patch("actions.windows_control._run_ps") as mock_run_ps:
            mock_run_ps.return_value = (0, "", "")
            windows_control.connect_bluetooth_device(malicious_device)

            self.assertTrue(mock_run_ps.called)
            script_sent = mock_run_ps.call_args_list[0][0][0]
            # Must NOT contain the unescaped quotes or malicious commands
            self.assertNotIn('MyHeadphones" ; calc.exe', script_sent)
            self.assertIn('$name = "MyHeadphones  calc.exe"', script_sent)


if __name__ == "__main__":
    unittest.main()
