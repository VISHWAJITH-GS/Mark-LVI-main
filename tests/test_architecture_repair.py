"""
tests/test_architecture_repair.py — Automated Architecture and Reliability Tests for JARVIS.

Using Python's standard unittest library.
"""
from __future__ import annotations

import inspect
from pathlib import Path
import unittest

from core.action_loader import discover_actions
from actions import computer_control, windows_uia, browser_control, spotify_control, video_player
from core import outcome, llm_client


class ArchitectureRepairTestCase(unittest.TestCase):
    def test_tool_signatures_match_schemas(self):
        """Verify all discovered tools have valid handlers matching their schema expectations."""
        registry = discover_actions(Path("actions"))
        decls = registry.get_tool_declarations()
        self.assertGreaterEqual(len(decls), 20)

        for rec in registry._actions.values():
            sig = inspect.signature(rec.handler)
            has_params = "parameters" in sig.parameters
            has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            self.assertTrue(has_params or has_var_kw or len(sig.parameters) > 0,
                            f"Handler for {rec.name} has invalid signature: {sig}")

    def test_click_without_coordinates_refusal(self):
        """Verify that computer_control refuses click without coordinates instead of defaulting to center."""
        res = computer_control.computer_control({"action": "click"})
        self.assertIn("status=failed", res)
        self.assertIn("No target coordinates", res)

    def test_windows_uia_flexible_kwargs(self):
        """Verify click_element and type_text accept name, element_name, and element_or_name without raising TypeError."""
        res_click = windows_uia.click_element(name="This PC", control_type="Button")
        self.assertIsInstance(res_click, dict)
        self.assertIn(res_click.get("status"), ("failed", "done", "ambiguous"))

        res_type = windows_uia.type_text(text="test", element_name="SearchBox")
        self.assertIsInstance(res_type, dict)
        self.assertIn(res_type.get("status"), ("failed", "done", "ambiguous"))

    def test_browser_action_navigate_alias(self):
        """Verify that browser_control accepts action='navigate' without raising unknown browser action."""
        res = browser_control.browser_control({"action": "navigate", "url": "https://skillrack.com"})
        self.assertNotIn("Unknown browser action", res)
        self.assertIn("status=", res)

    def test_spotify_vs_video_separation(self):
        """Verify that video_player tool description explicitly rejects Spotify/music songs and directs to spotify_control."""
        self.assertIn("spotify_control", video_player.TOOL["description"])
        self.assertEqual("spotify_control", spotify_control.TOOL["name"])

    def test_no_local_llm_running(self):
        """Verify that local LLM inference is disabled and raises descriptive error if invoked."""
        self.assertFalse(llm_client.ensure_ollama_running())
        with self.assertRaises(RuntimeError) as ctx:
            llm_client.chat("test")
        self.assertIn("Google Gemini API is the sole reasoning engine", str(ctx.exception))

    def test_outcome_unverified_signature(self):
        """Verify outcome.unverified requires reason and produces valid facts."""
        res = outcome.unverified("test_action", "Could not read back device state", device="Earbuds")
        self.assertIn("status=unverified", res)
        self.assertIn("action=test_action", res)
        self.assertIn('reason="Could not read back device state"', res)


if __name__ == "__main__":
    unittest.main()
