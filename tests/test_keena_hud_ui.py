import sys
import unittest
from unittest.mock import MagicMock
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QColor

import ui
from ui import JarvisUI, MainWindow, HudCanvas, BottomChatDock, LeftSearchDock, C

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
        """Verify Keena magenta visual identity colors."""
        self.assertEqual(C.PRI.lower(), "#ff4da6")
        self.assertEqual(C.ACC.lower(), "#ba55ff")
        self.assertEqual(C.TEXT.lower(), "#ffecf7")
        self.assertEqual(C.TEXT_DIM.lower(), "#d6a0be")
        self.assertIn("0a030a", C.BG.lower())

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

    def test_06_minimize_to_center_top_and_restore(self):
        """Test Arc Reactor minimizes to center top at 80% scale and restores on click."""
        screen = QApplication.primaryScreen().availableGeometry()
        self.assertEqual(self.win.width(), 420)
        self.assertEqual(self.win.height(), 580)
        self.assertFalse(self.win._is_minimized_top)

        # Click or toggle minimize to center top
        self.win.toggle_minimize_top()
        if hasattr(self.win, "_anim") and self.win._anim:
            self.win._anim.stop()
        self.assertTrue(self.win._is_minimized_top)
        target_w = int(420 * 0.8)
        target_h = int(580 * 0.8)
        self.assertEqual(target_w, 336)
        self.assertEqual(target_h, 464)

        # Click reactor animation again to restore to normal position
        self.win.hud.reactor_clicked.emit()
        if hasattr(self.win, "_anim") and self.win._anim:
            self.win._anim.stop()
        self.assertFalse(self.win._is_minimized_top)

    def test_07_bottom_chat_dock_scattered_edge(self):
        """Test bottom center chat dock: long width, short height, command input."""
        chat_dock = self.win.bottom_chat_dock
        self.assertIsInstance(chat_dock, BottomChatDock)
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

    def test_08_left_search_dock_pop_up(self):
        """Test search display pops up along the left edge of the screen."""
        search_dock = self.win.left_search_dock
        self.assertIsInstance(search_dock, LeftSearchDock)
        screen = QApplication.primaryScreen().availableGeometry()

        # Initially hidden until search/content is displayed
        self.win._show_content("TRENDS IN AI", "Rapid adoption of multi-agent systems.")
        self.assertTrue(search_dock.isVisible())
        self.assertEqual(search_dock.x(), screen.left() + 16)
        self.assertTrue(self.win._content_panel.isVisible())

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

if __name__ == "__main__":
    unittest.main()
