"""
core/skill_learning/ui_state_capture.py — Semantic UI State Inspection and Control Extraction.

Captures high-fidelity desktop UI state during user demonstration:
- Active window identification (title, process, executable)
- Accessible element hierarchy and actionable controls
- Control under cursor coordinates
- Password/credential protection
- Browser URL and state extraction
"""
from __future__ import annotations

import platform
import time
from typing import Any, Dict, List, Optional, Tuple

from core.skill_learning.models import UIState
from core.skill_learning.skill_security import is_sensitive_control

_IS_WINDOWS = platform.system() == "Windows"


def get_current_ui_state(max_elements: int = 25) -> UIState:
    """Capture snapshot of the active desktop window and actionable controls."""
    now = time.time()
    state = UIState(
        timestamp=now,
        app_name="Desktop",
        window_title="",
        process_name="",
        elements=[],
        focused_element=None,
        is_browser=False,
        url=None,
    )

    if not _IS_WINDOWS:
        return state

    try:
        from actions import windows_uia
        if windows_uia.is_available():
            win_info = windows_uia.get_active_window()
            if win_info.get("status") == "success":
                state.window_title = win_info.get("title", "")
                state.process_name = win_info.get("process_name", "")
                p_lower = state.process_name.lower()
                state.app_name = state.process_name.replace(".exe", "").capitalize()

                # Detect if browser
                if any(b in p_lower for b in ("chrome", "msedge", "edge", "firefox", "brave", "opera")):
                    state.is_browser = True

            # Inspect actionable controls
            ins = windows_uia.inspect_window(max_depth=3, limit=max_elements, only_actionable=True)
            if ins.get("status") == "success":
                clean_elems = []
                for e in ins.get("elements", []):
                    # Flag sensitive controls
                    is_sens = is_sensitive_control(e)
                    clean_elems.append({
                        "name": "[PROTECTED]" if is_sens else e.get("name", ""),
                        "control_type": e.get("control_type", ""),
                        "automation_id": e.get("automation_id", ""),
                        "class_name": e.get("class_name", ""),
                        "bounding_rectangle": e.get("bounding_rectangle"),
                        "is_sensitive": is_sens,
                    })
                state.elements = clean_elems

    except Exception as e:
        print(f"[UIStateCapture] Error capturing UI state: {e}")

    return state


def capture_element_at_point(x: int, y: int) -> Optional[dict]:
    """Inspect element directly under screen coordinates (x, y)."""
    if not _IS_WINDOWS:
        return None
    try:
        from actions import windows_uia
        elem_data = windows_uia.find_element_at_point(x, y)
        if elem_data and "info" in elem_data:
            info = dict(elem_data["info"])
            is_sens = is_sensitive_control(info)
            if is_sens:
                info["name"] = "[PROTECTED_PASSWORD_FIELD]"
                info["is_sensitive"] = True
            return info
    except Exception as e:
        print(f"[UIStateCapture] Point inspect error ({x}, {y}): {e}")
    return None
