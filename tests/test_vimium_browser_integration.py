"""
Unit and Integration Tests for Vimium C Browser Navigation & Red UI Theme
"""
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from actions.vimium_c import (
    VimiumCController,
    VimiumHint,
    get_vimium_controller,
    get_vimium_extension_path,
)
from actions.browser_control import TOOL as BROWSER_TOOL


class TestVimiumCIntegration(unittest.TestCase):

    def test_extension_path_discovery(self):
        """Verify Vimium C extension path is discovered and contains manifest.json."""
        ext_path = get_vimium_extension_path()
        self.assertIsNotNone(ext_path, "Vimium C extension path must be discovered.")
        manifest = Path(ext_path) / "manifest.json"
        self.assertTrue(manifest.exists(), f"manifest.json must exist at {manifest}")

    def test_vimium_hint_dataclass(self):
        """Verify VimiumHint formatting and representation."""
        hint = VimiumHint(
            hint_code="SA",
            x=150,
            y=240,
            tag="A",
            element_text="Documentation",
            href="https://example.com/docs",
            role="link",
        )
        self.assertEqual(hint.hint_code, "SA")
        summary = hint.summary()
        self.assertIn("[SA]", summary)
        self.assertIn("Documentation", summary)

    def test_find_hint_for_target_exact(self):
        """Verify exact text matching in hint resolution."""
        vc = VimiumCController()
        hints = [
            VimiumHint(hint_code="A", x=10, y=10, tag="BUTTON", element_text="Submit"),
            VimiumHint(hint_code="B", x=10, y=50, tag="BUTTON", element_text="Cancel"),
            VimiumHint(hint_code="C", x=10, y=90, tag="A", element_text="Home"),
        ]
        match = vc.match_hint("Submit", hints)
        self.assertIsNotNone(match)
        self.assertEqual(match.hint_code, "A")

    def test_find_hint_for_target_substring_and_aria(self):
        """Verify substring and aria-label matching."""
        vc = VimiumCController()
        hints = [
            VimiumHint(hint_code="D", x=10, y=10, tag="INPUT", placeholder="Search Google..."),
            VimiumHint(hint_code="E", x=10, y=50, tag="A", aria_label="View Shopping Cart"),
        ]
        match = vc.match_hint("Cart", hints)
        self.assertIsNotNone(match)
        self.assertEqual(match.hint_code, "E")

        match_input = vc.match_hint("Search Google", hints)
        self.assertIsNotNone(match_input)
        self.assertEqual(match_input.hint_code, "D")

    def test_find_hint_by_hint_code(self):
        """Verify user can directly target by typing the hint label itself."""
        vc = VimiumCController()
        hints = [
            VimiumHint(hint_code="F", x=10, y=10, tag="A", element_text="Overview"),
            VimiumHint(hint_code="JK", x=10, y=50, tag="A", element_text="Settings"),
        ]
        match = vc.match_hint("JK", hints)
        self.assertIsNotNone(match)
        self.assertEqual(match.hint_code, "JK")

    def test_browser_tool_actions_registered(self):
        """Verify browser_control TOOL includes Vimium actions."""
        actions_desc = BROWSER_TOOL["parameters"]["properties"]["action"]["description"]
        self.assertIn("hint_click", actions_desc)
        self.assertIn("hint_scroll", actions_desc)
        self.assertIn("vimium_command", actions_desc)
        self.assertIn("get_hints", actions_desc)


class TestVimiumAsyncController(unittest.IsolatedAsyncioTestCase):

    async def test_scroll_keyboard_mappings(self):
        """Verify directional scrolling sends proper Vimium keyboard keys."""
        vc = VimiumCController()
        mock_page = AsyncMock()

        # Down -> j
        await vc.scroll(mock_page, direction="down", amount=1)
        mock_page.keyboard.press.assert_called_with("j")

        # Up -> k
        await vc.scroll(mock_page, direction="up", amount=1)
        mock_page.keyboard.press.assert_called_with("k")

        # Top -> gg
        await vc.scroll(mock_page, direction="top", amount=1)
        mock_page.keyboard.press.assert_called_with("g")

        # Bottom -> G
        await vc.scroll(mock_page, direction="bottom", amount=1)
        mock_page.keyboard.press.assert_called_with("G")

    async def test_tab_and_input_commands(self):
        """Verify tab switching and input focus shortcuts."""
        vc = VimiumCController()
        mock_page = AsyncMock()

        await vc.next_tab(mock_page)
        mock_page.keyboard.press.assert_called_with("J")

        await vc.prev_tab(mock_page)
        mock_page.keyboard.press.assert_called_with("K")

        await vc.focus_input(mock_page)
        mock_page.keyboard.press.assert_called_with("i")


if __name__ == "__main__":
    unittest.main()
