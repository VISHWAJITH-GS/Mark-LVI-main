"""
tests/test_unified_architecture.py — Automated Tests for Unified Tool Execution Architecture,
Result Normalization Contract, Security Permissions, and Intelligent Memory.
"""
from __future__ import annotations

import inspect
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from core import outcome, permissions, llm_client
from core.action_loader import get_default_registry, discover_actions
from memory import config_manager, memory_manager


class UnifiedArchitectureTestCase(unittest.TestCase):
    def setUp(self):
        self.registry = get_default_registry()

    def test_all_actions_have_valid_schemas_and_handlers(self):
        """Verify all discovered tools have valid handlers matching schema parameters."""
        decls = self.registry.get_tool_declarations()
        self.assertGreaterEqual(len(decls), 20)

        for rec in self.registry._actions.values():
            self.assertTrue(rec.valid, f"Action {rec.name} marked invalid: {rec.error}")
            self.assertIsNotNone(rec.handler, f"Action {rec.name} has no handler")
            self.assertIn("type", rec.parameters)
            self.assertEqual(rec.parameters["type"], "OBJECT")

            # Check handler signature
            sig = inspect.signature(rec.handler)
            has_params = "parameters" in sig.parameters
            has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            self.assertTrue(has_params or has_var_kw or len(sig.parameters) > 0,
                            f"Handler for {rec.name} has unusable signature: {sig}")

    def test_action_result_contract_statuses(self):
        """Verify ActionResult enforces standard status values."""
        for st in outcome.ALLOWED_STATUSES:
            res = outcome.ActionResult(
                status=st,
                action="test_action",
                success=(st == outcome.STATUS_DONE),
                verified=(st == outcome.STATUS_DONE),
                message=f"Message for {st}",
            )
            d = res.to_dict()
            self.assertEqual(d["status"], st)
            self.assertEqual(d["action"], "test_action")
            self.assertIn("duration_ms", d)

    def test_result_normalizer_handles_strings_and_dicts(self):
        """Verify normalize_result correctly maps legacy string formats and dicts."""
        # 1. Done line
        r1 = outcome.normalize_result("status=done action=open_app app=Spotify verified=true")
        self.assertEqual(r1.status, outcome.STATUS_DONE)
        self.assertTrue(r1.success)
        self.assertTrue(r1.verified)

        # 2. Unverified line
        r2 = outcome.normalize_result('status=unverified action=windows_control reason="Could not verify radio"')
        self.assertEqual(r2.status, outcome.STATUS_UNVERIFIED)
        self.assertFalse(r2.success)
        self.assertFalse(r2.verified)

        # 3. Failed line
        r3 = outcome.normalize_result('status=failed action=click reason="Out of bounds"')
        self.assertEqual(r3.status, outcome.STATUS_FAILED)
        self.assertFalse(r3.success)

        # 4. Dictionary result
        r4 = outcome.normalize_result({"status": "done", "action": "spotify_control", "verified": True, "success": True, "track": "Beat It"})
        self.assertEqual(r4.status, outcome.STATUS_DONE)
        self.assertEqual(r4.data.get("track"), "Beat It")

    def test_outcome_done_never_crashes_with_string_or_positional_args(self):
        """Prevent TypeError: outcome.done() argument after ** must be a mapping, not str."""
        out = outcome.done("test_action", "legacy_positional_string", extra_key="value")
        self.assertIn("status=done", out)
        self.assertIn("action=test_action", out)
        self.assertIn("extra_key=value", out)
        self.assertIn("detail=legacy_positional_string", out)

    def test_registry_parameter_validation(self):
        """Verify registry rejects execution when required parameter is missing."""
        rec = self.registry.get_action_record("takeover_desktop")
        self.assertIsNotNone(rec)
        # 'goal' is required for takeover_desktop
        res = self.registry.execute("takeover_desktop", {})
        self.assertEqual(res.status, outcome.STATUS_FAILED)
        self.assertIn("Missing required parameter 'goal'", res.error)

    def test_security_permission_tiers_and_confirmation(self):
        """Verify permission levels are correctly assigned and confirmation is required for destructive actions."""
        # Read-only
        p_web = permissions.get_action_permission("web_search")
        self.assertEqual(p_web, permissions.READ_ONLY)
        self.assertFalse(permissions.requires_confirmation("web_search"))

        # Sensitive
        p_win = permissions.get_action_permission("windows_control")
        self.assertEqual(p_win, permissions.SENSITIVE)

        # Destructive (shutdown via computer_settings)
        p_shut = permissions.get_action_permission("computer_settings", {"action": "shutdown"})
        self.assertEqual(p_shut, permissions.DESTRUCTIVE)
        self.assertTrue(permissions.requires_confirmation("computer_settings", {"action": "shutdown"}))

        # Test execution gate on destructive action without confirmation
        res = self.registry.execute("computer_settings", {"action": "shutdown"})
        self.assertEqual(res.status, outcome.STATUS_NEEDS_CONFIRMATION)

    def test_secret_redaction_and_scanning(self):
        """Verify secrets are redacted from logs and memory."""
        fake_key = "AIzaSyD-FakeTestKeyForJarvisSecretScan001"
        text = f"An error occurred with key {fake_key} in request."

        # Scan
        found = config_manager.scan_for_secrets(text)
        self.assertGreater(len(found), 0)

        # Redact
        redacted = config_manager.redact_secret(text)
        self.assertNotIn(fake_key, redacted)
        self.assertIn("[REDACTED", redacted)

        # Memory update redaction
        target_dict = {}
        memory_manager._recursive_update(target_dict, {"notes": {"key": {"value": f"My secret is {fake_key}"}}})
        saved_val = target_dict["notes"]["key"]["value"]
        self.assertNotIn(fake_key, saved_val)

    def test_in_memory_short_term_and_task_history(self):
        """Verify short-term execution context and task step tracking."""
        memory_manager.clear_short_term_context()
        memory_manager.clear_task_history()

        memory_manager.update_short_term_context({"active_window": "Spotify", "task_id": "T-100"})
        ctx = memory_manager.get_short_term_context()
        self.assertEqual(ctx.get("active_window"), "Spotify")

        memory_manager.record_task_step("connect_bluetooth", "done", True, {"device": "OPPO Enco Buds 2"})
        memory_manager.record_task_step("play_track", "done", True, {"track": "Beat It"})

        history = memory_manager.get_task_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["action"], "connect_bluetooth")
        self.assertEqual(history[1]["action"], "play_track")

    def test_sole_gemini_engine_enforced(self):
        """Verify that provider mode is strictly gemini and local LLMs are disabled."""
        self.assertEqual(config_manager.get_provider_mode(), config_manager.PROVIDER_MODE_GEMINI)
        with self.assertRaises(RuntimeError) as ctx:
            llm_client.chat("test")
        self.assertIn("sole reasoning engine", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
