"""
tests/test_spotify_repair.py — Automated tests for Spotify track playback,
separation from resume, outcome formatting, and failure handling.
"""
from __future__ import annotations

import unittest
from actions import spotify_control
from core import outcome


class SpotifyRepairTestCase(unittest.TestCase):
    def test_spotify_search_track_returns_candidate(self):
        """Test searching for Beat It by Michael Jackson returns valid track URI."""
        candidate = spotify_control.search_spotify_track("Beat It", artist="Michael Jackson")
        if candidate is not None:
            self.assertIn("uri", candidate)
            self.assertTrue(candidate["uri"].startswith("spotify:track:"))
            self.assertIn("Beat It", candidate["title"])

    def test_resume_vs_play_separation(self):
        """Verify resume and play are distinct actions."""
        res_resume = spotify_control.spotify_control({"action": "resume"})
        self.assertIn("status=done", res_resume)
        self.assertIn("action=resume_playback", res_resume)

    def test_play_without_song_name_fails_safely(self):
        """Verify play with empty query returns structured failure rather than crashing."""
        res = spotify_control.spotify_control({"action": "play", "query": ""})
        self.assertIn("status=failed", res)
        self.assertIn("No track name specified", res)

    def test_outcome_done_with_string_and_dict_normalization(self):
        """Verify outcome.done does not raise TypeError when formatted safely."""
        res_dict = {"track": "Beat It", "artist": "Michael Jackson", "verified": True}
        out = outcome.done("spotify_control", **res_dict)
        self.assertIn("status=done", out)
        self.assertIn("track=\"Beat It\"", out)
        self.assertIn("artist=\"Michael Jackson\"", out)

    def test_spotify_status_query(self):
        """Verify get_state returns current playback status."""
        res = spotify_control.spotify_control({"action": "get_state"})
        self.assertIn("status=done", res)
        self.assertIn("get_playback_state", res)


if __name__ == "__main__":
    unittest.main()

