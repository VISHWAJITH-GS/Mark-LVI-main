"""
tests/test_cdp_browser_engine.py — Unit Tests for CDP + AXTree + SoM Browser-Use Engine.
"""
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from actions.browser_session import (
    BrowserSessionManager,
    BrowserSessionState,
    CDPConnectionError,
    TabInfo,
    get_browser_session_manager,
)
from actions.dom_distiller import (
    BoundingBox,
    DistilledElement,
    DOMDistiller,
    get_dom_distiller,
)
from actions.action_executor import (
    ActionExecutor,
    KEY_MAP,
    TOOLS as EXECUTOR_TOOLS,
    get_action_executor,
    tool_click_element,
    tool_input_text,
    tool_navigate_to,
    tool_observe_page,
)
from actions import browser_control
from core.action_loader import get_default_registry


class TestCDPBrowserEngine(unittest.TestCase):
    def setUp(self):
        self.session_mgr = get_browser_session_manager()
        self.distiller = get_dom_distiller()
        self.executor = get_action_executor()

    # ── Phase 2: Browser Session & Connection Guard ───────────────────────────

    def test_connection_guard_failure(self):
        """Verify connection guard raises clear actionable instructions when Edge is down."""
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            ok, msg = self.session_mgr.check_connection(timeout=0.1)
            self.assertFalse(ok)
            self.assertIn("msedge.exe --remote-debugging-port=9222", msg)
            self.assertIn("edge://inspect/#remote-debugging", msg)

    def test_tab_info_and_session_state(self):
        """Verify TabInfo and BrowserSessionState data contracts."""
        t1 = TabInfo(index=0, title="Google", url="https://google.com", is_active=True)
        t2 = TabInfo(index=1, title="WhatsApp", url="https://web.whatsapp.com", is_active=False)
        st = BrowserSessionState(
            is_connected=True,
            cdp_url="http://localhost:9222",
            active_title="Google",
            active_url="https://google.com",
            tab_count=2,
            tabs=[t1, t2],
        )
        self.assertTrue(st.is_connected)
        self.assertEqual(st.tab_count, 2)
        self.assertEqual(st.active_title, "Google")
        self.assertEqual(st.browser_type, "edge_cdp")

    # ── Phase 3: Perception Engine (AXTree & DOM Distiller) ────────────────────

    def test_distilled_element_serialization(self):
        """Verify that DistilledElement serializes into clean token-efficient prompt lines."""
        elem1 = DistilledElement(
            index=1,
            backend_node_id=101,
            tag="input",
            role="textbox",
            aria_label="Search Google",
            placeholder="Search",
            bounding_box=BoundingBox(10, 20, 200, 30),
            is_focused=True,
        )
        line1 = elem1.to_prompt_line()
        self.assertEqual(line1, '[1] <textbox> aria-label="Search Google" placeholder="Search" focused=true')

        elem2 = DistilledElement(
            index=2,
            backend_node_id=102,
            tag="button",
            role="button",
            text="Google Search",
            bounding_box=BoundingBox(50, 80, 100, 35),
            is_focused=False,
        )
        line2 = elem2.to_prompt_line()
        self.assertEqual(line2, '[2] <button> text="Google Search"')

        elem3 = DistilledElement(
            index=3,
            backend_node_id=103,
            tag="div",
            role="tab",
            aria_label="Unread",
            is_selected=False,
            bounding_box=BoundingBox(0, 0, 80, 40),
        )
        line3 = elem3.to_prompt_line()
        self.assertIn("[3] <tab>", line3)
        self.assertIn('aria-label="Unread"', line3)

    def test_bounding_box_center_calculation(self):
        """Verify center calculation for trusted mouse clicks."""
        box = BoundingBox(x=100.0, y=200.0, width=50.0, height=20.0)
        cx, cy = box.center
        self.assertEqual(cx, 125.0)
        self.assertEqual(cy, 210.0)

    def test_distiller_cache_invalidation(self):
        """Verify strict invalidation of lookup cache."""
        self.distiller._last_distilled_map[1] = DistilledElement(
            index=1, backend_node_id=99, tag="button", role="button"
        )
        self.assertIsNotNone(self.distiller.get_element_by_index(1))
        self.distiller.invalidate_cache()
        self.assertIsNone(self.distiller.get_element_by_index(1))

    # ── Phase 4: Action Executor & Tool Registration ──────────────────────────

    def test_key_map_coverage(self):
        """Verify common keys are correctly mapped to CDP keyboard codes."""
        for key in ("Enter", "Escape", "Tab", "Backspace", "ArrowDown", "ArrowUp"):
            self.assertIn(key, KEY_MAP)
            self.assertIn("windowsVirtualKeyCode", KEY_MAP[key])

    def test_atomic_tools_in_registry(self):
        """Verify that action_loader auto-discovers and registers atomic browser tools."""
        reg = get_default_registry()
        expected_tools = [
            "observe_page",
            "click_element",
            "input_text",
            "scroll_page",
            "scroll_element",
            "press_key",
            "navigate_to",
            "switch_tab",
            "go_back",
            "wait_page",
            "browser_control",
        ]
        for tool_name in expected_tools:
            self.assertTrue(reg.has(tool_name), f"Tool '{tool_name}' missing from ActionRegistry")

    # ── Phase 5: Browser Control Routing & Compatibility ──────────────────────

    def test_browser_control_unreachable_returns_actionable_error(self):
        """Verify browser_control returns helpful error when Edge is unreachable."""
        with patch.object(self.session_mgr, "check_connection", return_value=(False, "Edge not running")):
            res = browser_control.browser_control({"action": "observe"})
            self.assertIn("status=failed", res)
            self.assertIn("Edge not running", res)

    def test_browser_control_unknown_action(self):
        """Verify unknown actions fail gracefully with available actions list."""
        with patch.object(self.session_mgr, "check_connection", return_value=(True, "Connected")):
            res = browser_control.browser_control({"action": "fly_to_mars"})
            self.assertIn("status=failed", res)
            self.assertIn("Unknown browser action", res)


if __name__ == "__main__":
    unittest.main()

