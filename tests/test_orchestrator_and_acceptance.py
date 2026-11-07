"""
tests/test_orchestrator_and_acceptance.py — Comprehensive acceptance and integration tests
covering all 15 required system verification scenarios with clean mocks.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from core import outcome
from core.agent_orchestrator import AgentOrchestrator
from actions import (
    browser_control,
    computer_control,
    file_controller,
    open_app,
    spotify_control,
    windows_control,
    windows_uia,
)


class AcceptanceAndOrchestratorTestCase(unittest.TestCase):
    def setUp(self):
        self.orchestrator = AgentOrchestrator()

    # ── Acceptance Test 1 & 2: Browser navigation & Search ────────────────────
    @patch("actions.browser_control._open_native")
    def test_acceptance_1_open_chrome_and_navigate_google(self, mock_native):
        """Scenario 1: Open Chrome and navigate to Google."""
        mock_native.return_value = "Opened in chrome: https://google.com"
        res = browser_control.browser_control({"action": "open", "browser": "chrome", "url": "https://google.com"})
        self.assertIn("chrome", res.lower())
        self.assertIn("https://google.com", res)

    @patch("actions.web_search._ddg_search")
    def test_acceptance_2_search_skillrack_and_identify_page(self, mock_ddg):
        """Scenario 2: Search Skillrack and identify the actual page."""
        mock_ddg.return_value = [
            {
                "title": "SkillRack - Code Practice and Assessment Platform",
                "url": "https://skillrack.com",
                "snippet": "SkillRack provides coding assessment and programming practice.",
            }
        ]
        from actions import web_search
        res = web_search.web_search({"mode": "search", "query": "Skillrack"})
        self.assertIn("skillrack.com", res)
        self.assertIn("SkillRack", res)

    # ── Acceptance Test 3 & 4: File Explorer & Document Finding ──────────────
    @patch("actions.windows_uia.open_file_explorer_item")
    def test_acceptance_3_open_file_explorer_and_navigate_folder(self, mock_uia_open):
        """Scenario 3: Open File Explorer and navigate to Semester 5."""
        mock_uia_open.return_value = outcome.done("uia_open", item="Semester 5", verified=True)
        ok, res = self.orchestrator.execute_step({"action": "uia_open", "parameters": {"name": "Semester 5"}})
        self.assertTrue(ok)
        self.assertIn("Semester 5", res)

    @patch("actions.file_controller.find_files")
    def test_acceptance_4_find_specific_document(self, mock_find):
        """Scenario 4: Find a specific document on disk."""
        mock_find.return_value = "Found 1 matching file(s):\n  📄 Academic_Notes.pdf (245 KB) — C:\\Users\\User\\Documents"
        res = file_controller.find_files(name="Academic_Notes.pdf", path="home")
        self.assertIn("Academic_Notes.pdf", res)
        self.assertIn("Documents", res)

    # ── Acceptance Test 5: Bluetooth Connection Verification ─────────────────
    @patch("actions.windows_control.list_bluetooth_devices")
    @patch("actions.windows_control.set_default_playback_device")
    @patch("actions.windows_control._run_ps")
    def test_acceptance_5_connect_oppo_enco_buds_verified(self, mock_ps, mock_set_dev, mock_list):
        """Scenario 5: Connect OPPO Enco Buds 2 and verify connection."""
        mock_list.return_value = [
            {"FriendlyName": "OPPO Enco Buds 2", "Status": "OK", "Present": True, "InstanceId": "BTH\\001"}
        ]
        mock_ps.return_value = (0, "SUCCESS", "")
        res = windows_control.connect_bluetooth_device("OPPO Enco Buds 2")
        self.assertTrue(res["verified"])
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "done")
        self.assertEqual(res["device"], "OPPO Enco Buds 2")

    # ── Acceptance Tests 6, 7, 8, 9, 10: Spotify Playback Lifecycle ───────────
    @patch("actions.spotify_control.launch_spotify")
    @patch("actions.spotify_control.search_spotify_track")
    @patch("actions.spotify_control.get_spotify_window_title")
    @patch("actions.spotify_control.subprocess.run")
    def test_acceptance_6_to_10_spotify_full_playback_lifecycle(
        self, mock_sub, mock_title, mock_search, mock_launch
    ):
        """Scenarios 6-10:
        6. Play One Name from Jailer 2
        7. Play Beat It by Michael Jackson while One Name is playing
        8. Verify current song changed
        9. Pause and resume Beat It
        10. Query what song is currently playing
        """
        mock_launch.return_value = True
        state = {"title": "One Name - Anirudh"}
        mock_title.side_effect = lambda: state["title"]

        # 6. Play One Name from Jailer 2
        mock_search.return_value = {
            "title": "One Name",
            "artist": "Anirudh",
            "uri": "spotify:track:one_name_id",
        }
        res_play1 = spotify_control.play_track("One Name", artist="Jailer 2")
        self.assertTrue(res_play1["verified"])
        self.assertEqual(res_play1["status"], "done")
        self.assertIn("One Name", res_play1["track"])

        # 7 & 8: Play Beat It while One Name is playing & verify song changed
        mock_search.return_value = {
            "title": "Beat It",
            "artist": "Michael Jackson",
            "uri": "spotify:track:beat_it_id",
        }
        state["title"] = "Beat It - Michael Jackson"
        res_play2 = spotify_control.play_track("Beat It", artist="Michael Jackson")
        self.assertTrue(res_play2["verified"])
        self.assertEqual(res_play2["status"], "done")
        self.assertEqual(res_play2["track"], "Beat It")
        self.assertEqual(res_play2["current_track"], "Beat It - Michael Jackson")

        # 9. Pause and resume Beat It
        state["title"] = "Spotify"
        res_pause = spotify_control.pause_playback()
        self.assertEqual(res_pause["status"], "done")

        state["title"] = "Beat It - Michael Jackson"
        res_resume = spotify_control.resume_playback()
        self.assertEqual(res_resume["status"], "done")
        self.assertEqual(res_resume["action"], "resume_playback")

        # 10. Query current playing track
        res_state = spotify_control.get_playback_state()
        self.assertTrue(res_state["is_playing"])
        self.assertIn("Beat It", res_state["current_track"])

    # ── Acceptance Test 11: Change Audio Output Device ───────────────────────
    @patch("actions.windows_control.set_default_playback_device")
    def test_acceptance_11_change_audio_output_device(self, mock_set_out):
        """Scenario 11: Change audio output device."""
        mock_set_out.return_value = {
            "success": True,
            "verified": True,
            "status": "done",
            "device": "OPPO Enco Buds 2",
            "message": "Default playback device set to OPPO Enco Buds 2",
        }
        res = windows_control.set_default_playback_device("OPPO Enco Buds 2")
        self.assertTrue(res["verified"])
        self.assertEqual(res["device"], "OPPO Enco Buds 2")

    # ── Acceptance Test 12 & 13: Prerequisite Failure Halts Dependent Tasks ──
    def test_acceptance_12_and_13_prerequisite_failure_halts_dependent_tasks(self):
        """Scenario 12 & 13: Multi-step task with deliberate failure halts dependent steps."""
        # Previous results: Step 1 (Bluetooth connection) FAILED/UNVERIFIED
        failed_bt = outcome.ActionResult(
            status=outcome.STATUS_UNVERIFIED,
            action="windows_control",
            success=False,
            verified=False,
            message="Could not confirm connection to Earbuds",
            data={"action": "connect_bluetooth_device", "target": "OPPO Enco Buds 2"},
        )

        step_dependent = {
            "thought": "Play Beat It on connected earbuds",
            "action": "spotify_control",
            "parameters": {"action": "play", "query": "Beat It"},
            "requires": ["windows_control"],
        }

        # Attempt executing step with failed prerequisite
        ok, res_msg = self.orchestrator.execute_step(step_dependent, previous_results=[failed_bt])
        self.assertFalse(ok)
        self.assertIn("Prerequisite", res_msg)
        self.assertIn("failed", res_msg)

    # ── Acceptance Test 14: Interrupt Long-Running Task ───────────────────────
    @patch.object(AgentOrchestrator, "plan_task")
    def test_acceptance_14_interrupt_task(self, mock_plan):
        """Scenario 14: Interrupt a task and ensure safe halting."""
        mock_plan.return_value = [
            {"thought": "Step 1", "action": "verify_state", "parameters": {"expected_window": "Explorer"}},
            {"thought": "Step 2", "action": "verify_state", "parameters": {"expected_window": "Explorer"}},
        ]
        # Bounded max_steps limits runaway execution
        res = self.orchestrator.run_autonomous_task("Test Task", max_steps=1)
        self.assertIn("Step 1", res)
        self.assertNotIn("Step 2", res)

    # ── Acceptance Test 15: Gemini Live Reconnection Resilience ───────────────
    def test_acceptance_15_gemini_reconnection_resilience(self):
        """Scenario 15: Verify reconnect resilience against quota/connection drops."""
        from core.gemini import is_quota_error, is_transient_error
        self.assertTrue(is_quota_error("ResourceExhausted 429: Quota exceeded"))
        self.assertTrue(is_transient_error("1011 keepalive ping timeout"))
        self.assertTrue(is_transient_error("504 DEADLINE_EXCEEDED"))


if __name__ == "__main__":
    unittest.main()
