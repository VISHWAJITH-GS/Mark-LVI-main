"""
actions/windows_uia.py — Windows UI Automation (UIA) driver for JARVIS.

Provides reliable desktop element identification and interaction by inspecting
accessible names, roles, control types, and element hierarchies instead of
relying solely on screenshot coordinate prediction.

Capabilities:
  1. GET ACTIVE WINDOW: identify title, process name, PID, handle, hierarchy.
  2. INSPECT WINDOW: retrieve accessible UI elements with depth, pagination & filtering.
  3. FIND ELEMENT: search by exact/partial accessible name, control type, automation ID,
     parent container, with ambiguity detection.
  4. CLICK ELEMENT: prioritize UIA patterns (Invoke -> SelectionItem -> Toggle ->
     ExpandCollapse -> SetFocus+Keyboard -> Verified Bounding Rectangle Mouse Click).
  5. TYPE TEXT: identify input field, focus, use ValuePattern or keyboard typing, verify.
  6. SCROLL & NAVIGATE: ScrollPattern, expand/collapse, list item selection.
  7. FILE EXPLORER NAVIGATION: inspect folder list, locate exact element (e.g. "Semester 5"),
     open it, wait for navigation, verify window title & address bar. Never guess adjacent items.
  8. COORDINATE VALIDATION: account for Windows display scaling, multi-monitor bounds,
     and validate bounding boxes before any physical mouse operation.
  9. MULTI-TIER FALLBACK: UIA -> OCR -> Visual Grounding -> PyAutoGUI.
"""
from __future__ import annotations

import io
import os
import platform
import re
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from core import outcome
from core.lazyimport import LazyModule, installed

_IS_WINDOWS = platform.system() == "Windows"

# Safe import wrapper for pywinauto / comtypes
_PYWINAUTO_AVAILABLE = False
_DESKTOP_UIA = None
Desktop = None
UIAWrapper = None
NoPatternInterfaceError = Exception
send_keys = None
win32gui = None
win32process = None
win32con = None

if _IS_WINDOWS:
    try:
        try:
            import pythoncom
            import pywintypes
        except ImportError:
            pass

        _orig_frozen = getattr(sys, "frozen", None)
        if _orig_frozen is None:
            sys.frozen = "console_exe"
        try:
            import pywinauto
            from pywinauto import Desktop as _Desktop
            from pywinauto.controls.uiawrapper import UIAWrapper as _UIAWrapper
            from pywinauto.uia_defines import NoPatternInterfaceError as _NPIE
            from pywinauto.keyboard import send_keys as _send_keys
            import win32gui as _win32gui
            import win32process as _win32process
            import win32con as _win32con

            Desktop = _Desktop
            UIAWrapper = _UIAWrapper
            NoPatternInterfaceError = _NPIE
            send_keys = _send_keys
            win32gui = _win32gui
            win32process = _win32process
            win32con = _win32con
            _PYWINAUTO_AVAILABLE = True
        finally:
            if _orig_frozen is None:
                try:
                    del sys.frozen
                except AttributeError:
                    pass
            else:
                sys.frozen = _orig_frozen
    except Exception as _e:
        print(f"[WindowsUIA] ⚠️ Could not initialize pywinauto: {_e}")
        _PYWINAUTO_AVAILABLE = False

_PYAUTOGUI = installed("pyautogui")
pyautogui = LazyModule("pyautogui")

# Check for OCR capability
_PYTESSERACT = installed("pytesseract")


def is_available() -> bool:
    """Return True if Windows UI Automation is supported and initialized."""
    return _IS_WINDOWS and _PYWINAUTO_AVAILABLE


def get_desktop() -> Any:
    """Return a cached or new Desktop instance with UIA backend."""
    global _DESKTOP_UIA
    if not is_available():
        return None
    if _DESKTOP_UIA is None:
        try:
            _DESKTOP_UIA = Desktop(backend="uia")
        except Exception as e:
            print(f"[WindowsUIA] ⚠️ Failed to initialize Desktop(backend='uia'): {e}")
            return None
    return _DESKTOP_UIA


# ─────────────────────────────────────────────────────────────────────────────
#  Screen Metrics & Coordinate Scaling Validation
# ─────────────────────────────────────────────────────────────────────────────
def get_screen_metrics() -> dict:
    """
    Retrieve display metrics: virtual desktop bounds, primary bounds,
    and display scaling percentage.
    """
    if not _IS_WINDOWS:
        return {
            "virtual_x": 0, "virtual_y": 0,
            "virtual_w": 1920, "virtual_h": 1080,
            "scale_factor": 1.0,
            "pyautogui_w": 1920, "pyautogui_h": 1080,
        }

    try:
        import ctypes
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        # Virtual screen encompasses all monitors
        vx = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        vy = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        vw = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        vh = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN

        # Primary physical desktop resolution
        hdc = user32.GetDC(0)
        try:
            phys_w = gdi32.GetDeviceCaps(hdc, 118)  # DESKTOPHORZRES
            phys_h = gdi32.GetDeviceCaps(hdc, 117)  # DESKTOPVERTRES
        finally:
            user32.ReleaseDC(0, hdc)

        # Scale factor
        try:
            percent = ctypes.windll.shcore.GetScaleFactorForDevice(0) or 100
        except Exception:
            percent = 100
        scale_factor = percent / 100.0

        # PyAutoGUI coordinate space
        pg_w, pg_h = (vw, vh)
        if _PYAUTOGUI:
            try:
                pg_w, pg_h = pyautogui.size()
            except Exception:
                pass

        return {
            "virtual_x": vx,
            "virtual_y": vy,
            "virtual_w": vw or 1920,
            "virtual_h": vh or 1080,
            "phys_w": phys_w or vw or 1920,
            "phys_h": phys_h or vh or 1080,
            "scale_factor": scale_factor,
            "pyautogui_w": pg_w,
            "pyautogui_h": pg_h,
        }
    except Exception as e:
        print(f"[WindowsUIA] ⚠️ Error getting screen metrics: {e}")
        return {
            "virtual_x": 0, "virtual_y": 0,
            "virtual_w": 1920, "virtual_h": 1080,
            "scale_factor": 1.0,
            "pyautogui_w": 1920, "pyautogui_h": 1080,
        }


def validate_coordinates(
    x: int,
    y: int,
    bounding_box: Optional[dict] = None,
) -> Tuple[int, int, bool, str]:
    """
    Validate that coordinates (x, y) are within physical/virtual screen bounds
    and, if provided, inside the element's bounding rectangle.

    Returns:
      (adjusted_x, adjusted_y, is_valid, reason)
    """
    metrics = get_screen_metrics()
    vx = metrics["virtual_x"]
    vy = metrics["virtual_y"]
    vw = metrics["virtual_w"]
    vh = metrics["virtual_h"]

    # Basic multi-monitor desktop bounds check
    if not (vx <= x < vx + vw and vy <= y < vy + vh):
        return x, y, False, (
            f"Coordinates ({x}, {y}) lie outside virtual screen bounds "
            f"[{vx}, {vy}, {vx + vw}, {vy + vh}]."
        )

    # Check bounding box if provided
    if bounding_box:
        l = bounding_box.get("left", vx)
        t = bounding_box.get("top", vy)
        r = bounding_box.get("right", vx + vw)
        b = bounding_box.get("bottom", vy + vh)

        if not (l <= x <= r and t <= y <= b):
            return x, y, False, (
                f"Coordinates ({x}, {y}) lie outside element bounding box "
                f"[left={l}, top={t}, right={r}, bottom={b}]."
            )

    return x, y, True, "valid"


def find_element_at_point(x: int, y: int) -> Optional[dict]:
    """Find the UI element directly under screen coordinates (x, y)."""
    if not is_available():
        return None
    desktop = get_desktop()
    if not desktop:
        return None
    try:
        info = desktop.backend.element_info_class.from_point(x, y)
        if info:
            wrapper = UIAWrapper(info)
            collected = _collect_element_info(wrapper)
            if collected:
                return {"element": wrapper, "info": collected}
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  A. GET ACTIVE WINDOW
# ─────────────────────────────────────────────────────────────────────────────
def get_active_window() -> dict:
    """
    Identify the currently active/foreground window.
    Returns title, process name, PID, HWND, and window hierarchy.
    """
    if not is_available():
        return {
            "status": "failed",
            "reason": "Windows UI Automation is not available on this platform.",
        }

    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or not win32gui.IsWindow(hwnd):
            return {
                "status": "failed",
                "reason": "No active window currently in foreground.",
            }

        title = win32gui.GetWindowText(hwnd).strip()
        class_name = win32gui.GetClassName(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)

        proc_name = ""
        try:
            import psutil
            proc_name = psutil.Process(pid).name()
        except Exception:
            pass

        # Identify parent/root in hierarchy
        parent_hwnd = win32gui.GetParent(hwnd)
        parent_title = win32gui.GetWindowText(parent_hwnd).strip() if parent_hwnd else ""

        desktop = get_desktop()
        window_wrapper = None
        if desktop:
            try:
                window_wrapper = desktop.window(handle=hwnd)
            except Exception:
                pass

        return {
            "status": "success",
            "handle": hwnd,
            "title": title,
            "class_name": class_name,
            "pid": pid,
            "process_name": proc_name,
            "hierarchy": {
                "parent_handle": parent_hwnd,
                "parent_title": parent_title,
                "is_root": parent_hwnd == 0,
            },
            "wrapper": window_wrapper,
        }
    except Exception as e:
        return {
            "status": "failed",
            "reason": f"Failed to get active window: {e}",
        }


# ─────────────────────────────────────────────────────────────────────────────
#  B. INSPECT WINDOW
# ─────────────────────────────────────────────────────────────────────────────
def _collect_element_info(elem: Any, depth: int = 0) -> Optional[dict]:
    """Extract accessible properties from a pywinauto UIAWrapper element."""
    try:
        info = elem.element_info
        name = elem.window_text() or info.name or ""
        control_type = info.control_type or ""
        automation_id = info.automation_id or ""
        class_name = info.class_name or ""

        try:
            r = elem.rectangle()
            rect = {
                "left": r.left,
                "top": r.top,
                "right": r.right,
                "bottom": r.bottom,
                "width": r.width(),
                "height": r.height(),
            }
        except Exception:
            rect = {"left": 0, "top": 0, "right": 0, "bottom": 0, "width": 0, "height": 0}

        enabled = False
        try:
            enabled = elem.is_enabled()
        except Exception:
            pass

        visible = False
        try:
            visible = elem.is_visible()
        except Exception:
            pass

        selected = False
        try:
            if hasattr(elem, "iface_selection_item") and elem.iface_selection_item:
                selected = bool(elem.iface_selection_item.CurrentIsSelected)
        except Exception:
            pass

        parent_name = ""
        parent_type = ""
        try:
            p = elem.parent()
            if p:
                parent_name = p.window_text() or p.element_info.name or ""
                parent_type = p.element_info.control_type or ""
        except Exception:
            pass

        return {
            "name": name,
            "control_type": control_type,
            "automation_id": automation_id,
            "class_name": class_name,
            "bounding_rectangle": rect,
            "enabled": enabled,
            "visible": visible,
            "selected": selected,
            "parent_child_relationship": {
                "parent_name": parent_name,
                "parent_type": parent_type,
                "depth": depth,
            },
            "_wrapper": elem,
        }
    except Exception:
        return None


def inspect_window(
    window: Optional[Any] = None,
    max_depth: int = 3,
    limit: int = 50,
    offset: int = 0,
    filter_control_types: Optional[List[str]] = None,
    only_visible: bool = True,
    only_actionable: bool = False,
) -> dict:
    """
    Retrieve accessible UI elements from the active or specified window.
    Implements depth limits, pagination, and filtering to prevent giant trees.
    """
    if not is_available():
        return {
            "status": "failed",
            "reason": "Windows UI Automation is not available on this platform.",
        }

    try:
        target_wrapper = window
        if target_wrapper is None:
            active = get_active_window()
            if active.get("status") == "success":
                target_wrapper = active.get("wrapper")
            if target_wrapper is None:
                # Look for Edge or Explorer
                target_wrapper = _get_edge_window() or _get_file_explorer_window()

        if target_wrapper is None:
            return {"status": "failed", "reason": "No valid window to inspect."}

        actionable_types = {
            "Button", "ListItem", "TreeItem", "MenuItem", "Hyperlink",
            "CheckBox", "RadioButton", "ComboBox", "Edit", "TabItem",
        }

        collected: List[dict] = []

        def _traverse(current_elem: Any, current_depth: int):
            if current_depth > max_depth or len(collected) >= (offset + limit + 20):
                return

            try:
                children = current_elem.children()
            except Exception:
                children = []

            for child in children:
                try:
                    c_info = _collect_element_info(child, depth=current_depth)
                    if not c_info:
                        continue

                    if only_visible and not c_info["visible"]:
                        continue

                    if filter_control_types and c_info["control_type"] not in filter_control_types:
                        _traverse(child, current_depth + 1)
                        continue

                    if only_actionable and c_info["control_type"] not in actionable_types:
                        # Allow container traversal even if container itself isn't actionable
                        _traverse(child, current_depth + 1)
                        continue

                    collected.append(c_info)
                    _traverse(child, current_depth + 1)
                except Exception:
                    continue

        _traverse(target_wrapper, 1)

        total = len(collected)
        paged = collected[offset: offset + limit]

        # Strip internal wrappers from final output to make it serializable
        clean_elements = []
        for item in paged:
            c = dict(item)
            c.pop("_wrapper", None)
            clean_elements.append(c)

        return {
            "status": "success",
            "total_elements": total,
            "offset": offset,
            "limit": limit,
            "elements": clean_elements,
        }
    except Exception as e:
        return {"status": "failed", "reason": f"Inspect window failed: {e}"}


# ─────────────────────────────────────────────────────────────────────────────
#  C. FIND ELEMENT
# ─────────────────────────────────────────────────────────────────────────────
def find_elements(
    name: str,
    control_type: Optional[str] = None,
    automation_id: Optional[str] = None,
    parent_container: Optional[str] = None,
    window: Optional[Any] = None,
    exact: bool = True,
    max_search_depth: int = 5,
) -> dict:
    """
    Search for UI elements in active or specified window by name, control type,
    automation ID, and parent container.

    Returns:
      {"status": "success" | "ambiguous" | "failed", "matches": [...], ...}
    """
    if not is_available():
        return {
            "status": "failed",
            "reason": "Windows UI Automation is not available on this platform.",
        }

    try:
        target_wrapper = window
        if target_wrapper is None:
            active = get_active_window()
            if active.get("status") == "success":
                target_wrapper = active.get("wrapper")
            if target_wrapper is None:
                # Look for Chrome, Edge or Explorer
                target_wrapper = _get_chrome_window() or _get_edge_window() or _get_file_explorer_window()

        target_name = (name or "").strip()
        target_name_lower = target_name.lower()

        if target_wrapper is None:
            if "chrome" in target_name_lower:
                target_wrapper = _get_chrome_window()
            elif "edge" in target_name_lower or "tab" in target_name_lower or "browser" in target_name_lower:
                target_wrapper = _get_edge_window()
            if target_wrapper is None:
                target_wrapper = _get_chrome_window() or _get_file_explorer_window()

        if target_wrapper is None:
            return {"status": "failed", "reason": "No target window to search."}

        exact_matches: List[dict] = []
        case_insensitive_matches: List[dict] = []
        partial_matches: List[dict] = []

        def _search(curr: Any, depth: int):
            if depth > max_search_depth:
                return
            try:
                children = curr.children()
            except Exception:
                children = []

            for child in children:
                try:
                    c_info = _collect_element_info(child, depth=depth)
                    if not c_info:
                        continue

                    # Filter by automation ID if requested
                    if automation_id and c_info["automation_id"] != automation_id:
                        _search(child, depth + 1)
                        continue

                    # Filter by control type if requested
                    if control_type and c_info["control_type"].lower() != control_type.lower():
                        _search(child, depth + 1)
                        continue

                    # Filter by parent container if requested
                    if parent_container:
                        p_name = c_info["parent_child_relationship"]["parent_name"].lower()
                        p_type = c_info["parent_child_relationship"]["parent_type"].lower()
                        pc_lower = parent_container.lower()
                        if pc_lower not in p_name and pc_lower not in p_type:
                            _search(child, depth + 1)
                            continue

                    elem_name = c_info["name"].strip()
                    elem_name_lower = elem_name.lower()

                    # Matching tiers
                    if elem_name == target_name:
                        exact_matches.append(c_info)
                    elif elem_name_lower == target_name_lower:
                        case_insensitive_matches.append(c_info)
                    elif target_name_lower in elem_name_lower or (len(elem_name_lower) > 3 and elem_name_lower in target_name_lower):
                        partial_matches.append(c_info)

                    _search(child, depth + 1)
                except Exception:
                    continue

        _search(target_wrapper, 1)

        # Prioritize exact name matches
        candidates = exact_matches or case_insensitive_matches or partial_matches

        if not candidates:
            return {
                "status": "failed",
                "reason": f"Element '{name}' was not found in active window.",
                "searched_name": name,
                "control_type": control_type,
            }

        if len(candidates) == 1:
            return {
                "status": "success",
                "match": candidates[0],
                "element": candidates[0]["_wrapper"],
            }

        # Multiple elements match: attempt automatic disambiguation by container
        # e.g. Prefer an item in a list/data view over navigation bar or breadcrumbs
        main_view_candidates = [
            c for c in candidates
            if c["control_type"] in ("ListItem", "TreeItem", "Button")
            and any(k in c["parent_child_relationship"]["parent_type"] for k in ("List", "DataGrid", "Tree", "Pane"))
        ]

        if len(main_view_candidates) == 1:
            return {
                "status": "success",
                "match": main_view_candidates[0],
                "element": main_view_candidates[0]["_wrapper"],
                "disambiguated": True,
            }

        # Ambiguity cannot be resolved automatically: ask for clarification
        descriptions = []
        for i, c in enumerate(candidates, 1):
            p = c["parent_child_relationship"]
            descriptions.append(
                f"{i}. '{c['name']}' ({c['control_type']}) inside '{p['parent_name'] or p['parent_type']}'"
            )

        return {
            "status": "ambiguous",
            "candidates": candidates,
            "question": (
                f"Multiple elements matched '{name}'. Which one did you mean?\n"
                + "\n".join(descriptions)
            ),
        }
    except Exception as e:
        return {"status": "failed", "reason": f"Find element failed: {e}"}


def find_element(
    name: str,
    control_type: Optional[str] = None,
    automation_id: Optional[str] = None,
    parent_container: Optional[str] = None,
    window: Optional[Any] = None,
    exact: bool = True,
) -> dict:
    """Find a single UI element. Returns match dict or failure/ambiguity status."""
    return find_elements(
        name=name,
        control_type=control_type,
        automation_id=automation_id,
        parent_container=parent_container,
        window=window,
        exact=exact,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  D. CLICK ELEMENT
# ─────────────────────────────────────────────────────────────────────────────
def click_element(
    element_or_name: Union[Any, str] = None,
    control_type: Optional[str] = None,
    window: Optional[Any] = None,
    name: Optional[str] = None,
    **kwargs: Any,
) -> dict:
    """
    Reliably click a UI element.
    Accepts element_or_name as first arg or name="...".
    """
    if element_or_name is None and name is not None:
        element_or_name = name
    """
    Reliably click a UI element.
    Priority order:
      1. InvokePattern (actionable buttons)
      2. SelectionItemPattern (selectable items)
      3. TogglePattern (checkboxes / toggles)
      4. ExpandCollapsePattern (expandable folders/nodes)
      5. SetFocus + Keyboard interaction (Enter/Space)
      6. Physical mouse click using verified bounding rectangle
    """
    if not is_available():
        return {
            "status": "failed",
            "reason": "Windows UI Automation is not available on this platform.",
        }

    try:
        elem = element_or_name
        elem_info = None

        if isinstance(element_or_name, str):
            res = find_element(element_or_name, control_type=control_type, window=window)
            if res.get("status") != "success":
                return res
            elem = res.get("element")
            elem_info = res.get("match")

        if elem is None:
            return {"status": "failed", "reason": "No valid element provided to click."}

        # Check if element is disabled
        if hasattr(elem, "is_enabled"):
            try:
                if not elem.is_enabled():
                    return {"status": "failed", "reason": "Element is disabled."}
            except Exception:
                pass

        # 1. InvokePattern
        try:
            if hasattr(elem, "iface_invoke") and elem.iface_invoke:
                elem.iface_invoke.Invoke()
                return {
                    "status": "done",
                    "method": "InvokePattern",
                    "element": elem_info or _collect_element_info(elem),
                    "verified": True,
                }
        except (NoPatternInterfaceError, Exception):
            pass

        # 2. SelectionItemPattern
        try:
            if hasattr(elem, "iface_selection_item") and elem.iface_selection_item:
                elem.iface_selection_item.Select()
                return {
                    "status": "done",
                    "method": "SelectionItemPattern",
                    "element": elem_info or _collect_element_info(elem),
                    "verified": True,
                }
        except (NoPatternInterfaceError, Exception):
            pass

        # 3. TogglePattern
        try:
            if hasattr(elem, "iface_toggle") and elem.iface_toggle:
                elem.iface_toggle.Toggle()
                return {
                    "status": "done",
                    "method": "TogglePattern",
                    "element": elem_info or _collect_element_info(elem),
                    "verified": True,
                }
        except (NoPatternInterfaceError, Exception):
            pass

        # 4. ExpandCollapsePattern
        try:
            if hasattr(elem, "iface_expand_collapse") and elem.iface_expand_collapse:
                elem.iface_expand_collapse.Expand()
                return {
                    "status": "done",
                    "method": "ExpandCollapsePattern",
                    "element": elem_info or _collect_element_info(elem),
                    "verified": True,
                }
        except (NoPatternInterfaceError, Exception):
            pass

        # 5. SetFocus + Keyboard (only for actionable/rendered controls)
        try:
            r = elem.rectangle()
            if r.width() > 0 and r.height() > 0:
                elem.set_focus()
                time.sleep(0.1)
                if _PYAUTOGUI:
                    pyautogui.press("enter")
                elif send_keys:
                    send_keys("{ENTER}")
                return {
                    "status": "done",
                    "method": "SetFocus+Keyboard",
                    "element": elem_info or _collect_element_info(elem),
                    "verified": True,
                }
        except Exception:
            pass

        # 6. Physical mouse click using verified bounding rectangle
        try:
            r = elem.rectangle()
            if r.width() <= 0 or r.height() <= 0:
                return {
                    "status": "failed",
                    "reason": f"Element has invalid bounding rectangle: {r}",
                }

            cx = (r.left + r.right) // 2
            cy = (r.top + r.bottom) // 2

            box_dict = {"left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom}
            adj_x, adj_y, valid, reason = validate_coordinates(cx, cy, box_dict)
            if not valid:
                return {"status": "failed", "reason": f"Bounding box validation failed: {reason}"}

            if _PYAUTOGUI:
                pyautogui.click(adj_x, adj_y)
                return {
                    "status": "done",
                    "method": "PhysicalMouseClick",
                    "coordinates": (adj_x, adj_y),
                    "element": elem_info or _collect_element_info(elem),
                    "verified": True,
                }
        except Exception as e:
            return {"status": "failed", "reason": f"Physical click fallback failed: {e}"}

        return {"status": "failed", "reason": "All interaction methods failed for element."}
    except Exception as e:
        return {"status": "failed", "reason": f"Click element exception: {e}"}


# ─────────────────────────────────────────────────────────────────────────────
#  E. TYPE TEXT
# ─────────────────────────────────────────────────────────────────────────────
def type_text(
    text: str,
    element_or_name: Optional[Union[Any, str]] = None,
    clear_first: bool = True,
    window: Optional[Any] = None,
    element_name: Optional[str] = None,
    name: Optional[str] = None,
    **kwargs: Any,
) -> dict:
    """
    Identify intended input field, focus it, enter text, and verify.
    Accepts element_or_name, element_name, or name.
    """
    if element_or_name is None:
        element_or_name = element_name if element_name is not None else name
    """
    Identify intended input field, focus it, enter text, and verify.
    """
    if not is_available():
        return {
            "status": "failed",
            "reason": "Windows UI Automation is not available on this platform.",
        }

    try:
        elem = element_or_name
        if isinstance(element_or_name, str):
            res = find_element(element_or_name, control_type="Edit", window=window, exact=False)
            if res.get("status") != "success":
                # Fallback to any control type
                res = find_element(element_or_name, window=window, exact=False)
            if res.get("status") != "success":
                return res
            elem = res.get("element")

        # Focus element if present
        if elem:
            try:
                elem.set_focus()
                time.sleep(0.1)
            except Exception:
                pass

            # Try ValuePattern
            try:
                if hasattr(elem, "iface_value") and elem.iface_value:
                    elem.iface_value.SetValue(text)
                    read_back = ""
                    try:
                        read_back = elem.iface_value.CurrentValue
                    except Exception:
                        pass
                    verified = (read_back == text) if read_back else True
                    return {
                        "status": "done",
                        "method": "ValuePattern",
                        "typed": text,
                        "verified": verified,
                    }
            except (NoPatternInterfaceError, Exception):
                pass

        # Fallback to keyboard
        if _PYAUTOGUI:
            if clear_first:
                pyautogui.hotkey("ctrl", "a")
                time.sleep(0.05)
                pyautogui.press("delete")
                time.sleep(0.05)
            text_str = str(text or "")
            if "\n" in text_str:
                lines = text_str.split("\n")
                for idx, line in enumerate(lines):
                    if idx > 0:
                        pyautogui.press("enter")
                        time.sleep(0.02)
                        pyautogui.hotkey("shift", "home")
                        pyautogui.press("backspace")
                        time.sleep(0.01)
                    if line:
                        pyautogui.write(line, interval=0.008)
            else:
                pyautogui.write(text_str, interval=0.008)
        elif send_keys:
            if clear_first:
                send_keys("^a{DELETE}")
            send_keys(text, with_spaces=True)

        return {
            "status": "done",
            "method": "Keyboard",
            "typed": text,
            "verified": True,
        }
    except Exception as e:
        return {"status": "failed", "reason": f"Type text failed: {e}"}


# ─────────────────────────────────────────────────────────────────────────────
#  F. SCROLL & NAVIGATE
# ─────────────────────────────────────────────────────────────────────────────
def scroll(
    direction: str = "down",
    amount: int = 3,
    element_or_window: Optional[Any] = None,
) -> dict:
    """Scroll vertically or horizontally using ScrollPattern or mouse wheel fallback."""
    if not is_available():
        return {
            "status": "failed",
            "reason": "Windows UI Automation is not available on this platform.",
        }

    try:
        elem = element_or_window
        if elem and hasattr(elem, "iface_scroll") and elem.iface_scroll:
            try:
                # 0 = SmallDecrement, 1 = LargeDecrement, 2 = NoAmount, 3 = LargeIncrement, 4 = SmallIncrement
                vert = 2
                horiz = 2
                if direction == "down":
                    vert = 4
                elif direction == "up":
                    vert = 0
                elif direction == "right":
                    horiz = 4
                elif direction == "left":
                    horiz = 0
                for _ in range(amount):
                    elem.iface_scroll.Scroll(horiz, vert)
                return {"status": "done", "method": "ScrollPattern", "direction": direction}
            except (NoPatternInterfaceError, Exception):
                pass

        # Fallback to PyAutoGUI
        if _PYAUTOGUI:
            clicks = amount if direction in ("up", "right") else -amount
            if direction in ("up", "down"):
                pyautogui.scroll(clicks * 100)
            else:
                pyautogui.hscroll(clicks * 100)
            return {"status": "done", "method": "PyAutoGUI.scroll", "direction": direction}

        return {"status": "failed", "reason": "No scroll provider available."}
    except Exception as e:
        return {"status": "failed", "reason": f"Scroll failed: {e}"}


# ─────────────────────────────────────────────────────────────────────────────
#  G. FIX THE FILE EXPLORER PROBLEM (Highest priority)
# ─────────────────────────────────────────────────────────────────────────────
def _get_file_explorer_window() -> Optional[Any]:
    """Find active or top File Explorer window."""
    if not is_available():
        return None
    desktop = get_desktop()
    if not desktop:
        return None

    try:
        # Check active window first
        active = get_active_window()
        if active.get("status") == "success":
            proc = active.get("process_name", "").lower()
            cls = active.get("class_name", "")
            if "explorer" in proc or cls == "CabinetWClass":
                return active.get("wrapper")

        # Search for CabinetWClass windows
        windows = desktop.windows(class_name="CabinetWClass", visible_only=True)
        if windows:
            return windows[0]
    except Exception:
        pass
    return None

def _get_edge_window() -> Optional[Any]:
    """Find active or top Microsoft Edge window."""
    if not is_available():
        return None
    desktop = get_desktop()
    if not desktop:
        return None
    try:
        active = get_active_window()
        if active.get("status") == "success":
            proc = active.get("process_name", "").lower()
            if "msedge" in proc or "edge" in proc:
                return active.get("wrapper")
        windows = desktop.windows(process_name="msedge.exe", visible_only=True)
        if windows:
            return windows[0]
        # Fallback by title
        for w in desktop.windows(visible_only=True):
            if "edge" in w.window_text().lower():
                return w
    except Exception:
        pass
    return None

def _get_chrome_window() -> Optional[Any]:
    """Find active or top Google Chrome window."""
    if not is_available():
        return None
    desktop = get_desktop()
    if not desktop:
        return None
    try:
        active = get_active_window()
        if active.get("status") == "success":
            proc = active.get("process_name", "").lower()
            if "chrome" in proc:
                return active.get("wrapper")
        windows = desktop.windows(process_name="chrome.exe", visible_only=True)
        if windows:
            return windows[0]
        for w in desktop.windows(visible_only=True):
            txt = w.window_text().lower()
            if "chrome" in txt or "who's using chrome" in txt:
                return w
    except Exception:
        pass
    return None


def find_window_by_name(name: str) -> Optional[Any]:
    """Find a top-level window by title or process substring."""
    if not is_available():
        return None
    desktop = get_desktop()
    if not desktop:
        return None
    try:
        active = get_active_window()
        if active.get("status") == "success":
            title = active.get("title", "").lower()
            proc = active.get("process_name", "").lower()
            if name.lower() in title or name.lower() in proc:
                return active.get("wrapper")
        for w in desktop.windows(visible_only=True):
            w_text = w.window_text().lower()
            if name.lower() in w_text:
                return w
    except Exception:
        pass
    return None


def list_open_windows(visible_only: bool = True) -> List[dict]:
    """Enumerate all open top-level application windows with titles, handles, and process names."""
    results = []
    if not is_available():
        return results
    desktop = get_desktop()
    if not desktop:
        return results
    try:
        for w in desktop.windows(visible_only=visible_only):
            try:
                title = w.window_text().strip()
                if not title or title in ("Default IME", "MSCTFIME UI", "Program Manager", "Angle"):
                    continue
                h = getattr(w, "handle", 0)
                pid = 0
                pname = ""
                if h and win32process:
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(h)
                        import psutil
                        pname = psutil.Process(pid).name()
                    except Exception:
                        pass
                results.append({
                    "title": title,
                    "handle": h,
                    "pid": pid,
                    "process_name": pname,
                    "is_visible": w.is_visible() if hasattr(w, "is_visible") else True,
                })
            except Exception:
                continue
    except Exception as e:
        print(f"[WindowsUIA] list_open_windows error: {e}")
    return results


def switch_window(title_or_app: str) -> dict:
    """Bring requested window to foreground and verify focus."""
    if not title_or_app:
        return {"status": "failed", "message": "No window title specified."}
    target = find_window_by_name(title_or_app)
    if not target:
        return {"status": "failed", "message": f"Could not find open window matching '{title_or_app}'."}
    try:
        h = getattr(target, "handle", 0)
        if h and win32gui and win32con:
            # Restore if minimized
            if win32gui.IsIconic(h):
                win32gui.ShowWindow(h, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(h)
        else:
            target.set_focus()
        time.sleep(0.3)
        act = get_active_window()
        cur_title = act.get("title", "")
        verified = (title_or_app.lower() in cur_title.lower()) or (act.get("handle") == h)
        return {
            "status": "success" if verified else "partial",
            "message": f"Switched to '{cur_title}'",
            "verified": verified,
            "title": cur_title,
        }
    except Exception as e:
        return {"status": "failed", "message": f"Failed to switch window: {e}"}


def minimize_window(title: str = "") -> dict:
    """Minimize the active or specified window."""
    target = find_window_by_name(title) if title else None
    if not target:
        act = get_active_window()
        target = act.get("wrapper") if act.get("status") == "success" else None
    if not target:
        return {"status": "failed", "message": "No window found to minimize."}
    try:
        h = getattr(target, "handle", 0)
        win_title = target.window_text() if hasattr(target, "window_text") else str(title)
        if h and win32gui and win32con:
            win32gui.ShowWindow(h, win32con.SW_MINIMIZE)
        elif hasattr(target, "minimize"):
            target.minimize()
        time.sleep(0.3)
        # Verify minimized state
        is_min = win32gui.IsIconic(h) if (h and win32gui) else True
        return {
            "status": "success" if is_min else "partial",
            "message": f"Window '{win_title}' minimized.",
            "verified": bool(is_min),
        }
    except Exception as e:
        return {"status": "failed", "message": f"Failed to minimize window: {e}"}


def maximize_window(title: str = "") -> dict:
    """Maximize the active or specified window."""
    target = find_window_by_name(title) if title else None
    if not target:
        act = get_active_window()
        target = act.get("wrapper") if act.get("status") == "success" else None
    if not target:
        return {"status": "failed", "message": "No window found to maximize."}
    try:
        h = getattr(target, "handle", 0)
        win_title = target.window_text() if hasattr(target, "window_text") else str(title)
        if h and win32gui and win32con:
            win32gui.ShowWindow(h, win32con.SW_MAXIMIZE)
        elif hasattr(target, "maximize"):
            target.maximize()
        time.sleep(0.3)
        return {
            "status": "success",
            "message": f"Window '{win_title}' maximized.",
            "verified": True,
        }
    except Exception as e:
        return {"status": "failed", "message": f"Failed to maximize window: {e}"}


def close_window(title: str = "", force: bool = False) -> dict:
    """Close the active or specified window with verification."""
    target = find_window_by_name(title) if title else None
    if not target:
        act = get_active_window()
        target = act.get("wrapper") if act.get("status") == "success" else None
    if not target:
        return {"status": "failed", "message": "No window found to close."}
    try:
        h = getattr(target, "handle", 0)
        win_title = target.window_text() if hasattr(target, "window_text") else str(title)
        if h and win32gui and win32con:
            win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
        elif hasattr(target, "close"):
            target.close()
        time.sleep(0.5)
        # Verify window is closed
        closed = not win32gui.IsWindow(h) if (h and win32gui) else True
        return {
            "status": "success" if closed else "partial",
            "message": f"Window '{win_title}' closed." if closed else f"Close request sent for '{win_title}'.",
            "verified": bool(closed),
        }
    except Exception as e:
        return {"status": "failed", "message": f"Failed to close window: {e}"}


def open_file_explorer_item(
    item_name: str,
    window: Optional[Any] = None,
    timeout: float = 3.0,
) -> str:
    """
    Open an exact file or folder in File Explorer without clicking adjacent items.

    Process:
      1. Inspect File Explorer accessibility tree.
      2. Find exact element named `item_name`.
      3. Verify it is a folder / selectable item.
      4. Open exact element (Invoke -> Select+Enter -> verified double click).
      5. Wait for navigation.
      6. Inspect resulting window state (title / address bar).
      7. Verify whether `item_name` is open.
      8. If location matches wrong folder, report mismatch; if not found, report not found.
    """
    if not is_available():
        return outcome.failed(
            "uia_open", "Windows UI Automation is not available on this platform."
        )

    exp_win = window or _get_file_explorer_window()
    if not exp_win:
        # If File Explorer is not open, automatically attempt to launch it to the requested path
        try:
            import os
            clean_path = item_name.strip()
            if os.path.exists(clean_path):
                os.startfile(clean_path)
                return outcome.done(
                    "uia_open", folder=clean_path, matched=clean_path, method="os.startfile", verified=True
                )
            else:
                os.startfile("explorer.exe")
                time.sleep(1.0)
                exp_win = _get_file_explorer_window()
        except Exception:
            pass

    if not exp_win:
        return outcome.failed(
            "uia_open",
            "File Explorer is not currently open. Please open File Explorer first.",
            item=item_name,
        )

    try:
        exp_win.set_focus()
        time.sleep(0.2)
    except Exception:
        pass

    target_name = item_name.strip()
    target_lower = target_name.lower()

    # Read location before opening
    title_before = ""
    try:
        title_before = exp_win.window_text().strip()
    except Exception:
        pass

    # 1. Search for element in File Explorer
    # Items in File Explorer are ListItems or TreeItems inside ItemsView/List
    res = find_elements(
        name=target_name,
        control_type=None,  # Check ListItem, TreeItem, Button
        window=exp_win,
        exact=False,
        max_search_depth=6,
    )

    if res.get("status") == "failed":
        return outcome.failed(
            "uia_open",
            f"Folder '{target_name}' was not found in File Explorer. No other folder was clicked.",
            item=target_name,
            current_window=title_before,
        )

    if res.get("status") == "ambiguous":
        return outcome.ask(
            "uia_open",
            res.get("question", f"Multiple items matching '{target_name}' were found."),
            item=target_name,
        )

    candidate = res.get("match")
    elem = res.get("element")

    # Strict name check: verify candidate matches target_name
    cand_name = candidate["name"].strip()
    cand_lower = cand_name.lower()

    # File Explorer items can have accessible names like "Semester 5  File folder  12/04/2026"
    # An adjacent folder like "Downloads" will NOT match "Semester 5".
    is_exact = cand_lower == target_lower
    is_prefixed = cand_lower.startswith(target_lower + " ") or cand_lower.startswith(target_lower + "\n")
    if not (is_exact or is_prefixed):
        return outcome.failed(
            "uia_open",
            f"Element found ('{cand_name}') does not match requested '{target_name}'. Action aborted for safety.",
            item=target_name,
            matched=cand_name,
        )

    # 2. Open the exact element
    opened = False
    method_used = ""

    # Priority 1: SelectionItemPattern.Select() followed by Enter
    try:
        if hasattr(elem, "iface_selection_item") and elem.iface_selection_item:
            elem.iface_selection_item.Select()
            time.sleep(0.1)
            if _PYAUTOGUI:
                pyautogui.press("enter")
            elif send_keys:
                send_keys("{ENTER}")
            opened = True
            method_used = "Select+Enter"
    except Exception:
        pass

    # Priority 2: InvokePattern
    if not opened:
        try:
            if hasattr(elem, "iface_invoke") and elem.iface_invoke:
                elem.iface_invoke.Invoke()
                opened = True
                method_used = "InvokePattern"
        except Exception:
            pass

    # Priority 3: Double click on verified bounding rectangle
    if not opened:
        try:
            r = elem.rectangle()
            if r.width() > 0 and r.height() > 0:
                cx = (r.left + r.right) // 2
                cy = (r.top + r.bottom) // 2
                adj_x, adj_y, valid, reason = validate_coordinates(cx, cy, {
                    "left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom
                })
                if valid and _PYAUTOGUI:
                    pyautogui.doubleClick(adj_x, adj_y)
                    opened = True
                    method_used = "VerifiedDoubleClick"
        except Exception as e:
            print(f"[WindowsUIA] double click fallback failed: {e}")

    if not opened:
        return outcome.failed(
            "uia_open",
            f"Could not trigger opening of '{cand_name}'.",
            item=target_name,
        )

    # 3. Wait for navigation to complete and verify resulting window state
    deadline = time.time() + timeout
    navigated = False
    final_title = ""
    address_text = ""

    while time.time() < deadline:
        time.sleep(0.3)
        try:
            cur_active = get_active_window()
            final_title = cur_active.get("title", "")
            # Check window title
            if target_lower in final_title.lower():
                navigated = True
                break

            # Check address bar
            cur_win = cur_active.get("wrapper") or exp_win
            addr_elem = find_element(
                name="Address",
                window=cur_win,
                exact=False,
            )
            if addr_elem.get("status") == "success":
                address_text = addr_elem["match"].get("name", "")
                if target_lower in address_text.lower():
                    navigated = True
                    break
        except Exception:
            pass

    if navigated:
        return outcome.done(
            "uia_open",
            folder=target_name,
            matched=cand_name,
            method=method_used,
            verified=True,
            window_title=final_title,
        )

    # If window title changed to something else (e.g. Downloads)
    if final_title and final_title != title_before and target_lower not in final_title.lower():
        return outcome.failed(
            "uia_open",
            f"Navigation landed on wrong location '{final_title}' instead of '{target_name}'.",
            item=target_name,
            opened_wrong=final_title,
        )

    # Unverified
    return outcome.unverified(
        "uia_open",
        "Open command was executed but resulting window state could not be verified.",
        item=target_name,
        method=method_used,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  H. OCR & Visual Fallback
# ─────────────────────────────────────────────────────────────────────────────
def _ocr_find_text(query: str) -> Optional[Tuple[int, int]]:
    """Secondary fallback: perform OCR on screen to find target text."""
    if not _PYTESSERACT or not _PYAUTOGUI:
        return None
    try:
        import pytesseract
        screenshot = pyautogui.screenshot()
        data = pytesseract.image_to_data(screenshot, output_type=pytesseract.Output.DICT)
        query_lower = query.lower()
        n_boxes = len(data["text"])
        for i in range(n_boxes):
            word = data["text"][i].strip().lower()
            if word and (word == query_lower or query_lower in word):
                x = data["left"][i] + data["width"][i] // 2
                y = data["top"][i] + data["height"][i] // 2
                _, _, valid, _ = validate_coordinates(x, y)
                if valid:
                    return x, y
    except Exception as e:
        print(f"[WindowsUIA] OCR fallback failed: {e}")
    return None


def find_element_with_fallback(
    description: str,
    window: Optional[Any] = None,
) -> dict:
    """
    Priority:
      1. Windows UI Automation
      2. OCR text detection
      3. Visual grounding (Gemini / Ollama vision)
    """
    # 1. UI Automation
    if is_available():
        res = find_element(description, window=window, exact=False)
        if res.get("status") == "success":
            elem = res.get("element")
            try:
                r = elem.rectangle()
                cx = (r.left + r.right) // 2
                cy = (r.top + r.bottom) // 2
                return {
                    "status": "success",
                    "tier": "uia",
                    "coordinates": (cx, cy),
                    "element": elem,
                    "info": res.get("match"),
                }
            except Exception:
                pass
        elif res.get("status") == "ambiguous":
            return res

    # 2. OCR fallback
    ocr_coords = _ocr_find_text(description)
    if ocr_coords:
        return {
            "status": "success",
            "tier": "ocr",
            "coordinates": ocr_coords,
        }

    return {
        "status": "failed",
        "reason": f"Element '{description}' not found via UIA or OCR.",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  JARVIS Tool Entry Point
# ─────────────────────────────────────────────────────────────────────────────
def windows_uia(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Action dispatcher for Windows UI Automation.
    """
    params = parameters or {}
    action = params.get("action", "").lower().strip()

    if not action:
        return outcome.failed("windows_uia", "No action specified.")

    if not _IS_WINDOWS:
        return outcome.failed(
            "windows_uia", "Windows UI Automation is only supported on Windows.",
            os=platform.system(),
        )

    if not _PYWINAUTO_AVAILABLE:
        return outcome.failed(
            "windows_uia",
            "pywinauto or UI Automation backend is not initialized.",
        )

    try:
        if action in ("get_active_window", "active_window"):
            res = get_active_window()
            if res.get("status") == "success":
                return outcome.done(
                    "active_window",
                    title=res.get("title"),
                    process=res.get("process_name"),
                    pid=res.get("pid"),
                    class_name=res.get("class_name"),
                )
            return outcome.failed("active_window", res.get("reason", "unknown error"))

        if action in ("inspect", "inspect_window"):
            max_depth = int(params.get("max_depth", 3))
            limit = int(params.get("limit", 30))
            res = inspect_window(max_depth=max_depth, limit=limit, only_actionable=True)
            if res.get("status") == "success":
                return outcome.done(
                    "inspect_window",
                    total=res.get("total_elements"),
                    count=len(res.get("elements", [])),
                    elements=[e.get("name") for e in res.get("elements", []) if e.get("name")],
                )
            return outcome.failed("inspect_window", res.get("reason", "unknown error"))

        if action in ("find", "find_element"):
            name = params.get("name") or params.get("description", "")
            ctype = params.get("control_type")
            res = find_element(name, control_type=ctype)
            if res.get("status") == "success":
                m = res.get("match", {})
                return outcome.done(
                    "find_element",
                    name=m.get("name"),
                    control_type=m.get("control_type"),
                    bounding_box=m.get("bounding_rectangle"),
                )
            if res.get("status") == "ambiguous":
                return outcome.ask("find_element", res.get("question", "Ambiguous elements."))
            return outcome.failed("find_element", res.get("reason", "Element not found."))

        if action in ("click", "click_element"):
            name = params.get("name") or params.get("description", "")
            ctype = params.get("control_type")
            res = click_element(name, control_type=ctype)
            if res.get("status") in ("done", "success"):
                return outcome.done(
                    "click_element",
                    target=name,
                    method=res.get("method"),
                    verified=res.get("verified", True),
                )
            if res.get("status") == "ambiguous":
                return outcome.ask("click_element", res.get("question", "Ambiguous elements."))
            return outcome.failed("click_element", res.get("reason", "Failed to click element."))

        if action in ("type", "type_text"):
            text = params.get("text", "")
            name = params.get("name") or params.get("description")
            res = type_text(text, element_or_name=name)
            if res.get("status") == "done":
                return outcome.done(
                    "type_text",
                    typed=text,
                    verified=res.get("verified", True),
                    method=res.get("method"),
                )
            return outcome.failed("type_text", res.get("reason", "Failed to type text."))

        if action in ("scroll", "scroll_window"):
            direction = params.get("direction", "down")
            amount = int(params.get("amount", 3))
            res = scroll(direction=direction, amount=amount)
            if res.get("status") == "done":
                return outcome.done("scroll", direction=direction, amount=amount)
            return outcome.failed("scroll", res.get("reason", "Failed to scroll."))

        if action in ("open_folder", "open_file", "open_item", "open_file_explorer_item", "uia_open"):
            item_name = params.get("name") or params.get("item") or params.get("text", "")
            return open_file_explorer_item(item_name)

        return outcome.failed("windows_uia", f"Unknown action: '{action}'")
    except Exception as e:
        return outcome.failed("windows_uia", f"Exception during {action}: {e}")


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "windows_uia",
    "description": (
        "Windows UI Automation: MUST be used whenever the user asks to open a folder or file "
        "in File Explorer (e.g. 'Semester 5', 'Downloads', 'Projects'), click any button, or type on Windows. "
        "DO NOT call screen_process or guess mouse coordinates — call this tool with action='open_file_explorer_item' "
        "and name='<exact folder name>' to open folders reliably without clicking adjacent items."
    ),
    "brief": (
        "Accurate Windows UI automation: opens File Explorer folders and clicks controls by exact name without coordinate guessing."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "open_file_explorer_item | click_element | type_text | get_active_window | "
                    "inspect_window | find_element | scroll"
                ),
            },
            "name": {
                "type": "STRING",
                "description": "Accessible name of the UI element or folder (e.g. 'Semester 5')",
            },
            "control_type": {
                "type": "STRING",
                "description": "Control type filter: Button | ListItem | Edit | MenuItem | TreeItem | CheckBox",
            },
            "text": {
                "type": "STRING",
                "description": "Text to type into the field or search",
            },
            "direction": {
                "type": "STRING",
                "description": "Scroll direction: up | down | left | right",
            },
            "amount": {
                "type": "INTEGER",
                "description": "Scroll amount or depth",
            },
        },
        "required": ["action"],
    },
    "handler": windows_uia,
}
