"""
tests/test_confirmation_and_audit.py — Unit Tests for Sensitive Confirmations and Persistent Audit Log.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import confirm
from core.permissions import (
    DESTRUCTIVE,
    NORMAL,
    READ_ONLY,
    SENSITIVE,
    approve_sensitive_action_for_session,
    clear_audit_trail,
    get_action_permission,
    get_audit_trail,
    log_action_audit,
    requires_confirmation,
    reset_session_approvals,
)
from memory import config_manager


class TestConfirmationAndAudit(unittest.TestCase):
    def setUp(self):
        reset_session_approvals()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_user_dir = Path(self.temp_dir.name)

        # Patch user data dir for isolated SQLite test
        self.data_dir_patcher = patch("memory.config_manager.get_user_data_dir", return_value=self.test_user_dir)
        self.data_dir_patcher.start()

    def tearDown(self):
        self.data_dir_patcher.stop()
        self.temp_dir.cleanup()
        reset_session_approvals()

    # ── Task 4: Trust Levels & Confirmation Policy ────────────────────────────

    def test_trust_level_strict(self):
        """Strict trust level: SENSITIVE and DESTRUCTIVE always require confirmation."""
        with patch.object(config_manager, "get_trust_level", return_value="strict"):
            self.assertTrue(requires_confirmation("computer_control"))
            self.assertTrue(requires_confirmation("dev_agent"))
            self.assertTrue(requires_confirmation("shutdown_jarvis"))
            self.assertFalse(requires_confirmation("open_app"))
            self.assertFalse(requires_confirmation("web_search"))

    def test_trust_level_balanced_first_use_vs_approved(self):
        """Balanced trust level (default): SENSITIVE confirmed on 1st use, skipped once approved."""
        with patch.object(config_manager, "get_trust_level", return_value="balanced"):
            # First use requires confirmation
            self.assertTrue(requires_confirmation("windows_control"))
            self.assertTrue(requires_confirmation("computer_settings"))

            # Approve for session
            approve_sensitive_action_for_session("windows_control")
            self.assertFalse(requires_confirmation("windows_control"))

            # Other sensitive tool still requires confirmation
            self.assertTrue(requires_confirmation("computer_settings"))

            # DESTRUCTIVE always requires confirmation regardless of approval
            self.assertTrue(requires_confirmation("shutdown_jarvis"))

    def test_trust_level_trusted(self):
        """Trusted trust level: SENSITIVE runs without confirmation, DESTRUCTIVE still confirms."""
        with patch.object(config_manager, "get_trust_level", return_value="trusted"):
            self.assertFalse(requires_confirmation("windows_control"))
            self.assertFalse(requires_confirmation("computer_control"))
            self.assertTrue(requires_confirmation("shutdown_jarvis"))

    def test_voice_friendly_confirmation_flow(self):
        """Verify short English confirmation prompt and resolve_by_voice matching."""
        executed = []
        dummy_action = lambda: executed.append("run") or "Success"

        # Mock UI banner callbacks
        confirm.bind(show=lambda title, detail: None, hide=lambda: None)

        prompt = confirm.request("test_tool", "Change Display Resolution", "Resolution to 1080p", dummy_action)
        self.assertIn("[CONFIRMATION_PENDING]", prompt)
        self.assertIn("Please answer yes to confirm, or no to cancel.", prompt)
        self.assertEqual(confirm.pending_title(), "Change Display Resolution")

        # Voice confirms with 'yes'
        resolved = confirm.resolve_by_voice("Yes, please proceed")
        self.assertTrue(resolved)
        self.assertEqual(confirm.pending_title(), "")

    # ── Task 5: Persistent Tamper-Resistant Audit Log ─────────────────────────

    def test_audit_log_persistence_across_instances(self):
        """Verify audit entries are saved to SQLite and read back accurately."""
        # Log entry with sensitive secret
        test_secret = "AIzaSySecretInParams1234567890"
        with patch.object(config_manager, "get_gemini_key", return_value=test_secret):
            log_action_audit(
                action="test_action_persisted",
                parameters={"secret_param": test_secret, "file": "test.txt"},
                permission_level="sensitive",
                status="done",
                verified=True,
            )

        # Query audit trail from DB
        entries = get_audit_trail(limit=10)
        self.assertGreaterEqual(len(entries), 1)
        last = entries[-1]
        self.assertEqual(last["action"], "test_action_persisted")
        self.assertEqual(last["permission_level"], "sensitive")
        self.assertTrue(last["verified"])
        # Verified parameters redacted
        self.assertNotIn(test_secret, last["parameters"])
        self.assertIn("[REDACTED_API_KEY]", last["parameters"])

    def test_clear_audit_trail_requires_confirmation(self):
        """Verify clear_audit_trail refuses without explicit confirm=True."""
        with self.assertRaises(PermissionError):
            clear_audit_trail(confirm=False)

        # Succeeds when explicit confirm=True passed
        clear_audit_trail(confirm=True)
        self.assertEqual(len(get_audit_trail(limit=10)), 0)


if __name__ == "__main__":
    unittest.main()

