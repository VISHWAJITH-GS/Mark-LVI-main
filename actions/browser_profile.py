"""
actions/browser_profile.py — Native Browser Profile Selector for Chrome and Edge.

Handles native desktop profile selection when Chrome or Edge opens a profile picker
("Who's using Chrome?", "Who's using Edge?") or requires switching profiles from the
top toolbar via Windows UI Automation.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from core import outcome

logger = logging.getLogger(__name__)


def detect_profile_chooser(browser: str = "") -> Optional[Any]:
    """
    Detect whether a native profile chooser window or dialog is open for Edge or Chrome.
    Returns the UI Automation window wrapper if found, else None.
    """
    try:
        from actions import windows_uia
        if not windows_uia.is_available():
            return None
        desktop = windows_uia.get_desktop()
        if not desktop:
            return None

        b_filter = (browser or "").lower().strip()

        # Check foreground / active window first
        active = windows_uia.get_active_window()
        if active.get("status") == "success":
            title = active.get("title", "").lower()
            proc = active.get("process_name", "").lower()
            if any(term in title for term in ("who's using", "profile", "profiles")):
                return active.get("wrapper")
            if b_filter and b_filter in proc:
                if any(term in title for term in ("who's using", "choose a profile", "select profile")):
                    return active.get("wrapper")

        # Scan top-level visible windows
        for w in desktop.windows(visible_only=True):
            try:
                title = w.window_text().lower()
                cname = w.class_name()
                if "who's using" in title or "choose a profile" in title:
                    return w
                if ("chrome" in title or "edge" in title or "msedge" in title) and "profile" in title:
                    return w
                # Chrome / Edge profile dialogs often use Chrome_WidgetWin_1
                if cname == "Chrome_WidgetWin_1" and any(term in title for term in ("who's using", "profile")):
                    return w
            except Exception:
                continue

    except Exception as e:
        logger.debug("Error detecting profile chooser: %s", e)
    return None


def select_browser_profile(
    profile_name: str,
    browser: str = "",
    x: Optional[int] = None,
    y: Optional[int] = None,
) -> str:
    """
    Select an existing Chrome or Edge profile by name.
    """
    if not profile_name:
        return outcome.failed("browser_profile", "A profile_name is required (e.g. 'Student', 'Default').")

    from actions import windows_uia
    clean_target = profile_name.strip()
    clean_lower = clean_target.lower()

    # 1. Check if a dedicated profile chooser dialog is open
    chooser_window = detect_profile_chooser(browser)
    target_window = chooser_window

    if target_window is None:
        # Fall back to Chrome or Edge window
        if "chrome" in (browser or "").lower():
            target_window = windows_uia._get_chrome_window()
        else:
            target_window = windows_uia._get_edge_window() or windows_uia._get_chrome_window()

    # 2. Try to find the profile element in the window
    if target_window:
        try:
            target_window.set_focus()
            time.sleep(0.3)
        except Exception:
            pass

        # Search within target_window
        res = windows_uia.find_element(clean_target, window=target_window)
        if res.get("status") == "success" and res.get("element"):
            elem = res.get("element")
            click_res = windows_uia.click_element(elem)
            if click_res.get("status") == "done" or click_res.get("verified"):
                time.sleep(1.0)
                return outcome.done(
                    "browser_profile",
                    message=f"Selected browser profile '{clean_target}' via UI Automation.",
                    profile=clean_target,
                    method="uia_direct",
                )

        # 3. If not found in chooser, check if we need to open the profile menu first
        try:
            for p_btn_name in ("Profile", "Profile account and more", "Profiles", "Personal", "Work", "Account"):
                p_elem_res = windows_uia.find_element(p_btn_name, control_type="Button", window=target_window)
                if p_elem_res.get("status") == "success" and p_elem_res.get("element"):
                    windows_uia.click_element(p_elem_res.get("element"))
                    time.sleep(0.6)
                    # Retry searching for profile name in opened menu
                    res_menu = windows_uia.find_element(clean_target)
                    if res_menu.get("status") == "success" and res_menu.get("element"):
                        windows_uia.click_element(res_menu.get("element"))
                        time.sleep(1.0)
                        return outcome.done(
                            "browser_profile",
                            message=f"Opened profile flyout and selected '{clean_target}'.",
                            profile=clean_target,
                            method="uia_flyout",
                        )
                    break
        except Exception as ex:
            logger.debug("Flyout profile click attempt error: %s", ex)

    # 4. Fallback to desktop-wide search for any element matching profile_name
    try:
        desktop_res = windows_uia.find_element(clean_target)
        if desktop_res.get("status") == "success" and desktop_res.get("element"):
            click_res = windows_uia.click_element(desktop_res.get("element"))
            if click_res.get("status") == "done" or click_res.get("verified"):
                time.sleep(1.0)
                return outcome.done(
                    "browser_profile",
                    message=f"Selected profile '{clean_target}' across desktop elements.",
                    profile=clean_target,
                    method="desktop_uia",
                )
    except Exception as ex:
        logger.debug("Desktop-wide search error: %s", ex)

    # 5. Coordinate fallback if coordinates provided
    if x is not None and y is not None:
        try:
            from actions import computer_control
            comp_res = computer_control.computer_control({"action": "click", "x": int(x), "y": int(y)})
            time.sleep(1.0)
            return outcome.done(
                "browser_profile",
                message=f"Selected profile '{clean_target}' via demonstrated coordinates ({x}, {y}).",
                profile=clean_target,
                coordinates=(x, y),
                method="coordinates",
            )
        except Exception as ex:
            logger.debug("Coordinate click fallback failed: %s", ex)

    return outcome.failed(
        "browser_profile",
        f"Could not locate or select browser profile '{clean_target}'. Ensure the browser profile chooser or browser is visible.",
    )


def browser_profile(parameters: Optional[dict] = None, **kwargs: Any) -> str:
    """Unified handler for native browser profile selection."""
    p = dict(parameters or {})
    p.update(kwargs)

    action = (p.get("action") or "select_profile").lower().strip()
    profile_name = p.get("profile_name") or p.get("profile") or p.get("name", "")
    browser = p.get("browser") or p.get("app", "")
    x = p.get("x")
    y = p.get("y")

    if action in ("select_profile", "switch_profile", "select", "choose"):
        return select_browser_profile(profile_name=profile_name, browser=browser, x=x, y=y)
    elif action in ("detect", "detect_chooser"):
        found = detect_profile_chooser(browser)
        if found:
            return outcome.done("browser_profile", message="Browser profile chooser is currently active.", active=True)
        return outcome.done("browser_profile", message="No active browser profile chooser detected.", active=False)
    else:
        return outcome.failed("browser_profile", f"Unknown action '{action}'. Supported: select_profile, detect.")


TOOL = {
    "name": "browser_profile",
    "description": (
        "Selects or switches to a native browser profile (e.g. 'Student', 'Official', 'Personal') "
        "in Microsoft Edge or Google Chrome. Handles the 'Who's using Chrome/Edge?' profile chooser "
        "or switches profiles via Windows UI Automation and demonstrated coordinates."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "The profile action: 'select_profile' or 'detect'. Default is 'select_profile'.",
                "enum": ["select_profile", "detect"],
            },
            "profile_name": {
                "type": "STRING",
                "description": "The exact or partial name of the profile to switch to (e.g. 'Student', 'student.tce.edu').",
            },
            "browser": {
                "type": "STRING",
                "description": "Target browser: 'edge' or 'chrome'.",
            },
            "x": {
                "type": "INTEGER",
                "description": "Optional X coordinate from demonstration for direct coordinate selection fallback.",
            },
            "y": {
                "type": "INTEGER",
                "description": "Optional Y coordinate from demonstration for direct coordinate selection fallback.",
            },
        },
        "required": ["profile_name"],
    },
    "handler": browser_profile,
}

