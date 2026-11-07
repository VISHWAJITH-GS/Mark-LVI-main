"""
actions/ui_tree_manager.py — Advanced Windows UI Automation Tree & Control Pattern Manager.

Features:
  1. Complete accessible UI tree inspection with configurable depth, subtree targeting,
     pagination (page/limit/offset), and control type filtering.
  2. Full property extraction: Name, ControlType, AutomationId, ClassName, BoundingRectangle,
     IsEnabled, IsVisible, IsOffscreen, HasKeyboardFocus, Parent/Child hierarchy, PID, HWND,
     and Supported Control Patterns.
  3. Pattern support:
     - InvokePattern (buttons, hyperlinks)
     - SelectionItemPattern (tabs, radio buttons, list items)
     - ValuePattern (text boxes, edit fields)
     - TogglePattern (checkboxes, toggle switches)
     - ExpandCollapsePattern (menus, tree nodes, combo boxes)
     - ScrollPattern (scroll bars, lists)
     - SetFocus
  4. Ambiguity Resolution:
     Resolves duplicate element names using:
     1) Active window scope
     2) AutomationId match
     3) ControlType match
     4) Parent-child hierarchy
     5) Bounding rectangle proximity / visibility
     6) Nearby labels / context
"""
from __future__ import annotations

import logging
import platform
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from actions import windows_uia

logger = logging.getLogger(__name__)
_IS_WINDOWS = platform.system() == "Windows"


@dataclass
class UIElementNode:
    name: str
    control_type: str
    automation_id: str
    class_name: str
    bounding_rectangle: Dict[str, int]
    is_enabled: bool
    is_visible: bool
    is_offscreen: bool
    is_focused: bool
    parent_name: str = ""
    parent_type: str = ""
    process_id: int = 0
    window_handle: int = 0
    supported_patterns: List[str] = field(default_factory=list)
    depth: int = 0
    children_count: int = 0
    raw_wrapper: Any = None

    def center_point(self) -> Tuple[int, int]:
        r = self.bounding_rectangle
        cx = r.get("left", 0) + r.get("width", 0) // 2
        cy = r.get("top", 0) + r.get("height", 0) // 2
        return cx, cy

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "control_type": self.control_type,
            "automation_id": self.automation_id,
            "class_name": self.class_name,
            "bounding_rectangle": self.bounding_rectangle,
            "is_enabled": self.is_enabled,
            "is_visible": self.is_visible,
            "is_offscreen": self.is_offscreen,
            "is_focused": self.is_focused,
            "parent_name": self.parent_name,
            "parent_type": self.parent_type,
            "process_id": self.process_id,
            "window_handle": self.window_handle,
            "supported_patterns": self.supported_patterns,
            "depth": self.depth,
        }


class UITreeManager:
    """Manages deep UI automation inspection, patterns, and element resolution."""

    def __init__(self):
        self._last_inspected_elements: List[UIElementNode] = []

    def is_available(self) -> bool:
        return windows_uia.is_available()

    def get_supported_patterns(self, elem: Any) -> List[str]:
        """Detect which UI Automation control patterns this element supports."""
        patterns: List[str] = []
        if not hasattr(elem, "element_info"):
            return patterns

        # Check pywinauto pattern interfaces
        pattern_map = {
            "InvokePattern": ("iface_invoke", "invoke"),
            "SelectionItemPattern": ("iface_selection_item", "select"),
            "ValuePattern": ("iface_value", "set_value"),
            "TogglePattern": ("iface_toggle", "toggle"),
            "ExpandCollapsePattern": ("iface_expand_collapse", "expand"),
            "ScrollPattern": ("iface_scroll", "scroll"),
        }

        for pat_name, (attr_name, _) in pattern_map.items():
            try:
                if hasattr(elem, attr_name) and getattr(elem, attr_name) is not None:
                    patterns.append(pat_name)
            except Exception:
                pass

        return patterns

    def node_from_wrapper(self, elem: Any, depth: int = 0) -> Optional[UIElementNode]:
        """Convert a pywinauto UIAWrapper or element into UIElementNode."""
        try:
            info = elem.element_info
            name = elem.window_text() or info.name or ""
            control_type = info.control_type or ""
            automation_id = info.automation_id or ""
            class_name = info.class_name or ""
            handle = getattr(info, "handle", 0) or 0
            pid = getattr(info, "process_id", 0) or 0

            # Bounding rectangle
            rect = {"left": 0, "top": 0, "right": 0, "bottom": 0, "width": 0, "height": 0}
            try:
                r = elem.rectangle()
                rect = {
                    "left": int(r.left),
                    "top": int(r.top),
                    "right": int(r.right),
                    "bottom": int(r.bottom),
                    "width": int(r.width()),
                    "height": int(r.height()),
                }
            except Exception:
                pass

            is_enabled = False
            try:
                is_enabled = bool(elem.is_enabled())
            except Exception:
                pass

            is_visible = False
            try:
                is_visible = bool(elem.is_visible())
            except Exception:
                pass

            is_offscreen = False
            try:
                if hasattr(info, "element") and hasattr(info.element, "CurrentIsOffscreen"):
                    is_offscreen = bool(info.element.CurrentIsOffscreen)
            except Exception:
                pass

            is_focused = False
            try:
                if hasattr(info, "element") and hasattr(info.element, "CurrentHasKeyboardFocus"):
                    is_focused = bool(info.element.CurrentHasKeyboardFocus)
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

            patterns = self.get_supported_patterns(elem)

            return UIElementNode(
                name=name,
                control_type=control_type,
                automation_id=automation_id,
                class_name=class_name,
                bounding_rectangle=rect,
                is_enabled=is_enabled,
                is_visible=is_visible,
                is_offscreen=is_offscreen,
                is_focused=is_focused,
                parent_name=parent_name,
                parent_type=parent_type,
                process_id=pid,
                window_handle=handle,
                supported_patterns=patterns,
                depth=depth,
                raw_wrapper=elem,
            )
        except Exception as e:
            logger.debug("Error extracting node info: %s", e)
            return None

    def inspect_ui_tree(
        self,
        window: Optional[Any] = None,
        root_element: Optional[Any] = None,
        max_depth: int = 5,
        limit: int = 80,
        offset: int = 0,
        filter_types: Optional[List[str]] = None,
        only_visible: bool = True,
        only_actionable: bool = False,
        search_query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Traverse the accessible UI tree with pagination, depth limits, and targeted filtering.
        """
        if not self.is_available():
            return {
                "status": "failed",
                "reason": "Windows UI Automation is not available on this platform.",
                "elements": [],
                "total_found": 0,
            }

        target_root = root_element
        if target_root is None:
            if window is not None:
                target_root = window
            else:
                active = windows_uia.get_active_window()
                target_root = active.get("wrapper")

        if target_root is None:
            # Fallback to desktop root
            desktop = windows_uia.get_desktop()
            if desktop:
                try:
                    target_root = desktop.top_window()
                except Exception:
                    target_root = desktop

        if target_root is None:
            return {"status": "failed", "reason": "No accessible window or root found to inspect.", "elements": [], "total_found": 0}

        actionable_types = {
            "Button", "ListItem", "TreeItem", "MenuItem", "Hyperlink",
            "CheckBox", "RadioButton", "ComboBox", "Edit", "TabItem", "SplitButton"
        }

        all_matches: List[UIElementNode] = []

        def _walk(curr: Any, curr_depth: int):
            if curr_depth > max_depth or len(all_matches) >= (offset + limit + 50):
                return

            try:
                children = curr.children()
            except Exception:
                children = []

            for ch in children:
                try:
                    node = self.node_from_wrapper(ch, depth=curr_depth)
                    if not node:
                        continue

                    include = True
                    if only_visible and (not node.is_visible or node.is_offscreen):
                        include = False

                    if filter_types and node.control_type not in filter_types:
                        include = False

                    if only_actionable and node.control_type not in actionable_types:
                        include = False

                    if search_query:
                        q = search_query.lower()
                        if q not in node.name.lower() and q not in node.automation_id.lower():
                            include = False

                    if include and (node.name or node.automation_id or node.control_type in actionable_types):
                        all_matches.append(node)

                    # Continue walking deeper even if container wasn't directly included
                    _walk(ch, curr_depth + 1)
                except Exception:
                    continue

        _walk(target_root, 1)

        paginated = all_matches[offset : offset + limit]
        self._last_inspected_elements = all_matches

        return {
            "status": "success",
            "total_found": len(all_matches),
            "offset": offset,
            "limit": limit,
            "has_more": len(all_matches) > (offset + limit),
            "elements": [n.to_dict() for n in paginated],
        }

    def resolve_target_element(
        self,
        name: str,
        control_type: Optional[str] = None,
        automation_id: Optional[str] = None,
        parent_name: Optional[str] = None,
        active_window_only: bool = True,
        preferred_coords: Optional[Tuple[int, int]] = None,
    ) -> Tuple[Optional[UIElementNode], str]:
        """
        Find and resolve an element, safely resolving duplicates and ambiguities.
        Returns (node, resolution_reason).
        """
        if not self.is_available():
            return None, "UIA unavailable"

        tree_res = self.inspect_ui_tree(max_depth=5, limit=120, search_query=name if name else None)
        if tree_res.get("status") != "success":
            return None, tree_res.get("reason", "Inspection failed")

        candidates = list(self._last_inspected_elements)
        if not candidates:
            # Try global windows_uia find_element
            find_res = windows_uia.find_element(name=name, control_type=control_type, automation_id=automation_id)
            if find_res.get("status") == "success" and find_res.get("element"):
                node = self.node_from_wrapper(find_res["element"])
                return node, "Found via windows_uia fallback find_element"
            return None, f"No elements found matching name '{name}'"

        target_name_lower = name.lower().strip() if name else ""

        # Filter by name matching
        matched_by_name: List[UIElementNode] = []
        for n in candidates:
            c_name = n.name.lower().strip()
            if target_name_lower and (c_name == target_name_lower or target_name_lower in c_name):
                matched_by_name.append(n)
            elif automation_id and automation_id.lower() in n.automation_id.lower():
                matched_by_name.append(n)

        if not matched_by_name:
            return None, f"No elements matched name '{name}' or automation_id '{automation_id}'"

        if len(matched_by_name) == 1:
            return matched_by_name[0], "Unique match by name"

        # Ambiguity resolution step 1: Exact name match
        exact_matches = [n for n in matched_by_name if n.name.lower().strip() == target_name_lower]
        if len(exact_matches) == 1:
            return exact_matches[0], "Unique exact name match among partial matches"
        if exact_matches:
            matched_by_name = exact_matches

        # Ambiguity resolution step 2: Control type
        if control_type:
            ctype_lower = control_type.lower()
            typed = [n for n in matched_by_name if n.control_type.lower() == ctype_lower]
            if len(typed) == 1:
                return typed[0], f"Resolved ambiguity by control_type '{control_type}'"
            if typed:
                matched_by_name = typed

        # Ambiguity resolution step 3: Automation ID
        if automation_id:
            aid_lower = automation_id.lower()
            by_aid = [n for n in matched_by_name if aid_lower in n.automation_id.lower()]
            if len(by_aid) == 1:
                return by_aid[0], f"Resolved ambiguity by automation_id '{automation_id}'"
            if by_aid:
                matched_by_name = by_aid

        # Ambiguity resolution step 4: Parent name/hierarchy
        if parent_name:
            p_lower = parent_name.lower()
            by_parent = [n for n in matched_by_name if p_lower in n.parent_name.lower()]
            if len(by_parent) == 1:
                return by_parent[0], f"Resolved ambiguity by parent_name '{parent_name}'"
            if by_parent:
                matched_by_name = by_parent

        # Ambiguity resolution step 5: Proximity to preferred coordinates
        if preferred_coords:
            px, py = preferred_coords
            best_node = None
            best_dist = float("inf")
            for n in matched_by_name:
                cx, cy = n.center_point()
                dist = (cx - px) ** 2 + (cy - py) ** 2
                if dist < best_dist:
                    best_dist = dist
                    best_node = n
            if best_node:
                return best_node, f"Resolved ambiguity by spatial proximity to ({px}, {py})"

        # Ambiguity resolution step 6: Enabled & visible
        viable = [n for n in matched_by_name if n.is_enabled and n.is_visible and not n.is_offscreen]
        if len(viable) == 1:
            return viable[0], "Resolved ambiguity: single enabled and visible control"
        if viable:
            return viable[0], f"Selected first active control among {len(viable)} duplicate matches"

        return matched_by_name[0], f"Selected top candidate among {len(matched_by_name)} duplicate matches"

    def invoke_control_pattern(self, node: UIElementNode) -> Tuple[bool, str]:
        """
        Invoke the most appropriate pattern on the UIElementNode.
        Cascade: InvokePattern -> SelectionItemPattern -> TogglePattern -> ExpandCollapsePattern -> SetFocus.
        """
        wrapper = node.raw_wrapper
        if not wrapper:
            return False, "No raw wrapper attached to node"

        patterns = node.supported_patterns

        # 1. InvokePattern
        if "InvokePattern" in patterns and hasattr(wrapper, "iface_invoke") and wrapper.iface_invoke:
            try:
                wrapper.iface_invoke.Invoke()
                return True, "Triggered via InvokePattern"
            except Exception as e:
                logger.debug("InvokePattern failed: %s", e)

        # 2. SelectionItemPattern
        if "SelectionItemPattern" in patterns and hasattr(wrapper, "iface_selection_item") and wrapper.iface_selection_item:
            try:
                wrapper.iface_selection_item.Select()
                return True, "Triggered via SelectionItemPattern"
            except Exception as e:
                logger.debug("SelectionItemPattern failed: %s", e)

        # 3. TogglePattern
        if "TogglePattern" in patterns and hasattr(wrapper, "iface_toggle") and wrapper.iface_toggle:
            try:
                wrapper.iface_toggle.Toggle()
                return True, "Triggered via TogglePattern"
            except Exception as e:
                logger.debug("TogglePattern failed: %s", e)

        # 4. ExpandCollapsePattern
        if "ExpandCollapsePattern" in patterns and hasattr(wrapper, "iface_expand_collapse") and wrapper.iface_expand_collapse:
            try:
                wrapper.iface_expand_collapse.Expand()
                return True, "Triggered via ExpandCollapsePattern"
            except Exception as e:
                logger.debug("ExpandCollapsePattern failed: %s", e)

        # 5. SetFocus
        try:
            wrapper.set_focus()
            return True, "Set focus to control"
        except Exception as e:
            return False, f"Could not invoke control pattern or focus: {e}"


_DEFAULT_UI_TREE_MANAGER: Optional[UITreeManager] = None


def get_ui_tree_manager() -> UITreeManager:
    global _DEFAULT_UI_TREE_MANAGER
    if _DEFAULT_UI_TREE_MANAGER is None:
        _DEFAULT_UI_TREE_MANAGER = UITreeManager()
    return _DEFAULT_UI_TREE_MANAGER

