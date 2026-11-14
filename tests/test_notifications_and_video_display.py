"""
tests/test_notifications_and_video_display.py — Verification for Video Display Removal & Notification Center.

Tests:
1. Video playback in HUD display is removed: video requests open externally in browser/media player.
2. Notification reader parses notifications without JSON syntax errors (handling leading booleans).
3. Notification reader extracts recent messages from messaging apps (e.g. WhatsApp) and system notifications.
"""
import unittest
from unittest.mock import MagicMock, patch

from actions.notification_reader import DesktopNotification, NotificationReader, notification_center
from actions.video_player import video_player


class TestNotificationsAndVideoDisplay(unittest.TestCase):

    @patch("webbrowser.open")
    def test_video_opens_in_browser_not_in_display(self, mock_web_open):
        """Video player must NOT invoke player.show_video to embed video in HUD display."""
        mock_player = MagicMock()

        res = video_player({"action": "play", "source": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}, player=mock_player)
        self.assertIn("Opened", res)
        self.assertIn("browser", res)

        # player.show_video must NOT have been called
        self.assertFalse(mock_player.show_video.called)
        # Browser must have been opened
        mock_web_open.assert_called_once()

    @patch("actions.notification_reader.NotificationReader._run_ps")
    def test_notification_reader_handles_leading_boolean_stdout(self, mock_run_ps):
        """Notification reader must cleanly parse JSON despite PowerShell method output (e.g. True\\nTrue\\n[...])."""
        mock_stdout = """True
True
[
    {
        "Id": 1466,
        "App": "WhatsApp",
        "Title": "Optimization Techniques (CAT-2)",
        "Body": "Assignment due tomorrow"
    },
    {
        "Id": 1467,
        "App": "WhatsApp",
        "Title": "MATHS NOTES",
        "Body": "Check PDF"
    }
]"""
        mock_run_ps.return_value = (0, mock_stdout, "")

        reader = NotificationReader()
        ok, notifs, err = reader._query_winrt_notifications()

        self.assertTrue(ok)
        self.assertEqual(len(notifs), 2)
        # Newest first
        self.assertEqual(notifs[0].title, "MATHS NOTES")
        self.assertEqual(notifs[1].title, "Optimization Techniques (CAT-2)")

    @patch("actions.notification_reader.get_notification_reader")
    def test_notification_center_formats_speech_and_updates_ui(self, mock_get_reader):
        """notification_center must format speech and update player UI logs and content panel."""
        mock_reader = MagicMock()
        mock_reader.get_notifications.return_value = MagicMock(
            status="done",
            success=True,
            verified=True,
            message="You have 2 notifications:\n• From WhatsApp: MATHS NOTES\n• From WhatsApp: Optimization Techniques",
            to_speech_text=lambda: "You have 2 notifications: From WhatsApp: MATHS NOTES and Optimization Techniques",
            data={"count": 2},
        )
        mock_get_reader.return_value = mock_reader

        mock_player = MagicMock()
        res = notification_center({"action": "read"}, player=mock_player)

        self.assertIn("UNTRUSTED", res)
        self.assertIn("WhatsApp", res)
        self.assertTrue(mock_player.write_log.called)
        self.assertTrue(mock_player.show_content.called)


if __name__ == "__main__":
    unittest.main()
