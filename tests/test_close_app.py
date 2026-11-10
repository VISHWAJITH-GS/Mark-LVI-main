import unittest
import subprocess
import time
from actions import computer_control

class TestCloseApp(unittest.TestCase):
    def test_close_calculator(self):
        # Open Calculator
        p = subprocess.Popen("calc.exe")
        time.sleep(1.5)
        res = computer_control.computer_control({"action": "close_app", "app_name": "calculator"})
        self.assertIn("status=done", res)
        self.assertIn("verified=true", res)

    def test_close_file_explorer(self):
        # Open an explorer window
        p = subprocess.Popen(["explorer.exe", "C:\\"])
        time.sleep(1.5)
        res = computer_control.computer_control({"action": "close_app", "app_name": "File Explorer"})
        self.assertIn("status=done", res)
        self.assertIn("verified=true", res)

    def test_close_notepad(self):
        # Open Notepad
        p = subprocess.Popen("notepad.exe")
        time.sleep(1.5)
        res = computer_control.computer_control({"action": "close_app", "app_name": "notepad"})
        self.assertIn("status=done", res)
        self.assertIn("verified=true", res)

    def test_action_aliases(self):
        # Test close_window alias
        p = subprocess.Popen("notepad.exe")
        time.sleep(1.5)
        res = computer_control.computer_control({"action": "close_window", "title": "notepad"})
        self.assertIn("status=done", res)
        self.assertIn("verified=true", res)

if __name__ == "__main__":
    unittest.main()

