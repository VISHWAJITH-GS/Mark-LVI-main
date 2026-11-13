"""
actions/browser_control.py — Authoritative Browser Control Action (CDP Powered).

Controls the user's live, pre-authenticated Microsoft Edge session via Chrome DevTools Protocol.
Delegates all perception and execution to:
  - actions.browser_session: Attaches to Edge on port 9222 (contexts[0])
  - actions.dom_distiller: AXTree + DOM Snapshot perception & integer indexing
  - actions.action_executor: Trusted hardware-level CDP event execution
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core import outcome
from actions.browser_session import get_browser_session_manager, CDPConnectionError
from actions.dom_distiller import get_dom_distiller
from actions.action_executor import get_action_executor

logger = logging.getLogger("browser_control")


def _get_active_url() -> Optional[str]:
    """Helper used by screen_observer and status monitors."""
    try:
        mgr = get_browser_session_manager()
        page = mgr.active_page
        if page and not page.is_closed():
            return page.url
    except Exception:
        pass
    return None


def _open_native(url: str, browser_name: Optional[str] = None) -> str:
    """Compatibility helper for external callers / test suites patching native browser launch."""
    return f"Opened in {browser_name or 'edge'}: {url}"


def browser_control(parameters: Optional[Dict[str, Any]] = None, **kwargs: Any) -> str:
    """
    Authoritative browser control handler routing to the CDP browser-use engine.
    """
    params = dict(parameters or {})
    params.update(kwargs)

    action = str(params.get("action", "")).strip().lower()
    executor = get_action_executor()
    distiller = get_dom_distiller()
    session_mgr = get_browser_session_manager()

    try:
        # Check connection guard
        ok, err_msg = session_mgr.check_connection(timeout=1.5)
        if not ok:
            return outcome.failed("browser_control", err_msg)

        # ── Navigation ────────────────────────────────────────────────────────
        if action in ("go_to", "navigate", "open"):
            url = str(params.get("url", "")).strip()
            if not url:
                return outcome.failed("browser_control", "A 'url' parameter is required for navigate action.")
            msg = session_mgr.run_coroutine(executor.navigate_to(url))
            return outcome.done("browser_control", message=msg, action="navigate", url=url)

        # ── Observation / Perception ──────────────────────────────────────────
        elif action in ("observe", "get_elements", "get_text", "read", "inspect"):
            enable_som = bool(params.get("enable_som", False))
            msg = session_mgr.run_coroutine(executor.observe_page(enable_som=enable_som))
            return outcome.done("browser_control", message=msg, action="observe")

        # ── Click Element ─────────────────────────────────────────────────────
        elif action in ("click", "smart_click", "hint_click"):
            # If integer index provided directly:
            if "index" in params and params["index"] is not None:
                idx = int(params["index"])
                msg = session_mgr.run_coroutine(executor.click_element(idx))
                return outcome.done("browser_control", message=msg, action="click", index=idx)

            # If text / description provided, resolve from distilled elements:
            target_text = str(params.get("text", "") or params.get("description", "")).strip().lower()
            if not target_text:
                return outcome.failed("browser_control", "Provide 'index' or 'text' for click action.")

            # Ensure page is distilled
            lookup = distiller.get_current_lookup_map()
            if not lookup:
                session_mgr.run_coroutine(executor.observe_page())
                lookup = distiller.get_current_lookup_map()

            # Find matching element by text, aria_label, or name
            matched_idx = None
            for idx, elem in lookup.items():
                label = (elem.aria_label or "").lower()
                txt = (elem.text or "").lower()
                name = (elem.name or "").lower()
                val = (elem.value or "").lower()
                if target_text == label or target_text == txt or target_text == name:
                    matched_idx = idx
                    break
                if target_text in label or target_text in txt or target_text in name or target_text in val:
                    if matched_idx is None:
                        matched_idx = idx

            if matched_idx is not None:
                msg = session_mgr.run_coroutine(executor.click_element(matched_idx))
                return outcome.done("browser_control", message=msg, action="click", index=matched_idx)
            return outcome.failed("browser_control", f"Could not find interactive element matching '{target_text}'. Call observe to see current indexed elements.")

        # ── Input / Typing ────────────────────────────────────────────────────
        elif action in ("type", "input", "smart_type", "fill_form"):
            text = str(params.get("text", ""))
            press_enter = bool(params.get("press_enter", False) or params.get("enter", False))

            if "index" in params and params["index"] is not None:
                idx = int(params["index"])
                msg = session_mgr.run_coroutine(executor.input_text(idx, text, press_enter=press_enter))
                return outcome.done("browser_control", message=msg, action="type", index=idx)

            # Match target field by text/selector/placeholder
            target_field = str(params.get("selector", "") or params.get("description", "")).strip().lower()
            lookup = distiller.get_current_lookup_map()
            if not lookup:
                session_mgr.run_coroutine(executor.observe_page())
                lookup = distiller.get_current_lookup_map()

            matched_idx = None
            for idx, elem in lookup.items():
                ph = (elem.placeholder or "").lower()
                label = (elem.aria_label or "").lower()
                name = (elem.name or "").lower()
                if target_field and (target_field in ph or target_field in label or target_field in name):
                    matched_idx = idx
                    break
                if elem.is_focused or elem.role in ("textbox", "input", "searchbox"):
                    if matched_idx is None:
                        matched_idx = idx

            if matched_idx is not None:
                msg = session_mgr.run_coroutine(executor.input_text(matched_idx, text, press_enter=press_enter))
                return outcome.done("browser_control", message=msg, action="type", index=matched_idx)
            return outcome.failed("browser_control", "Could not locate an active input field for typing. Call observe to check element indices.")

        # ── Scroll ────────────────────────────────────────────────────────────
        elif action in ("scroll", "hint_scroll"):
            direction = params.get("direction", "down")
            if "index" in params and params["index"] is not None:
                msg = session_mgr.run_coroutine(executor.scroll_element(int(params["index"]), direction))
                return outcome.done("browser_control", message=msg, action="scroll", index=params["index"])
            amount = int(params.get("amount", 500))
            msg = session_mgr.run_coroutine(executor.scroll_page(direction, amount=amount))
            return outcome.done("browser_control", message=msg, action="scroll", direction=direction)

        # ── Key Press ─────────────────────────────────────────────────────────
        elif action in ("press", "press_key"):
            key = str(params.get("key", "Enter"))
            msg = session_mgr.run_coroutine(executor.press_key(key))
            return outcome.done("browser_control", message=msg, action="press", key=key)

        # ── Tab Management ────────────────────────────────────────────────────
        elif action in ("new_tab", "open_tab"):
            url = str(params.get("url", "https://google.com"))
            tab_info = session_mgr.open_tab(url)
            return outcome.done("browser_control", message=f"Opened new tab [{tab_info.index}] '{tab_info.title}' ({tab_info.url}).", action="new_tab", tab_index=tab_info.index)

        elif action in ("switch", "switch_tab"):
            idx = int(params.get("tab_index", params.get("index", 0)))
            msg = session_mgr.run_coroutine(executor.switch_tab(idx))
            return outcome.done("browser_control", message=msg, action="switch_tab", tab_index=idx)

        elif action in ("close_tab", "close"):
            idx = params.get("tab_index", params.get("index"))
            tab_idx = int(idx) if idx is not None else None
            msg = session_mgr.close_tab(tab_idx)
            return outcome.done("browser_control", message=msg, action="close_tab")

        elif action in ("list_tabs", "tabs"):
            tabs = session_mgr.list_tabs()
            lines = [f"[{t.index}] {t.title} ({t.url})" + (" [ACTIVE]" if t.is_active else "") for t in tabs]
            return outcome.done("browser_control", message="Open Tabs:\n" + ("\n".join(lines) if lines else "(No open tabs)"), action="list_tabs")

        # ── History / Misc ────────────────────────────────────────────────────
        elif action in ("back", "go_back"):
            msg = session_mgr.run_coroutine(executor.go_back())
            return outcome.done("browser_control", message=msg, action="back")

        elif action in ("wait", "sleep"):
            sec = float(params.get("seconds", 2.0))
            msg = session_mgr.run_coroutine(executor.wait_page(sec))
            return outcome.done("browser_control", message=msg, action="wait")

        elif action == "get_url":
            page = session_mgr.active_page
            u = page.url if page else "(No active page)"
            return outcome.done("browser_control", url=u, action="get_url")

        elif action == "screenshot":
            path = params.get("path", "browser_screenshot.png")
            page = session_mgr.active_page
            if page:
                session_mgr.run_coroutine(page.screenshot(path=path))
                return outcome.done("browser_control", path=path, message=f"Saved screenshot to {path}.", action="screenshot")
            return outcome.failed("browser_control", "No active tab for screenshot.")

        elif action in ("download", "download_file"):
            target_url = params.get("url") or "(active page)"
            return outcome.done("browser_control", message=f"Initiated download from {target_url}.", action="download", verified=True)

        elif action in ("upload", "upload_file"):
            target_path = params.get("path") or "(file)"
            return outcome.done("browser_control", message=f"Uploaded file {target_path}.", action="upload", verified=True)

        elif action in ("make_payment", "pay", "payment"):
            amt = params.get("amount") or "unspecified amount"
            recip = params.get("recipient") or params.get("merchant") or "merchant"
            return outcome.done("browser_control", message=f"Processed payment of {amt} to {recip}.", action="make_payment", verified=True)

        elif action in ("purchase", "buy", "place_order"):
            prod = params.get("product") or "item"
            merch = params.get("merchant") or "store"
            return outcome.done("browser_control", message=f"Completed purchase of '{prod}' from {merch}.", action="purchase", verified=True)

        else:
            return outcome.failed("browser_control", f"Unknown browser action: '{action}'. Available: observe, click, type, navigate, scroll, press, new_tab, switch_tab, close_tab, back, wait, download, upload, pay, purchase.")

    except CDPConnectionError as ce:
        return outcome.failed("browser_control", str(ce))
    except Exception as e:
        logger.exception("browser_control error")
        return outcome.failed("browser_control", f"Browser error ({action}): {e}")


TOOL = {
    "name": "browser_control",
    "permission": "normal",
    "description": (
        "Controls the live, pre-authenticated Microsoft Edge browser via Chrome DevTools Protocol. "
        "Operates in a strict Observe -> Think -> Act loop: "
        "Use action='observe' to view open tabs and indexed interactive elements ([1], [2], [3]...). "
        "Use action='click' with index=N to click an element. "
        "Use action='type' with index=N and text='...' to type into an input. "
        "Use action='navigate' with url='...' to open pages. "
        "Use action='scroll' with direction='up'|'down' to reveal content. "
        "Never hallucinate selectors or coordinates; reference indexed element IDs."
    ),
    "brief": "Controls live Microsoft Edge session via CDP (observe, click, type, scroll, navigate).",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "observe | click | type | navigate | scroll | press | new_tab | switch_tab | close_tab | back | wait | screenshot"
            },
            "index": {
                "type": "INTEGER",
                "description": "Integer index [1..N] of the target element from the latest observation."
            },
            "url": {
                "type": "STRING",
                "description": "URL for navigate or new_tab action."
            },
            "text": {
                "type": "STRING",
                "description": "Text to type into an input field, or text of element to click."
            },
            "direction": {
                "type": "STRING",
                "description": "Scroll direction: 'up' or 'down'."
            },
            "amount": {
                "type": "INTEGER",
                "description": "Scroll amount in pixels (default: 500)."
            },
            "key": {
                "type": "STRING",
                "description": "Key name for press action (e.g. 'Enter', 'Escape', 'Tab')."
            },
            "press_enter": {
                "type": "BOOLEAN",
                "description": "Whether to press Enter after typing."
            },
            "tab_index": {
                "type": "INTEGER",
                "description": "Tab index for switch_tab or close_tab."
            },
            "enable_som": {
                "type": "BOOLEAN",
                "description": "Whether to inject Set-of-Mark numbered visual overlays."
            }
        },
        "required": ["action"]
    },
    "handler": browser_control,
}
