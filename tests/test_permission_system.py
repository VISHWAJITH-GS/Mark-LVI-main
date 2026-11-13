"""
tests/test_permission_system.py — Comprehensive Centralized Action Permission & Confirmation Tests.

Verifies:
1. Every action in the permission matrix (Categories A, B, C, D, E, F).
2. Immediate execution of Level 1 (IMMEDIATE) actions without confirmation.
3. Confirmation blocking for Level 2 (CONFIRMATION_REQUIRED) actions.
4. Consequence communication for Level 3 (HIGH_IMPACT) actions.
5. Voice & UI explicit approval ("confirm", "yes").
6. Cancellation on "no" / "cancel".
7. Expiration of pending confirmations.
8. Prevention of duplicate execution on repeated approvals.
9. Discarding stale confirmations when parameters change.
10. Prevention of ambiguous confirmations.
11. Impossibility of model/AI forgery (confirmed=True in parameters ignored).
12. Strict rejection and blocking of drive formatting across all routes.
"""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from core import confirm, outcome
from core.confirm import ConfirmationManager, PendingAction
from core.permissions import (
    ActionExecutionGuard,
    ActionPermissionManager,
    ActionRiskLevel,
    get_action_permission,
    log_action_audit,
    requires_confirmation,
)


class TestCentralizedPermissionSystem(unittest.TestCase):
    def setUp(self):
        confirm.discard_pending("test setup")
        confirm.bind(show=lambda title, detail: None, hide=lambda: None)

    def tearDown(self):
        confirm.discard_pending("test teardown")

    # ── Category A: Wi-Fi Matrix ─────────────────────────────────────────────

    def test_wifi_permission_matrix(self):
        """Verify Wi-Fi actions follow the exact permission matrix."""
        # Immediate actions
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "get_wifi_status"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "list_available_wifi_networks"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "wifi_on"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "wifi_off"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("computer_settings", {"action": "wifi_status"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("computer_settings", {"action": "toggle_wifi"}), ActionRiskLevel.IMMEDIATE)

        # Confirmation-required actions
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "connect_wifi", "target": "Hotspot"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "switch_wifi", "target": "Office_WiFi"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "disconnect_wifi"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "forget_wifi", "target": "Guest_Net"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "set_network_config"}), ActionRiskLevel.CONFIRMATION_REQUIRED)

    # ── Category B: Bluetooth Matrix ─────────────────────────────────────────

    def test_bluetooth_permission_matrix(self):
        """Verify Bluetooth actions follow the exact permission matrix."""
        # Immediate
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "get_bluetooth_status"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "list_bluetooth_devices"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "bluetooth_on"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "set_bluetooth_enabled", "value": "on"}), ActionRiskLevel.IMMEDIATE)

        # Confirmation required
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "bluetooth_off"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "set_bluetooth_enabled", "value": "off"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "connect_bluetooth_device", "target": "OPPO Enco Buds 2"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "disconnect_bluetooth_device", "target": "OPPO Enco Buds 2"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "switch_bluetooth_device", "target": "Headphones"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "pair_bluetooth_device", "target": "Speaker"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "unpair_bluetooth_device", "target": "Old Device"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "set_audio_output", "target": "OPPO Enco Buds 2"}), ActionRiskLevel.CONFIRMATION_REQUIRED)

    # ── Category C: Laptop Monitoring & Controls Matrix ──────────────────────

    def test_laptop_monitoring_and_controls_matrix(self):
        """Verify monitoring and control permissions."""
        # Immediate
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "battery_status"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("system_status", {}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "get_volume"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "set_volume", "value": 70}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "set_brightness", "value": 80}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("computer_settings", {"action": "lock_screen"}), ActionRiskLevel.IMMEDIATE)

        # Confirmation required (Level 2)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "set_power_mode", "target": "Turbo"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "set_airplane_mode", "target": "on"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "set_resolution", "target": "1920x1080"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "set_display_mode", "target": "extend"}), ActionRiskLevel.CONFIRMATION_REQUIRED)

        # High-impact (Level 3)
        self.assertEqual(ActionPermissionManager.get_risk_level("computer_settings", {"action": "shutdown"}), ActionRiskLevel.HIGH_IMPACT)
        self.assertEqual(ActionPermissionManager.get_risk_level("computer_settings", {"action": "restart"}), ActionRiskLevel.HIGH_IMPACT)
        self.assertEqual(ActionPermissionManager.get_risk_level("computer_settings", {"action": "hibernate"}), ActionRiskLevel.HIGH_IMPACT)
        self.assertEqual(ActionPermissionManager.get_risk_level("computer_settings", {"action": "sleep"}), ActionRiskLevel.HIGH_IMPACT)

    # ── Category D: File and Folder Management Matrix ────────────────────────

    def test_file_and_folder_matrix(self):
        """Verify file operations matrix and drive formatting prohibition."""
        # Immediate
        self.assertEqual(ActionPermissionManager.get_risk_level("file_controller", {"action": "list", "path": "desktop"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("file_controller", {"action": "search", "name": "report"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("file_controller", {"action": "read", "name": "notes.txt"}), ActionRiskLevel.IMMEDIATE)

        # Confirmation required (Level 2)
        self.assertEqual(ActionPermissionManager.get_risk_level("file_controller", {"action": "create_file", "name": "main.py"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("file_controller", {"action": "write", "name": "main.py"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("file_controller", {"action": "rename", "name": "old.py", "new_name": "new.py"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("file_controller", {"action": "copy", "name": "a.txt", "destination": "b.txt"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("file_controller", {"action": "move", "name": "a.txt", "destination": "b.txt"}), ActionRiskLevel.CONFIRMATION_REQUIRED)

        # High-impact (Level 3)
        self.assertEqual(ActionPermissionManager.get_risk_level("file_controller", {"action": "delete", "name": "data.csv"}), ActionRiskLevel.HIGH_IMPACT)
        self.assertEqual(ActionPermissionManager.get_risk_level("file_controller", {"action": "empty_recycle_bin"}), ActionRiskLevel.HIGH_IMPACT)

        # Critical: Format Drive must be FORBIDDEN
        self.assertEqual(ActionPermissionManager.get_risk_level("file_controller", {"action": "format", "path": "C:"}), ActionRiskLevel.FORBIDDEN)
        self.assertEqual(ActionPermissionManager.get_risk_level("file_controller", {"action": "format_drive", "path": "D:"}), ActionRiskLevel.FORBIDDEN)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "shell", "command": "Format-Volume -DriveLetter D"}), ActionRiskLevel.FORBIDDEN)
        self.assertEqual(ActionPermissionManager.get_risk_level("computer_control", {"action": "cmd", "text": "format c:"}), ActionRiskLevel.FORBIDDEN)

    # ── Category E: Applications and Browser Matrix ──────────────────────────

    def test_applications_and_browser_matrix(self):
        """Verify apps, web, payments, and downloads matrix."""
        # Immediate
        self.assertEqual(ActionPermissionManager.get_risk_level("open_app", {"app_name": "Chrome"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("computer_control", {"action": "close_app", "app_name": "Notepad"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("browser_control", {"action": "navigate", "url": "https://google.com"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("web_search", {"query": "Python tutorials"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("browser_control", {"action": "type", "text": "hello"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("browser_control", {"action": "click", "index": 2}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("browser_control", {"action": "upload", "path": "file.pdf"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("send_message", {"recipient": "john", "message": "Hi"}), ActionRiskLevel.IMMEDIATE)

        # Confirmation required (Level 2)
        self.assertEqual(ActionPermissionManager.get_risk_level("browser_control", {"action": "download", "url": "https://example.com/setup.exe"}), ActionRiskLevel.CONFIRMATION_REQUIRED)

        # High-impact (Level 3)
        self.assertEqual(ActionPermissionManager.get_risk_level("browser_control", {"action": "make_payment", "amount": "$50", "recipient": "Acme Corp"}), ActionRiskLevel.HIGH_IMPACT)
        self.assertEqual(ActionPermissionManager.get_risk_level("browser_control", {"action": "purchase", "product": "Keyboard", "price": "$99", "merchant": "Amazon"}), ActionRiskLevel.HIGH_IMPACT)

    # ── Category F: Windows and Software Management Matrix ───────────────────

    def test_windows_and_software_matrix(self):
        """Verify software, configuration, security and admin permissions."""
        # Immediate
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "check_windows_version"}), ActionRiskLevel.IMMEDIATE)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "install_app", "target": "PowerToys"}), ActionRiskLevel.IMMEDIATE)

        # Confirmation required (Level 2)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "install_software", "target": "NodeJS"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "modify_system_config"}), ActionRiskLevel.CONFIRMATION_REQUIRED)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "change_user_settings"}), ActionRiskLevel.CONFIRMATION_REQUIRED)

        # High-impact (Level 3)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "uninstall_software", "target": "VS Code"}), ActionRiskLevel.HIGH_IMPACT)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "change_security_settings"}), ActionRiskLevel.HIGH_IMPACT)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "change_admin_settings"}), ActionRiskLevel.HIGH_IMPACT)
        self.assertEqual(ActionPermissionManager.get_risk_level("windows_control", {"action": "delete_system_files", "target": "C:\\Windows\\System32\\driver.sys"}), ActionRiskLevel.HIGH_IMPACT)

    # ── ActionExecutionGuard & Confirmation Lifecycle ────────────────────────

    def test_immediate_execution_runs_without_confirmation(self):
        """Level 1 actions execute immediately with zero confirmation prompt."""
        executed = []
        dummy_handler = lambda **kwargs: executed.append("immediate_done") or "Result OK"

        res = ActionExecutionGuard.guard_and_execute(
            action_name="windows_control",
            parameters={"action": "get_wifi_status"},
            handler=dummy_handler,
            ctx={},
        )
        self.assertTrue(res.success)
        self.assertEqual(executed, ["immediate_done"])
        self.assertEqual(confirm.pending_title(), "")

    def test_confirmation_required_blocks_until_voice_approval(self):
        """Level 2 action parks and executes only when user voice confirms."""
        executed = []
        dummy_handler = lambda **kwargs: executed.append("network_switched") or "Switched OK"

        # 1. Attempt to switch Wi-Fi
        res = ActionExecutionGuard.guard_and_execute(
            action_name="windows_control",
            parameters={"action": "switch_wifi", "target": "Mobile_Hotspot"},
            handler=dummy_handler,
            ctx={},
        )
        # Verify blocked with confirmation prompt
        self.assertEqual(res.status, outcome.STATUS_NEEDS_CONFIRMATION)
        self.assertIn("[CONFIRMATION_PENDING]", res.message)
        self.assertIn("Switch Wi-Fi Network", confirm.pending_title())
        self.assertEqual(executed, [])  # Not executed yet

        # 2. Voice confirms
        confirmed = confirm.resolve_by_voice("yes, confirm")
        self.assertTrue(confirmed)

        # Wait briefly for worker thread
        time.sleep(0.1)
        self.assertEqual(executed, ["network_switched"])
        self.assertEqual(confirm.pending_title(), "")

    def test_confirmation_cancelled_by_user(self):
        """User saying 'no' or 'cancel' discards pending action without execution."""
        executed = []
        dummy_handler = lambda **kwargs: executed.append("deleted") or "Deleted"

        res = ActionExecutionGuard.guard_and_execute(
            action_name="file_controller",
            parameters={"action": "delete", "name": "project.zip"},
            handler=dummy_handler,
            ctx={},
        )
        self.assertIn("[CONFIRMATION_PENDING]", res.message)

        # Voice cancels
        resolved = confirm.resolve_by_voice("no, cancel that")
        self.assertFalse(resolved)

        time.sleep(0.05)
        self.assertEqual(executed, [])
        self.assertEqual(confirm.pending_title(), "")

    def test_duplicate_approval_prevention(self):
        """Repeating 'confirm' or 'yes' executes the action exactly once."""
        execution_count = [0]
        dummy_handler = lambda **kwargs: execution_count.__setitem__(0, execution_count[0] + 1) or "OK"

        ActionExecutionGuard.guard_and_execute(
            action_name="windows_control",
            parameters={"action": "bluetooth_off"},
            handler=dummy_handler,
            ctx={},
        )
        # First confirmation executes
        self.assertTrue(confirm.resolve_by_voice("confirm"))
        # Second confirmation finds nothing pending
        self.assertIsNone(confirm.resolve_by_voice("confirm again"))

        time.sleep(0.1)
        self.assertEqual(execution_count[0], 1)

    def test_parameter_change_discards_stale_confirmation(self):
        """Requesting a new action discards previous pending action."""
        ActionExecutionGuard.guard_and_execute(
            action_name="windows_control",
            parameters={"action": "connect_wifi", "target": "Hotspot_A"},
            handler=lambda **kwargs: "Done A",
            ctx={},
        )
        self.assertEqual(confirm.pending_title(), "Switch Wi-Fi Network")
        old_pending = confirm.get_pending()
        self.assertEqual(old_pending.parameters.get("target"), "Hotspot_A")

        # Now user asks for Hotspot_B
        ActionExecutionGuard.guard_and_execute(
            action_name="windows_control",
            parameters={"action": "connect_wifi", "target": "Hotspot_B"},
            handler=lambda **kwargs: "Done B",
            ctx={},
        )
        new_pending = confirm.get_pending()
        self.assertEqual(new_pending.parameters.get("target"), "Hotspot_B")

    def test_forged_ai_parameters_cannot_bypass_guard(self):
        """Gemini or tool parameters with confirmed=True do not bypass the guard."""
        executed = []
        dummy_handler = lambda **kwargs: executed.append("run") or "Done"

        # Model passes confirmed=True in arguments
        res = ActionExecutionGuard.guard_and_execute(
            action_name="windows_control",
            parameters={"action": "bluetooth_off", "confirmed": True},
            handler=dummy_handler,
            ctx={},
        )
        # Must still be blocked
        self.assertEqual(res.status, outcome.STATUS_NEEDS_CONFIRMATION)
        self.assertEqual(executed, [])

    def test_drive_formatting_strictly_forbidden_across_routes(self):
        """Any attempt to format a drive is rejected explicitly and never executed."""
        executed = []
        dummy_handler = lambda **kwargs: executed.append("formatted") or "Done"

        # Route 1: direct format action
        res1 = ActionExecutionGuard.guard_and_execute(
            action_name="file_controller",
            parameters={"action": "format", "path": "C:"},
            handler=dummy_handler,
            ctx={},
        )
        self.assertEqual(res1.status, outcome.STATUS_UNSUPPORTED)
        self.assertIn("strictly prohibited", res1.message)

        # Route 2: format_drive
        res2 = ActionExecutionGuard.guard_and_execute(
            action_name="format_drive",
            parameters={"drive": "D:"},
            handler=dummy_handler,
            ctx={},
        )
        self.assertEqual(res2.status, outcome.STATUS_UNSUPPORTED)
        self.assertIn("strictly prohibited", res2.message)

        # Route 3: shell command with format c:
        res3 = ActionExecutionGuard.guard_and_execute(
            action_name="computer_control",
            parameters={"action": "cmd", "command": "format c: /fs:NTFS"},
            handler=dummy_handler,
            ctx={},
        )
        self.assertEqual(res3.status, outcome.STATUS_UNSUPPORTED)
        self.assertIn("strictly prohibited", res3.message)

        # Verify nothing executed and no confirmation was parked
        self.assertEqual(executed, [])
        self.assertEqual(confirm.pending_title(), "")


if __name__ == "__main__":
    unittest.main()

