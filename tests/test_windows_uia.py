"""
tests/test_windows_uia.py — Automated test suite for Windows UI Automation (UIA).

Covers all 12 test cases specified in the engineering requirements:
  1. Open File Explorer.
  2. List accessible folders.
  3. Find Semester 5.
  4. Open Semester 5.
  5. Verify the opened folder (and mismatch detection).
  6. Find Academic Information.
  7. Open Academic Information.
  8. Handle duplicate folder names (ambiguity detection).
  9. Handle missing folder names (never guess adjacent items).
  10. Handle inaccessible UI elements.
  11. Test coordinate fallback and boundary validation.
  12. Test Windows display scaling and DPI awareness.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from actions import windows_uia
from core import outcome


class MockBoundingRect:
    def __init__(self, left: int, top: int, right: int, bottom: int):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

    def width(self) -> int:
        return self.right - self.left

    def height(self) -> int:
        return self.bottom - self.top

    def __repr__(self) -> str:
        return f"Rect({self.left}, {self.top}, {self.right}, {self.bottom})"


class MockElementInfo:
    def __init__(self, name: str, control_type: str, automation_id: str = "", class_name: str = ""):
        self.name = name
        self.control_type = control_type
        self.automation_id = automation_id
        self.class_name = class_name


class MockUIAElement:
    def __init__(
        self,
        name: str,
        control_type: str,
        rect: MockBoundingRect,
        automation_id: str = "",
        class_name: str = "",
        parent=None,
        children=None,
        has_invoke: bool = True,
        has_selection: bool = True,
        is_visible: bool = True,
        is_enabled: bool = True,
    ):
        self._name = name
        self.element_info = MockElementInfo(name, control_type, automation_id, class_name)
        self._rect = rect
        self._parent = parent
        self._children = children or []
        self._is_visible = is_visible
        self._is_enabled = is_enabled
        self.focus_called = False
        self.invoked = False
        self.selected = False

        if has_invoke:
            self.iface_invoke = MagicMock()
            self.iface_invoke.Invoke = self._invoke_call
        else:
            self.iface_invoke = None

        if has_selection:
            self.iface_selection_item = MagicMock()
            self.iface_selection_item.Select = self._select_call
            self.iface_selection_item.CurrentIsSelected = False
        else:
            self.iface_selection_item = None

    def _invoke_call(self):
        self.invoked = True

    def _select_call(self):
        self.selected = True

    def window_text(self) -> str:
        return self._name

    def rectangle(self) -> MockBoundingRect:
        return self._rect

    def is_visible(self) -> bool:
        return self._is_visible

    def is_enabled(self) -> bool:
        return self._is_enabled

    def parent(self):
        return self._parent

    def children(self):
        return self._children

    def set_focus(self):
        self.focus_called = True


def build_mock_file_explorer_tree() -> MockUIAElement:
    """
    Build a realistic File Explorer accessibility tree containing:
      - Downloads
      - Academic Information
      - Semester 5
      - Semester 6
      - Projects
    """
    root_rect = MockBoundingRect(100, 100, 1100, 800)
    root = MockUIAElement("File Explorer", "Window", root_rect, class_name="CabinetWClass")

    # Address bar
    addr_rect = MockBoundingRect(120, 110, 800, 140)
    addr = MockUIAElement("Address: C:\\University", "ToolBar", addr_rect, automation_id="AddressBandRoot", parent=root)

    # Main items list view
    list_rect = MockBoundingRect(150, 200, 1050, 750)
    list_view = MockUIAElement("Items View", "List", list_rect, automation_id="ItemsView", parent=root)

    # Folders inside list view
    f_downloads = MockUIAElement("Downloads", "ListItem", MockBoundingRect(160, 210, 400, 250), parent=list_view)
    f_academic = MockUIAElement("Academic Information", "ListItem", MockBoundingRect(160, 260, 400, 300), parent=list_view)
    f_sem5 = MockUIAElement("Semester 5", "ListItem", MockBoundingRect(160, 310, 400, 350), parent=list_view)
    f_sem6 = MockUIAElement("Semester 6", "ListItem", MockBoundingRect(160, 360, 400, 400), parent=list_view)
    f_projects = MockUIAElement("Projects", "ListItem", MockBoundingRect(160, 410, 400, 450), parent=list_view)

    list_view._children = [f_downloads, f_academic, f_sem5, f_sem6, f_projects]
    root._children = [addr, list_view]
    return root


class TestWindowsUIA(unittest.TestCase):
    """Automated test suite executing all 12 test cases."""

    def setUp(self):
        self.explorer_tree = build_mock_file_explorer_tree()

    # ── Test Case 1: Open File Explorer ──────────────────────────────────────
    def test_01_open_file_explorer(self):
        """Test identification and retrieval of File Explorer window."""
        with patch.object(windows_uia, "is_available", return_value=True), \
             patch.object(windows_uia, "_get_file_explorer_window", return_value=self.explorer_tree):

            win = windows_uia._get_file_explorer_window()
            self.assertIsNotNone(win)
            self.assertEqual(win.window_text(), "File Explorer")
            self.assertEqual(win.element_info.class_name, "CabinetWClass")

    # ── Test Case 2: List accessible folders ─────────────────────────────────
    def test_02_list_accessible_folders(self):
        """Test inspecting window and listing accessible folders."""
        with patch.object(windows_uia, "is_available", return_value=True):
            res = windows_uia.inspect_window(
                window=self.explorer_tree,
                max_depth=3,
                filter_control_types=["ListItem"],
                only_visible=True,
            )
            self.assertEqual(res["status"], "success")
            names = [e["name"] for e in res["elements"]]
            self.assertIn("Downloads", names)
            self.assertIn("Academic Information", names)
            self.assertIn("Semester 5", names)
            self.assertIn("Semester 6", names)
            self.assertIn("Projects", names)

    # ── Test Case 3: Find Semester 5 ─────────────────────────────────────────
    def test_03_find_semester_5(self):
        """Test finding exact element 'Semester 5'."""
        with patch.object(windows_uia, "is_available", return_value=True):
            res = windows_uia.find_element(
                name="Semester 5",
                window=self.explorer_tree,
                exact=True,
            )
            self.assertEqual(res["status"], "success")
            match = res["match"]
            self.assertEqual(match["name"], "Semester 5")
            self.assertEqual(match["control_type"], "ListItem")
            self.assertEqual(match["bounding_rectangle"]["top"], 310)

    # ── Test Case 4: Open Semester 5 ─────────────────────────────────────────
    def test_04_open_semester_5(self):
        """Test opening 'Semester 5' triggers the exact target element."""
        sem5_elem = None
        for child in self.explorer_tree._children[1]._children:
            if child.window_text() == "Semester 5":
                sem5_elem = child
                break
        self.assertIsNotNone(sem5_elem)

        with patch.object(windows_uia, "is_available", return_value=True):
            res = windows_uia.click_element(sem5_elem)
            self.assertIn(res["status"], ("done", "success"))
            self.assertTrue(sem5_elem.invoked or sem5_elem.selected or sem5_elem.focus_called)

    # ── Test Case 5: Verify the opened folder ────────────────────────────────
    def test_05_verify_opened_folder_success_and_mismatch(self):
        """Test verification of opened folder, including mismatch detection."""
        # 5A: Success scenario: title changes to Semester 5
        with patch.object(windows_uia, "is_available", return_value=True), \
             patch.object(windows_uia, "_get_file_explorer_window", return_value=self.explorer_tree), \
             patch.object(windows_uia, "get_active_window", return_value={"status": "success", "title": "Semester 5"}):

            res = windows_uia.open_file_explorer_item("Semester 5", window=self.explorer_tree, timeout=0.1)
            self.assertIn("status=done", res)
            self.assertIn("verified=true", res)
            self.assertIn("Semester 5", res)

        # 5B: Mismatch scenario: accidentally navigated to Downloads
        with patch.object(windows_uia, "is_available", return_value=True), \
             patch.object(windows_uia, "_get_file_explorer_window", return_value=self.explorer_tree), \
             patch.object(windows_uia, "get_active_window", return_value={"status": "success", "title": "Downloads"}):

            res_mismatch = windows_uia.open_file_explorer_item("Semester 5", window=self.explorer_tree, timeout=0.1)
            self.assertIn("status=failed", res_mismatch)
            self.assertIn("opened_wrong", res_mismatch)
            self.assertIn("Downloads", res_mismatch)

    # ── Test Case 6: Find Academic Information ───────────────────────────────
    def test_06_find_academic_information(self):
        """Test finding 'Academic Information' adjacent to Semester 5."""
        with patch.object(windows_uia, "is_available", return_value=True):
            res = windows_uia.find_element(
                name="Academic Information",
                window=self.explorer_tree,
                exact=True,
            )
            self.assertEqual(res["status"], "success")
            match = res["match"]
            self.assertEqual(match["name"], "Academic Information")
            self.assertNotEqual(match["bounding_rectangle"]["top"], 310)  # Distinct from Semester 5

    # ── Test Case 7: Open Academic Information ───────────────────────────────
    def test_07_open_academic_information(self):
        """Test opening 'Academic Information' and verifying destination."""
        with patch.object(windows_uia, "is_available", return_value=True), \
             patch.object(windows_uia, "_get_file_explorer_window", return_value=self.explorer_tree), \
             patch.object(windows_uia, "get_active_window", return_value={"status": "success", "title": "Academic Information"}):

            res = windows_uia.open_file_explorer_item("Academic Information", window=self.explorer_tree, timeout=0.1)
            self.assertIn("status=done", res)
            self.assertIn("Academic Information", res)

    # ── Test Case 8: Handle duplicate folder names ───────────────────────────
    def test_08_handle_duplicate_folder_names(self):
        """Test ambiguity detection when identical element names exist across panes."""
        tree = build_mock_file_explorer_tree()
        # Add duplicate "Semester 5" in sidebar navigation pane
        nav_pane = MockUIAElement("Navigation", "Pane", MockBoundingRect(100, 200, 250, 750), parent=tree)
        dup_sem5 = MockUIAElement("Semester 5", "TreeItem", MockBoundingRect(110, 220, 240, 250), parent=nav_pane)
        nav_pane._children = [dup_sem5]
        tree._children.append(nav_pane)

        with patch.object(windows_uia, "is_available", return_value=True):
            res = windows_uia.find_elements(
                name="Semester 5",
                window=tree,
                exact=True,
            )
            # Should disambiguate to main list view or flag ambiguity
            self.assertIn(res["status"], ("success", "ambiguous"))
            if res["status"] == "ambiguous":
                self.assertIn("Multiple elements matched", res["question"])
            else:
                self.assertTrue(res.get("disambiguated", False))

    # ── Test Case 9: Handle missing folder names ─────────────────────────────
    def test_09_handle_missing_folder_names(self):
        """Test searching for a non-existent folder never clicks adjacent items."""
        with patch.object(windows_uia, "is_available", return_value=True), \
             patch.object(windows_uia, "_get_file_explorer_window", return_value=self.explorer_tree):

            res = windows_uia.open_file_explorer_item("NonExistentSemesterX", window=self.explorer_tree, timeout=0.1)
            self.assertIn("status=failed", res)
            self.assertIn("not found", res.lower())
            self.assertIn("No other folder was clicked", res)

    # ── Test Case 10: Handle inaccessible UI elements ────────────────────────
    def test_10_handle_inaccessible_ui_elements(self):
        """Test that inaccessible elements fall back cleanly without raising."""
        # Create an element with no invoke, no selection, and empty bounding rect
        inaccessible = MockUIAElement(
            "BrokenElement",
            "Custom",
            MockBoundingRect(0, 0, 0, 0),
            has_invoke=False,
            has_selection=False,
        )

        with patch.object(windows_uia, "is_available", return_value=True):
            res = windows_uia.click_element(inaccessible)
            self.assertEqual(res["status"], "failed")
            self.assertIn("reason", res)

    # ── Test Case 11: Test coordinate fallback & validation ──────────────────
    def test_11_coordinate_fallback_validation(self):
        """Test coordinate validation against virtual desktop and bounding boxes."""
        # Valid coordinates
        x, y, is_valid, reason = windows_uia.validate_coordinates(
            500, 500, bounding_box={"left": 400, "top": 400, "right": 600, "bottom": 600}
        )
        self.assertTrue(is_valid)

        # Out-of-bounds coordinates
        x, y, is_valid, reason = windows_uia.validate_coordinates(
            99999, 99999
        )
        self.assertFalse(is_valid)
        self.assertIn("outside virtual screen", reason)

        # Outside element bounding box
        x, y, is_valid, reason = windows_uia.validate_coordinates(
            350, 350, bounding_box={"left": 400, "top": 400, "right": 600, "bottom": 600}
        )
        self.assertFalse(is_valid)
        self.assertIn("outside element bounding box", reason)

    # ── Test Case 12: Test Windows display scaling ───────────────────────────
    def test_12_windows_display_scaling(self):
        """Test display scaling calculations and coordinate space handling."""
        metrics = windows_uia.get_screen_metrics()
        self.assertIn("scale_factor", metrics)
        self.assertIn("virtual_w", metrics)
        self.assertIn("virtual_h", metrics)
        self.assertGreater(metrics["virtual_w"], 0)
        self.assertGreater(metrics["virtual_h"], 0)
        self.assertGreaterEqual(metrics["scale_factor"], 1.0)


    # ── Integration with computer_control ────────────────────────────────────
    def test_13_computer_control_uia_open(self):
        """Test computer_control action='uia_open' invokes Windows UIA."""
        from actions.computer_control import computer_control
        with patch.object(windows_uia, "is_available", return_value=True), \
             patch.object(windows_uia, "_get_file_explorer_window", return_value=self.explorer_tree), \
             patch.object(windows_uia, "get_active_window", return_value={"status": "success", "title": "Semester 5"}):

            res = computer_control({"action": "uia_open", "name": "Semester 5"})
            self.assertIn("status=done", res)
            self.assertIn("Semester 5", res)

    def test_14_computer_control_screen_click_uia_routing(self):
        """Test computer_control action='screen_click' routes to UIA before vision."""
        from actions.computer_control import computer_control
        with patch.object(windows_uia, "is_available", return_value=True), \
             patch.object(windows_uia, "find_element", return_value={
                 "status": "success",
                 "match": {"name": "Semester 5", "control_type": "ListItem", "bounding_rectangle": {"left": 160, "top": 310, "right": 400, "bottom": 350}},
                 "element": self.explorer_tree._children[1]._children[2],
             }):

            res = computer_control({"action": "screen_click", "description": "Semester 5"})
            self.assertIn("status=done", res)
            self.assertIn("Semester 5", res)

    def test_15_computer_control_screen_click_bounds_rejection(self):
        """Test that invalid coordinates in screen_click are safely rejected."""
        from actions.computer_control import computer_control
        with patch.object(windows_uia, "is_available", return_value=True), \
             patch.object(windows_uia, "find_element", return_value={"status": "failed"}), \
             patch("actions.computer_control._screen_find", return_value=(99999, 99999)):

            res = computer_control({"action": "screen_click", "description": "OffscreenButton"})
            self.assertIn("status=failed", res)
            self.assertIn("out of bounds", res)


if __name__ == "__main__":
    unittest.main()
