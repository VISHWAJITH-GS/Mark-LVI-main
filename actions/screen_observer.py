"""
actions/screen_observer.py — Structured Event-Driven Screen Observation Subsystem.

Produces rich, structured snapshot observations of the Windows desktop:
  - Full screenshot & optimized Gemini preview
  - Active window title, handle, PID, process name, and bounds
  - Monitor count, active monitor, and desktop bounds
  - Mouse cursor position
  - Key visible & actionable UI elements with bounding boxes
  - Current browser URL when a supported browser window is active
  - Timestamp & trigger reason
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from actions.display_manager import get_display_manager, CapturedScreenshot, MonitorInfo
from actions.ui_tree_manager import get_ui_tree_manager

logger = logging.getLogger(__name__)


@dataclass
class ScreenObservation:
    timestamp: float
    trigger_reason: str
    active_window: Dict[str, Any]
    monitors: List[Dict[str, Any]]
    cursor_position: Tuple[int, int]
    visible_elements: List[Dict[str, Any]]
    browser_url: Optional[str] = None
    preview_screenshot: Optional[CapturedScreenshot] = None
    original_screenshot: Optional[CapturedScreenshot] = None

    def to_dict(self, include_images: bool = False) -> dict:
        d = {
            "timestamp": self.timestamp,
            "trigger_reason": self.trigger_reason,
            "active_window": self.active_window,
            "monitors": self.monitors,
            "cursor_position": {"x": self.cursor_position[0], "y": self.cursor_position[1]},
            "visible_elements": self.visible_elements,
            "browser_url": self.browser_url,
        }
        if include_images:
            d["preview_width"] = self.preview_screenshot.width if self.preview_screenshot else 0
            d["preview_height"] = self.preview_screenshot.height if self.preview_screenshot else 0
        return d

    def format_for_gemini(self) -> str:
        """Format a concise text context summary for Gemini reasoning."""
        win_title = self.active_window.get("title", "Unknown")
        proc = self.active_window.get("process_name", "Unknown")
        cur_x, cur_y = self.cursor_position
        mon_count = len(self.monitors)

        lines = [
            f"Active Window: '{win_title}' (Process: {proc})",
            f"Mouse Cursor Position: ({cur_x}, {cur_y})",
            f"Monitors: {mon_count} connected",
        ]
        if self.browser_url:
            lines.append(f"Browser URL: {self.browser_url}")

        elem_summary = []
        for e in self.visible_elements[:20]:
            name = e.get("name")
            ctype = e.get("control_type")
            rect = e.get("bounding_rectangle", {})
            if name:
                elem_summary.append(f"[{ctype}] '{name}' at ({rect.get('left')}, {rect.get('top')})")

        if elem_summary:
            lines.append("Visible Controls:\n  - " + "\n  - ".join(elem_summary))
        else:
            lines.append("Visible Controls: [None directly indexed in active tree]")

        return "\n".join(lines)


class ScreenObserver:
    """Coordinates event-driven screen observation without wasteful background streaming."""

    def __init__(self):
        self.display_manager = get_display_manager()
        self.ui_tree_manager = get_ui_tree_manager()
        self._last_observation: Optional[ScreenObservation] = None

    def get_current_browser_url(self) -> Optional[str]:
        """Attempt to extract active browser URL if browser is in foreground."""
        try:
            from actions import browser_control
            # Check if active session page exists
            if hasattr(browser_control, "_get_active_url"):
                url = browser_control._get_active_url()
                if url:
                    return url
        except Exception:
            pass

        # Try to read address bar from UIA for Edge/Chrome
        try:
            from actions import windows_uia
            active = self.display_manager.get_active_window_info()
            proc = (active.get("process_name") or "").lower()
            if "chrome" in proc or "msedge" in proc or "brave" in proc:
                res = windows_uia.inspect_window(max_depth=3, limit=15, filter_control_types=["Edit"])
                if res.get("status") == "success":
                    for elem in res.get("elements", []):
                        val = elem.get("name") or ""
                        if "http" in val or ".com" in val or ".org" in val or "localhost" in val:
                            return val
        except Exception:
            pass
        return None

    def capture_observation(
        self,
        trigger_reason: str = "user_command",
        capture_images: bool = True,
        max_ui_elements: int = 40,
    ) -> ScreenObservation:
        """
        Capture a complete structured snapshot of the current desktop state.
        """
        t0 = time.time()
        win_info = self.display_manager.get_active_window_info()
        monitors = [m.to_dict() for m in self.display_manager.get_monitors()]
        cursor_pos = self.display_manager.get_cursor_position()
        browser_url = self.get_current_browser_url()

        # Capture images if requested
        preview = None
        orig = None
        if capture_images:
            try:
                orig = self.display_manager.capture_screenshot(monitor_index=0)
                preview = self.display_manager.generate_preview(orig)
            except Exception as e:
                logger.warning("Screenshot capture failed during observation: %s", e)

        # Inspect visible UI elements
        visible_elements: List[Dict[str, Any]] = []
        try:
            tree_res = self.ui_tree_manager.inspect_ui_tree(
                max_depth=4,
                limit=max_ui_elements,
                only_visible=True,
                only_actionable=True,
            )
            if tree_res.get("status") == "success":
                visible_elements = tree_res.get("elements", [])
        except Exception as e:
            logger.warning("UI tree inspection failed during observation: %s", e)

        obs = ScreenObservation(
            timestamp=t0,
            trigger_reason=trigger_reason,
            active_window=win_info,
            monitors=monitors,
            cursor_position=cursor_pos,
            visible_elements=visible_elements,
            browser_url=browser_url,
            preview_screenshot=preview,
            original_screenshot=orig,
        )
        self._last_observation = obs
        return obs

    def get_last_observation(self) -> Optional[ScreenObservation]:
        return self._last_observation


_DEFAULT_SCREEN_OBSERVER: Optional[ScreenObserver] = None


def get_screen_observer() -> ScreenObserver:
    global _DEFAULT_SCREEN_OBSERVER
    if _DEFAULT_SCREEN_OBSERVER is None:
        _DEFAULT_SCREEN_OBSERVER = ScreenObserver()
    return _DEFAULT_SCREEN_OBSERVER

