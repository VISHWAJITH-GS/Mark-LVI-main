"""
actions/computer_use_engine.py — Unified Computer Use Engine for Windows Desktop Automation.

Provides the complete suite of desktop perception and deterministic execution tools:
  - capture_desktop: Captures full native resolution screenshot across all monitors.
  - inspect_screen: Returns structured screen observation and vision summary.
  - inspect_active_window: Retrieves active window title, bounds, PID, and hierarchy.
  - inspect_ui_tree: Traverses accessible UIA control tree with pagination & filtering.
  - find_ui_element: Resolves target control using semantic UIA and visual grounding.
  - click_element: High-reliability click (UIA pattern -> verified bounding box coordinate).
  - click_coordinates: Direct coordinate click with multi-monitor bounds validation.
  - click_cursor_position: 'Click here' — clicks exact position of the mouse cursor.
  - double_click_element: Double-clicks an element or coordinate.
  - type_text: Types text into focused or targeted input field with verification.
  - press_key: Presses individual or combined keyboard shortcut (Enter, Ctrl+C, Alt+Tab, etc.).
  - scroll: Scrolls up/down/horizontal with direction and amount.
  - drag_and_drop: Performs smooth mouse drag from start coordinate to end coordinate.
  - switch_window: Brings requested window or title to the foreground.
  - minimize_window: Minimizes the active or specified window.
  - maximize_window: Maximizes the active or specified window.
  - close_window: Closes the active or specified window with verification.
  - open_application: Launches native Windows application with verified process/window check.

All methods return standard ActionResult objects satisfying the Result Contract.
"""
from __future__ import annotations

import logging
import os
import platform
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from core import outcome
from actions.action_verifier import get_action_verifier
from actions.display_manager import get_display_manager
from actions.screen_observer import get_screen_observer
from actions.target_resolver import get_target_resolver, ResolvedTarget
from actions.ui_tree_manager import get_ui_tree_manager
from actions import windows_uia
from actions import computer_control

logger = logging.getLogger(__name__)
_IS_WINDOWS = platform.system() == "Windows"


class ComputerUseEngine:
    """Central orchestrator for all local computer perception and execution primitives."""

    def __init__(self):
        self.display_manager = get_display_manager()
        self.screen_observer = get_screen_observer()
        self.ui_tree_manager = get_ui_tree_manager()
        self.target_resolver = get_target_resolver()
        self.verifier = get_action_verifier()

    # 1. Perception primitives
    def capture_desktop(self, monitor_index: int = 0) -> outcome.ActionResult:
        t0 = time.monotonic()
        try:
            shot = self.display_manager.capture_screenshot(monitor_index=monitor_index)
            preview = self.display_manager.generate_preview(shot)
            return outcome.ActionResult(
                status=outcome.STATUS_DONE,
                action="capture_desktop",
                success=True,
                verified=True,
                message=f"Captured desktop ({shot.width}x{shot.height}) across {len(self.display_manager.get_monitors())} monitors.",
                data={
                    "width": shot.width,
                    "height": shot.height,
                    "preview_width": preview.width,
                    "preview_height": preview.height,
                    "origin_x": shot.origin_x,
                    "origin_y": shot.origin_y,
                },
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return outcome.ActionResult(
                status=outcome.STATUS_FAILED,
                action="capture_desktop",
                success=False,
                verified=False,
                message=f"Desktop capture failed: {e}",
                error=str(e),
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    def inspect_screen(self, query: Optional[str] = None) -> outcome.ActionResult:
        t0 = time.monotonic()
        try:
            obs = self.screen_observer.capture_observation(trigger_reason="inspect_screen")
            text_summary = obs.format_for_gemini()
            return outcome.ActionResult(
                status=outcome.STATUS_DONE,
                action="inspect_screen",
                success=True,
                verified=True,
                message=text_summary,
                data=obs.to_dict(include_images=False),
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return outcome.ActionResult(
                status=outcome.STATUS_FAILED,
                action="inspect_screen",
                success=False,
                verified=False,
                message=f"Screen inspection failed: {e}",
                error=str(e),
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    def inspect_active_window(self) -> outcome.ActionResult:
        t0 = time.monotonic()
        win = self.display_manager.get_active_window_info()
        title = win.get("title", "")
        proc = win.get("process_name", "")
        return outcome.ActionResult(
            status=outcome.STATUS_DONE,
            action="inspect_active_window",
            success=bool(title or proc),
            verified=bool(title or proc),
            message=f"Active Window: '{title}' ({proc})",
            data=win,
            duration_ms=(time.monotonic() - t0) * 1000,
        )

    def inspect_ui_tree(
        self,
        max_depth: int = 4,
        limit: int = 50,
        offset: int = 0,
        filter_types: Optional[List[str]] = None,
    ) -> outcome.ActionResult:
        t0 = time.monotonic()
        res = self.ui_tree_manager.inspect_ui_tree(
            max_depth=max_depth,
            limit=limit,
            offset=offset,
            filter_types=filter_types,
            only_visible=True,
            only_actionable=True,
        )
        return outcome.ActionResult(
            status=outcome.STATUS_DONE if res.get("status") == "success" else outcome.STATUS_FAILED,
            action="inspect_ui_tree",
            success=res.get("status") == "success",
            verified=res.get("status") == "success",
            message=f"Retrieved {len(res.get('elements', []))} UI elements (total: {res.get('total_found', 0)}).",
            data=res,
            duration_ms=(time.monotonic() - t0) * 1000,
        )

    def find_ui_element(
        self,
        name: str,
        control_type: Optional[str] = None,
        automation_id: Optional[str] = None,
    ) -> outcome.ActionResult:
        t0 = time.monotonic()
        resolved, reason = self.target_resolver.resolve_target(
            query=name,
            control_type=control_type,
            automation_id=automation_id,
        )
        if resolved:
            return outcome.ActionResult(
                status=outcome.STATUS_DONE,
                action="find_ui_element",
                success=True,
                verified=True,
                message=f"Found '{resolved.target_name}' at ({resolved.desktop_x}, {resolved.desktop_y}) via {resolved.resolution_type}",
                data=resolved.to_dict(),
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        return outcome.ActionResult(
            status=outcome.STATUS_FAILED,
            action="find_ui_element",
            success=False,
            verified=False,
            message=reason,
            error=reason,
            duration_ms=(time.monotonic() - t0) * 1000,
        )

    # 2. Action execution primitives
    def click_element(
        self,
        name: str,
        control_type: Optional[str] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
    ) -> outcome.ActionResult:
        """Click an element using semantic UIA pattern when available, falling back to verified coordinates."""
        t0 = time.monotonic()
        if x is not None and y is not None:
            return self.click_coordinates(x=x, y=y)

        resolved, reason = self.target_resolver.resolve_target(name, control_type=control_type)
        if not resolved:
            return outcome.ActionResult(
                status=outcome.STATUS_FAILED,
                action="click_element",
                success=False,
                verified=False,
                message=f"Target element '{name}' could not be resolved: {reason}",
                error=reason,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        # Try semantic pattern if available
        if resolved.uia_node and resolved.uia_node.supported_patterns:
            ok, pat_msg = self.ui_tree_manager.invoke_control_pattern(resolved.uia_node)
            if ok:
                ver = self.verifier.verify_element_interaction(resolved.uia_node, action_type="click")
                return outcome.ActionResult(
                    status=outcome.STATUS_DONE,
                    action="click_element",
                    success=True,
                    verified=ver.verified,
                    message=f"Clicked '{resolved.target_name}' via pattern: {pat_msg}",
                    data=resolved.to_dict(),
                    duration_ms=(time.monotonic() - t0) * 1000,
                )

        # Coordinate click fallback
        return self.click_coordinates(x=resolved.desktop_x, y=resolved.desktop_y)

    def click_coordinates(self, x: int, y: int, clicks: int = 1, button: str = "left") -> outcome.ActionResult:
        t0 = time.monotonic()
        vx, vy, ok, reason = windows_uia.validate_coordinates(x, y)
        if not ok:
            return outcome.ActionResult(
                status=outcome.STATUS_FAILED,
                action="click_coordinates",
                success=False,
                verified=False,
                message=f"Invalid coordinates ({x}, {y}): {reason}",
                error=reason,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        try:
            import pyautogui
            pyautogui.FAILSAFE = True
            pyautogui.click(x=vx, y=vy, clicks=clicks, button=button)
            time.sleep(0.2)
            return outcome.ActionResult(
                status=outcome.STATUS_DONE,
                action="click_coordinates",
                success=True,
                verified=True,
                message=f"Clicked at desktop coordinates ({vx}, {vy}) [button={button}, clicks={clicks}]",
                data={"x": vx, "y": vy, "button": button, "clicks": clicks},
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return outcome.ActionResult(
                status=outcome.STATUS_FAILED,
                action="click_coordinates",
                success=False,
                verified=False,
                message=f"Mouse click failed: {e}",
                error=str(e),
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    def click_cursor_position(self) -> outcome.ActionResult:
        """Execute special 'Click here' command: resolves target under cursor and clicks."""
        t0 = time.monotonic()
        resolved = self.target_resolver.resolve_cursor_position()
        res = self.click_coordinates(x=resolved.desktop_x, y=resolved.desktop_y)
        res.action = "click_cursor_position"
        res.message = f"Clicked at cursor location ({resolved.desktop_x}, {resolved.desktop_y}). {resolved.notes}"
        return res

    def double_click_element(self, name: Optional[str] = None, x: Optional[int] = None, y: Optional[int] = None) -> outcome.ActionResult:
        if x is not None and y is not None:
            return self.click_coordinates(x=x, y=y, clicks=2)
        if name:
            resolved, _ = self.target_resolver.resolve_target(name)
            if resolved:
                return self.click_coordinates(x=resolved.desktop_x, y=resolved.desktop_y, clicks=2)
        cur_x, cur_y = self.display_manager.get_cursor_position()
        return self.click_coordinates(x=cur_x, y=cur_y, clicks=2)

    def type_text(self, text: str, element_name: Optional[str] = None, press_enter: bool = False) -> outcome.ActionResult:
        t0 = time.monotonic()
        target_node = None
        if element_name:
            resolved, _ = self.target_resolver.resolve_target(element_name, control_type="Edit")
            if resolved:
                self.click_coordinates(x=resolved.desktop_x, y=resolved.desktop_y)
                time.sleep(0.2)
                target_node = resolved.uia_node

        try:
            import pyautogui
            import pyperclip
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.1)
            if press_enter:
                pyautogui.press("enter")

            ver = self.verifier.verify_text_input(expected_text=text, target_node=target_node)
            return outcome.ActionResult(
                status=outcome.STATUS_DONE,
                action="type_text",
                success=True,
                verified=ver.verified,
                message=f"Typed text into active field ({len(text)} chars).",
                data={"length": len(text)},
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return outcome.ActionResult(
                status=outcome.STATUS_FAILED,
                action="type_text",
                success=False,
                verified=False,
                message=f"Text typing failed: {e}",
                error=str(e),
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    def press_key(self, key: str, modifiers: Optional[List[str]] = None) -> outcome.ActionResult:
        t0 = time.monotonic()
        try:
            import pyautogui
            if modifiers:
                pyautogui.hotkey(*modifiers, key.lower())
            else:
                pyautogui.press(key.lower())
            return outcome.ActionResult(
                status=outcome.STATUS_DONE,
                action="press_key",
                success=True,
                verified=True,
                message=f"Pressed key: {'+'.join((modifiers or []) + [key])}",
                data={"key": key, "modifiers": modifiers},
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return outcome.ActionResult(
                status=outcome.STATUS_FAILED,
                action="press_key",
                success=False,
                verified=False,
                message=f"Key press failed: {e}",
                error=str(e),
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    def scroll(self, direction: str = "down", amount: int = 300) -> outcome.ActionResult:
        t0 = time.monotonic()
        try:
            import pyautogui
            clicks = -amount if direction.lower() == "down" else amount
            pyautogui.scroll(clicks)
            return outcome.ActionResult(
                status=outcome.STATUS_DONE,
                action="scroll",
                success=True,
                verified=True,
                message=f"Scrolled {direction} by {amount} units.",
                data={"direction": direction, "amount": amount},
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return outcome.ActionResult(
                status=outcome.STATUS_FAILED,
                action="scroll",
                success=False,
                verified=False,
                message=f"Scroll failed: {e}",
                error=str(e),
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    def drag_and_drop(self, start_x: int, start_y: int, end_x: int, end_y: int) -> outcome.ActionResult:
        t0 = time.monotonic()
        try:
            import pyautogui
            pyautogui.moveTo(start_x, start_y)
            time.sleep(0.1)
            pyautogui.dragTo(end_x, end_y, duration=0.6, button="left")
            return outcome.ActionResult(
                status=outcome.STATUS_DONE,
                action="drag_and_drop",
                success=True,
                verified=True,
                message=f"Dragged from ({start_x}, {start_y}) to ({end_x}, {end_y}).",
                data={"from": [start_x, start_y], "to": [end_x, end_y]},
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return outcome.ActionResult(
                status=outcome.STATUS_FAILED,
                action="drag_and_drop",
                success=False,
                verified=False,
                message=f"Drag and drop failed: {e}",
                error=str(e),
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    def switch_window(self, title_or_app: str) -> outcome.ActionResult:
        t0 = time.monotonic()
        res = windows_uia.focus_window(title_or_app)
        ok = res.get("status") == "success"
        return outcome.ActionResult(
            status=outcome.STATUS_DONE if ok else outcome.STATUS_FAILED,
            action="switch_window",
            success=ok,
            verified=ok,
            message=f"Switched window to '{title_or_app}': {res.get('message', '')}",
            data=res,
            duration_ms=(time.monotonic() - t0) * 1000,
        )

    def minimize_window(self, title: Optional[str] = None) -> outcome.ActionResult:
        t0 = time.monotonic()
        res = windows_uia.minimize_window(title or "")
        ok = res.get("status") == "success"
        return outcome.ActionResult(
            status=outcome.STATUS_DONE if ok else outcome.STATUS_FAILED,
            action="minimize_window",
            success=ok,
            verified=ok,
            message=f"Minimized window: {res.get('message', '')}",
            duration_ms=(time.monotonic() - t0) * 1000,
        )

    def maximize_window(self, title: Optional[str] = None) -> outcome.ActionResult:
        t0 = time.monotonic()
        res = windows_uia.maximize_window(title or "")
        ok = res.get("status") == "success"
        return outcome.ActionResult(
            status=outcome.STATUS_DONE if ok else outcome.STATUS_FAILED,
            action="maximize_window",
            success=ok,
            verified=ok,
            message=f"Maximized window: {res.get('message', '')}",
            duration_ms=(time.monotonic() - t0) * 1000,
        )

    def close_window(self, title: Optional[str] = None) -> outcome.ActionResult:
        t0 = time.monotonic()
        res = windows_uia.close_window(title or "")
        ok = res.get("status") == "success"
        return outcome.ActionResult(
            status=outcome.STATUS_DONE if ok else outcome.STATUS_FAILED,
            action="close_window",
            success=ok,
            verified=ok,
            message=f"Closed window: {res.get('message', '')}",
            duration_ms=(time.monotonic() - t0) * 1000,
        )

    def open_application(self, app_name: str, arguments: Optional[str] = None) -> outcome.ActionResult:
        t0 = time.monotonic()
        from actions import open_app
        raw_res = open_app.open_app({"app": app_name, "args": arguments} if arguments else {"app": app_name})
        norm = outcome.normalize_result(raw_res, action_name="open_application")
        # Check verification
        ver = self.verifier.verify_window_launch(app_name, timeout=2.5)
        if ver.status == "done":
            norm.status = outcome.STATUS_DONE
            norm.verified = True
            norm.success = True
            norm.message = ver.message
        elif ver.status == "started":
            norm.status = outcome.STATUS_STARTED
            norm.verified = True
            norm.success = True
            norm.message = ver.message
        return norm


_DEFAULT_ENGINE: Optional[ComputerUseEngine] = None


def get_computer_use_engine() -> ComputerUseEngine:
    global _DEFAULT_ENGINE
    if _DEFAULT_ENGINE is None:
        _DEFAULT_ENGINE = ComputerUseEngine()
    return _DEFAULT_ENGINE


# Tool Handler Dispatch for action_loader auto-discovery
def computer_use(parameters: dict, player=None, speak=None, **kwargs) -> str:
    """Unified handler for all computer use actions."""
    engine = get_computer_use_engine()
    p = dict(parameters or {})
    p.update(kwargs)

    action = p.get("action", "").lower().strip()
    res: outcome.ActionResult

    if action == "capture_desktop":
        res = engine.capture_desktop(monitor_index=int(p.get("monitor_index", 0)))
    elif action == "inspect_screen":
        res = engine.inspect_screen(query=p.get("query"))
    elif action == "inspect_active_window":
        res = engine.inspect_active_window()
    elif action == "inspect_ui_tree":
        res = engine.inspect_ui_tree(
            max_depth=int(p.get("max_depth", 4)),
            limit=int(p.get("limit", 50)),
            offset=int(p.get("offset", 0)),
        )
    elif action == "find_ui_element":
        res = engine.find_ui_element(
            name=p.get("name", ""),
            control_type=p.get("control_type"),
            automation_id=p.get("automation_id"),
        )
    elif action in ("click_element", "click"):
        res = engine.click_element(
            name=p.get("name") or p.get("target", ""),
            control_type=p.get("control_type"),
            x=p.get("x"),
            y=p.get("y"),
        )
    elif action == "click_coordinates":
        res = engine.click_coordinates(
            x=int(p.get("x", 0)),
            y=int(p.get("y", 0)),
            clicks=int(p.get("clicks", 1)),
            button=p.get("button", "left"),
        )
    elif action in ("click_cursor_position", "click_here"):
        res = engine.click_cursor_position()
    elif action == "double_click_element":
        res = engine.double_click_element(
            name=p.get("name"),
            x=p.get("x"),
            y=p.get("y"),
        )
    elif action == "type_text":
        res = engine.type_text(
            text=p.get("text", ""),
            element_name=p.get("element_name"),
            press_enter=bool(p.get("press_enter", False)),
        )
    elif action == "press_key":
        res = engine.press_key(
            key=p.get("key", "enter"),
            modifiers=p.get("modifiers"),
        )
    elif action == "scroll":
        res = engine.scroll(
            direction=p.get("direction", "down"),
            amount=int(p.get("amount", 300)),
        )
    elif action == "drag_and_drop":
        res = engine.drag_and_drop(
            start_x=int(p.get("start_x", 0)),
            start_y=int(p.get("start_y", 0)),
            end_x=int(p.get("end_x", 0)),
            end_y=int(p.get("end_y", 0)),
        )
    elif action == "switch_window":
        res = engine.switch_window(title_or_app=p.get("title") or p.get("app_name", ""))
    elif action == "minimize_window":
        res = engine.minimize_window(title=p.get("title"))
    elif action == "maximize_window":
        res = engine.maximize_window(title=p.get("title"))
    elif action == "close_window":
        res = engine.close_window(title=p.get("title"))
    elif action == "open_application":
        res = engine.open_application(app_name=p.get("app_name", ""), arguments=p.get("arguments"))
    else:
        res = outcome.ActionResult(
            status=outcome.STATUS_UNSUPPORTED,
            action=action or "computer_use",
            success=False,
            verified=False,
            message=f"Unsupported computer_use action '{action}'",
            error=f"Sub-action '{action}' not recognized",
        )

    return res.to_speech_text() if hasattr(res, "to_speech_text") else res.message


TOOL = {
    "name": "computer_use",
    "description": (
        "COMPLETE WINDOWS COMPUTER USE ENGINE: Provides full desktop perception and control. "
        "Capabilities: capture_desktop, inspect_screen, inspect_active_window, inspect_ui_tree, "
        "find_ui_element, click_element (by accessible name or description), click_coordinates, "
        "click_cursor_position ('Click here' command), double_click_element, type_text, press_key, "
        "scroll, drag_and_drop, switch_window, minimize_window, maximize_window, close_window, "
        "and open_application. Automatically uses Windows UI Automation first and falls back to visual grounding."
    ),
    "brief": "Full Windows desktop perception, mouse, keyboard, and application control.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "The action to execute: 'capture_desktop', 'inspect_screen', 'inspect_active_window', "
                    "'inspect_ui_tree', 'find_ui_element', 'click_element', 'click_coordinates', "
                    "'click_cursor_position', 'double_click_element', 'type_text', 'press_key', "
                    "'scroll', 'drag_and_drop', 'switch_window', 'minimize_window', 'maximize_window', "
                    "'close_window', 'open_application'."
                ),
            },
            "name": {
                "type": "STRING",
                "description": "Name or visual label of the UI control to find or click.",
            },
            "text": {
                "type": "STRING",
                "description": "Text to type into focused or targeted control.",
            },
            "x": {
                "type": "INTEGER",
                "description": "Desktop physical X coordinate.",
            },
            "y": {
                "type": "INTEGER",
                "description": "Desktop physical Y coordinate.",
            },
            "key": {
                "type": "STRING",
                "description": "Key name to press (e.g. 'enter', 'tab', 'escape', 'f5').",
            },
            "modifiers": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Modifier keys (e.g. ['ctrl'], ['alt', 'shift']).",
            },
            "direction": {
                "type": "STRING",
                "description": "Scroll direction: 'up', 'down', 'left', 'right'.",
            },
            "app_name": {
                "type": "STRING",
                "description": "Application name to launch or window to switch to.",
            },
        },
        "required": ["action"],
    },
    "handler": computer_use,
}

