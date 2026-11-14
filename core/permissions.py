"""
core/permissions.py — Centralized Action Permission Manager, Risk Levels, and Audit Logging.

Authoritative permission and confirmation system for all JARVIS voice-controlled actions:
1. Permission Levels:
   - IMMEDIATE (Level 1): Immediate execution without confirmation.
   - CONFIRMATION_REQUIRED (Level 2): Explains intended action and waits for user approval.
   - HIGH_IMPACT (Level 3): Requires explicit approval with consequences clearly explained.
   - FORBIDDEN: Strictly prohibited operations (e.g. drive formatting).
2. Centralized ActionExecutionGuard:
   - Intercepts all action execution attempts before invocation.
   - Authoritative: AI-generated parameters or instructions CANNOT override classifications.
   - Direct integration with ConfirmationManager.
3. Action Audit Logging:
   - Persistent, tamper-resistant append-only SQLite log with credential redaction.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import inspect
import json
from pathlib import Path
import re
import sqlite3
from threading import Lock
import time
import traceback
from typing import Any, Callable, Dict, Optional, Tuple

from core import outcome

# ── Risk Level Definitions ───────────────────────────────────────────────────

class ActionRiskLevel(str, Enum):
    IMMEDIATE = "immediate"                 # Level 1 - Immediate, no confirmation
    CONFIRMATION_REQUIRED = "confirmation"  # Level 2 - Intended action explained, confirmation required
    HIGH_IMPACT = "high_impact"             # Level 3 - High-impact, clear consequences explained
    FORBIDDEN = "forbidden"                 # Blocked / Strictly prohibited (e.g. drive format)


# Backward-compatible tier strings
READ_ONLY = "read_only"
NORMAL = "normal"
SENSITIVE = "sensitive"
DESTRUCTIVE = "destructive"
FORBIDDEN = "forbidden"

PERMISSION_LEVELS = {READ_ONLY, NORMAL, SENSITIVE, DESTRUCTIVE, FORBIDDEN}

# Fallback mapping for generic tool declarations
ACTION_PERMISSIONS: dict[str, str] = {
    # Read-only tools (Immediate)
    "web_search": READ_ONLY,
    "system_status": READ_ONLY,
    "recall_memory": READ_ONLY,
    "file_processor": READ_ONLY,
    "flight_finder": READ_ONLY,
    "weather_report": READ_ONLY,
    "youtube_video": READ_ONLY,
    "observe_page": READ_ONLY,
    "code_helper": READ_ONLY,
    "leetcode_assistant": READ_ONLY,

    # Normal operational tools (Immediate)
    "open_app": NORMAL,
    "spotify_control": NORMAL,
    "video_player": NORMAL,
    "windows_uia": NORMAL,
    "browser_control": NORMAL,
    "click_element": NORMAL,
    "input_text": NORMAL,
    "scroll_page": NORMAL,
    "scroll_element": NORMAL,
    "press_key": NORMAL,
    "navigate_to": NORMAL,
    "switch_tab": NORMAL,
    "go_back": NORMAL,
    "wait_page": NORMAL,
    "reminder": NORMAL,
    "send_message": NORMAL,
    "save_memory": NORMAL,
    "undo": NORMAL,
    "takeover_desktop": NORMAL,
    "computer_use": NORMAL,
    "desktop_control": NORMAL,
    "display_window": NORMAL,
    "file_controller": NORMAL,
    "media_control": NORMAL,
    "notification_center": NORMAL,
    "skill_learner": NORMAL,
    "browser_qa": NORMAL,

    # Sensitive tools (Require confirmation by default unless sub-action is immediate)
    "computer_control": SENSITIVE,
    "windows_control": SENSITIVE,
    "computer_settings": SENSITIVE,
    "dev_agent": SENSITIVE,
    "game_updater": SENSITIVE,

    # Destructive actions (High-impact)
    "shutdown_jarvis": DESTRUCTIVE,
    "delete_file": DESTRUCTIVE,
    "kill_process": DESTRUCTIVE,
}

_audit_log: list[dict] = []
_audit_lock = Lock()


# ── ActionPermissionManager ──────────────────────────────────────────────────

class ActionPermissionManager:
    """Authoritative centralized permission manager for all JARVIS actions.
    
    Implements the exact permission matrix. AI-generated instructions and Gemini
    cannot override these classifications.
    """

    FORBIDDEN_FORMAT_PATTERNS = (
        "format",
        "format_drive",
        "format_disk",
        "format_volume",
        "format-volume",
        "clear-disk",
        "initialize-disk",
        "diskpart",
        "format c:",
        "format d:",
        "format e:",
    )

    @classmethod
    def is_drive_format_attempt(cls, action_name: str, parameters: dict | None = None) -> bool:
        """Check if an action or its parameters attempt drive or volume formatting."""
        act = str(action_name or "").lower().strip()
        params = parameters or {}
        sub_act = str(params.get("action") or "").lower().strip()
        target = str(params.get("target") or params.get("path") or params.get("command") or params.get("text") or params.get("query") or "").lower()

        # Direct action name or sub-action check
        if act in ("format", "format_drive", "format_disk") or sub_act in ("format", "format_drive", "format_disk", "format_volume"):
            return True

        combined = f"{act} {sub_act} {target}".lower()
        for pat in cls.FORBIDDEN_FORMAT_PATTERNS:
            if pat in combined:
                # Do not misclassify text reformatting in file_processor or code_helper
                if act in ("file_processor", "code_helper", "video_player") and "drive" not in combined and "disk" not in combined and "c:" not in combined:
                    continue
                return True
        return False

    @classmethod
    def get_risk_level(cls, action_name: str, parameters: dict | None = None) -> ActionRiskLevel:
        """Determine the exact risk level for an action according to the permission matrix."""
        act = str(action_name or "").lower().strip()
        params = parameters or {}
        sub_act = str(params.get("action") or "").lower().strip()
        target = str(params.get("target") or params.get("device") or params.get("name") or params.get("ssid") or params.get("path") or "").lower()

        # 0. Check FORBIDDEN (Drive Formatting)
        if cls.is_drive_format_attempt(act, params):
            return ActionRiskLevel.FORBIDDEN

        # Destructive sub-action parameter escalation (kill, destroy, shutdown, etc.)
        if sub_act in ("kill", "kill_process", "destroy"):
            return ActionRiskLevel.HIGH_IMPACT

        # =====================================================================
        # A. WI-FI
        # =====================================================================
        # - Check status / network / list: IMMEDIATE
        # - Turn Wi-Fi ON: IMMEDIATE
        # - Turn Wi-Fi OFF: IMMEDIATE
        # - Switch network: CONFIRMATION REQUIRED
        # - Disconnect: CONFIRMATION REQUIRED
        # - Forget network: CONFIRMATION REQUIRED
        # - Change network configuration: CONFIRMATION REQUIRED
        if act in ("windows_control", "computer_settings") or "wifi" in act:
            if sub_act in ("get_wifi_status", "wifi_status", "check_wifi", "is_wifi_on",
                           "list_available_wifi_networks", "list_wifi_networks", "available_wifi", "wifi_networks",
                           "current_wifi", "connected_wifi", "wifi_name"):
                return ActionRiskLevel.IMMEDIATE

            if sub_act in ("set_wifi_enabled", "wifi_on", "wifi_off", "enable_wifi", "disable_wifi", "toggle_wifi"):
                return ActionRiskLevel.IMMEDIATE

            if sub_act in ("connect_wifi", "connect_network", "switch_wifi", "switch_network"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("disconnect_wifi", "disconnect_network"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("forget_wifi", "forget_network", "delete_wifi_profile"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("set_network_config", "change_network_config", "change_dns", "set_ip_config"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

        # =====================================================================
        # B. BLUETOOTH
        # =====================================================================
        # - Check status / list paired / list connected: IMMEDIATE
        # - Turn Bluetooth ON: IMMEDIATE
        # - Turn Bluetooth OFF: CONFIRMATION REQUIRED
        # - Connect / Disconnect / Switch / Pair / Forget / Audio output: CONFIRMATION REQUIRED
        if act in ("windows_control", "computer_settings") or "bluetooth" in act or "bt" in act:
            if sub_act in ("get_bluetooth_status", "bluetooth_status", "check_bluetooth", "is_bluetooth_on",
                           "list_bluetooth_devices", "bluetooth_devices", "paired_bluetooth_devices",
                           "list_connected_bluetooth_devices", "connected_bluetooth_devices", "connected_devices"):
                return ActionRiskLevel.IMMEDIATE

            if sub_act in ("bluetooth_on", "enable_bluetooth"):
                return ActionRiskLevel.IMMEDIATE

            if sub_act == "set_bluetooth_enabled":
                val = str(params.get("value") or target).lower()
                if val in ("off", "false", "0", "disable"):
                    return ActionRiskLevel.CONFIRMATION_REQUIRED
                return ActionRiskLevel.IMMEDIATE

            if sub_act in ("bluetooth_off", "disable_bluetooth"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("connect_bluetooth_device", "connect_bluetooth", "connect_device"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("disconnect_bluetooth_device", "disconnect_bluetooth", "disconnect_device"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("switch_bluetooth_device", "switch_device"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("pair_bluetooth_device", "pair_bluetooth", "pair_device"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("unpair_bluetooth_device", "forget_bluetooth_device", "remove_bluetooth_device", "forget_device"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("set_audio_output", "switch_audio_output", "set_default_playback_device", "change_audio_output"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

        # =====================================================================
        # C. LAPTOP MONITORING AND CONTROLS
        # =====================================================================
        # - Check battery, CPU, RAM, storage, volume: IMMEDIATE
        # - Increase/decrease volume: IMMEDIATE
        # - Increase/decrease brightness: IMMEDIATE
        # - Lock laptop: IMMEDIATE
        # - Change power mode, Airplane mode, display resolution, display config: CONFIRMATION REQUIRED
        # - Shutdown, Restart, Hibernate, Sleep: HIGH-IMPACT
        if act in ("windows_control", "computer_settings", "computer_control", "system_status", "system_monitor"):
            if sub_act in ("get_power_status", "battery_status", "battery", "battery_percentage",
                           "cpu_usage", "ram_usage", "storage_usage", "disk_usage",
                           "get_volume", "check_volume", "volume_status", "volume_get",
                           "get_brightness", "brightness_get",
                           "volume_up", "volume_down", "set_volume", "volume", "adjust_volume",
                           "mute", "unmute",
                           "brightness_up", "brightness_down", "set_brightness", "brightness",
                           "lock_screen", "lock_laptop", "lock"):
                return ActionRiskLevel.IMMEDIATE

            if sub_act in ("set_power_mode", "power_mode", "change_power_mode"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("set_airplane_mode", "airplane_mode", "airplane_mode_on", "airplane_mode_off"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("set_resolution", "change_resolution", "change_display_resolution"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("set_display_mode", "display_mode", "change_display_configuration", "display_configuration"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("shutdown", "shutdown_computer", "shutdown_laptop") or act == "shutdown_jarvis":
                return ActionRiskLevel.HIGH_IMPACT

            if sub_act in ("restart", "restart_computer", "restart_laptop", "reboot"):
                return ActionRiskLevel.HIGH_IMPACT

            if sub_act in ("hibernate", "hibernate_laptop", "hibernate_computer"):
                return ActionRiskLevel.HIGH_IMPACT

            if sub_act in ("sleep", "sleep_display", "sleep_laptop", "sleep_computer"):
                return ActionRiskLevel.HIGH_IMPACT

        # =====================================================================
        # D. FILE AND FOLDER MANAGEMENT
        # =====================================================================
        # - List folders, search files, read files: IMMEDIATE
        # - Create file, edit file, rename, copy, move: CONFIRMATION REQUIRED
        # - Delete files/folders, empty recycle bin: HIGH-IMPACT
        # - Format drive: FORBIDDEN
        if act in ("file_controller", "file_processor", "dev_agent"):
            if sub_act in ("list", "find", "search", "search_files", "read", "extract_text", "summarize", "largest", "disk_usage", "info"):
                return ActionRiskLevel.IMMEDIATE

            if sub_act in ("create_file", "create_folder", "write", "rename", "copy", "move", "organize_desktop", "edit_file"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("delete", "delete_file", "delete_folder", "remove"):
                return ActionRiskLevel.HIGH_IMPACT

            if sub_act in ("empty_recycle_bin", "empty_trash"):
                return ActionRiskLevel.HIGH_IMPACT

        # =====================================================================
        # E. APPLICATIONS AND BROWSER
        # =====================================================================
        # - Open/close app: IMMEDIATE
        # - Open website, search Google, navigate, fill form, submit form: IMMEDIATE
        # - Upload file: IMMEDIATE
        # - Send email: IMMEDIATE
        # - Download file: CONFIRMATION REQUIRED
        # - Make payment, purchase: HIGH-IMPACT
        if act in ("open_app", "computer_control", "browser_control", "action_executor", "web_search", "send_message"):
            if act in ("open_app", "web_search", "send_message"):
                return ActionRiskLevel.IMMEDIATE

            if sub_act in ("open_app", "close_app", "minimize_app", "maximize_app", "switch_app",
                           "list_running_apps", "get_active_window",
                           "observe", "click", "type", "navigate", "scroll", "press", "new_tab",
                           "switch_tab", "close_tab", "back", "wait", "screenshot",
                           "fill_form", "submit_form", "upload", "upload_file"):
                return ActionRiskLevel.IMMEDIATE

            if sub_act in ("download", "download_file"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("make_payment", "pay", "payment"):
                return ActionRiskLevel.HIGH_IMPACT

            if sub_act in ("purchase", "buy", "place_order"):
                return ActionRiskLevel.HIGH_IMPACT

        # =====================================================================
        # F. WINDOWS AND SOFTWARE MANAGEMENT
        # =====================================================================
        # - Check Windows version: IMMEDIATE
        # - Install application: IMMEDIATE
        # - Install software: CONFIRMATION REQUIRED
        # - Uninstall software: HIGH-IMPACT
        # - Modify software/system configuration: CONFIRMATION REQUIRED
        # - Change security settings: HIGH-IMPACT
        # - Change administrator settings: HIGH-IMPACT
        # - Change user settings: CONFIRMATION REQUIRED
        # - Delete system files: HIGH-IMPACT
        if act in ("windows_control", "computer_control"):
            if sub_act in ("check_windows_version", "windows_version", "system_info", "install_app", "install_application"):
                return ActionRiskLevel.IMMEDIATE

            if sub_act in ("install_software", "install_package"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("uninstall_software", "uninstall_app", "uninstall_package"):
                return ActionRiskLevel.HIGH_IMPACT

            if sub_act in ("modify_system_config", "modify_software_config", "system_config"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("change_security_settings", "security_settings", "disable_firewall", "antivirus_settings"):
                return ActionRiskLevel.HIGH_IMPACT

            if sub_act in ("change_admin_settings", "admin_settings", "uac_settings"):
                return ActionRiskLevel.HIGH_IMPACT

            if sub_act in ("change_user_settings", "user_settings"):
                return ActionRiskLevel.CONFIRMATION_REQUIRED

            if sub_act in ("delete_system_files", "delete_system_file"):
                return ActionRiskLevel.HIGH_IMPACT

        # Check generic table fallback
        if act in ACTION_PERMISSIONS:
            tier = ACTION_PERMISSIONS[act]
            if tier in (READ_ONLY, NORMAL):
                return ActionRiskLevel.IMMEDIATE
            if tier == DESTRUCTIVE:
                return ActionRiskLevel.HIGH_IMPACT
            return ActionRiskLevel.CONFIRMATION_REQUIRED

        # Fail-closed: default to CONFIRMATION_REQUIRED
        return ActionRiskLevel.CONFIRMATION_REQUIRED

    @classmethod
    def get_confirmation_details(cls, action_name: str, parameters: dict | None = None) -> Tuple[str, str, Optional[dict]]:
        """Construct descriptive title, consequence details, and payment metadata."""
        act = str(action_name or "").lower().strip()
        params = parameters or {}
        sub_act = str(params.get("action") or "").lower().strip()
        target = str(params.get("target") or params.get("device") or params.get("name") or params.get("ssid") or params.get("path") or "").strip()
        payment_info: Optional[dict] = None

        # Wi-Fi
        if sub_act in ("connect_wifi", "switch_wifi", "connect_network"):
            title = "Switch Wi-Fi Network"
            detail = f"Disconnect current connection and connect to '{target or 'selected network'}'."
        elif sub_act in ("disconnect_wifi", "disconnect_network"):
            title = "Disconnect Wi-Fi"
            detail = "Disconnect from the currently active Wi-Fi network."
        elif sub_act in ("forget_wifi", "forget_network", "delete_wifi_profile"):
            title = "Forget Wi-Fi Network"
            detail = f"Remove stored credentials and profile for '{target}'."
        elif sub_act in ("set_network_config", "change_network_config", "change_dns", "set_ip_config"):
            title = "Change Network Configuration"
            detail = "Apply changes to network interface configuration."

        # Bluetooth
        elif sub_act in ("bluetooth_off", "disable_bluetooth") or (sub_act == "set_bluetooth_enabled" and str(params.get("value") or target).lower() in ("off", "false", "0", "disable")):
            title = "Turn Bluetooth Off"
            detail = "Turn off the Bluetooth adapter and disconnect all connected devices."
        elif sub_act in ("connect_bluetooth_device", "connect_bluetooth", "connect_device"):
            title = "Connect Bluetooth Device"
            detail = f"Connect to Bluetooth device '{target}'."
        elif sub_act in ("disconnect_bluetooth_device", "disconnect_bluetooth", "disconnect_device"):
            title = "Disconnect Bluetooth Device"
            detail = f"Disconnect Bluetooth device '{target}'."
        elif sub_act in ("switch_bluetooth_device", "switch_device"):
            title = "Switch Bluetooth Device"
            detail = f"Switch active connection to '{target}'."
        elif sub_act in ("pair_bluetooth_device", "pair_bluetooth", "pair_device"):
            title = "Pair Bluetooth Device"
            detail = f"Initiate pairing with '{target}'."
        elif sub_act in ("unpair_bluetooth_device", "forget_bluetooth_device", "remove_bluetooth_device"):
            title = "Remove Paired Device"
            detail = f"Unpair and remove Bluetooth device '{target}'."
        elif sub_act in ("set_audio_output", "switch_audio_output", "set_default_playback_device", "change_audio_output"):
            title = "Change Audio Output"
            detail = f"Set default audio playback endpoint to '{target}'."

        # Laptop Controls
        elif sub_act in ("shutdown", "shutdown_computer", "shutdown_laptop") or act == "shutdown_jarvis":
            title = "Shut Down Laptop"
            detail = "Power off the computer. Unsaved work in open applications will be lost."
        elif sub_act in ("restart", "restart_computer", "restart_laptop", "reboot"):
            title = "Restart Laptop"
            detail = "Restart the operating system. Any unsaved work will be lost."
        elif sub_act in ("hibernate", "hibernate_laptop", "hibernate_computer"):
            title = "Hibernate Laptop"
            detail = "Save memory state to disk and put the laptop into deep hibernation."
        elif sub_act in ("sleep", "sleep_display", "sleep_laptop", "sleep_computer"):
            title = "Put Laptop to Sleep"
            detail = "Put the computer into low-power sleep mode."
        elif sub_act in ("set_power_mode", "power_mode", "change_power_mode"):
            title = "Change Power Mode"
            detail = f"Switch power scheme to '{target}'."
        elif sub_act in ("set_airplane_mode", "airplane_mode", "airplane_mode_on", "airplane_mode_off"):
            title = "Toggle Airplane Mode"
            detail = "Turn Airplane Mode on/off, altering all wireless communications."
        elif sub_act in ("set_resolution", "change_resolution", "change_display_resolution"):
            title = "Change Display Resolution"
            detail = f"Set screen resolution to '{target}'."
        elif sub_act in ("set_display_mode", "display_mode", "change_display_configuration"):
            title = "Change Display Configuration"
            detail = f"Set display configuration mode to '{target}'."

        # File Management
        elif sub_act in ("create_file", "create_folder"):
            title = "Create New File/Folder"
            detail = f"Create '{target or params.get('name', 'new item')}'."
        elif sub_act in ("write", "edit_file"):
            title = "Edit File"
            detail = f"Modify the contents of '{target or params.get('name', 'file')}'."
        elif sub_act == "rename":
            title = "Rename File or Folder"
            detail = f"Rename '{target or params.get('name', '')}' to '{params.get('new_name', '')}'."
        elif sub_act == "copy":
            title = "Copy Files"
            detail = f"Copy '{target or params.get('name', '')}' to '{params.get('destination', '')}'."
        elif sub_act == "move":
            title = "Move Files"
            detail = f"Move '{target or params.get('name', '')}' to '{params.get('destination', '')}'."
        elif sub_act in ("delete", "delete_file", "delete_folder", "remove"):
            title = "Delete File or Folder"
            detail = f"Delete '{target or params.get('name', '')}'. This item will be removed."
        elif sub_act in ("empty_recycle_bin", "empty_trash"):
            title = "Empty Recycle Bin"
            detail = "Permanently delete all contents of the Windows Recycle Bin."

        # Applications & Browser
        elif sub_act in ("download", "download_file"):
            title = "Download File"
            detail = f"Download file from '{params.get('url') or target or 'web'}'. Verify the source before confirming."
        elif sub_act in ("make_payment", "pay", "payment"):
            amt = params.get("amount") or "unspecified amount"
            recip = params.get("recipient") or params.get("merchant") or "merchant"
            title = "Authorize Payment"
            detail = f"Authorize payment transaction of {amt} to {recip}."
            payment_info = {"amount": amt, "recipient": recip}
        elif sub_act in ("purchase", "buy", "place_order"):
            prod = params.get("product") or params.get("item") or "item"
            price = params.get("price") or params.get("amount") or "unspecified price"
            merch = params.get("merchant") or params.get("store") or "store"
            title = "Complete Purchase"
            detail = f"Complete purchase of '{prod}' for {price} from '{merch}'."
            payment_info = {"product": prod, "price": price, "merchant": merch}

        # Software & Windows Management
        elif sub_act in ("install_software", "install_package"):
            title = "Install Software"
            detail = f"Run installation package for '{target}'."
        elif sub_act in ("uninstall_software", "uninstall_app"):
            title = "Uninstall Software"
            detail = f"Uninstall '{target}' and remove its associated files from the computer."
        elif sub_act in ("modify_system_config", "modify_software_config"):
            title = "Modify System Configuration"
            detail = "Apply changes to system or software configuration."
        elif sub_act in ("change_security_settings", "security_settings", "disable_firewall"):
            title = "Change Security Settings"
            detail = "Modify Windows security or firewall settings. This impacts system security."
        elif sub_act in ("change_admin_settings", "admin_settings"):
            title = "Change Administrator Settings"
            detail = "Modify administrative or elevation policy settings."
        elif sub_act in ("change_user_settings", "user_settings"):
            title = "Change User Settings"
            detail = "Update Windows user profile or account settings."
        elif sub_act in ("delete_system_files", "delete_system_file"):
            title = "Delete System Files"
            detail = f"Permanently delete system file '{target}'. Warning: Deleting system files can corrupt Windows."

        else:
            title = f"Execute {action_name}"
            detail = f"Execute action '{action_name}' with target '{target}'."

        return title, detail, payment_info


# ── Centralized Action Execution Guard ───────────────────────────────────────

class ActionExecutionGuard:
    """Authoritative execution guard for all registered actions."""

    @classmethod
    def guard_and_execute(
        cls,
        action_name: str,
        parameters: dict,
        handler: Callable,
        ctx: dict,
        logger: Optional[Callable] = None,
        t0: Optional[float] = None,
    ) -> outcome.ActionResult:
        """Evaluate action permission, check forbidden operations, park or execute."""
        start_time = t0 or time.monotonic()
        name = str(action_name or "").strip()
        params = dict(parameters or {})
        _log = logger or (lambda msg: None)

        # 1. Drive Formatting / Forbidden Check
        if ActionPermissionManager.is_drive_format_attempt(name, params):
            dur = (time.monotonic() - start_time) * 1000
            msg = "Drive formatting is strictly prohibited and not supported on this system."
            log_action_audit(name, params, "forbidden", "blocked", False, error="FORBIDDEN_DRIVE_FORMAT")
            return outcome.ActionResult(
                status=outcome.STATUS_UNSUPPORTED,
                action=name,
                success=False,
                verified=False,
                message=msg,
                error=msg,
                duration_ms=dur,
            )

        # 2. Check if already authorized by human confirmation
        # Internal flag set ONLY when user confirms through ConfirmationManager
        if params.get("_execution_authorized") is True:
            clean_params = {k: v for k, v in params.items() if k != "_execution_authorized"}
            try:
                raw = _call_handler_safe(handler, clean_params, ctx)
                dur = (time.monotonic() - start_time) * 1000
                res = outcome.normalize_result(raw, action_name=name, duration_ms=dur)
                log_action_audit(name, clean_params, "authorized", res.status, res.verified, res.error)
                return res
            except Exception as e:
                dur = (time.monotonic() - start_time) * 1000
                _log(f"Action '{name}' failed during authorized execute: {e}")
                traceback.print_exc()
                res = outcome.ActionResult(
                    status=outcome.STATUS_FAILED,
                    action=name,
                    success=False,
                    verified=False,
                    message=f"Action '{name}' failed: {e}",
                    error=str(e),
                    duration_ms=dur,
                )
                log_action_audit(name, clean_params, "authorized", res.status, res.verified, res.error)
                return res

        # 3. Check Risk Level from Authoritative Permission Manager
        risk_level = ActionPermissionManager.get_risk_level(name, params)

        if risk_level == ActionRiskLevel.IMMEDIATE:
            # Level 1: Immediate execution
            try:
                raw = _call_handler_safe(handler, params, ctx)
                dur = (time.monotonic() - start_time) * 1000
                res = outcome.normalize_result(raw, action_name=name, duration_ms=dur)
                log_action_audit(name, params, str(risk_level.value), res.status, res.verified, res.error)
                return res
            except Exception as e:
                dur = (time.monotonic() - start_time) * 1000
                _log(f"Action '{name}' failed during immediate execute: {e}")
                traceback.print_exc()
                res = outcome.ActionResult(
                    status=outcome.STATUS_FAILED,
                    action=name,
                    success=False,
                    verified=False,
                    message=f"Action '{name}' failed: {e}",
                    error=str(e),
                    duration_ms=dur,
                )
                log_action_audit(name, params, str(risk_level.value), res.status, res.verified, res.error)
                return res

        # Level 2 or 3: Confirmation Required
        title, detail, payment_info = ActionPermissionManager.get_confirmation_details(name, params)

        from core import confirm

        # Runner closure to execute only after user confirms
        def _confirmed_runner() -> str:
            auth_params = {**params, "_execution_authorized": True}
            try:
                raw = _call_handler_safe(handler, auth_params, ctx)
                res = outcome.normalize_result(raw, action_name=name)
                log_action_audit(name, params, str(risk_level.value), res.status, res.verified, res.error)
                # Output to UI log & content if player available
                player = ctx.get("player")
                if player:
                    if hasattr(player, "write_log"):
                        player.write_log(f"SYS: Confirmed {title} executed. {res.message or 'Done.'}")
                    if hasattr(player, "show_content") and res.message:
                        player.show_content(title, res.message)
                return res.message or "Done."
            except Exception as err:
                log_action_audit(name, params, str(risk_level.value), "failed", False, error=str(err))
                return f"Failed: {err}"

        # Park behind confirmation gate
        prompt_msg = confirm.request_action(
            action=name,
            parameters=params,
            risk_level=str(risk_level.value),
            title=title,
            detail=detail,
            run=_confirmed_runner,
            payment_details=payment_info,
        )

        dur = (time.monotonic() - start_time) * 1000
        res = outcome.ActionResult(
            status=outcome.STATUS_NEEDS_CONFIRMATION,
            action=name,
            success=False,
            verified=False,
            message=prompt_msg,
            duration_ms=dur,
        )
        log_action_audit(name, params, str(risk_level.value), "pending_confirmation", False)
        return res


_CTX_KEYS = ("player", "response", "session_memory", "speak")


def _call_handler_safe(fn: Callable, parameters: dict, ctx: dict) -> Any:
    """Invoke the handler passing parameters dict or individual kwargs based on signature."""
    sig = inspect.signature(fn)
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

    # Filter context arguments
    kwargs = {}
    for key in _CTX_KEYS:
        if has_var_kw or key in sig.parameters:
            kwargs[key] = ctx.get(key)

    # Standard JARVIS tool handlers accept (parameters: dict, ...)
    if "parameters" in sig.parameters:
        return fn(parameters=parameters, **kwargs)
    elif has_var_kw:
        try:
            return fn(parameters=parameters, **kwargs)
        except TypeError:
            clean_args = dict(parameters)
            clean_args.update(kwargs)
            return fn(**clean_args)
    else:
        clean_args = {k: v for k, v in parameters.items() if k in sig.parameters}
        clean_args.update(kwargs)
        return fn(**clean_args)


# ── Backward Compatibility API ──────────────────────────────────────────────

def get_action_permission(action_name: str, parameters: dict | None = None) -> str:
    """Map action to backward-compatible tier string."""
    risk = ActionPermissionManager.get_risk_level(action_name, parameters)
    if risk == ActionRiskLevel.FORBIDDEN:
        return FORBIDDEN
    if risk == ActionRiskLevel.HIGH_IMPACT:
        return DESTRUCTIVE
    if risk == ActionRiskLevel.CONFIRMATION_REQUIRED:
        return SENSITIVE
    return NORMAL


def requires_confirmation(action_name: str, parameters: dict | None = None, from_untrusted: bool | None = None) -> bool:
    """Authoritative check: returns True if action requires explicit confirmation."""
    from memory.config_manager import (
        get_trust_level,
        TRUST_LEVEL_STRICT,
        TRUST_LEVEL_BALANCED,
        TRUST_LEVEL_TRUSTED,
    )

    act = (action_name or "").lower().strip()
    risk = ActionPermissionManager.get_risk_level(act, parameters)

    untrusted_origin = _untrusted_context if from_untrusted is None else bool(from_untrusted)
    if untrusted_origin and risk in (ActionRiskLevel.HIGH_IMPACT, ActionRiskLevel.CONFIRMATION_REQUIRED):
        return True

    if risk == ActionRiskLevel.FORBIDDEN:
        return True

    if risk == ActionRiskLevel.HIGH_IMPACT:
        return True

    if risk == ActionRiskLevel.CONFIRMATION_REQUIRED:
        sub_act = (parameters or {}).get("action")
        # When evaluated at tool level without specific sub-action, respect session trust level
        if not sub_act:
            trust = get_trust_level()
            if trust == TRUST_LEVEL_TRUSTED:
                return False
            if trust == TRUST_LEVEL_BALANCED:
                with _session_lock:
                    return act not in _session_approved_sensitive
        return True

    return False


_session_approved_sensitive: set[str] = set()
_session_lock = Lock()


def reset_session_approvals() -> None:
    with _session_lock:
        _session_approved_sensitive.clear()


def approve_sensitive_action_for_session(action_name: str) -> None:
    with _session_lock:
        _session_approved_sensitive.add((action_name or "").lower().strip())


_untrusted_context: bool = False

def set_untrusted_context(active: bool) -> None:
    global _untrusted_context
    _untrusted_context = bool(active)

def is_untrusted_context() -> bool:
    return _untrusted_context


# ── SQLite Audit Log & Secret Redaction ──────────────────────────────────────

_MAX_AUDIT_ENTRIES = 5000

def _get_audit_db_path() -> Path:
    try:
        from memory.config_manager import get_user_data_dir
        d = get_user_data_dir()
    except Exception:
        d = Path.home() / ".jarvis"
    d.mkdir(parents=True, exist_ok=True)
    return d / "audit.db"


def _init_audit_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            action TEXT NOT NULL,
            permission_level TEXT NOT NULL,
            status TEXT NOT NULL,
            verified INTEGER NOT NULL,
            parameters TEXT,
            error TEXT
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_trail (timestamp);")
    conn.commit()


def _sanitize_secrets(params: dict) -> str:
    """Mask sensitive fields such as passwords, pins, credentials, credit cards."""
    from memory.config_manager import redact_secret
    cleaned = dict(params or {})
    sensitive_keys = {"password", "pin", "cvv", "credit_card", "card_number", "ssid_password"}
    for k, v in list(cleaned.items()):
        if any(sk == k.lower() or sk in k.lower().split("_") for sk in sensitive_keys):
            cleaned[k] = "***REDACTED***"
        elif isinstance(v, str):
            cleaned[k] = redact_secret(v)
    return redact_secret(str(cleaned))


def log_action_audit(
    action: str,
    parameters: dict,
    permission_level: str,
    status: str,
    verified: bool,
    error: Optional[str] = None,
) -> dict:
    """Record an action execution to persistent SQLite audit log."""
    from memory.config_manager import redact_secret

    redacted_params = _sanitize_secrets(parameters)
    now_str = datetime.now().isoformat()

    entry = {
        "timestamp": now_str,
        "action": str(action),
        "permission_level": str(permission_level),
        "status": str(status),
        "verified": bool(verified),
        "parameters": redacted_params,
        "error": redact_secret(str(error)) if error else None,
    }

    with _audit_lock:
        _audit_log.append(entry)
        if len(_audit_log) > 500:
            _audit_log.pop(0)

        try:
            db_path = _get_audit_db_path()
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            try:
                _init_audit_db(conn)
                conn.execute(
                    """
                    INSERT INTO audit_trail (timestamp, action, permission_level, status, verified, parameters, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (now_str, entry["action"], entry["permission_level"], entry["status"],
                     1 if entry["verified"] else 0, entry["parameters"], entry["error"]),
                )
                conn.execute(
                    f"DELETE FROM audit_trail WHERE id NOT IN (SELECT id FROM audit_trail ORDER BY id DESC LIMIT {_MAX_AUDIT_ENTRIES})"
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    return entry


def get_audit_trail(limit: int = 50) -> list[dict]:
    """Retrieve recent action audit events from persistent database."""
    with _audit_lock:
        try:
            db_path = _get_audit_db_path()
            if db_path.exists():
                conn = sqlite3.connect(str(db_path), timeout=5.0)
                try:
                    _init_audit_db(conn)
                    cursor = conn.execute(
                        """
                        SELECT timestamp, action, permission_level, status, verified, parameters, error
                        FROM audit_trail ORDER BY id DESC LIMIT ?
                        """,
                        (int(limit),),
                    )
                    rows = cursor.fetchall()
                    if rows:
                        results = []
                        for r in reversed(rows):
                            results.append({
                                "timestamp": r[0],
                                "action": r[1],
                                "permission_level": r[2],
                                "status": r[3],
                                "verified": bool(r[4]),
                                "parameters": r[5],
                                "error": r[6],
                            })
                        return results
                finally:
                    conn.close()
        except Exception:
            pass
        return list(_audit_log[-limit:])


def clear_audit_trail(confirm: bool = False) -> None:
    """Clear audit log history. Requires confirm=True."""
    if not confirm:
        raise PermissionError("clear_audit_trail requires explicit confirm=True flag.")

    with _audit_lock:
        _audit_log.clear()
        try:
            db_path = _get_audit_db_path()
            if db_path.exists():
                conn = sqlite3.connect(str(db_path), timeout=5.0)
                try:
                    conn.execute("DELETE FROM audit_trail;")
                    conn.commit()
                finally:
                    conn.close()
        except Exception:
            pass
