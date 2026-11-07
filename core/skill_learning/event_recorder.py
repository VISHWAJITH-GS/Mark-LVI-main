"""
core/skill_learning/event_recorder.py — Desktop Event Listener & Interaction Recorder.

Records user interactions during a demonstration session:
- Mouse clicks & element interactions (mapped via Windows UI Automation)
- Text input (clustered and sanitized to never record credentials)
- Window activations & application switches
- Browser navigations and dialog events
"""
from __future__ import annotations

import platform
import threading
import time
from typing import Callable, List, Optional

from core.skill_learning.models import UIEvent
from core.skill_learning.skill_security import is_sensitive_control, sanitize_input_value
from core.skill_learning.ui_state_capture import capture_element_at_point, get_current_ui_state

_IS_WINDOWS = platform.system() == "Windows"


class EventRecorder:
    """Thread-safe user interaction recorder for demonstration sessions."""

    def __init__(self, on_event_cb: Optional[Callable[[UIEvent], None]] = None):
        self.on_event_cb = on_event_cb
        self._events: list[UIEvent] = []
        self._is_recording = False
        self._is_paused = False
        self._lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._last_window_title = ""
        self._last_process_name = ""

    def start(self) -> None:
        """Start recording user actions."""
        with self._lock:
            self._events.clear()
            self._is_recording = True
            self._is_paused = False
            self._last_window_title = ""
            self._last_process_name = ""

        # Launch background poller for window changes & focus transitions
        self._monitor_thread = threading.Thread(
            target=self._poll_desktop_state,
            daemon=True,
            name="SkillEventRecorderThread",
        )
        self._monitor_thread.start()

    def pause(self) -> None:
        """Temporarily pause event capture."""
        with self._lock:
            self._is_paused = True

    def resume(self) -> None:
        """Resume event capture."""
        with self._lock:
            self._is_paused = False

    def stop(self) -> list[UIEvent]:
        """Stop recording and return captured events."""
        with self._lock:
            self._is_recording = False
            self._is_paused = False
            return list(self._events)

    def is_active(self) -> bool:
        with self._lock:
            return self._is_recording and not self._is_paused

    def record_click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> Optional[UIEvent]:
        """Record a mouse click on screen mapped to semantic UI element."""
        if not self.is_active():
            return None

        now = time.time()
        elem_info = capture_element_at_point(x, y) or {}
        ui_state = get_current_ui_state(max_elements=10)

        # Build semantic selector
        selector = {}
        if elem_info.get("name") and elem_info.get("name") != "[PROTECTED_PASSWORD_FIELD]":
            selector["name"] = elem_info["name"]
        if elem_info.get("control_type"):
            selector["control_type"] = elem_info["control_type"]
        if elem_info.get("automation_id"):
            selector["automation_id"] = elem_info["automation_id"]

        event = UIEvent(
            timestamp=now,
            event_type="double_click" if clicks == 2 else "click",
            app_name=ui_state.app_name or elem_info.get("class_name", "Unknown"),
            window_title=ui_state.window_title,
            element_name=elem_info.get("name", ""),
            control_type=elem_info.get("control_type", ""),
            automation_id=elem_info.get("automation_id", ""),
            role=elem_info.get("control_type", ""),
            selector=selector,
            action=f"click_{button}",
            input_value="",
            bounding_box=elem_info.get("bounding_rectangle"),
        )
        self._emit_event(event)
        return event

    def record_text_input(self, text: str, element_info: Optional[dict] = None) -> Optional[UIEvent]:
        """Record sanitized text input."""
        if not self.is_active():
            return None

        now = time.time()
        elem = element_info or {}
        ui_state = get_current_ui_state(max_elements=5)

        # Strictly sanitize / redact credentials
        clean_text = sanitize_input_value(elem, text)

        selector = {}
        if elem.get("name") and not elem.get("is_sensitive"):
            selector["name"] = elem["name"]
        if elem.get("control_type"):
            selector["control_type"] = elem["control_type"]
        if elem.get("automation_id"):
            selector["automation_id"] = elem["automation_id"]

        event = UIEvent(
            timestamp=now,
            event_type="type_text",
            app_name=ui_state.app_name,
            window_title=ui_state.window_title,
            element_name=elem.get("name", "Text Field"),
            control_type=elem.get("control_type", "Edit"),
            automation_id=elem.get("automation_id", ""),
            role="Edit",
            selector=selector,
            action="type_text",
            input_value=clean_text,
        )
        self._emit_event(event)
        return event

    def record_custom_event(
        self,
        event_type: str,
        app_name: str,
        window_title: str,
        action: str,
        element_name: str = "",
        selector: Optional[dict] = None,
        input_value: str = "",
    ) -> UIEvent:
        """Inject or record custom structured event (e.g. browser navigation, file dialog)."""
        now = time.time()
        from memory.config_manager import redact_secret
        clean_val = redact_secret(input_value)

        event = UIEvent(
            timestamp=now,
            event_type=event_type,
            app_name=app_name,
            window_title=window_title,
            element_name=element_name,
            selector=selector or {},
            action=action,
            input_value=clean_val,
        )
        self._emit_event(event)
        return event

    def _emit_event(self, event: UIEvent) -> None:
        with self._lock:
            self._events.append(event)
        if self.on_event_cb:
            try:
                self.on_event_cb(event)
            except Exception:
                pass

    def _poll_desktop_state(self) -> None:
        """Background thread polling for active window switches."""
        while True:
            with self._lock:
                if not self._is_recording:
                    break
                paused = self._is_paused

            if not paused and _IS_WINDOWS:
                try:
                    from actions import windows_uia
                    if windows_uia.is_available():
                        win = windows_uia.get_active_window()
                        if win.get("status") == "success":
                            title = win.get("title", "")
                            pname = win.get("process_name", "")
                            if (title and title != self._last_window_title) or (pname and pname != self._last_process_name):
                                self._last_window_title = title
                                self._last_process_name = pname
                                evt = UIEvent(
                                    timestamp=time.time(),
                                    event_type="window_activate",
                                    app_name=pname.replace(".exe", "").capitalize(),
                                    window_title=title,
                                    action="activate_window",
                                )
                                self._emit_event(evt)
                except Exception:
                    pass
            time.sleep(0.4)
