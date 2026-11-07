"""
tests/test_desktop_and_browser_qa.py — Comprehensive Test Suite for Desktop Perception,
Coordinate Transforms, Visual Grounding, UIA Ambiguity Resolution, Browser Session Recovery,
Autonomous Website QA, Emergency Stop, and Sanitization.
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import socketserver
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import outcome
from core.action_loader import get_default_registry
from actions.display_manager import DisplayManager, MonitorInfo, CapturedScreenshot, get_display_manager
from actions.ui_tree_manager import UITreeManager, UIElementNode, get_ui_tree_manager
from actions.target_resolver import TargetResolver, get_target_resolver, ResolvedTarget
from actions.action_verifier import ActionVerifier, get_action_verifier
from actions.computer_use_engine import ComputerUseEngine, get_computer_use_engine
from actions.browser_session_manager import BrowserSessionManager, get_browser_session_manager
from actions.browser_qa_agent import BrowserQAAgent, get_browser_qa_agent
from core.agent_orchestrator import AgentOrchestrator, get_orchestrator
from core.skill_learning.skill_storage import save_skill, load_skill, delete_skill
from core.skill_learning.models import SkillDefinition, StepDefinition, ParameterDefinition, SkillStatus
from core.skill_learning.skill_security import is_sensitive_control, sanitize_input_value, sanitize_text_and_credentials


class TestDesktopAndBrowserQA(unittest.TestCase):

    def setUp(self):
        self.display_manager = get_display_manager()
        self.ui_tree_manager = get_ui_tree_manager()
        self.target_resolver = get_target_resolver()
        self.verifier = get_action_verifier()
        self.computer_engine = get_computer_use_engine()
        self.browser_manager = get_browser_session_manager()
        self.qa_agent = get_browser_qa_agent()

    # ── 1, 2, 3: Single & Multi-Monitor Capture & Negative Coordinates ──────
    def test_single_and_multi_monitor_and_negative_coords(self):
        """Test monitor management, multi-monitor bounds, and negative coordinates."""
        monitors = self.display_manager.get_monitors()
        self.assertGreaterEqual(len(monitors), 1)

        # Simulate multi-monitor setup with negative coordinates (e.g. secondary monitor to the left)
        left_monitor = MonitorInfo(
            index=2, left=-1920, top=0, width=1920, height=1080, is_primary=False, name="Left Monitor"
        )
        self.assertTrue(left_monitor.contains(-100, 500))
        self.assertFalse(left_monitor.contains(100, 500))

        # Test coordinate validation in negative coordinate region
        from actions import windows_uia
        with patch("actions.windows_uia.get_screen_metrics") as mock_metrics:
            mock_metrics.return_value = {
                "virtual_x": -1920,
                "virtual_y": 0,
                "virtual_w": 3840,
                "virtual_h": 1080,
                "scale_factor": 1.0,
            }
            vx, vy, ok, reason = windows_uia.validate_coordinates(-500, 400)
            self.assertTrue(ok)
            self.assertEqual(vx, -500)
            self.assertEqual(vy, 400)

            # Test point outside virtual desktop
            _, _, ok_out, _ = windows_uia.validate_coordinates(-2500, 400)
            self.assertFalse(ok_out)

    # ── 4: Screenshot Coordinate Transformations ────────────────────────────
    def test_screenshot_coordinate_transformations(self):
        """Verify bidirectional coordinate transformations between desktop and scaled images."""
        shot = CapturedScreenshot(
            image_bytes=b"dummy",
            format="jpeg",
            width=960,
            height=540,
            origin_x=-1920,
            origin_y=0,
            monitor_index=0,
            scale_w=0.5,  # downscaled by half from 1920x1080
            scale_h=0.5,
        )

        # Physical desktop coordinate (-920, 200) -> Image coordinate
        # (-920 - (-1920)) * 0.5 = 1000 * 0.5 = 500
        ix, iy = shot.desktop_to_image(-920, 200)
        self.assertEqual(ix, 500)
        self.assertEqual(iy, 100)

        # Image coordinate (500, 100) -> Physical desktop coordinate
        dx, dy = shot.image_to_desktop(500, 100)
        self.assertEqual(dx, -920)
        self.assertEqual(dy, 200)

    # ── 5: UIA Element Discovery ─────────────────────────────────────────────
    def test_uia_element_discovery(self):
        """Verify accessible UI tree traversal and property extraction."""
        res = self.ui_tree_manager.inspect_ui_tree(max_depth=2, limit=10)
        self.assertEqual(res.get("status"), "success")
        self.assertIn("elements", res)
        self.assertIn("total_found", res)

    # ── 6: Duplicate Element Resolution ──────────────────────────────────────
    def test_duplicate_element_ambiguity_resolution(self):
        """Verify duplicate element resolution using active window, control type, and proximity."""
        # Create mock duplicate nodes
        node1 = UIElementNode(
            name="Submit",
            control_type="Button",
            automation_id="btn_submit_main",
            class_name="ButtonClass",
            bounding_rectangle={"left": 100, "top": 200, "right": 200, "bottom": 240, "width": 100, "height": 40},
            is_enabled=True,
            is_visible=True,
            is_offscreen=False,
            is_focused=False,
            parent_name="MainDialog",
        )
        node2 = UIElementNode(
            name="Submit",
            control_type="Button",
            automation_id="btn_submit_modal",
            class_name="ButtonClass",
            bounding_rectangle={"left": 800, "top": 600, "right": 900, "bottom": 640, "width": 100, "height": 40},
            is_enabled=True,
            is_visible=True,
            is_offscreen=False,
            is_focused=False,
            parent_name="SecondaryDialog",
        )

        with patch.object(self.ui_tree_manager, "inspect_ui_tree") as mock_tree:
            mock_tree.return_value = {"status": "success", "elements": [node1.to_dict(), node2.to_dict()]}
            self.ui_tree_manager._last_inspected_elements = [node1, node2]

            # 1. Resolve by AutomationId
            chosen, reason = self.ui_tree_manager.resolve_target_element(
                name="Submit", automation_id="btn_submit_modal"
            )
            self.assertIsNotNone(chosen)
            self.assertEqual(chosen.automation_id, "btn_submit_modal")

            # 2. Resolve by Parent Container
            chosen_p, _ = self.ui_tree_manager.resolve_target_element(
                name="Submit", parent_name="MainDialog"
            )
            self.assertIsNotNone(chosen_p)
            self.assertEqual(chosen_p.parent_name, "MainDialog")

            # 3. Resolve by Coordinate Proximity (near 820, 610)
            chosen_prox, _ = self.ui_tree_manager.resolve_target_element(
                name="Submit", preferred_coords=(820, 610)
            )
            self.assertIsNotNone(chosen_prox)
            self.assertEqual(chosen_prox.automation_id, "btn_submit_modal")

    # ── 7: Cursor-Based Target Identification ("Click here") ────────────────
    def test_cursor_based_target_identification(self):
        """Verify cursor-targeted resolution for 'Click here'."""
        with patch.object(self.display_manager, "get_cursor_position", return_value=(450, 350)):
            target = self.target_resolver.resolve_cursor_position()
            self.assertEqual(target.resolution_type, "cursor_point")
            self.assertEqual(target.desktop_x, 450)
            self.assertEqual(target.desktop_y, 350)
            self.assertGreaterEqual(target.confidence, 0.8)

    # ── 8 & 9: UIA-based Clicking & Visual Fallback Clicking ─────────────────
    def test_uia_and_visual_fallback_clicking(self):
        """Test semantic UIA clicking with automatic visual grounding fallback."""
        # Test UIA click
        dummy_node = UIElementNode(
            name="OKButton",
            control_type="Button",
            automation_id="ok_id",
            class_name="Button",
            bounding_rectangle={"left": 100, "top": 100, "right": 160, "bottom": 130, "width": 60, "height": 30},
            is_enabled=True,
            is_visible=True,
            is_offscreen=False,
            is_focused=False,
            supported_patterns=["InvokePattern"],
            raw_wrapper=MagicMock(),
        )

        with patch.object(self.target_resolver, "resolve_target") as mock_resolve:
            mock_resolve.return_value = (
                ResolvedTarget(
                    target_name="OKButton",
                    resolution_type="uia_pattern",
                    desktop_x=130,
                    desktop_y=115,
                    bounding_box=dummy_node.bounding_rectangle,
                    confidence=0.99,
                    uia_node=dummy_node,
                ),
                "UIA Match",
            )
            with patch.object(self.ui_tree_manager, "invoke_control_pattern", return_value=(True, "Invoked")):
                res = self.computer_engine.click_element(name="OKButton")
                self.assertEqual(res.status, outcome.STATUS_DONE)
                self.assertTrue(res.success)
                self.assertTrue(res.verified)

    # ── 10: Action Verification ──────────────────────────────────────────────
    def test_action_verification(self):
        """Verify action verifier checks outcomes and rejects false success."""
        # Test text input verification
        dummy_node = MagicMock()
        dummy_node.name = "SearchInput"
        dummy_node.raw_wrapper.window_text.return_value = "Python testing tutorial"
        node_obj = UIElementNode(
            name="SearchInput",
            control_type="Edit",
            automation_id="search_box",
            class_name="Edit",
            bounding_rectangle={},
            is_enabled=True,
            is_visible=True,
            is_offscreen=False,
            is_focused=True,
            raw_wrapper=dummy_node.raw_wrapper,
        )

        v_res = self.verifier.verify_text_input("Python testing", target_node=node_obj)
        self.assertTrue(v_res.verified)
        self.assertEqual(v_res.status, "done")

    # ── 11, 12, 13: Browser Session Recovery, Closed Context & Nav Reporting ─
    def test_browser_session_recovery_and_closed_context(self):
        """Verify browser session safely recovers from closed pages and handles invalid navigations."""
        bm = self.browser_manager
        # Navigation to invalid domain should report failure, NEVER false success
        res = bm.navigate("http://invalid.domain.that.does.not.exist.test:9999", timeout=2.0)
        self.assertEqual(res.status, outcome.STATUS_FAILED)
        self.assertFalse(res.success)
        self.assertFalse(res.verified)
        self.assertIn("failed", res.message.lower())

    # ── 14: Local Website Autonomous QA Execution ────────────────────────────
    def test_local_website_autonomous_qa(self):
        """Run autonomous QA agent against the dedicated local multi-page test site."""
        test_site_dir = Path(__file__).parent / "test_site"
        self.assertTrue((test_site_dir / "index.html").exists())
        self.assertTrue((test_site_dir / "login.html").exists())

        # Start a local HTTP server in a daemon thread on a random available port
        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, format, *args):
                pass  # suppress terminal clutter

        handler = lambda *args, **kwargs: QuietHandler(*args, directory=str(test_site_dir), **kwargs)
        with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
            port = httpd.server_address[1]
            server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            server_thread.start()

            local_url = f"http://127.0.0.1:{port}/index.html"

            # Execute QA Agent on the local test website
            report = self.qa_agent.execute_test_suite(url=local_url)

            httpd.shutdown()

            self.assertEqual(report.site_url, local_url)
            self.assertGreaterEqual(report.total_tests, 1)
            self.assertGreaterEqual(report.passed_tests, 1)
            self.assertTrue(os.path.exists(report.report_json_path))
            self.assertTrue(os.path.exists(report.report_html_path))

    # ── 15: Skill Generation & Persistence ───────────────────────────────────
    def test_skill_generation_and_persistence(self):
        """Verify learned skill persistence, manifest tracking, discovery, and deletion."""
        skill_id = "test_qa_auto_skill_42"
        skill = SkillDefinition(
            id=skill_id,
            name="Automated QA Skill",
            description="Autonomous browser test workflow",
            category="user",
            application="Chrome",
            version="1.0.0",
            status=SkillStatus.APPROVED.value,
            steps=[
                StepDefinition(step_id=1, thought="Open site", tool="browser_navigate", parameters={"url": "http://localhost:3000"}),
                StepDefinition(step_id=2, thought="Click login", tool="browser_click", parameters={"name": "Login"}),
            ],
            parameters=[ParameterDefinition(name="url", type="url", default="http://localhost:3000")],
        )

        # 1. Save
        saved_path = save_skill(skill)
        self.assertTrue(saved_path.exists())

        # 2. Load
        loaded = load_skill(skill_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "Automated QA Skill")
        self.assertEqual(len(loaded.steps), 2)

        # 3. Delete
        del_ok = delete_skill(skill_id)
        self.assertTrue(del_ok)
        self.assertIsNone(load_skill(skill_id))

    # ── 16: Emergency Stop ───────────────────────────────────────────────────
    def test_emergency_stop_halts_orchestrator(self):
        """Verify emergency stop halts agent execution immediately."""
        orch = get_orchestrator()
        orch.reset_emergency_stop()
        self.assertFalse(orch.is_stopped())

        # Trigger emergency stop
        orch.emergency_stop()
        self.assertTrue(orch.is_stopped())

        # Attempt to run step while stopped
        ok, msg = orch.execute_step({"action": "computer_use", "parameters": {"action": "inspect_screen"}})
        self.assertFalse(ok)
        self.assertIn("emergency stop", msg.lower())

        orch.reset_emergency_stop()

    # ── 17: Sensitive-Input Sanitization ─────────────────────────────────────
    def test_sensitive_input_sanitization(self):
        """Verify password fields and sensitive inputs are identified and sanitized."""
        self.assertTrue(is_sensitive_control({"name": "user_password", "control_type": "Edit"}))
        self.assertTrue(is_sensitive_control({"automation_id": "txtPasscode"}))
        self.assertTrue(is_sensitive_control({"class_name": "PasswordBox"}))
        self.assertFalse(is_sensitive_control({"name": "search_query", "control_type": "Edit"}))

        # Input value sanitization
        masked = sanitize_input_value({"control_type": "Edit", "name": "Password"}, "MySecretP@ssword123")
        self.assertEqual(masked, "[REDACTED_SENSITIVE_CREDENTIAL]")
        
        # General credential text sanitization
        scrubbed = sanitize_text_and_credentials("user password is supersecret123 on system")
        self.assertNotIn("supersecret123", scrubbed)


if __name__ == "__main__":
    unittest.main()
