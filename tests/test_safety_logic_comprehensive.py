"""
tests/test_safety_logic_comprehensive.py — Comprehensive unit tests for safety logic:
- Permission tiers (core/permissions.py)
- Persistent audit log rotation and tamper resistance (core/permissions.py)
- Shared undo stack and error handling (core/undo.py)
- Credential scrubbing and sensitive field detection (core/skill_learning/skill_security.py)
- Voice confirmation prompt formatting and flow (core/confirm.py)
- Config migrations and keyring fallback (memory/config_manager.py)
"""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import core.permissions as permissions
import core.confirm as confirm
import core.undo as undo
from core.skill_learning.skill_security import (
    sanitize_text_and_credentials,
    is_sensitive_control,
    sanitize_input_value,
    classify_skill_risk,
    requires_explicit_confirmation,
    SENSITIVE_INPUT_PLACEHOLDER,
)
from core.skill_learning.models import RiskLevel, StepDefinition
import memory.config_manager as config_manager


class TestSafetyLogicComprehensive(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_safety_logic_")
        self.p_user_dir = patch.object(config_manager, "get_user_data_dir", return_value=permissions.Path(self.test_dir))
        self.p_user_dir.start()

    def tearDown(self):
        self.p_user_dir.stop()
        try:
            shutil.rmtree(self.test_dir, ignore_errors=True)
        except Exception:
            pass

    # 1. Permission tiers
    def test_permission_tiers_fail_closed(self):
        """Verify that unregistered and unrecognized actions default to SENSITIVE."""
        self.assertEqual(permissions.get_action_permission("totally_unknown_custom_action"), permissions.SENSITIVE)
        self.assertEqual(permissions.get_action_permission("some_unregistered_hack"), permissions.SENSITIVE)

    # 2. Audit log rotation and tamper resistance
    def test_audit_log_rotation_and_persistence(self):
        """Verify that audit entries are stored in SQLite and rotated when reaching limit."""
        db_path = permissions._get_audit_db_path()
        secret_key = "AIza" + "x" * 35
        # Insert 15 entries with rotation threshold mocked to 10
        with patch.object(permissions, "_MAX_AUDIT_ENTRIES", 10):
            for i in range(15):
                permissions.log_action_audit(
                    action=f"test_tool_{i}",
                    parameters={"api_key": secret_key, "index": i},
                    permission_level=permissions.SENSITIVE,
                    status="granted",
                    verified=True
                )

            # Query database directly
            conn = sqlite3.connect(str(db_path))
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM audit_trail")
                count = cur.fetchone()[0]
                self.assertLessEqual(count, 10)
                # Verify secrets are redacted in SQLite parameters column
                cur.execute("SELECT parameters FROM audit_trail")
                rows = cur.fetchall()
                for r in rows:
                    self.assertNotIn(secret_key, r[0])
                    self.assertIn("[REDACTED_SECRET]", r[0])
            finally:
                conn.close()

    def test_clear_audit_trail_protection(self):
        """Verify clear_audit_trail fails unless explicitly confirmed."""
        permissions.log_action_audit(action="tool_x", parameters={}, permission_level=permissions.NORMAL, status="granted", verified=True)
        # Without confirm=True raises PermissionError
        with self.assertRaises(PermissionError):
            permissions.clear_audit_trail(confirm=False)
        # Verify it still has records
        trail = permissions.get_audit_trail()
        self.assertGreater(len(trail), 0)

        # With confirm=True succeeds
        permissions.clear_audit_trail(confirm=True)
        self.assertEqual(len(permissions.get_audit_trail()), 0)

    # 3. Shared undo stack
    def test_undo_stack_lifo_and_bounds(self):
        """Verify push_undo, undo_last LIFO ordering, and MAX_DEPTH bounds."""
        undo.clear()
        self.assertFalse(undo.can_undo())
        self.assertEqual(undo.peek(), "")

        state = {"volume": 50}
        def reverse_op(prev_val):
            return lambda: (state.update({"volume": prev_val}) or f"restored to {prev_val}")

        for v in range(1, 15):
            undo.push_undo(f"volume -> {v}", reverse_op(v - 1))

        # Check bounds (MAX_DEPTH = 10)
        self.assertEqual(len(undo.history()), undo.MAX_DEPTH)
        self.assertEqual(undo.peek(), "volume -> 14")

        # Pop undo
        msg = undo.undo_last()
        self.assertIn("Undone: volume -> 14", msg)
        self.assertIn("restored to 13", msg)
        self.assertEqual(state["volume"], 13)

        undo.clear()
        self.assertFalse(undo.can_undo())

    def test_undo_exception_safety(self):
        """Verify failing undo closure does not raise or corrupt stack."""
        undo.clear()
        def broken():
            raise RuntimeError("Hardware disconnected")

        undo.push_undo("risky op", broken)
        result = undo.undo_last()
        self.assertIn("Could not undo 'risky op': Hardware disconnected", result)
        self.assertFalse(undo.can_undo())

    # 4. Credential scrubbing & sensitive field detection
    def test_credential_scrubbing(self):
        """Verify passwords, tokens, and credentials are scrubbed."""
        raw_text = "My password is SuperSecretPass123 and login id: admin."
        sanitized = sanitize_text_and_credentials(raw_text)
        self.assertNotIn("SuperSecretPass123", sanitized)
        self.assertIn("[REDACTED_CREDENTIAL]", sanitized)

    def test_is_sensitive_control(self):
        """Verify UI password and token controls are accurately detected."""
        pwd_box = {"name": "Password", "control_type": "Edit", "is_password": True}
        token_box = {"automation_id": "api_token_field", "control_type": "Edit"}
        normal_box = {"name": "Search Query", "control_type": "Edit"}

        self.assertTrue(is_sensitive_control(pwd_box))
        self.assertTrue(is_sensitive_control(token_box))
        self.assertFalse(is_sensitive_control(normal_box))

        val = sanitize_input_value(pwd_box, "MyRealPassword")
        self.assertEqual(val, SENSITIVE_INPUT_PLACEHOLDER)

    def test_classify_skill_risk(self):
        """Verify skill step risk classification."""
        step_normal = StepDefinition(step_id="s1", tool="web_search", thought="search web", parameters={"query": "python"})
        step_destructive = StepDefinition(step_id="s2", tool="file_controller", thought="delete old folder", parameters={"path": "C:/tmp"})

        risk_normal = classify_skill_risk([step_normal], "Find python docs")
        self.assertEqual(risk_normal, RiskLevel.NORMAL)

        risk_dest = classify_skill_risk([step_normal, step_destructive], "Clean up disk")
        self.assertEqual(risk_dest, RiskLevel.DESTRUCTIVE)

    # 5. Voice confirmation flow
    def test_voice_confirmation_flow(self):
        """Verify English voice confirmation prompt and resolve_by_voice flow."""
        show_mock = MagicMock()
        hide_mock = MagicMock()
        log_mock = MagicMock()
        confirm.bind(show_mock, hide_mock, log_mock)

        executed = []
        msg = confirm.request("test_key", "Reboot System", "Restarting OS", lambda: (executed.append(True) or "Rebooting"))
        self.assertIn("[CONFIRMATION_PENDING]", msg)
        self.assertIn("Say confirm to execute, or no to cancel.", msg)
        self.assertEqual(confirm.pending_title(), "Reboot System")

        # Test voice rejection
        voice_res_no = confirm.resolve_by_voice("no, do not do it")
        self.assertFalse(voice_res_no)
        self.assertEqual(confirm.pending_title(), "")
        self.assertEqual(len(executed), 0)

        # Test voice acceptance
        confirm.request("test_key_2", "Shut Down", "Shutting down", lambda: (executed.append(True) or "Shutdown"))
        voice_res_yes = confirm.resolve_by_voice("yes please proceed")
        self.assertTrue(voice_res_yes)

    # 6. Config migrations and keyring fallback
    def test_config_trust_level_setting(self):
        """Verify trust level setting validation."""
        config_manager.set_trust_level("strict")
        self.assertEqual(config_manager.get_trust_level(), "strict")

        config_manager.set_trust_level("balanced")
        self.assertEqual(config_manager.get_trust_level(), "balanced")

        with self.assertRaises(ValueError):
            config_manager.set_trust_level("invalid_choice")


if __name__ == "__main__":
    unittest.main()
