"""
tests/test_code_typing_and_editors.py — Verification for Code Insertion & Multi-Language Formatting.

Tests:
1. Exact preservation of indentation, newlines, and curly braces across languages:
   - C / C++ (#include <stdio.h>, braces)
   - Python (4-space indentation)
   - Java (public class, methods)
   - HTML / CSS / JS / SQL
2. Prevention of cascading auto-closing braces and merged lines by utilizing clipboard insertion.
3. IDLE window distinction: when IDLE Shell is active, pressing Ctrl+N to obtain a clean Editor window.
4. code_helper 'insert_code' action dispatch.
"""
import unittest
from unittest.mock import MagicMock, call, patch

from actions.code_helper import code_helper
from actions.computer_control import _insert_code, _is_code_or_multiline, _type, computer_control


class TestCodeTypingAndEditors(unittest.TestCase):

    def test_code_detection_heuristic(self):
        """Must accurately detect code and multi-line structures to route away from keystroke mangling."""
        c_code = "#include <stdio.h>\nint main() {\n    printf(\"Hello\\n\");\n    return 0;\n}"
        py_code = "def calculate_factorial(n):\n    if n <= 1:\n        return 1\n    return n * calculate_factorial(n - 1)"
        single_line_plain = "regular text typing without any newlines"

        self.assertTrue(_is_code_or_multiline(c_code))
        self.assertTrue(_is_code_or_multiline(py_code))
        self.assertFalse(_is_code_or_multiline(single_line_plain))

    @patch("actions.computer_control.pyautogui")
    @patch("actions.computer_control.pyperclip")
    def test_c_code_insertion_preserves_braces_and_headers(self, mock_clip, mock_gui):
        """C code must be copied to clipboard and pasted with Ctrl+V, preventing double braces."""
        c_code = (
            "#include <stdio.h>\n"
            "#include <stdlib.h>\n\n"
            "int main() {\n"
            "    int a = 10;\n"
            "    if (a > 5) {\n"
            "        printf(\"Greater\\n\");\n"
            "    }\n"
            "    return 0;\n"
            "}"
        )

        res = _insert_code(code=c_code, editor_title="Notepad", language="c")
        self.assertIn("Successfully inserted", res)
        self.assertIn("lines of code (c)", res)

        # Must copy the exact C code intact
        mock_clip.copy.assert_called_once_with(c_code)
        # Must send Ctrl+V
        mock_gui.hotkey.assert_called_with("ctrl", "v")

    @patch("actions.computer_control.pyautogui")
    @patch("actions.computer_control.pyperclip")
    def test_python_code_preserves_indentation(self, mock_clip, mock_gui):
        """Python multi-line code must preserve 4-space indentation without stripping or doubling."""
        py_code = (
            "def is_prime(n: int) -> bool:\n"
            "    if n <= 1:\n"
            "        return False\n"
            "    for i in range(2, int(n ** 0.5) + 1):\n"
            "        if n % i == 0:\n"
            "            return False\n"
            "    return True\n"
        )

        res = _insert_code(code=py_code, editor_title="Visual Studio Code", language="python")
        self.assertIn("Successfully inserted", res)
        mock_clip.copy.assert_called_once_with(py_code.strip())
        mock_gui.hotkey.assert_called_with("ctrl", "v")

    @patch("actions.computer_control.pyautogui")
    @patch("actions.computer_control.pyperclip")
    @patch("actions.windows_uia.get_active_window")
    def test_idle_opens_new_editor_if_shell_is_active(self, mock_active_win, mock_clip, mock_gui):
        """When IDLE is targeted and Python Shell is active, send Ctrl+N to open clean Editor window."""
        mock_active_win.return_value = {"status": "success", "title": "Python 3.13.0 Shell"}
        sample_code = "print('Hello IDLE')"

        res = _insert_code(code=sample_code, editor_title="IDLE", language="python")
        self.assertIn("Successfully inserted", res)

        # Must have sent Ctrl+N to open Editor window
        mock_gui.hotkey.assert_any_call("ctrl", "n")
        # Must have pasted via Ctrl+V
        mock_gui.hotkey.assert_called_with("ctrl", "v")

    @patch("actions.computer_control.pyautogui")
    @patch("actions.computer_control.pyperclip")
    def test_type_action_falls_back_to_clipboard_on_multiline(self, mock_clip, mock_gui):
        """Invoking computer_control with action='type' on multi-line text must safely use clipboard."""
        multiline_sql = "SELECT id, username, email\nFROM users\nWHERE active = 1\nORDER BY created_at DESC;"

        res = _type(multiline_sql)
        self.assertIn("Pasted", res)
        mock_clip.copy.assert_called_once_with(multiline_sql)
        mock_gui.hotkey.assert_called_with("ctrl", "v")

    @patch("actions.computer_control.pyautogui")
    @patch("actions.computer_control.pyperclip")
    def test_code_helper_insert_code_action(self, mock_clip, mock_gui):
        """code_helper tool with action='insert_code' must insert code into target editor."""
        html_code = "<!DOCTYPE html>\n<html>\n<head><title>Test</title></head>\n<body><h1>Hello</h1></body>\n</html>"

        res = code_helper({
            "action": "insert_code",
            "code": html_code,
            "language": "html",
            "editor": "Notepad",
        })
        self.assertIn("Successfully inserted", res)
        mock_clip.copy.assert_called_once_with(html_code)
        mock_gui.hotkey.assert_called_with("ctrl", "v")


if __name__ == "__main__":
    unittest.main()
