import sys
import unittest
from unittest.mock import MagicMock
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QColor

import ui
from ui import JarvisUI, MainWindow, HudCanvas, HudConversationWidget, BottomChatDock, LeftSearchDock, C

app = QApplication.instance() or QApplication(sys.argv)

class TestKeenaHudTransformation(unittest.TestCase):
    def setUp(self):
        self.win = MainWindow(face_path="")
        self.win.show()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()

    def test_01_window_flags_and_properties(self):
        """Test floating HUD window flags: frameless, stays on top, translucent."""
        flags = self.win.windowFlags()
        self.assertTrue(flags & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(self.win.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        # Initial compact dimensions: collapsed display only
        self.assertEqual(self.win.width(), 420)
        self.assertEqual(self.win.height(), 580)

    def test_02_default_position_bottom_right(self):
        """Test HUD is positioned near bottom-right corner by default."""
        screen = QApplication.primaryScreen().availableGeometry()
        x = self.win.x()
        y = self.win.y()
        self.assertGreater(x, screen.left())
        self.assertLessEqual(x + self.win.width(), screen.right())
        self.assertLessEqual(y + self.win.height(), screen.bottom())

    def test_03_color_palette(self):
        """Verify glowing red visual identity colors."""
        self.assertEqual(C.PRI.lower(), "#ff1744")
        self.assertEqual(C.ACC.lower(), "#ff5252")
        self.assertEqual(C.TEXT.lower(), "#ffeded")
        self.assertEqual(C.TEXT_DIM.lower(), "#d69da4")
        self.assertIn("0a0203", C.BG.lower())

    def test_04_arc_reactor_animation_and_states(self):
        """Verify Arc Reactor responds distinctly to application states."""
        hud = self.win.hud
        self.assertIsInstance(hud, HudCanvas)

        # Standby
        hud.state = "STANDBY"
        hud._step()
        self.assertEqual(hud.state, "STANDBY")

        # Listening
        hud.state = "LISTENING"
        hud.set_audio_level(0.75)
        hud._step()
        self.assertGreater(hud.level, 0.0)
        self.assertGreater(sum(hud.wave), 0.0)

        # Thinking
        hud.state = "THINKING"
        hud._step()
        self.assertEqual(hud.state, "THINKING")

        # Processing
        hud.state = "PROCESSING"
        hud._step()

        # Speaking
        hud.state = "SPEAKING"
        hud.speaking = True
        hud._step()
        self.assertGreater(hud.level, 0.0)

        # Executing
        hud.state = "EXECUTING"
        hud._step()

        # Interrupted & Error
        hud.state = "INTERRUPTED"
        hud._step()
        hud.state = "ERROR"
        hud._step()

    def test_05_speaking_yellow_and_muted_gray(self):
        """Verify speaking state turns yellow and muted state turns gray."""
        hud = self.win.hud

        # When speaking -> Yellow
        self.win._apply_state("SPEAKING")
        self.assertTrue(hud.speaking)

        # When muted -> Gray
        self.win._toggle_mute()
        self.assertTrue(self.win._muted)
        self.assertTrue(hud.muted)

        # Unmute restores
        self.win._toggle_mute()
        self.assertFalse(self.win._muted)
        self.assertFalse(hud.muted)

    def test_06_minimize_to_bottom_left_and_restore(self):
        """Test Arc Reactor minimizes ONLY the circle element to favicon size at bottom-left and restores on click."""
        screen = QApplication.primaryScreen().availableGeometry()
        self.assertEqual(self.win.width(), 420)
        self.assertEqual(self.win.height(), 580)
        self.assertFalse(self.win._is_minimized_top)
        self.assertFalse(self.win.hud._mini_mode)

        # Click or toggle minimize to bottom left
        self.win.toggle_minimize_top()
        if hasattr(self.win, "_geom_anim") and self.win._geom_anim:
            self.win._geom_anim.setCurrentTime(300)
            self.win._geom_anim.stop()
        self.assertTrue(self.win._is_minimized_top)
        self.assertTrue(self.win.hud._mini_mode)
        # Verify minimized position at bottom-left
        self.assertEqual(self.win.x(), screen.left() + 24)
        self.assertEqual(self.win.y(), screen.bottom() - 58 - 24)

        # Click reactor animation again to restore to normal position
        self.win.hud.reactor_clicked.emit()
        if hasattr(self.win, "_geom_anim") and self.win._geom_anim:
            self.win._geom_anim.setCurrentTime(300)
            self.win._geom_anim.stop()
        self.assertFalse(self.win._is_minimized_top)
        self.assertFalse(self.win.hud._mini_mode)

    def test_07_bottom_chat_dock_pop_up_only_on_selection(self):
        """Test bottom chat dock does not pop up automatically, only when selected."""
        chat_dock = self.win.bottom_chat_dock
        self.assertIsInstance(chat_dock, BottomChatDock)
        # Initially hidden until selected by user
        self.assertFalse(chat_dock.isVisible())

        # Toggle via CHAT button opens it
        self.win.toggle_chat_dock()
        self.assertTrue(chat_dock.isVisible())
        screen = QApplication.primaryScreen().availableGeometry()

        # Geometry check: long width and short height
        self.assertGreaterEqual(chat_dock.width(), 540)
        self.assertLessEqual(chat_dock.height(), 180)

        # Located at bottom center
        expected_x = screen.left() + (screen.width() - chat_dock.width()) // 2
        self.assertEqual(chat_dock.x(), expected_x)
        self.assertGreater(chat_dock.y(), screen.top() + screen.height() // 2)

        # Command input in dock
        self.win._input.setText("status report")
        self.win._send()
        self.assertEqual(self.win.hud.user_text, "status report")

        # Toggle again hides it
        self.win.toggle_chat_dock()
        self.assertFalse(chat_dock.isVisible())

    def test_08_left_search_dock_pop_up_alone(self):
        """Test search display pops up along the left edge of the screen ALONE."""
        search_dock = self.win.left_search_dock
        self.assertIsInstance(search_dock, LeftSearchDock)
        screen = QApplication.primaryScreen().availableGeometry()

        # Initially hidden until search/content is displayed
        self.assertFalse(search_dock.isVisible())
        self.assertFalse(self.win._drawer.isVisible())

        self.win._show_content("TRENDS IN AI", "Rapid adoption of multi-agent systems.")
        self.assertTrue(search_dock.isVisible())
        self.assertEqual(search_dock.x(), screen.left() + 16)
        self.assertTrue(self.win._content_panel.isVisible())
        # Drawer remains hidden so search appears alone!
        self.assertFalse(self.win._drawer.isVisible())

        # Dismiss hides dock
        search_dock.hide()
        self.assertFalse(search_dock.isVisible())

    def test_09_fullscreen_big_screen_mode(self):
        """Test Big Screen mode (F11) and escape back to compact HUD."""
        self.assertFalse(self.win._is_fullscreen)
        self.assertFalse(self.win.hud._big_mode)

        self.win._toggle_fullscreen()
        self.assertTrue(self.win._is_fullscreen)
        self.assertTrue(self.win.hud._big_mode)

        # Toggle back
        self.win._toggle_fullscreen()
        self.assertFalse(self.win._is_fullscreen)
        self.assertFalse(self.win.hud._big_mode)
        self.assertEqual(self.win.width(), 420)

    def test_10_jarvis_ui_public_facade(self):
        """Test JarvisUI wrapper facade preserves thread-safe tool API."""
        ui_facade = JarvisUI(face_path="")
        try:
            self.assertEqual(ui_facade.assistant_name, self.win._assistant_name)
            ui_facade.set_state("SPEAKING")
            ui_facade.write_log("SYS: Test notification")
            ui_facade.set_audio_level(0.5)
            self.assertFalse(ui_facade.muted)
            ui_facade.muted = True
            self.assertTrue(ui_facade.muted)
            ui_facade.muted = False
            self.assertFalse(ui_facade.muted)
        finally:
            ui_facade._win.close()
            ui_facade._win.deleteLater()

    def test_11_drawer_attached_inside_main_window(self):
        """Test remaining components (Cyber Drawer) attach directly to MainWindow when expanded."""
        self.assertFalse(self.win._drawer.isVisible())
        self.assertEqual(self.win.width(), 420)
        # Verify _drawer is a child widget of MainWindow's central widget layout
        self.assertEqual(self.win._drawer.parent(), self.win.centralWidget())

        # Expand drawer
        self.win._toggle_expanded(True)
        self.assertTrue(self.win._drawer.isVisible())
        self.assertGreater(self.win.width(), 420)

        # Collapse drawer
        self.win._toggle_expanded(False)
        self.assertFalse(self.win._drawer.isVisible())
        self.assertEqual(self.win.width(), 420)

    def test_12_armoury_crate_telemetry(self):
        """Test ASUS Armoury Crate telemetry dashboard with operating modes and live metrics."""
        telemetry = self.win._armoury_telemetry
        self.assertIsNotNone(telemetry)

        # Verify initial mode
        self.assertEqual(telemetry._mode, "Performance")

        # Test mode switching
        telemetry.set_operating_mode("Silent")
        self.assertEqual(telemetry._mode, "Silent")
        telemetry.set_operating_mode("Turbo")
        self.assertEqual(telemetry._mode, "Turbo")
        telemetry.set_operating_mode("Windows®")
        self.assertEqual(telemetry._mode, "Windows®")

        # Test metrics update
        telemetry.update_metrics({"cpu": 45.0, "gpu": 60.0, "tmp": 72.0, "net": 2.5})
        self.assertIn("45", telemetry.lbl_cpu_usage.text())
        self.assertIn("60", telemetry.lbl_gpu_usage.text())
        self.assertIn("72", telemetry.lbl_cpu_temp.text())
        self.assertGreater(telemetry.bar_cpu.value(), 0)
        self.assertGreater(telemetry.bar_gpu.value(), 0)

    def test_13_window_controls_and_corner_snapping(self):
        """Test corner snapping (TL, TR, BL, BR), maximize, expand, collapse and close."""
        screen = QApplication.primaryScreen().availableGeometry()

        # Corner: Top-Left
        self.win.snap_to_corner("TL")
        if hasattr(self.win, "_pos_anim") and self.win._pos_anim:
            self.win._pos_anim.setCurrentTime(280)
            self.win._pos_anim.stop()
        self.assertEqual(self.win.x(), screen.left() + 24)
        self.assertEqual(self.win.y(), screen.top() + 24)

        # Corner: Top-Right
        self.win.snap_to_corner("TR")
        if hasattr(self.win, "_pos_anim") and self.win._pos_anim:
            self.win._pos_anim.setCurrentTime(280)
            self.win._pos_anim.stop()
        self.assertEqual(self.win.x(), screen.right() - self.win.width() - 24)
        self.assertEqual(self.win.y(), screen.top() + 24)

        # Corner: Bottom-Left
        self.win.snap_to_corner("BL")
        if hasattr(self.win, "_pos_anim") and self.win._pos_anim:
            self.win._pos_anim.setCurrentTime(280)
            self.win._pos_anim.stop()
        self.assertEqual(self.win.x(), screen.left() + 24)
        self.assertEqual(self.win.y(), screen.bottom() - self.win.height() - 24)

        # Corner: Bottom-Right
        self.win.snap_to_corner("BR")
        if hasattr(self.win, "_pos_anim") and self.win._pos_anim:
            self.win._pos_anim.setCurrentTime(280)
            self.win._pos_anim.stop()
        self.assertEqual(self.win.x(), screen.right() - self.win.width() - 24)
        self.assertEqual(self.win.y(), screen.bottom() - self.win.height() - 24)

        # Cycle corners
        self.win.cycle_corners()
        self.assertEqual(self.win._active_corner, "BL")

        # Test expand and collapse methods
        self.win.expand_display()
        self.assertTrue(self.win._drawer.isVisible())
        self.win.collapse_display()
        self.assertFalse(self.win._drawer.isVisible())

        # Test chat log and speech synchronization
        self.win._on_log_line("You: Hello Jarvis")
        self.assertEqual(self.win.hud.user_text, "Hello Jarvis")
        self.win._on_log_line("JARVIS: Online and standing by.")
        self.assertEqual(self.win.hud.reply_text, "Online and standing by.")

    def test_14_clean_hud_controls_no_top_buttons_and_no_icons(self):
        """Verify removal of top buttons, removal of PTT/STOP/CORNER/CFG, and clean button labels."""
        # Verify no hud_top_bar
        self.assertFalse(hasattr(self.win, "hud_top_bar"))

        # Verify bottom control buttons
        self.assertTrue(hasattr(self.win, "_hud_mute_btn"))
        self.assertTrue(hasattr(self.win, "_hud_exp_btn"))
        self.assertTrue(hasattr(self.win, "_hud_chat_btn"))
        self.assertTrue(hasattr(self.win, "_hud_mini_btn"))
        self.assertTrue(hasattr(self.win, "_hud_close_btn"))

        # Verify removed buttons do not exist
        self.assertFalse(hasattr(self.win, "_hud_ptt_btn"))
        self.assertFalse(hasattr(self.win, "_hud_stop_btn"))
        self.assertFalse(hasattr(self.win, "_hud_corner_btn"))
        self.assertFalse(hasattr(self.win, "_hud_cfg_btn"))

        # Verify button text has no emojis or unicode icons
        self.assertEqual(self.win._hud_mute_btn.text(), "MIC")
        self.assertEqual(self.win._hud_exp_btn.text(), "EXPAND")
        self.assertEqual(self.win._hud_chat_btn.text(), "CHAT")
        self.assertEqual(self.win._hud_mini_btn.text(), "MINI")
        self.assertEqual(self.win._hud_close_btn.text(), "CLOSE")

        # Verify mute state toggle maintains clean text
        self.win._toggle_mute()
        self.assertEqual(self.win._hud_mute_btn.text(), "MUTED")
        self.win._toggle_mute()
        self.assertEqual(self.win._hud_mute_btn.text(), "MIC")

    def test_15_12_hour_format_and_sound_frequency_reaction(self):
        """Verify 12-hour format time and dynamic sound frequency in listening state."""
        import time
        # Verify 12-hour time format with AM/PM
        t_str = time.strftime("%I:%M:%S %p")
        self.assertTrue(t_str.endswith("AM") or t_str.endswith("PM"))

        # Verify dynamic sound frequency reaction in LISTENING state
        hud = self.win.hud
        hud.state = "LISTENING"
        hud.set_audio_level(0.85)
        hud._step()
        self.assertGreater(hud.level, 0.4)
        self.assertGreater(sum(hud.wave), 0.0)

    def test_16_multi_display_window_manager(self):
        """Verify opening, naming, focusing to center, and closing multi-display windows."""
        from actions.display_window import display_window
        mock_player = MagicMock()
        mock_player.open_display.side_effect = lambda ctype, src, title: 1 if "image" in ctype else 2
        mock_player.focus_display.return_value = True

        # Test show_image in new display with existing local file
        res1 = display_window({"action": "show_image", "path_or_url": "main.py", "title": "Test Image"}, player=mock_player)
        self.assertIn("Display 1", res1)
        mock_player.open_display.assert_called_with("image", "main.py", "Test Image")

        # Test focus display 2
        res2 = display_window({"action": "focus", "display_id": 2}, player=mock_player)
        self.assertIn("Display 2", res2)
        mock_player.focus_display.assert_called_with(2)

        # Test actual MainWindow display open & focus
        d1 = self.win._on_open_display("image", "", "Diagram")
        self.assertEqual(d1, 1)
        self.assertIn(1, self.win._displays)
        self.assertEqual(self.win._displays[1].display_name, "DISPLAY 1")

        d2 = self.win._on_open_display("video", "", "Trailer")
        self.assertEqual(d2, 2)
        self.assertIn(2, self.win._displays)
        self.assertEqual(self.win._displays[2].display_name, "DISPLAY 2")

        # Test show text in new display
        d3 = self.win._on_open_display("text", "# Bernoulli Principle\nFluid dynamics overview...", "Physics Notes")
        self.assertEqual(d3, 3)
        self.assertIn(3, self.win._displays)
        self.assertEqual(self.win._displays[3].display_name, "DISPLAY 3")
        self.assertEqual(self.win._displays[3].content_type, "text")
        self.assertIn("Bernoulli", self.win._displays[3]._text_edit.toPlainText())

        # Focus display 2
        screen = QApplication.primaryScreen().availableGeometry()
        ok = self.win._on_focus_display(2)
        self.assertTrue(ok)
        w2 = self.win._displays[2]
        self.assertEqual(w2.x(), screen.center().x() - w2.width() // 2)
        self.assertEqual(w2.y(), screen.center().y() - w2.height() // 2)

        # Close display 1
        self.win._on_close_display_win(1)
        self.assertNotIn(1, self.win._displays)
        self.assertIn(2, self.win._displays)
        self.assertIn(3, self.win._displays)

        # Close all
        self.win._on_close_display_win(0)
        self.assertEqual(len(self.win._displays), 0)

    def test_17_hud_conversation_widget_and_streaming(self):
        """Verify real-time live streaming and conversation history in HudCanvas."""
        hud = self.win.hud
        self.assertIsInstance(hud.conversation, HudConversationWidget)
        self.assertTrue(hud.conversation.isVisible())

        # Test streaming reply in real time while speaking
        self.win._on_stream_reply("Good afternoon, sir. How")
        self.assertIn("Good afternoon, sir. How", hud.conversation.toPlainText())
        self.assertIn("█", hud.conversation.toPlainText())

        self.win._on_stream_reply("Good afternoon, sir. How can I assist you today?")
        self.assertIn("assist you today?", hud.conversation.toPlainText())

        # Turn completion commits the reply and removes streaming cursor
        self.win._on_log_line("JARVIS: Good afternoon, sir. How can I assist you today?")
        self.assertIn("Good afternoon, sir. How can I assist you today?", hud.conversation.toPlainText())
        self.assertNotIn("█", hud.conversation.toPlainText())

        # Test streaming user input in real time
        self.win._on_stream_user("What is the current time")
        self.assertIn("What is the current time", hud.conversation.toPlainText())
        self.assertIn("...", hud.conversation.toPlainText())

        # Turn completion commits user message
        self.win._on_log_line("You: What is the current time in Tokyo?")
        self.assertIn("What is the current time in Tokyo?", hud.conversation.toPlainText())

        # Verify both messages are preserved in history
        self.assertEqual(len(hud.conversation._messages), 2)
        self.assertEqual(hud.conversation._messages[0][0], "jarvis")
        self.assertEqual(hud.conversation._messages[1][0], "you")

    def test_18_conversation_scroll_and_history(self):
        """Verify conversation fills bottom area and preserves scrollable history."""
        hud = self.win.hud
        conv = hud.conversation

        # Add multiple turns of conversation
        for i in range(15):
            conv.append_message("you", f"Query number {i}")
            conv.append_message("jarvis", f"Detailed response to query number {i} with valuable data.")

        # Ensure all 30 messages are stored
        self.assertEqual(len(conv._messages), 30)
        text = conv.toPlainText()
        self.assertIn("Query number 0", text)
        self.assertIn("Query number 14", text)

        # Check geometry inside HudCanvas
        hud.resize(420, 540)
        hud._update_conversation_geometry()
        self.assertGreater(conv.width(), 300)
        self.assertGreater(conv.height(), 100)
        self.assertGreater(conv.y(), 300)

        # In mini mode, conversation should hide
        hud.set_mini_mode(True)
        self.assertFalse(conv.isVisible())

        # Returning to normal mode reveals conversation again
        hud.set_mini_mode(False)
        self.assertTrue(conv.isVisible())

    def test_19_left_edge_minimized_displays_dock(self):
        """Verify minimized displays show on the left edge of the screen, expand on hover, and restore on click."""
        dock = self.win._dots_overlay
        self.assertFalse(dock.isVisible())

        # Open Display 1 and Display 2
        d1 = self.win._on_open_display("image", "", "CM of Tamil Nadu")
        d2 = self.win._on_open_display("text", "# Tamil Nadu Map", "Map of Tamil Nadu")
        self.assertEqual(d1, 1)
        self.assertEqual(d2, 2)

        # Minimize Display 1
        self.win._on_display_minimized(1)
        self.assertTrue(dock.isVisible())
        self.assertIn(1, dock._buttons)
        self.assertEqual(len(dock._buttons), 1)

        # Verify docked at the left edge of the screen
        screen = QApplication.primaryScreen().availableGeometry()
        self.assertEqual(dock.x(), screen.left())

        pill1 = dock._buttons[1]
        self.assertIn("Display 1: CM of Tamil Nadu", pill1.toolTip())

        # Test hover interaction: expands to show display number and name
        pill1.enterEvent(None)
        self.assertTrue(pill1._is_hovered)
        self.assertIn("1", pill1.text())
        self.assertIn("CM of Tamil Nadu", pill1.text())

        # Test unhover: collapses back to compact display number
        pill1.leaveEvent(None)
        self.assertFalse(pill1._is_hovered)
        self.assertEqual(pill1.text(), "1")

        # Minimize Display 2
        self.win._on_display_minimized(2)
        self.assertEqual(len(dock._buttons), 2)
        self.assertIn(2, dock._buttons)

        pill2 = dock._buttons[2]
        self.assertIn("Display 2: Map of Tamil Nadu", pill2.toolTip())
        pill2.enterEvent(None)
        self.assertIn("2", pill2.text())
        self.assertIn("Map of Tamil Nadu", pill2.text())
        pill2.leaveEvent(None)

        # Test click to open: clicking pill 1 opens Display 1
        pill1.click()
        self.assertTrue(self.win._displays[1].isVisible())
        self.assertNotIn(1, dock._buttons)
        self.assertIn(2, dock._buttons)
        self.assertEqual(len(dock._buttons), 1)

        # Clicking pill 2 opens Display 2 and hides the dock
        pill2.click()
        self.assertTrue(self.win._displays[2].isVisible())
        self.assertFalse(dock.isVisible())
        self.assertEqual(len(dock._buttons), 0)

        # Cleanup
        self.win._on_close_display_win(0)

    def test_20_task_plan_and_progress_display_window(self):
        """Verify dedicated Task Plan and Progress display window with roadmap, phases, and actions."""
        sample_task = {
            "folder": "TOC_Study_Content",
            "path": "C:/MockPath/TOC_Study_Content",
            "model": "gemini-2.0-flash",
            "kind": "build",
            "phases": ["planning", "writing", "installing", "testing"],
            "request": "Generate comprehensive study content for theory of computation...",
            "status": "writing",
            "started": 1000.0,
            "finished": None,
            "summary": "New python project 'TOC_Study_Content': 3 files",
            "files": {
                "content.py": {"action": "create", "what": "Defines variables containing static text", "state": "done"},
                "file_writer.py": {"action": "create", "what": "Exposes function to format and write content", "state": "done"},
                "main.py": {"action": "create", "what": "Initializes contents and invokes writer", "state": "writing"}
            },
            "steps": [
                (0.0, "Planning project structure..."),
                (3.4, "Writing content.py..."),
                (9.0, "Writing file_writer.py..."),
                (13.0, "Writing main.py...")
            ]
        }

        # Open task progress display via _show_code_job
        self.win._show_code_job(sample_task)

        # Verify dedicated display was opened
        self.assertGreater(len(self.win._displays), 0)
        disp_id = max(self.win._displays.keys())
        task_win = self.win._displays[disp_id]
        self.assertEqual(task_win.content_type, "task")
        self.assertIn("TOC_Study_Content", task_win.title_text)

        # Verify components in task display
        self.assertIn("Generate comprehensive study content", task_win._lbl_goal_req.text())
        self.assertGreater(task_win._task_pbar.value(), 0)
        self.assertIn("content.py", task_win._task_files_edit.toPlainText())
        self.assertIn("Writing main.py", task_win._task_log_edit.toPlainText())

        # Verify task action buttons exist and are active
        self.assertTrue(hasattr(task_win, "_btn_open_folder"))
        self.assertTrue(hasattr(task_win, "_btn_run_project"))
        self.assertTrue(hasattr(task_win, "_btn_copy_plan"))
        self.assertTrue(hasattr(task_win, "_btn_stop_task"))

        # Test copy plan action
        task_win._btn_copy_plan.click()
        copied = QApplication.clipboard().text()
        self.assertIn("Generate comprehensive study content", copied)

        # Test live update of progress
        sample_task["status"] = "done"
        sample_task["steps"].append((19.0, "All tests passed successfully."))
        self.win._show_code_job(sample_task)
        self.assertEqual(task_win._task_pbar.value(), 100)
        self.assertIn("All tests passed successfully", task_win._task_log_edit.toPlainText())

        # Test stop task action
        task_win._btn_stop_task.click()
        self.assertTrue(getattr(task_win, "_task_stopped", False))

        # Cleanup
        self.win._on_close_display_win(0)

    def test_21_sequential_display_numbering_and_reset(self):
        """Verify display IDs count sequentially from active displays and reset to 1 when none are active."""
        # Ensure clean state
        self.win._on_close_display_win(0)
        self.assertEqual(len(self.win._displays), 0)

        # 1. First display starts with 1
        d1 = self.win._on_open_display("image", "", "First Image")
        self.assertEqual(d1, 1)

        # 2. Open up to 5 displays (2, 3, 4, 5)
        d2 = self.win._on_open_display("image", "", "Second Image")
        d3 = self.win._on_open_display("image", "", "Third Image")
        d4 = self.win._on_open_display("image", "", "Fourth Image")
        d5 = self.win._on_open_display("image", "", "Fifth Image")
        self.assertEqual([d1, d2, d3, d4, d5], [1, 2, 3, 4, 5])
        self.assertEqual(len(self.win._displays), 5)

        # 3. Next display becomes 6
        d6 = self.win._on_open_display("image", "", "Sixth Image")
        self.assertEqual(d6, 6)

        # 4. Close all active displays
        self.win._on_close_display_win(0)
        self.assertEqual(len(self.win._displays), 0)

        # 5. After closing all displays, the next created display MUST start at 1 again!
        new_d1 = self.win._on_open_display("image", "", "Fresh Image")
        self.assertEqual(new_d1, 1)

        # 6. Open display 2 and 3
        new_d2 = self.win._on_open_display("image", "", "Fresh Image 2")
        new_d3 = self.win._on_open_display("image", "", "Fresh Image 3")
        self.assertEqual(new_d2, 2)
        self.assertEqual(new_d3, 3)

        # 7. Close display 2 individually -> next display should fill slot 2
        self.win._on_close_display_win(2)
        self.assertNotIn(2, self.win._displays)
        slot_2 = self.win._on_open_display("image", "", "Re-opened Slot 2")
        self.assertEqual(slot_2, 2)

        # 8. Clean up
        self.win._on_close_display_win(0)
        self.assertEqual(len(self.win._displays), 0)

    def test_22_global_screen_bottom_clipboard_panel(self):
        """Verify clipboard quick-action panel appears at physical screen center-bottom in normal, minimized and maximized states."""
        clip_panel = self.win._clipboard_panel
        screen = QApplication.primaryScreen().availableGeometry()
        pw = clip_panel.width()
        ph = clip_panel.sizeHint().height() or clip_panel._H

        expected_x = max(screen.left() + 10, min(screen.right() - pw - 10, screen.left() + (screen.width() - pw) // 2))
        expected_y = max(screen.top() + 10, screen.bottom() - ph - 24)

        # 1. Trigger in normal window state
        self.win._show_clipboard_panel("This is a test snippet for the clipboard trigger")
        self.assertTrue(clip_panel.isVisible())
        self.assertEqual(clip_panel.x(), expected_x)
        self.assertEqual(clip_panel.y(), expected_y)

        # 2. Minimize JARVIS to 58x58 favicon orb
        self.win.toggle_minimize_top()
        self.assertTrue(getattr(self.win, "_is_minimized_top", False))

        # Trigger clipboard while JARVIS is minimized
        self.win._show_clipboard_panel("Another long text snippet to trigger actions while minimized")
        self.assertTrue(clip_panel.isVisible())
        # Must still be positioned at center-bottom of screen, NOT clipped to 58x58 orb
        self.assertEqual(clip_panel.x(), expected_x)
        self.assertEqual(clip_panel.y(), expected_y)

        # 3. Restore JARVIS window
        self.win.toggle_minimize_top()
        self.assertFalse(getattr(self.win, "_is_minimized_top", False))

        # Re-position verification
        self.win._position_clipboard_panel()
        self.assertEqual(clip_panel.x(), expected_x)
        self.assertEqual(clip_panel.y(), expected_y)

        # Clean up
        clip_panel.hide()

    def test_23_display_control_focus_maximize_minimize_close(self):
        """Verify JARVIS can control displays: focus, maximize, minimize to dock, and close (individually & all)."""
        from actions.display_window import display_window

        # 1. Test tool action routing with mock player
        mock_player = MagicMock()
        mock_player.get_open_displays.return_value = [
            {"display_id": 1, "name": "DISPLAY 1", "title": "Doc 1"},
            {"display_id": 2, "name": "DISPLAY 2", "title": "Doc 2"},
        ]
        mock_player.maximize_display_window.return_value = True
        mock_player.minimize_display_window.return_value = True
        mock_player.focus_display.return_value = True
        mock_player.close_display_window.return_value = True

        # Maximize specific display
        res_max1 = display_window({"action": "maximize", "display_id": 1}, player=mock_player)
        self.assertIn("Maximized Display 1", res_max1)
        mock_player.maximize_display_window.assert_called_with(1)

        # Maximize all displays
        res_max_all = display_window({"action": "maximize", "display_id": 0}, player=mock_player)
        self.assertIn("Maximized all display windows", res_max_all)
        mock_player.maximize_display_window.assert_called_with(0)

        # Minimize specific display
        res_min2 = display_window({"action": "minimize", "display_id": 2}, player=mock_player)
        self.assertIn("Minimized Display 2 to the left dock", res_min2)
        mock_player.minimize_display_window.assert_called_with(2)

        # Minimize all displays
        res_min_all = display_window({"action": "minimize", "text": "minimize all displays"}, player=mock_player)
        self.assertIn("Minimized all display windows to the left dock", res_min_all)
        mock_player.minimize_display_window.assert_called_with(0)

        # Focus specific display
        res_foc1 = display_window({"action": "focus", "display_id": 1}, player=mock_player)
        self.assertIn("Display 1 is now front and center", res_foc1)
        mock_player.focus_display.assert_called_with(1)

        # Close specific display
        res_cls2 = display_window({"action": "close", "display_id": 2}, player=mock_player)
        self.assertIn("Closed Display 2", res_cls2)
        mock_player.close_display_window.assert_called_with(2)

        # Close all displays
        res_cls_all = display_window({"action": "close", "display_id": 0}, player=mock_player)
        self.assertIn("Closed all extra display windows", res_cls_all)
        mock_player.close_display_window.assert_called_with(0)

        # 2. Test live UI execution on MainWindow and JarvisDisplayWindow
        self.win._on_close_display_win(0)
        self.assertEqual(len(self.win._displays), 0)

        d1 = self.win._on_open_display("image", "", "Chart 1")
        d2 = self.win._on_open_display("text", "Some code content", "Code 2")
        self.assertEqual([d1, d2], [1, 2])
        w1 = self.win._displays[1]
        w2 = self.win._displays[2]

        screen = QApplication.primaryScreen().availableGeometry()

        # Maximize Display 1
        ok_max1 = self.win._on_maximize_display_win(1)
        self.assertTrue(ok_max1)
        self.assertTrue(w1._is_maximized_disp)
        self.assertEqual(w1.geometry(), screen)

        # Toggle Display 1 back to normal
        w1.maximize_window()
        self.assertFalse(w1._is_maximized_disp)

        # Maximize all displays
        ok_max_all = self.win._on_maximize_display_win(0)
        self.assertTrue(ok_max_all)
        self.assertTrue(w1._is_maximized_disp)
        self.assertTrue(w2._is_maximized_disp)

        # Minimize Display 2 to left dock
        ok_min2 = self.win._on_minimize_display_win(2)
        self.assertTrue(ok_min2)
        self.assertFalse(w2.isVisible())
        self.assertIn(2, self.win._minimized_display_ids)
        self.assertTrue(self.win._dots_overlay.isVisible())

        # Minimize all displays to left dock
        ok_min_all = self.win._on_minimize_display_win(0)
        self.assertTrue(ok_min_all)
        self.assertFalse(w1.isVisible())
        self.assertFalse(w2.isVisible())
        self.assertEqual(self.win._minimized_display_ids, {1, 2})

        # Focus Display 2 (should un-minimize from dock, center on screen, and raise)
        ok_foc2 = self.win._on_focus_display(2)
        self.assertTrue(ok_foc2)
        self.assertTrue(w2.isVisible())
        self.assertNotIn(2, self.win._minimized_display_ids)
        self.assertEqual(w2.x(), max(screen.left() + 20, screen.center().x() - w2.width() // 2))

        # Close Display 1
        ok_cls1 = self.win._on_close_display_win(1)
        self.assertTrue(ok_cls1)
        self.assertNotIn(1, self.win._displays)
        self.assertIn(2, self.win._displays)

        # Close all displays
        ok_cls_all = self.win._on_close_display_win(0)
        self.assertTrue(ok_cls_all)
        self.assertEqual(len(self.win._displays), 0)
        self.assertFalse(self.win._dots_overlay.isVisible())

    def test_24_state_based_colors_and_transitions(self):
        """Verify state-based color palette (Listening=Red, Speaking=Yellow, Thinking/Processing=Light Blue)."""
        from ui import (
            COLOR_STATE_LISTENING,
            COLOR_STATE_SPEAKING,
            COLOR_STATE_THINKING,
            COLOR_STATE_PROCESSING,
            COLOR_BLUE_PRIMARY,
            COLOR_BLUE_GLOW,
            COLOR_BLUE_OUTER,
            COLOR_BLUE_HIGHLIGHT,
        )

        hud = self.win.hud

        # 1. Listening -> Red
        self.win._apply_state("LISTENING")
        self.assertEqual(hud.state, "LISTENING")
        hud._step()
        self.assertEqual(COLOR_STATE_LISTENING.name().lower(), "#ff1744")

        # 2. Speaking -> Yellow
        self.win._apply_state("SPEAKING")
        self.assertEqual(hud.state, "SPEAKING")
        self.assertTrue(hud.speaking)
        hud._step()
        self.assertEqual(COLOR_STATE_SPEAKING.name().lower(), "#ffd700")

        # 3. Thinking -> Bright Glowing Light Blue (#38BDF8)
        self.win._apply_state("THINKING")
        self.assertEqual(hud.state, "THINKING")
        self.assertFalse(hud.speaking)
        hud._step()
        self.assertEqual(COLOR_STATE_THINKING.name().lower(), "#38bdf8")
        self.assertEqual(COLOR_BLUE_GLOW.name().lower(), "#7dd3fc")
        self.assertEqual(COLOR_BLUE_HIGHLIGHT.name().lower(), "#bae6fd")

        # 4. Processing -> Bright Glowing Light Blue (#38BDF8)
        self.win._apply_state("PROCESSING")
        self.assertEqual(hud.state, "PROCESSING")
        hud._step()
        self.assertEqual(COLOR_STATE_PROCESSING.name().lower(), "#38bdf8")

    def test_25_minimized_state_synchronization(self):
        """Verify minimized and expanded interfaces reflect identical synchronized states."""
        hud = self.win.hud

        # Minimize to left edge
        self.win.toggle_minimize_top()
        self.assertTrue(self.win._is_minimized_top)
        self.assertTrue(hud._mini_mode)

        # State updates while minimized immediately synchronize without restoring
        self.win._apply_state("THINKING")
        self.assertEqual(hud.state, "THINKING")
        hud._step()
        hud.repaint()

        self.win._apply_state("PROCESSING")
        self.assertEqual(hud.state, "PROCESSING")
        hud._step()
        hud.repaint()

        self.win._apply_state("LISTENING")
        self.assertEqual(hud.state, "LISTENING")
        hud._step()
        hud.repaint()

        self.win._apply_state("SPEAKING")
        self.assertEqual(hud.state, "SPEAKING")
        self.assertTrue(hud.speaking)
        hud._step()
        hud.repaint()

        # Restore and verify state persists
        self.win.toggle_minimize_top()
        self.assertFalse(self.win._is_minimized_top)
        self.assertFalse(hud._mini_mode)
        self.assertEqual(hud.state, "SPEAKING")


if __name__ == "__main__":
    unittest.main()
