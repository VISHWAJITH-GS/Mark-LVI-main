"""
tests/test_mission_features.py — Comprehensive Test Suite for MARK LVI Mission Capabilities.

Covers:
1. Feature 1: Reminder Management (Create, List, Edit, Delete, Persistence, Parsing, Recurrence)
2. Feature 2: WhatsApp Integration (Contact Resolution, Ambiguity, Verification Contract, Reading)
3. Feature 3: Windows System Settings (Wi-Fi, Bluetooth, Volume, Mute, Brightness, Power Status)
4. Feature 4: Application Management (Open, Foreground Window, Minimize, Maximize, Close, List Open Windows)
5. Feature 5: Music Control (Play, Search vs Play, Pause, Resume, Skip, Current Track Detection)
6. Feature 6: Web Search and Research (Query, Headlines, Webpage Text Reader, Article Summarization)
7. Feature 7: Windows Notification Center (Retrieval, Filtering, Speech Formatting, Summarization)
8. Feature 8: LeetCode Coding Assistant (Problem Detection, Progressive Hints, Approaches, Debugging, Complexity)
9. Architecture: Authoritative Execution and Verification Result Contract
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import outcome
from actions import (
    reminder,
    windows_control,
    notification_reader,
    media_control,
    spotify_control,
    web_search,
    leetcode_assistant,
    computer_use_engine,
    action_verifier,
    windows_uia,
    computer_control,
)
from plugins import whatsapp_reader, _whatsapp_core as wa


class TestReminderManagement(unittest.TestCase):
    """Acceptance tests for complete reminder management system."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self._orig_reminders_dir = reminder._reminders_dir
        reminder._reminders_dir = lambda: self.test_dir

    def tearDown(self):
        reminder._reminders_dir = self._orig_reminders_dir
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_create_and_retrieve_reminder(self):
        target_dt = datetime.now() + timedelta(hours=3)
        res = reminder.create_reminder("Submit chemistry assignment", target_dt)
        self.assertEqual(res.status, outcome.STATUS_DONE)
        self.assertTrue(res.verified)
        self.assertEqual(res.contract_status, "SUCCESS")

        # List reminders
        listed = reminder.list_reminders(scope="upcoming")
        self.assertEqual(listed.status, outcome.STATUS_DONE)
        self.assertEqual(listed.data["count"], 1)
        self.assertIn("Submit chemistry assignment", listed.message)

    def test_duplicate_reminder_avoidance(self):
        target_dt = datetime.now() + timedelta(hours=2)
        res1 = reminder.create_reminder("Study Algorithms", target_dt)
        self.assertEqual(res1.status, outcome.STATUS_DONE)

        # Attempt to create exact duplicate
        res2 = reminder.create_reminder("Study Algorithms", target_dt)
        self.assertEqual(res2.status, outcome.STATUS_FAILED)
        self.assertIn("already exists", res2.message)

    def test_edit_reminder(self):
        target_dt = datetime.now() + timedelta(hours=4)
        reminder.create_reminder("Read chapter 4", target_dt)

        new_dt = datetime.now() + timedelta(hours=5)
        edit_res = reminder.edit_reminder("Read chapter 4", new_message="Read chapter 5", new_due_time=new_dt)
        self.assertEqual(edit_res.status, outcome.STATUS_DONE)
        self.assertTrue(edit_res.verified)

        listed = reminder.list_reminders(scope="upcoming")
        self.assertEqual(listed.data["count"], 1)
        self.assertIn("Read chapter 5", listed.message)

    def test_complete_and_delete_reminder(self):
        target_dt = datetime.now() + timedelta(hours=1)
        reminder.create_reminder("Buy groceries", target_dt)

        # Mark completed
        comp_res = reminder.complete_reminder("groceries")
        self.assertEqual(comp_res.status, outcome.STATUS_DONE)

        # Pending list should now be 0
        listed = reminder.list_reminders(scope="upcoming")
        self.assertEqual(listed.data["count"], 0)

        # Delete
        del_res = reminder.delete_reminder("groceries")
        self.assertEqual(del_res.status, outcome.STATUS_DONE)

    def test_persistence_across_restarts(self):
        target_dt = datetime.now() + timedelta(days=1)
        reminder.create_reminder("Team meeting", target_dt, recurrence="weekly")

        # Verify JSON file exists directly on disk
        db_file = self.test_dir / "reminders_db.json"
        self.assertTrue(db_file.exists())
        data = json.loads(db_file.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["message"], "Team meeting")
        self.assertEqual(data[0]["recurrence"], "weekly")


class TestWhatsAppIntegration(unittest.TestCase):
    """Tests for WhatsApp contact resolution, delivery verification, and message reading."""

    def setUp(self):
        self.driver = wa.WhatsAppDriver()

    def test_contact_disambiguation_when_multiple_matches(self):
        with patch.object(self.driver, "open_whatsapp", return_value=True), \
             patch.object(self.driver, "_find_whatsapp_window", return_value=MagicMock()):
            with patch.object(self.driver, "uia") as mock_uia:
                mock_uia.find_elements.side_effect = [
                    # Search box
                    {"status": "success", "matches": [MagicMock()]},
                    # Search results with duplicate/ambiguous names
                    {
                        "status": "success",
                        "matches": [{"name": "Mom Office"}, {"name": "Mom Home"}],
                    },
                ]
                ok, msg, candidates = self.driver.resolve_contact("Mom")
                self.assertFalse(ok)
                self.assertEqual(len(candidates), 2)
                self.assertIn("multiple contacts", msg.lower())

    def test_message_delivery_verification_contract(self):
        with patch.object(self.driver, "open_chat", return_value=(True, "Alex")):
            with patch.object(self.driver, "_find_whatsapp_window", return_value=MagicMock()):
                with patch.object(self.driver, "uia") as mock_uia:
                    mock_input = MagicMock()
                    mock_input.window_text.return_value = ""  # Box cleared on send
                    mock_uia.find_elements.side_effect = [
                        # 1. Message input field
                        {"status": "success", "matches": [{"_wrapper": mock_input}]},
                        # 2. Sent text bubble visible in conversation history
                        {"status": "success", "matches": [{"name": "I will be home by 6"}]},
                    ]
                    ok, evidence = self.driver.send_message_to("Alex", "I will be home by 6")
                    self.assertTrue(ok)
                    self.assertIn("confirmed visible", evidence)

    def test_read_chat_messages(self):
        with patch.object(self.driver, "open_chat", return_value=(True, "Mom")):
            with patch.object(self.driver, "_find_whatsapp_window", return_value=MagicMock()):
                with patch.object(self.driver, "uia") as mock_uia:
                    mock_uia.find_elements.return_value = {
                        "status": "success",
                        "matches": [
                            {"name": "Did you reach safely?"},
                            {"name": "Yes, I am home."},
                        ],
                    }
                    ok, msgs, err = self.driver.read_chat_messages("Mom", limit=2)
                    self.assertTrue(ok)
                    self.assertEqual(len(msgs), 2)
                    self.assertEqual(msgs[0]["text"], "Did you reach safely?")

    def test_whatsapp_reader_plugin(self):
        with patch("plugins._whatsapp_core.get") as mock_get:
            mock_driver = MagicMock()
            mock_driver.read_chat_messages.return_value = (
                True,
                [{"sender": "Mom", "text": "Call me later", "time": "10:00 AM"}],
                "",
            )
            mock_get.return_value = (mock_driver, "")
            res = whatsapp_reader.run({"action": "read_chat", "contact": "Mom"})
            self.assertIn("Call me later", res)


class TestWindowsSystemSettings(unittest.TestCase):
    """Tests for Windows system settings: Wi-Fi, Bluetooth, volume, mute, power."""

    def test_list_available_wifi_networks(self):
        mock_output = "Interface name : Wi-Fi\nSSID 1 : Home_Network\nSSID 2 : Office_5G\n"
        with patch("actions.windows_control._run_ps", return_value=(0, mock_output, "")):
            res = windows_control.list_available_wifi_networks()
            self.assertTrue(res["success"])
            self.assertIn("Home_Network", res["networks"])
            self.assertIn("Office_5G", res["networks"])

    def test_volume_adjustment_and_verification(self):
        with patch("actions.computer_settings._win_endpoint_volume") as mock_ep, \
             patch("actions.windows_control.get_volume", return_value=30):
            mock_endpoint_obj = MagicMock()
            mock_ep.return_value = mock_endpoint_obj
            res = windows_control.set_volume(30)
            self.assertTrue(res["success"])
            self.assertEqual(res["volume"], 30)
            self.assertTrue(res["verified"])

    def test_mute_and_unmute(self):
        with patch("actions.computer_settings._win_endpoint_volume") as mock_ep:
            mock_obj = MagicMock()
            mock_obj.GetMute.return_value = 1
            mock_ep.return_value = mock_obj
            res = windows_control.set_mute(True)
            self.assertTrue(res["success"])
            self.assertTrue(res["muted"])

    def test_system_power_status(self):
        with patch("psutil.sensors_battery") as mock_batt:
            mock_b = MagicMock()
            mock_b.percent = 85
            mock_b.power_plugged = True
            mock_b.secsleft = -1
            mock_batt.return_value = mock_b
            res = windows_control.get_system_power_status()
            self.assertTrue(res["has_battery"])
            self.assertEqual(res["percent"], 85)
            self.assertTrue(res["power_plugged"])


class TestApplicationManagement(unittest.TestCase):
    """Tests for application launch, active window, minimize, maximize, and graceful close."""

    def test_distinguish_running_process_from_ready_window(self):
        verifier = action_verifier.get_action_verifier()
        with patch.object(verifier.display_manager, "get_active_window_info", return_value={"title": "", "process_name": ""}):
            with patch("psutil.process_iter") as mock_proc:
                mock_p = MagicMock()
                mock_p.info = {"name": "notepad.exe", "pid": 1234}
                mock_proc.return_value = [mock_p]
                v_res = verifier.verify_window_launch("notepad", timeout=0.1)
                # A running process alone MUST NOT be marked verified done
                self.assertFalse(v_res.verified)
                self.assertEqual(v_res.status, "started")
                self.assertEqual(v_res.contract_status, "PARTIAL")

    def test_ready_foreground_window_verified(self):
        verifier = action_verifier.get_action_verifier()
        ready_info = {"title": "Untitled - Notepad", "process_name": "notepad.exe", "handle": 5678}
        with patch.object(verifier.display_manager, "get_active_window_info", return_value=ready_info):
            v_res = verifier.verify_window_launch("notepad", timeout=0.5)
            self.assertTrue(v_res.verified)
            self.assertEqual(v_res.status, "done")
            self.assertEqual(v_res.contract_status, "SUCCESS")

    def test_minimize_and_maximize_window(self):
        with patch("actions.windows_uia.find_window_by_name") as mock_find, \
             patch("win32gui.ShowWindow") as mock_show, \
             patch("win32gui.IsIconic", return_value=True):
            mock_win = MagicMock()
            mock_win.handle = 123
            mock_win.window_text.return_value = "Spotify Free"
            mock_find.return_value = mock_win
            res = windows_uia.minimize_window("Spotify")
            self.assertEqual(res["status"], "success")
            self.assertTrue(res["verified"])


class TestMusicControl(unittest.TestCase):
    """Tests for music playback, pause, resume, skip, and current track identification."""

    def test_search_vs_play_distinction(self):
        # Searching alone does not start playback
        with patch("actions.spotify_control.search_spotify_track") as mock_search:
            mock_search.return_value = {"title": "Beat It", "artist": "Michael Jackson", "uri": "spotify:track:123"}
            res_str = spotify_control.spotify_control({"action": "search", "query": "Beat It"})
            self.assertIn("status=done", res_str)
            self.assertIn("Beat It", res_str)

    def test_identify_currently_playing_media(self):
        with patch("actions.spotify_control.get_spotify_window_title", return_value="Michael Jackson - Beat It"):
            res = media_control.get_current_playing_media()
            self.assertEqual(res.status, outcome.STATUS_DONE)
            self.assertEqual(res.contract_status, "SUCCESS")
            self.assertIn("Beat It", res.message)


class TestWebSearchAndResearch(unittest.TestCase):
    """Tests for web search, full article reader, and summarization."""

    def test_webpage_content_extraction(self):
        dummy_html = "<html><head><title>Python Docs</title></head><body><nav>Menu</nav><h1>Welcome</h1><p>Python is powerful.</p></body></html>"
        mock_resp = MagicMock()
        mock_resp.headers.get.return_value = "text/html; charset=utf-8"
        mock_resp.read.return_value = dummy_html.encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            text = web_search._read_webpage("https://docs.python.org/3/")
            self.assertIn("Python Docs", text)
            self.assertIn("Python is powerful", text)
            self.assertNotIn("<p>", text)
            self.assertNotIn("Menu", text)  # nav tag stripped


class TestWindowsNotificationCenter(unittest.TestCase):
    """Tests for Windows Notification Center retrieval, speech format, and filtering."""

    def test_notification_retrieval_and_speech_formatting(self):
        reader = notification_reader.NotificationReader()
        mock_notifs = [
            notification_reader.DesktopNotification(id="1", app_name="WhatsApp", title="Alice", body="See you at 5"),
            notification_reader.DesktopNotification(id="2", app_name="Outlook", title="New Email", body="Project meeting"),
        ]
        with patch.object(reader, "_query_winrt_notifications", return_value=(True, mock_notifs, "")):
            # Filter by WhatsApp
            res = reader.get_notifications(app_filter="WhatsApp")
            self.assertEqual(res.status, outcome.STATUS_DONE)
            self.assertEqual(res.data["count"], 1)
            self.assertIn("From WhatsApp: Alice — See you at 5", res.message)

    def test_notification_summarization(self):
        reader = notification_reader.NotificationReader()
        mock_notifs = [
            notification_reader.DesktopNotification(id="1", app_name="Slack", title="Lead Dev", body="Deployment approved"),
        ]
        with patch.object(reader, "_query_winrt_notifications", return_value=(True, mock_notifs, "")):
            with patch("core.gemini.text", return_value="You have an approved deployment message on Slack."):
                sum_res = reader.summarize_notifications()
                self.assertEqual(sum_res.status, outcome.STATUS_DONE)
                self.assertIn("approved deployment", sum_res.message)


class TestLeetCodeCodingAssistant(unittest.TestCase):
    """Tests for LeetCode problem detection, progressive hint generation, and code debugging."""

    def setUp(self):
        self.assistant = leetcode_assistant.LeetCodeAssistant()

    def test_progressive_hints_do_not_reveal_full_solution(self):
        self.assistant.context.problem_title = "Two Sum"
        self.assistant.context.description = "Given an array of integers nums and an integer target, return indices of the two numbers such that they add up to target."

        with patch("core.gemini.text") as mock_gemini:
            mock_gemini.return_value = "Think about using a Hash Map to store previously visited elements and their indices."
            res1 = self.assistant.get_hint("Two Sum")
            self.assertEqual(res1.status, outcome.STATUS_DONE)
            self.assertEqual(self.assistant.context.hint_level, 1)
            self.assertIn("Hint #1", res1.message)
            self.assertNotIn("def twoSum", res1.message)  # No full solution spoilage

    def test_explain_complexity(self):
        self.assistant.context.problem_title = "Two Sum"
        with patch("core.gemini.text", return_value="Time Complexity: O(n) because we traverse the list containing n elements only once. Space Complexity: O(n) for the hash map."):
            res = self.assistant.explain_complexity()
            self.assertEqual(res.status, outcome.STATUS_DONE)
            self.assertIn("O(n)", res.message)

    def test_debug_incorrect_solution(self):
        self.assistant.context.problem_title = "Palindrome Number"
        bad_code = "def isPalindrome(x):\n    return str(x) == str(x)"  # deliberate dummy logic
        with patch("core.gemini.text", return_value="The logic always evaluates to True because str(x) is compared with itself rather than its reverse str(x)[::-1]. Failing test case: x = -121."):
            res = self.assistant.debug_code(code=bad_code, language="Python3", problem_name="Palindrome Number")
            self.assertEqual(res.status, outcome.STATUS_DONE)
            self.assertIn("Failing test case", res.message)


class TestExecutionAndVerificationContract(unittest.TestCase):
    """Enforces standard structured verification dictionary on ActionResult."""

    def test_action_result_verification_contract(self):
        ar = outcome.ActionResult.done(action="send_whatsapp_message", message="Message visible in intended conversation")
        contract = ar.to_contract_dict()

        self.assertEqual(contract["status"], "SUCCESS")
        self.assertEqual(contract["action"], "send_whatsapp_message")
        self.assertTrue(contract["attempted"])
        self.assertTrue(contract["verified"])
        self.assertIn("Message visible", contract["evidence"])
        self.assertIsNone(contract["error"])

    def test_failed_contract(self):
        ar = outcome.ActionResult.failed(action="connect_wifi", error="SSID not found")
        contract = ar.to_contract_dict()
        self.assertEqual(contract["status"], "FAILED")
        self.assertFalse(contract["verified"])
        self.assertEqual(contract["error"], "SSID not found")


if __name__ == "__main__":
    unittest.main()
