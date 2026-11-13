"""
actions/action_executor.py — Trusted CDP Action Execution & DOM Stabilization Engine.

Executes structured tool calls using hardware-level Chrome DevTools Protocol input events
(bypassing React/Angular event.isTrusted === false synthetic event blocks):
  - click_element: Scrolls into view, computes box model center, dispatches mouseMoved -> mousePressed -> mouseReleased
  - input_text: Focuses element, clears text, dispatches Input.insertText / Enter
  - scroll_page & scroll_element: Dispatches hardware mouseWheel events
  - press_key: Dispatches hardware key events
  - navigate_to, switch_tab, go_back, wait_page
  - observe_page: Extracts AXTree + DOM Snapshot and returns indexed element map

After every action:
  - Waits for network and DOM quiescence (~300-500ms settle debounce)
  - Strictly invalidates ephemeral index lookup table
  - Runs a fresh Perception extraction pass so the LLM always has current state.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Literal, Optional, Tuple

from core import outcome
from actions.browser_session import get_browser_session_manager, BrowserSessionManager, CDPConnectionError
from actions.dom_distiller import get_dom_distiller, DOMDistiller, DistilledElement

logger = logging.getLogger("action_executor")


# Mapping common key names to CDP Windows Virtual Key Codes and key names
KEY_MAP = {
    "Enter": {"windowsVirtualKeyCode": 13, "code": "Enter", "key": "Enter", "text": "\r"},
    "Return": {"windowsVirtualKeyCode": 13, "code": "Enter", "key": "Enter", "text": "\r"},
    "Escape": {"windowsVirtualKeyCode": 27, "code": "Escape", "key": "Escape"},
    "Esc": {"windowsVirtualKeyCode": 27, "code": "Escape", "key": "Escape"},
    "Tab": {"windowsVirtualKeyCode": 9, "code": "Tab", "key": "Tab"},
    "Backspace": {"windowsVirtualKeyCode": 8, "code": "Backspace", "key": "Backspace"},
    "Space": {"windowsVirtualKeyCode": 32, "code": "Space", "key": " ", "text": " "},
    "ArrowDown": {"windowsVirtualKeyCode": 40, "code": "ArrowDown", "key": "ArrowDown"},
    "Down": {"windowsVirtualKeyCode": 40, "code": "ArrowDown", "key": "ArrowDown"},
    "ArrowUp": {"windowsVirtualKeyCode": 38, "code": "ArrowUp", "key": "ArrowUp"},
    "Up": {"windowsVirtualKeyCode": 38, "code": "ArrowUp", "key": "ArrowUp"},
    "ArrowLeft": {"windowsVirtualKeyCode": 37, "code": "ArrowLeft", "key": "ArrowLeft"},
    "Left": {"windowsVirtualKeyCode": 37, "code": "ArrowLeft", "key": "ArrowLeft"},
    "ArrowRight": {"windowsVirtualKeyCode": 39, "code": "ArrowRight", "key": "ArrowRight"},
    "Right": {"windowsVirtualKeyCode": 39, "code": "ArrowRight", "key": "ArrowRight"},
    "PageDown": {"windowsVirtualKeyCode": 34, "code": "PageDown", "key": "PageDown"},
    "PageUp": {"windowsVirtualKeyCode": 33, "code": "PageUp", "key": "PageUp"},
    "Delete": {"windowsVirtualKeyCode": 46, "code": "Delete", "key": "Delete"},
    "Home": {"windowsVirtualKeyCode": 36, "code": "Home", "key": "Home"},
    "End": {"windowsVirtualKeyCode": 35, "code": "End", "key": "End"},
}


class ActionExecutor:
    """
    Executes trusted browser actions via CDP and stabilizes the DOM.
    """
    def __init__(self, session_manager: Optional[BrowserSessionManager] = None, distiller: Optional[DOMDistiller] = None):
        self.session_mgr = session_manager or get_browser_session_manager()
        self.distiller = distiller or get_dom_distiller()

    async def _stabilize_dom(self, page: Any, cdp_session: Any, delay: float = 0.4):
        """Wait for DOM and network quiescence."""
        await asyncio.sleep(delay)
        if page and not page.is_closed():
            try:
                # Allow any microtasks / pending DOM mutations to settle
                await page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 50)))")
            except Exception:
                pass

    async def _post_action_refresh(self, page: Any, cdp_session: Any, delay: float = 0.0) -> Tuple[str, str]:
        """
        Stabilizes the DOM, invalidates the old index map, and generates fresh observation.
        Returns (summary_header, fresh_observation_text).
        """
        if delay > 0:
            await asyncio.sleep(delay)
        await self._stabilize_dom(page, cdp_session)
        self.distiller.invalidate_cache()
        title = await page.title() if (page and not page.is_closed()) else "(closed)"
        url = page.url if (page and not page.is_closed()) else ""
        header = f"Page: '{title}' ({url})"
        obs_text, _ = await self.distiller.distill_page(page, cdp_session)
        return header, obs_text

    # ── Core Action Implementations ───────────────────────────────────────────

    async def click_element(self, index: int) -> str:
        """
        Clicks element by integer index using trusted CDP Input events.
        """
        elem = self.distiller.get_element_by_index(index)
        if not elem:
            return f"[Error] Element index [{index}] not found in latest observation. Call observe_page to refresh."

        page = self.session_mgr.active_page
        cdp = await self.session_mgr._get_cdp_session()
        await self.distiller.remove_som_overlay(page)

        # Resolve coordinates: try CDP BoxModel first, fallback to bounding box center
        x, y = elem.bounding_box.center
        if elem.backend_node_id and cdp:
            try:
                await cdp.send("DOM.scrollIntoViewIfNeeded", {"backendNodeId": elem.backend_node_id})
                box_res = await cdp.send("DOM.getBoxModel", {"backendNodeId": elem.backend_node_id})
                border = box_res.get("model", {}).get("border")
                if border and len(border) >= 8:
                    x = (border[0] + border[2] + border[4] + border[6]) / 4.0
                    y = (border[1] + border[3] + border[5] + border[7]) / 4.0
            except Exception as e:
                logger.debug(f"CDP box model resolution fallback: {e}")

        # Dispatch real trusted hardware mouse events
        await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        await asyncio.sleep(0.05)
        await cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})

        header, obs = await self._post_action_refresh(page, cdp)
        return f"Clicked [{index}] <{elem.role or elem.tag}> '{elem.aria_label or elem.text or elem.name}'.\n{header}\nUpdated Elements:\n{obs}"

    async def input_text(self, index: int, text: str, press_enter: bool = False) -> str:
        """
        Clicks/focuses element, clears existing text, and inserts new text via CDP Input.
        """
        elem = self.distiller.get_element_by_index(index)
        if not elem:
            return f"[Error] Element index [{index}] not found in latest observation. Call observe_page to refresh."

        page = self.session_mgr.active_page
        cdp = await self.session_mgr._get_cdp_session()
        await self.distiller.remove_som_overlay(page)

        x, y = elem.bounding_box.center
        if elem.backend_node_id and cdp:
            try:
                await cdp.send("DOM.scrollIntoViewIfNeeded", {"backendNodeId": elem.backend_node_id})
                box_res = await cdp.send("DOM.getBoxModel", {"backendNodeId": elem.backend_node_id})
                border = box_res.get("model", {}).get("border")
                if border and len(border) >= 8:
                    x = (border[0] + border[2] + border[4] + border[6]) / 4.0
                    y = (border[1] + border[3] + border[5] + border[7]) / 4.0
            except Exception:
                pass

        # 1. Click to focus
        await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        await cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
        await asyncio.sleep(0.05)

        # 2. Clear existing text (Ctrl+A -> Backspace)
        await cdp.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", "windowsVirtualKeyCode": 17, "code": "ControlLeft", "key": "Control", "modifiers": 2})
        await cdp.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", "windowsVirtualKeyCode": 65, "code": "KeyA", "key": "a", "modifiers": 2})
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "windowsVirtualKeyCode": 65, "code": "KeyA", "key": "a", "modifiers": 0})
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "windowsVirtualKeyCode": 17, "code": "ControlLeft", "key": "Control", "modifiers": 0})
        await cdp.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", "windowsVirtualKeyCode": 8, "code": "Backspace", "key": "Backspace"})
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "windowsVirtualKeyCode": 8, "code": "Backspace", "key": "Backspace"})
        await asyncio.sleep(0.05)

        # 3. Insert text via CDP Input.insertText (triggers real onInput/onChange events)
        await cdp.send("Input.insertText", {"text": text})

        # 4. Optional Enter key press
        if press_enter:
            await asyncio.sleep(0.05)
            await cdp.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", "windowsVirtualKeyCode": 13, "code": "Enter", "key": "Enter", "text": "\r", "unmodifiedText": "\r"})
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "windowsVirtualKeyCode": 13, "code": "Enter", "key": "Enter"})

        header, obs = await self._post_action_refresh(page, cdp)
        enter_str = " (pressed Enter)" if press_enter else ""
        return f"Typed '{text}'{enter_str} into [{index}] <{elem.role or elem.tag}>.\n{header}\nUpdated Elements:\n{obs}"

    async def scroll_page(self, direction: Literal["up", "down"] = "down", amount: int = 500) -> str:
        """
        Scrolls the active page up or down using hardware mouseWheel events.
        """
        page = self.session_mgr.active_page
        cdp = await self.session_mgr._get_cdp_session()
        await self.distiller.remove_som_overlay(page)

        cx, cy = 600.0, 400.0
        delta_y = float(amount if str(direction).lower() == "down" else -amount)

        await cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseWheel",
            "x": cx,
            "y": cy,
            "deltaX": 0.0,
            "deltaY": delta_y,
        })

        header, obs = await self._post_action_refresh(page, cdp, delay=0.3)
        return f"Scrolled {direction} by {amount}px.\n{header}\nUpdated Elements:\n{obs}"

    async def scroll_element(self, index: int, direction: Literal["up", "down"] = "down") -> str:
        """
        Scrolls a specific container element (e.g. chat list in WhatsApp/Gmail) up or down.
        """
        elem = self.distiller.get_element_by_index(index)
        if not elem:
            return f"[Error] Element index [{index}] not found in latest observation."

        page = self.session_mgr.active_page
        cdp = await self.session_mgr._get_cdp_session()
        await self.distiller.remove_som_overlay(page)

        x, y = elem.bounding_box.center
        delta_y = 350.0 if str(direction).lower() == "down" else -350.0

        await cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseWheel",
            "x": x,
            "y": y,
            "deltaX": 0.0,
            "deltaY": delta_y,
        })

        header, obs = await self._post_action_refresh(page, cdp, delay=0.3)
        return f"Scrolled element [{index}] {direction}.\n{header}\nUpdated Elements:\n{obs}"

    async def press_key(self, key: str) -> str:
        """
        Dispatches hardware key down and up events (e.g. Escape, Enter, Tab).
        """
        page = self.session_mgr.active_page
        cdp = await self.session_mgr._get_cdp_session()
        await self.distiller.remove_som_overlay(page)

        key_info = KEY_MAP.get(key, {
            "windowsVirtualKeyCode": 0,
            "code": key,
            "key": key,
        })

        payload_down: Dict[str, Any] = {"type": "rawKeyDown", "key": key_info["key"]}
        if key_info.get("windowsVirtualKeyCode"):
            payload_down["windowsVirtualKeyCode"] = key_info["windowsVirtualKeyCode"]
        if key_info.get("code"):
            payload_down["code"] = key_info["code"]
        if key_info.get("text"):
            payload_down["text"] = key_info["text"]

        payload_up: Dict[str, Any] = {"type": "keyUp", "key": key_info["key"]}
        if key_info.get("windowsVirtualKeyCode"):
            payload_up["windowsVirtualKeyCode"] = key_info["windowsVirtualKeyCode"]
        if key_info.get("code"):
            payload_up["code"] = key_info["code"]

        await cdp.send("Input.dispatchKeyEvent", payload_down)
        await asyncio.sleep(0.05)
        await cdp.send("Input.dispatchKeyEvent", payload_up)

        header, obs = await self._post_action_refresh(page, cdp)
        return f"Pressed '{key}'.\n{header}\nUpdated Elements:\n{obs}"

    async def navigate_to(self, url: str) -> str:
        """
        Navigates active tab to a URL.
        """
        page = self.session_mgr.active_page
        cdp = await self.session_mgr._get_cdp_session()
        await self.distiller.remove_som_overlay(page)

        clean_url = url.strip()
        if "://" not in clean_url:
            clean_url = "https://" + clean_url

        await page.goto(clean_url, wait_until="domcontentloaded", timeout=30000)
        header, obs = await self._post_action_refresh(page, cdp, delay=0.6)
        return f"Navigated to {clean_url}.\n{header}\nUpdated Elements:\n{obs}"

    async def switch_tab(self, tab_index: int) -> str:
        """
        Switches focus to an open Edge tab by index.
        """
        tab_info = await self.session_mgr._switch_tab(tab_index)
        page = self.session_mgr.active_page
        cdp = await self.session_mgr._get_cdp_session()
        header, obs = await self._post_action_refresh(page, cdp)
        return f"Switched to tab [{tab_index}] '{tab_info.title}' ({tab_info.url}).\n{header}\nUpdated Elements:\n{obs}"

    async def go_back(self) -> str:
        """
        Navigates back in browser history.
        """
        page = self.session_mgr.active_page
        cdp = await self.session_mgr._get_cdp_session()
        await self.distiller.remove_som_overlay(page)
        await page.go_back(wait_until="domcontentloaded", timeout=15000)
        header, obs = await self._post_action_refresh(page, cdp, delay=0.5)
        return f"Navigated back.\n{header}\nUpdated Elements:\n{obs}"

    async def wait_page(self, seconds: float = 2.0) -> str:
        """
        Waits for a given duration then updates the page observation.
        """
        page = self.session_mgr.active_page
        cdp = await self.session_mgr._get_cdp_session()
        await asyncio.sleep(min(seconds, 30.0))
        header, obs = await self._post_action_refresh(page, cdp, delay=0.1)
        return f"Waited {seconds}s.\n{header}\nUpdated Elements:\n{obs}"

    async def observe_page(self, enable_som: bool = False) -> str:
        """
        Extracts open tabs and visible indexed elements for the active page.
        """
        await self.session_mgr._ensure_attached()
        page = self.session_mgr.active_page
        cdp = await self.session_mgr._get_cdp_session()
        tabs = await self.session_mgr._list_tabs()
        tab_lines = [f"  [{t.index}] {t.title} ({t.url})" + (" [ACTIVE]" if t.is_active else "") for t in tabs]
        tabs_text = "Open Tabs:\n" + ("\n".join(tab_lines) if tab_lines else "  (No tabs open)")

        title = await page.title()
        url = page.url
        header = f"Active Tab: '{title}' ({url})"
        obs_text, _ = await self.distiller.distill_page(page, cdp, enable_som_overlay=enable_som)
        from core import outcome
        from core.permissions import set_untrusted_context
        set_untrusted_context(True)
        return outcome.untrusted("web", f"{tabs_text}\n\n{header}\nInteractive Elements in Viewport:\n{obs_text}")


_global_executor: Optional[ActionExecutor] = None


def get_action_executor() -> ActionExecutor:
    global _global_executor
    if _global_executor is None:
        _global_executor = ActionExecutor()
    return _global_executor


# ── Synchronous Tool Handlers for Tool Registry / Planner Loop ────────────────

def tool_click_element(parameters: dict, **kwargs) -> str:
    executor = get_action_executor()
    idx = int(parameters.get("index", 0))
    try:
        return executor.session_mgr.run_coroutine(executor.click_element(idx))
    except CDPConnectionError as ce:
        return str(ce)
    except Exception as e:
        return f"click_element failed: {e}"


def tool_input_text(parameters: dict, **kwargs) -> str:
    executor = get_action_executor()
    idx = int(parameters.get("index", 0))
    text = str(parameters.get("text", ""))
    press_enter = bool(parameters.get("press_enter", False))
    try:
        return executor.session_mgr.run_coroutine(executor.input_text(idx, text, press_enter=press_enter))
    except CDPConnectionError as ce:
        return str(ce)
    except Exception as e:
        return f"input_text failed: {e}"


def tool_scroll_page(parameters: dict, **kwargs) -> str:
    executor = get_action_executor()
    direction = parameters.get("direction", "down")
    amount = int(parameters.get("amount", 500))
    try:
        return executor.session_mgr.run_coroutine(executor.scroll_page(direction, amount=amount))
    except CDPConnectionError as ce:
        return str(ce)
    except Exception as e:
        return f"scroll_page failed: {e}"


def tool_scroll_element(parameters: dict, **kwargs) -> str:
    executor = get_action_executor()
    idx = int(parameters.get("index", 0))
    direction = parameters.get("direction", "down")
    try:
        return executor.session_mgr.run_coroutine(executor.scroll_element(idx, direction))
    except CDPConnectionError as ce:
        return str(ce)
    except Exception as e:
        return f"scroll_element failed: {e}"


def tool_press_key(parameters: dict, **kwargs) -> str:
    executor = get_action_executor()
    key = str(parameters.get("key", "Enter"))
    try:
        return executor.session_mgr.run_coroutine(executor.press_key(key))
    except CDPConnectionError as ce:
        return str(ce)
    except Exception as e:
        return f"press_key failed: {e}"


def tool_navigate_to(parameters: dict, **kwargs) -> str:
    executor = get_action_executor()
    url = str(parameters.get("url", ""))
    try:
        return executor.session_mgr.run_coroutine(executor.navigate_to(url))
    except CDPConnectionError as ce:
        return str(ce)
    except Exception as e:
        return f"navigate_to failed: {e}"


def tool_switch_tab(parameters: dict, **kwargs) -> str:
    executor = get_action_executor()
    idx = int(parameters.get("tab_index", 0))
    try:
        return executor.session_mgr.run_coroutine(executor.switch_tab(idx))
    except CDPConnectionError as ce:
        return str(ce)
    except Exception as e:
        return f"switch_tab failed: {e}"


def tool_go_back(parameters: dict = None, **kwargs) -> str:
    executor = get_action_executor()
    try:
        return executor.session_mgr.run_coroutine(executor.go_back())
    except CDPConnectionError as ce:
        return str(ce)
    except Exception as e:
        return f"go_back failed: {e}"


def tool_wait(parameters: dict = None, **kwargs) -> str:
    executor = get_action_executor()
    sec = float((parameters or {}).get("seconds", 2.0))
    try:
        return executor.session_mgr.run_coroutine(executor.wait_page(sec))
    except CDPConnectionError as ce:
        return str(ce)
    except Exception as e:
        return f"wait failed: {e}"


def tool_observe_page(parameters: dict = None, **kwargs) -> str:
    executor = get_action_executor()
    enable_som = bool((parameters or {}).get("enable_som", False))
    try:
        return executor.session_mgr.run_coroutine(executor.observe_page(enable_som=enable_som))
    except CDPConnectionError as ce:
        return str(ce)
    except Exception as e:
        return f"observe_page failed: {e}"


# ── Atomic Tool Declarations for ActionRegistry ──────────────────────────────

TOOLS = [
    {
        "name": "observe_page",
        "description": "Perception tool: Inspects the live Microsoft Edge session via CDP. Returns list of open tabs and indexed interactive elements ([1], [2], [3]...) currently visible in the active tab viewport.",
        "permission": "read_only",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "enable_som": {
                    "type": "BOOLEAN",
                    "description": "Whether to inject Set-of-Mark numbered visual overlays on interactive elements."
                }
            },
            "required": []
        },
        "handler": tool_observe_page,
    },
    {
        "name": "click_element",
        "description": "Clicks an interactive element by its integer index [1..N] using trusted hardware CDP mouse events. Bypasses synthetic event restrictions.",
        "permission": "normal",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "index": {
                    "type": "INTEGER",
                    "description": "The [index] number of the element from the latest observation (e.g. 1, 2, 3)."
                }
            },
            "required": ["index"]
        },
        "handler": tool_click_element,
    },
    {
        "name": "input_text",
        "description": "Focuses an input field or contenteditable element by index, clears existing text, and types text via CDP text insertion. Triggers SPA listeners reliably.",
        "permission": "normal",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "index": {
                    "type": "INTEGER",
                    "description": "The [index] number of the input/textbox element to type into."
                },
                "text": {
                    "type": "STRING",
                    "description": "The text to insert into the element."
                },
                "press_enter": {
                    "type": "BOOLEAN",
                    "description": "Whether to press Enter immediately after typing."
                }
            },
            "required": ["index", "text"]
        },
        "handler": tool_input_text,
    },
    {
        "name": "scroll_page",
        "description": "Scrolls the active webpage up or down by a given amount of pixels using hardware mouse wheel events.",
        "permission": "normal",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "direction": {
                    "type": "STRING",
                    "description": "Scroll direction: 'up' or 'down' (default: down)."
                },
                "amount": {
                    "type": "INTEGER",
                    "description": "Scroll distance in pixels (default: 500)."
                }
            },
            "required": ["direction"]
        },
        "handler": tool_scroll_page,
    },
    {
        "name": "scroll_element",
        "description": "Scrolls an inner scrollable container (e.g. chat list or conversation panel in WhatsApp, Gmail, or ChatGPT) by element index.",
        "permission": "normal",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "index": {
                    "type": "INTEGER",
                    "description": "The [index] number of the scrollable container element."
                },
                "direction": {
                    "type": "STRING",
                    "description": "Scroll direction: 'up' or 'down' (default: down)."
                }
            },
            "required": ["index"]
        },
        "handler": tool_scroll_element,
    },
    {
        "name": "press_key",
        "description": "Sends a hardware key press (e.g. 'Escape', 'Enter', 'Tab', 'ArrowDown', 'Backspace') to the active browser window.",
        "permission": "normal",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "key": {
                    "type": "STRING",
                    "description": "Key name: 'Enter', 'Escape', 'Tab', 'ArrowDown', 'ArrowUp', 'Backspace', 'Space'."
                }
            },
            "required": ["key"]
        },
        "handler": tool_press_key,
    },
    {
        "name": "navigate_to",
        "description": "Navigates the active Microsoft Edge tab to a new URL.",
        "permission": "normal",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "url": {
                    "type": "STRING",
                    "description": "The destination URL (e.g. 'https://mail.google.com', 'https://web.whatsapp.com')."
                }
            },
            "required": ["url"]
        },
        "handler": tool_navigate_to,
    },
    {
        "name": "switch_tab",
        "description": "Switches the active tab in Microsoft Edge to the given tab index [0..N].",
        "permission": "normal",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "tab_index": {
                    "type": "INTEGER",
                    "description": "Tab index from the open tabs list."
                }
            },
            "required": ["tab_index"]
        },
        "handler": tool_switch_tab,
    },
    {
        "name": "go_back",
        "description": "Navigates back to the previous page in history in the active tab.",
        "permission": "normal",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        },
        "handler": tool_go_back,
    },
    {
        "name": "wait_page",
        "description": "Pauses execution for a specified number of seconds then returns a fresh page observation.",
        "permission": "normal",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "seconds": {
                    "type": "NUMBER",
                    "description": "Duration to wait in seconds (default: 2.0)."
                }
            },
            "required": []
        },
        "handler": tool_wait,
    },
]
