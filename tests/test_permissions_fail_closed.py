"""
tests/test_permissions_fail_closed.py — Unit Tests for Fail-Closed Permissions Policy.
"""
import unittest

from core.permissions import (
    READ_ONLY,
    NORMAL,
    SENSITIVE,
    DESTRUCTIVE,
    PERMISSION_LEVELS,
    get_action_permission,
    ACTION_PERMISSIONS,
)
from core.action_loader import get_default_registry


class TestPermissionsFailClosed(unittest.TestCase):
    def test_unknown_tool_defaults_to_sensitive(self):
        """Verify fail-closed policy: any unknown tool strictly defaults to SENSITIVE."""
        self.assertEqual(get_action_permission("completely_unknown_tool"), SENSITIVE)
        self.assertEqual(get_action_permission("malicious_injected_action"), SENSITIVE)
        self.assertEqual(get_action_permission(""), SENSITIVE)
        self.assertEqual(get_action_permission(None), SENSITIVE)

    def test_destructive_subactions_detected(self):
        """Verify actions with destructive parameter intents escalate to DESTRUCTIVE."""
        self.assertEqual(get_action_permission("computer_settings", {"action": "shutdown"}), DESTRUCTIVE)
        self.assertEqual(get_action_permission("computer_control", {"action": "kill"}), DESTRUCTIVE)
        self.assertEqual(get_action_permission("file_controller", {"action": "delete"}), DESTRUCTIVE)

    def test_every_registered_action_has_explicit_tier(self):
        """Verify that every tool discovered in the ActionRegistry has an explicit valid permission tier."""
        reg = get_default_registry()
        self.assertGreater(len(reg.names()), 0)

        for name in reg.names():
            rec = reg.get_action_record(name)
            self.assertIsNotNone(rec, f"Record for '{name}' not found")
            tier = rec.permission
            self.assertIn(
                tier,
                PERMISSION_LEVELS,
                f"Action '{name}' has missing or invalid permission tier '{tier}'",
            )
            # Ensure ACTION_PERMISSIONS also maps it or it equals rec.permission
            effective = get_action_permission(name)
            self.assertIn(
                effective,
                PERMISSION_LEVELS,
                f"Action '{name}' effective permission is invalid: '{effective}'",
            )


if __name__ == "__main__":
    unittest.main()

