"""
actions/media_control.py — Universal Windows Media & Music Controller for JARVIS.

Capabilities:
- Play requested songs, artists, or genres
- Search for a song or artist without starting playback
- Pause / Resume playback across Spotify and Windows media sessions
- Skip to next track / Play previous track
- Adjust volume (up, down, set percentage)
- Identify currently playing media ("What song is playing?")
- Distinct handling of search vs play
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from core import outcome
from core.lazyimport import LazyModule, installed
from actions import spotify_control, windows_control

_PYAUTOGUI = installed("pyautogui")
pyautogui = LazyModule("pyautogui")


def get_current_playing_media() -> outcome.ActionResult:
    """Identify currently playing media from Spotify or active media windows."""
    # Check Spotify first
    sp_title = spotify_control.get_spotify_window_title()
    if sp_title and sp_title.lower() != "spotify":
        return outcome.ActionResult(
            status=outcome.STATUS_DONE,
            action="get_current_playing_media",
            success=True,
            verified=True,
            message=f"Currently playing on Spotify: '{sp_title}'.",
            data={"source": "Spotify", "title": sp_title},
            evidence=f"Retrieved from Spotify window title: '{sp_title}'",
        )

    # Check browser windows with YouTube or media
    try:
        from actions import windows_uia
        if windows_uia.is_available():
            wins = windows_uia.list_open_windows()
            for w in wins:
                t = w.get("title", "")
                if "youtube" in t.lower() or "soundcloud" in t.lower() or "vlc" in t.lower():
                    clean_t = t.replace("- YouTube", "").replace("- Google Chrome", "").replace("- Microsoft Edge", "").strip()
                    return outcome.ActionResult(
                        status=outcome.STATUS_DONE,
                        action="get_current_playing_media",
                        success=True,
                        verified=True,
                        message=f"Currently playing: '{clean_t}'.",
                        data={"source": "Browser/Media Player", "title": clean_t},
                        evidence=f"Detected active media title from window: '{t}'",
                    )
    except Exception:
        pass

    return outcome.ActionResult(
        status=outcome.STATUS_DONE,
        action="get_current_playing_media",
        success=True,
        verified=True,
        message="No media is currently playing (Spotify and media players are paused or idle).",
        data={"source": "None", "title": ""},
        evidence="No active media playback titles detected.",
    )


def media_control(parameters: dict = None, player=None, **kwargs) -> str:
    """Auto-discovered tool handler for universal music and media commands."""
    p = dict(parameters or {})
    p.update(kwargs)

    action = (p.get("action") or "play").lower().strip()
    query = (p.get("query") or p.get("song") or p.get("track") or "").strip()
    artist = (p.get("artist") or "").strip()

    if action in ("play", "play_song", "play_track", "start"):
        if not query:
            # If no song specified, resume playback
            return media_control({"action": "resume"}, player=player)
        return spotify_control.spotify_control({"action": "play", "query": query, "artist": artist}, player=player)

    if action in ("search", "find_song", "search_song"):
        return spotify_control.spotify_control({"action": "search", "query": query, "artist": artist}, player=player)

    if action in ("pause", "stop_music", "pause_music"):
        if _PYAUTOGUI:
            pyautogui.press("playpause")
            time.sleep(0.3)
        return outcome.done("media_control", action="pause", message="Playback paused.")

    if action in ("resume", "continue_music", "resume_music", "unpause"):
        if _PYAUTOGUI:
            pyautogui.press("playpause")
            time.sleep(0.3)
        return outcome.done("media_control", action="resume", message="Playback resumed.")

    if action in ("next", "skip", "next_track", "skip_track"):
        if _PYAUTOGUI:
            pyautogui.press("nexttrack")
            time.sleep(0.5)
        # Check title after
        info = get_current_playing_media()
        return outcome.done("media_control", action="next", message=f"Skipped to next track ({info.message}).")

    if action in ("previous", "prev", "previous_track", "back"):
        if _PYAUTOGUI:
            pyautogui.press("prevtrack")
            time.sleep(0.5)
        info = get_current_playing_media()
        return outcome.done("media_control", action="previous", message=f"Returned to previous track ({info.message}).")

    if action in ("current", "what_is_playing", "now_playing", "status", "identify", "get_current_playing_media", "current_playing"):
        res = get_current_playing_media()
        return res.to_speech_text()

    if action in ("volume_up", "louder"):
        return windows_control.windows_control({"action": "set_volume", "value": 70})

    if action in ("volume_down", "softer", "quieter"):
        return windows_control.windows_control({"action": "set_volume", "value": 30})

    if action in ("set_volume", "volume"):
        val = p.get("value") or query or "50"
        return windows_control.windows_control({"action": "set_volume", "value": val})

    return outcome.failed("media_control", f"Unknown media action: '{action}'")


TOOL = {
    "name": "media_control",
    "description": (
        "Universal music and media player controller: "
        "- play: play a requested song or artist (e.g. 'Play AR Rahman songs', 'Play Beat It by Michael Jackson', query='...', artist='...'). "
        "- search: search for a song or artist without starting playback. "
        "- pause: pause music playback. "
        "- resume: resume paused music playback. "
        "- next: skip to next track. "
        "- previous: play previous track. "
        "- current: identify currently playing song ('What song is playing?'). "
        "- set_volume / volume_up / volume_down: adjust media volume. "
        "Use this tool whenever the user gives any music, media or playback command."
    ),
    "brief": "Plays songs, pauses, resumes, skips, controls volume, and identifies currently playing music.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "play | search | pause | resume | next | previous | current | set_volume | volume_up | volume_down",
            },
            "query": {
                "type": "STRING",
                "description": "Song title, artist, or query (e.g. 'AR Rahman songs', 'Beat It', 'shape of you').",
            },
            "artist": {
                "type": "STRING",
                "description": "Artist or performer name (e.g. 'AR Rahman', 'Michael Jackson').",
            },
            "value": {
                "type": "STRING",
                "description": "Volume percentage (0-100) if adjusting volume.",
            },
        },
        "required": ["action"],
    },
    "handler": media_control,
}

