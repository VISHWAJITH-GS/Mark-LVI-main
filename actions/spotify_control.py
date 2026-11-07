"""
actions/spotify_control.py — Native Spotify Music Search and Playback Controller.

Supports natural voice requests such as:
- "Open Spotify"
- "Play the song One Name from Jailer 2"
- "Play Anirudh's latest songs"
- "Play relaxing Tamil songs"
- "Pause Spotify" / "Resume playback"
- "Skip this song" / "Previous song"
- "Increase Spotify volume"

Execution Flow:
1. Extract search query (track, artist, album, playlist).
2. Start / focus Spotify desktop app or web player via Spotify URI protocol (e.g. spotify:search:<query>).
3. Automate playback via media keys / Windows UI Automation / Spotify URI execution.
4. Verify playback status by checking process audio session / window title / endpoint state.
5. Provide structured outcome report.
"""
from __future__ import annotations

import os
import platform
import subprocess
import time
import urllib.parse
from typing import Any, Dict, Optional

from core import appidentity, outcome
from core.lazyimport import LazyModule, installed

_PYAUTOGUI = installed("pyautogui")
pyautogui = LazyModule("pyautogui")

_IS_WINDOWS = platform.system() == "Windows"
_WIN_HIDE = {"creationflags": subprocess.CREATE_NO_WINDOW} if _IS_WINDOWS else {}


def is_spotify_running() -> bool:
    """Check if Spotify desktop application process is running."""
    import psutil
    for p in psutil.process_iter(["name"]):
        try:
            if "spotify" in p.info["name"].lower():
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def get_spotify_window_title() -> str:
    """Return Spotify main window title, which shows the current song or 'Spotify'."""
    if not _IS_WINDOWS:
        return ""
    try:
        import win32gui
        spotify_titles = []
        def _enum_win(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                cls = win32gui.GetClassName(hwnd)
                if "Spotify" in cls or "Chrome_WidgetWin" in cls:
                    if title and title != "Angle" and title != "Default IME":
                        spotify_titles.append(title)
        win32gui.EnumWindows(_enum_win, None)
        for t in spotify_titles:
            if t != "Spotify":
                return t
        return spotify_titles[0] if spotify_titles else ""
    except Exception:
        return ""


def launch_spotify() -> bool:
    """Launch or focus Spotify application."""
    if is_spotify_running():
        # Focus existing Spotify window
        try:
            from actions import windows_uia
            win = windows_uia.find_window_by_name("Spotify")
            if win:
                win.set_focus()
                return True
        except Exception:
            pass

    # Launch via URI or shell / start command
    try:
        if _IS_WINDOWS:
            # Using start command handles URI protocol even when os.startfile gets access denied in sandbox
            subprocess.run(["cmd.exe", "/c", "start", "spotify:"], **_WIN_HIDE, timeout=5)
            time.sleep(1.5)
            return True
        else:
            subprocess.Popen(["spotify"], **_WIN_HIDE)
            time.sleep(1.5)
            return True
    except Exception as e:
        print(f"[Spotify] Launch error: {e}")
        return False


def play_search_query(query: str) -> dict:
    """
    Search and play music on Spotify.
    Flow:
    1. Launch / focus Spotify.
    2. Open search URI: spotify:search:<encoded_query>
    3. Send Play hotkey (Enter or Media Play/Pause) to trigger top result.
    4. Verify playback.
    """
    clean_query = query.strip()
    if not clean_query:
        return outcome.failed("spotify_control", "no song or query specified")

    # 1. Launch Spotify
    launch_spotify()
    time.sleep(1.0)

    # 2. Open search URI
    encoded = urllib.parse.quote(clean_query)
    uri = f"spotify:search:{encoded}"
    try:
        if _IS_WINDOWS:
            subprocess.run(["cmd.exe", "/c", "start", uri], **_WIN_HIDE, timeout=5)
        else:
            subprocess.Popen(["xdg-open", uri])
    except Exception as e:
        print(f"[Spotify] URI search error: {e}")

    # Wait for search results to load
    time.sleep(2.0)

    # 3. Focus and trigger playback
    try:
        if _IS_WINDOWS:
            from actions import windows_uia
            win = windows_uia.find_window_by_name("Spotify")
            if win:
                win.set_focus()
                time.sleep(0.3)
                # In Spotify desktop, pressing Enter or Space on top result starts playing
                pyautogui.press("enter")
                time.sleep(0.5)
                # If still not playing, try Space or Media Play
                pyautogui.press("playpause")
            else:
                pyautogui.press("playpause")
        else:
            pyautogui.press("playpause")
    except Exception as e:
        print(f"[Spotify] Keypress error: {e}")

    time.sleep(1.0)
    title_after = get_spotify_window_title()
    is_playing = bool(title_after and title_after != "Spotify")

    return {
        "success": True,
        "action": "play_spotify",
        "query": clean_query,
        "current_track": title_after or clean_query,
        "verified": is_playing,
        "message": f"Playing '{title_after or clean_query}' on Spotify.",
    }


def playback_control(action: str) -> dict:
    """
    Media controls for Spotify:
    'pause', 'resume', 'play_pause', 'next', 'previous', 'volume_up', 'volume_down'
    """
    act = action.lower().strip()
    if act in ("pause", "resume", "play", "play_pause", "toggle"):
        pyautogui.press("playpause")
        time.sleep(0.5)
        title = get_spotify_window_title()
        return {
            "success": True,
            "action": act,
            "current_track": title,
            "message": f"Spotify playback toggled ({act}).",
        }
    elif act in ("next", "next_track", "skip"):
        pyautogui.press("nexttrack")
        time.sleep(0.8)
        title = get_spotify_window_title()
        return {
            "success": True,
            "action": "next_track",
            "current_track": title,
            "message": f"Skipped to next track: {title or 'next song'}.",
        }
    elif act in ("prev", "previous", "previous_track", "back"):
        pyautogui.press("prevtrack")
        time.sleep(0.8)
        title = get_spotify_window_title()
        return {
            "success": True,
            "action": "previous_track",
            "current_track": title,
            "message": f"Returned to previous track: {title or 'previous song'}.",
        }
    elif act in ("volume_up", "louder"):
        for _ in range(3):
            pyautogui.hotkey("ctrl", "up")
        return {"success": True, "action": "volume_up", "message": "Spotify volume increased."}
    elif act in ("volume_down", "quieter"):
        for _ in range(3):
            pyautogui.hotkey("ctrl", "down")
        return {"success": True, "action": "volume_down", "message": "Spotify volume decreased."}

    return {"success": False, "action": act, "message": f"Unknown playback action: {act}"}


def spotify_control(parameters: dict = None, player=None, **kwargs) -> str:
    """Auto-discovered tool handler for Spotify."""
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    query = params.get("query") or params.get("song") or params.get("music") or params.get("search", "")

    if not action:
        if query:
            action = "play"
        else:
            action = "open"

    if action in ("open", "launch"):
        launch_spotify()
        return outcome.done("spotify_control", message="Spotify opened.")

    if action in ("play", "search", "play_search"):
        res = play_search_query(query)
        return outcome.done("spotify_control", **res)

    res = playback_control(action)
    return outcome.done("spotify_control", **res)


TOOL = {
    "name": "spotify_control",
    "description": (
        "Controls Spotify desktop music player: search and play songs, albums, artists, "
        "playlists, resume, pause, skip track, previous track, and adjust volume. "
        "Use when user asks to play music (e.g. 'Play One Name from Jailer 2', 'Play Anirudh songs', "
        "'Pause music', 'Skip this song')."
    ),
    "brief": "Search and play music, pause, skip tracks, and control playback in Spotify.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "The action: play | pause | resume | next | previous | open | volume_up | volume_down",
            },
            "query": {
                "type": "STRING",
                "description": "Song name, artist, album, language, or playlist to search and play.",
            },
        },
        "required": [],
    },
    "handler": spotify_control,
}

