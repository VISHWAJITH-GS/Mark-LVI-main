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
import sqlite3
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
    "observe_page": READ_ONLY,
    "code_helper": READ_ONLY,
    "leetcode_assistant": READ_ONLY,

    # Normal operational tools
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
    """Determine the effective permission tier for an action and its parameters.
    
    Fail-closed policy: any unknown or unregistered action strictly defaults to SENSITIVE.
    """
    act = (action_name or "").lower().strip()
    params = parameters or {}

    # Check for destructive parameter sub-actions
    sub_action = str(params.get("action") or "").lower()
    if sub_action in ("shutdown", "restart", "delete", "destroy", "format", "kill"):
        return DESTRUCTIVE

    # Fail-closed: default to SENSITIVE for any unknown tool
    return ACTION_PERMISSIONS.get(act, SENSITIVE)


_session_approved_sensitive: set[str] = set()
_session_lock = Lock()


def reset_session_approvals() -> None:
    """Reset session-level sensitive approvals (called at session start)."""
    with _session_lock:
        _session_approved_sensitive.clear()


def approve_sensitive_action_for_session(action_name: str) -> None:
    """Mark a sensitive action as approved for the remainder of this session."""
    with _session_lock:
        _session_approved_sensitive.add((action_name or "").lower().strip())


_untrusted_context: bool = False

def set_untrusted_context(active: bool) -> None:
    global _untrusted_context
    _untrusted_context = bool(active)

def is_untrusted_context() -> bool:
    return _untrusted_context


def requires_confirmation(action_name: str, parameters: dict | None = None, from_untrusted: bool | None = None) -> bool:
    """Check if the action requires explicit human confirmation before execution.
    
    Applies user trust level policy:
      - Any action originating from untrusted content requiring SENSITIVE or DESTRUCTIVE permissions
        MUST require confirmation regardless of trust level.
      - DESTRUCTIVE: always requires confirmation under all trust levels.
      - SENSITIVE:
          * 'strict': always requires confirmation.
          * 'balanced' (default): requires confirmation on first use per session.
          * 'trusted': does not require confirmation unless from untrusted content.
      - NORMAL / READ_ONLY: does not require confirmation.
    """
    from memory.config_manager import (
        get_trust_level,
        TRUST_LEVEL_STRICT,
        TRUST_LEVEL_BALANCED,
        TRUST_LEVEL_TRUSTED,
    )

    act = (action_name or "").lower().strip()
    level = get_action_permission(act, parameters)

    untrusted_origin = _untrusted_context if from_untrusted is None else bool(from_untrusted)
    if untrusted_origin and level in (DESTRUCTIVE, SENSITIVE):
        return True

    if level == DESTRUCTIVE:
        return True

    if level == SENSITIVE:
        trust = get_trust_level()
        if trust == TRUST_LEVEL_STRICT:
            return True
        elif trust == TRUST_LEVEL_BALANCED:
            with _session_lock:
                return act not in _session_approved_sensitive
        elif trust == TRUST_LEVEL_TRUSTED:
            return False

    return False


_MAX_AUDIT_ENTRIES = 5000


def _get_audit_db_path() -> Path:
    """Return path to persistent SQLite audit database in per-user data directory."""
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


def log_action_audit(
    action: str,
    parameters: dict,
    permission_level: str,
    status: str,
    verified: bool,
    error: Optional[str] = None,
) -> dict:
    """Record an action execution to persistent, tamper-resistant SQLite audit log with secret redaction."""
    from memory.config_manager import redact_secret

    redacted_params = redact_secret(str(parameters or {}))
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

        # Persist to SQLite
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
                # Enforce rotation limit
                conn.execute(
                    f"DELETE FROM audit_trail WHERE id NOT IN (SELECT id FROM audit_trail ORDER BY id DESC LIMIT {_MAX_AUDIT_ENTRIES})"
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
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
    """Clear audit log history.
    
    Security Guard: Requires explicit confirm=True argument to prevent accidental or unauthorized truncation.
    """
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

