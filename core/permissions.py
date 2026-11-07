"""
core/permissions.py — Security, Permission Levels, and Action Audit Logging.

Enforces execution boundaries across all registered tools:
1. Permission Levels:
   - READ_ONLY: Read-only inspection (web search, screen inspection, system status).
   - NORMAL: Standard assistant operations (open app, Spotify playback, typing).
   - SENSITIVE: Modifying hardware or settings (Bluetooth, Wi-Fi, audio routing, dev agent).
   - DESTRUCTIVE: Irreversible operations (system shutdown, permanent file deletion, process killing).
2. Action Audit Logging:
   - Maintains an append-only audit trail of executed tools with timestamps, parameters, and outcomes.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, Optional

READ_ONLY = "read_only"
NORMAL = "normal"
SENSITIVE = "sensitive"
DESTRUCTIVE = "destructive"

PERMISSION_LEVELS = {READ_ONLY, NORMAL, SENSITIVE, DESTRUCTIVE}

# Tool to permission mapping allowlist
ACTION_PERMISSIONS: dict[str, str] = {
    # Read-only tools
    "web_search": READ_ONLY,
    "system_status": READ_ONLY,
    "recall_memory": READ_ONLY,
    "file_processor": READ_ONLY,
    "flight_finder": READ_ONLY,
    "weather_report": READ_ONLY,
    "youtube_video": READ_ONLY,

    # Normal operational tools
    "open_app": NORMAL,
    "spotify_control": NORMAL,
    "video_player": NORMAL,
    "windows_uia": NORMAL,
    "browser_control": NORMAL,
    "reminder": NORMAL,
    "send_message": NORMAL,
    "save_memory": NORMAL,
    "undo": NORMAL,
    "takeover_desktop": NORMAL,

    # Sensitive tools
    "computer_control": SENSITIVE,
    "windows_control": SENSITIVE,
    "computer_settings": SENSITIVE,
    "dev_agent": SENSITIVE,
    "game_updater": SENSITIVE,

    # Destructive actions (within tools, e.g. shutdown, format, delete)
    "shutdown_jarvis": DESTRUCTIVE,
    "delete_file": DESTRUCTIVE,
    "kill_process": DESTRUCTIVE,
}

_audit_log: list[dict] = []
_audit_lock = Lock()


def get_action_permission(action_name: str, parameters: dict | None = None) -> str:
    """Determine the effective permission tier for an action and its parameters."""
    act = (action_name or "").lower().strip()
    params = parameters or {}

    # Check for destructive parameter sub-actions
    sub_action = str(params.get("action") or "").lower()
    if sub_action in ("shutdown", "restart", "delete", "destroy", "format", "kill"):
        return DESTRUCTIVE

    return ACTION_PERMISSIONS.get(act, NORMAL)


def requires_confirmation(action_name: str, parameters: dict | None = None) -> bool:
    """Check if the action requires explicit human confirmation before execution."""
    level = get_action_permission(action_name, parameters)
    return level == DESTRUCTIVE


def log_action_audit(
    action: str,
    parameters: dict,
    permission_level: str,
    status: str,
    verified: bool,
    error: Optional[str] = None,
) -> dict:
    """Record an action execution to the secure in-memory audit log."""
    from memory.config_manager import redact_secret

    entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "permission_level": permission_level,
        "status": status,
        "verified": verified,
        "parameters": redact_secret(str(parameters)),
        "error": error,
    }

    with _audit_lock:
        _audit_log.append(entry)
        if len(_audit_log) > 500:
            _audit_log.pop(0)

    return entry


def get_audit_trail(limit: int = 50) -> list[dict]:
    """Retrieve recent action audit events."""
    with _audit_lock:
        return list(_audit_log[-limit:])


def clear_audit_trail() -> None:
    with _audit_lock:
        _audit_log.clear()

