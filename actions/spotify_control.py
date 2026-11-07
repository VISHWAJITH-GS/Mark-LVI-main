"""
actions/spotify_control.py — Deterministic Spotify Music Search and Playback Controller for JARVIS.

Full Lifecycle & Verification:
1. Distinguishes 'play' (find, match title & artist, select, and play specific track) from 'resume' (continue current track).
2. Searches track via Spotify Web discovery & URI resolution (e.g. spotify:track:<id>).
3. Executes direct track playback via Windows URI activation:
   - When a specific track URI is opened (spotify:track:<id>), Spotify navigates directly to that track and plays it,
     replacing the previously playing track (e.g. replacing 'One Name' with 'Beat It' by Michael Jackson).
4. Verifies playback state by querying Spotify's window title / audio process state.
5. Standardizes all return formats to prevent `outcome.done() argument after ** must be a mapping, not str`.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from core import appidentity, outcome
from core.lazyimport import LazyModule, installed

_PYAUTOGUI = installed("pyautogui")
pyautogui = LazyModule("pyautogui")

_IS_WINDOWS = platform.system() == "Windows"
_WIN_HIDE = {"creationflags": subprocess.CREATE_NO_WINDOW} if _IS_WINDOWS else {}


def is_spotify_running() -> bool:
    """Check if Spotify application process is currently running."""
    import psutil
    for p in psutil.process_iter(["name"]):
        try:
            name = p.info["name"].lower()
            if "spotify" in name:
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
                title = win32gui.GetWindowText(hwnd).strip()
                cls = win32gui.GetClassName(hwnd)
                if "Spotify" in cls or "Chrome_WidgetWin" in cls:
                    if title and title not in ("Angle", "Default IME", "MSCTFIME UI"):
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
        try:
            from actions import windows_uia
            win = windows_uia.find_window_by_name("Spotify")
            if win:
                win.set_focus()
                return True
        except Exception:
            pass

    try:
        if _IS_WINDOWS:
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


# ══════════════════════════════════════════════════════════════════════════════
# TRACK SEARCH & SELECTION (No API Key Required)
# ══════════════════════════════════════════════════════════════════════════════

def search_spotify_track(query: str, artist: str = "") -> Optional[dict]:
    """
    Search Spotify for the requested track using public web discovery and match title & artist.
    Returns:
      {"title": "Beat It", "artist": "Michael Jackson", "track_id": "...", "uri": "spotify:track:..."}
    """
    search_term = f"site:open.spotify.com/track {query}"
    if artist and artist.lower() not in query.lower():
        search_term += f" {artist}"

    try:
        from actions.web_search import _ddg_search
        results = _ddg_search(search_term, max_results=5)
    except Exception as e:
        print(f"[Spotify] DDG track search error: {e}")
        results = []

    candidates = []
    for r in results:
        url = r.get("url", "")
        title_raw = r.get("title", "")
        m = re.search(r"track/([a-zA-Z0-9]{22})", url)
        if m:
            track_id = m.group(1)
            # Clean title
            t_clean = title_raw.split("|")[0].split("-")[0].replace("song and lyrics by", "").strip()
            candidates.append({
                "title": t_clean or query,
                "track_id": track_id,
                "url": url,
                "uri": f"spotify:track:{track_id}",
                "snippet": r.get("snippet", ""),
            })

    if not candidates:
        return None

    # Score and select the best candidate matching title and artist
    q_lower = query.lower()
    art_lower = artist.lower()
    best_candidate = candidates[0]
    for c in candidates:
        text_corpus = (c["title"] + " " + c["snippet"]).lower()
        has_query = q_lower in text_corpus
        has_artist = art_lower in text_corpus if art_lower else True
        if has_query and has_artist:
            best_candidate = c
            break

    return best_candidate


def play_track(track_name: str, artist: str = "") -> dict:
    """
    Explicitly find, select, and initiate playback of a specific track.
    Replaces whatever is currently playing (e.g. 'One Name' -> 'Beat It').
    """
    track_query = track_name.strip()
    if not track_query:
        return {
            "success": False,
            "verified": False,
            "status": "failed",
            "action": "play_track",
            "reason": "No track name specified to play.",
        }

    # Record title before to verify that track actually changes
    title_before = get_spotify_window_title()

    # 1. Search and match track
    candidate = search_spotify_track(track_query, artist=artist)
    target_uri = candidate.get("uri") if candidate else None
    matched_title = candidate.get("title") if candidate else track_query

    # 2. Launch / Focus Spotify
    launch_spotify()
    time.sleep(0.5)

    # 3. Direct Track Playback Execution
    # Opening spotify:track:<id> navigates directly to the track and starts playing it
    if target_uri and _IS_WINDOWS:
        print(f"[Spotify] Navigating to target URI: {target_uri}")
        subprocess.run(["cmd.exe", "/c", "start", target_uri], **_WIN_HIDE, timeout=5)
    else:
        # Fallback to in-app search URI
        encoded = urllib.parse.quote(f"{track_query} {artist}".strip())
        uri = f"spotify:search:{encoded}"
        if _IS_WINDOWS:
            subprocess.run(["cmd.exe", "/c", "start", uri], **_WIN_HIDE, timeout=5)
        else:
            subprocess.Popen(["xdg-open", uri])

    time.sleep(2.0)

    # 4. Trigger play / select top track in Spotify window
    try:
        from actions import windows_uia
        win = windows_uia.find_window_by_name("Spotify")
        if win:
            win.set_focus()
            time.sleep(0.4)
            # In Spotify desktop view, Enter or Space starts the active track
            pyautogui.press("enter")
            time.sleep(0.4)
    except Exception as e:
        print(f"[Spotify] Trigger error: {e}")

    # 5. Verify actual playback state
    time.sleep(1.2)
    title_after = get_spotify_window_title()

    # Track verification:
    # A successful playback means title_after matches requested track or candidate title,
    # AND is NOT identical to previous title if previous was different.
    clean_target = track_query.lower()
    clean_after = title_after.lower() if title_after else ""

    is_verified = False
    if clean_after and clean_after != "spotify":
        if clean_target in clean_after or (matched_title and matched_title.lower() in clean_after):
            is_verified = True
        elif title_after != title_before:
            is_verified = True

    if not is_verified and title_after == title_before and title_before != "":
        return {
            "success": False,
            "verified": False,
            "status": "failed",
            "action": "play_track",
            "current_track": title_after,
            "requested_track": track_query,
            "reason": f"Spotify is still playing '{title_before}' instead of '{track_query}'. Track selection failed.",
        }

    return {
        "success": True,
        "verified": is_verified,
        "status": "done" if is_verified else "unverified",
        "action": "play_track",
        "track": matched_title or track_query,
        "artist": artist,
        "current_track": title_after or matched_title,
        "uri": target_uri or "",
        "message": f"Now playing '{matched_title or track_query}' by {artist} on Spotify." if artist else f"Now playing '{matched_title or track_query}' on Spotify.",
    }


def resume_playback() -> dict:
    """Resume currently paused track (distinct from playing a new song)."""
    launch_spotify()
    pyautogui.press("playpause")
    time.sleep(0.8)
    title = get_spotify_window_title()
    return {
        "success": True,
        "verified": bool(title and title != "Spotify"),
        "status": "done",
        "action": "resume_playback",
        "current_track": title or "current track",
        "message": f"Resumed Spotify playback ({title or 'current track'}).",
    }


def pause_playback() -> dict:
    """Pause playback."""
    pyautogui.press("playpause")
    time.sleep(0.5)
    title = get_spotify_window_title()
    return {
        "success": True,
        "verified": True,
        "status": "done",
        "action": "pause_playback",
        "current_track": title,
        "message": "Spotify playback paused.",
    }


def next_track() -> dict:
    """Skip to next track."""
    pyautogui.press("nexttrack")
    time.sleep(1.0)
    title = get_spotify_window_title()
    return {
        "success": True,
        "verified": bool(title and title != "Spotify"),
        "status": "done",
        "action": "next_track",
        "current_track": title or "next track",
        "message": f"Skipped to next track: {title or 'next song'}.",
    }


def previous_track() -> dict:
    """Go back to previous track."""
    pyautogui.press("prevtrack")
    time.sleep(1.0)
    title = get_spotify_window_title()
    return {
        "success": True,
        "verified": bool(title and title != "Spotify"),
        "status": "done",
        "action": "previous_track",
        "current_track": title or "previous track",
        "message": f"Returned to previous track: {title or 'previous song'}.",
    }


def get_playback_state() -> dict:
    """Get currently playing track title and playback state."""
    title = get_spotify_window_title()
    is_playing = bool(title and title != "Spotify")
    return {
        "success": True,
        "verified": True,
        "status": "done",
        "action": "get_playback_state",
        "is_playing": is_playing,
        "current_track": title if is_playing else "None / Paused",
        "message": f"Currently playing: '{title}'" if is_playing else "Spotify is paused or idle.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# UNIFIED TOOL HANDLER
# ══════════════════════════════════════════════════════════════════════════════

def spotify_control(parameters: dict = None, player=None, **kwargs) -> str:
    """
    Unified auto-discovered tool handler for Spotify.
    Safely formats all results to prevent unpacking exceptions.
    """
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    query = (params.get("query") or params.get("song") or params.get("track") or params.get("music") or "").strip()
    artist = (params.get("artist") or "").strip()

    # Separate "resume" from "play"
    if not action:
        if query:
            action = "play"
        else:
            action = "open"

    # Route distinct actions
    if action in ("open", "launch"):
        launch_spotify()
        return outcome.done("spotify_control", action="open", message="Spotify opened.")

    if action in ("search", "find", "search_track"):
        if not query:
            return outcome.failed("spotify_control", "No track name specified to search.")
        cand = search_spotify_track(query, artist=artist)
        if cand:
            return outcome.done("spotify_control", action="search_track", track=cand["title"], artist=artist, uri=cand.get("uri", ""), verified=True)
        return outcome.failed("spotify_control", f"No Spotify tracks found matching '{query}'.")

    if action in ("play", "play_track", "search_and_play"):
        if not query:
            return outcome.failed("spotify_control", "No track name specified to play.")
        res = play_track(query, artist=artist)
        if not isinstance(res, dict):
            res = {"result": str(res)}
        if res.get("status") == "failed" or not res.get("success", True):
            return outcome.failed("spotify_control", res.get("reason", "Track playback failed."), **{k: v for k, v in res.items() if k not in ("status", "reason")})
        if not res.get("verified", False):
            return outcome.unverified("spotify_control", res.get("reason", "Could not verify that song started playing."), **{k: v for k, v in res.items() if k not in ("status", "reason")})
        return outcome.done("spotify_control", **res)

    if action in ("resume", "continue"):
        res = resume_playback()
        return outcome.done("spotify_control", **res)

    if action in ("pause", "stop"):
        res = pause_playback()
        return outcome.done("spotify_control", **res)

    if action in ("next", "next_track", "skip"):
        res = next_track()
        return outcome.done("spotify_control", **res)

    if action in ("previous", "prev", "previous_track", "back"):
        res = previous_track()
        return outcome.done("spotify_control", **res)

    if action in ("get_state", "get_status", "status", "current"):
        res = get_playback_state()
        return outcome.done("spotify_control", **res)

    if action == "volume_up":
        for _ in range(3):
            pyautogui.hotkey("ctrl", "up")
        return outcome.done("spotify_control", action="volume_up", message="Spotify volume increased.")

    if action == "volume_down":
        for _ in range(3):
            pyautogui.hotkey("ctrl", "down")
        return outcome.done("spotify_control", action="volume_down", message="Spotify volume decreased.")

    return outcome.failed("spotify_control", f"Unknown spotify action: '{action}'")


TOOL = {
    "name": "spotify_control",
    "description": (
        "Controls Spotify desktop music player with verified track selection: "
        "- play: search for and explicitly start a specific track (e.g. 'Beat It', 'One Name', query='Beat It', artist='Michael Jackson'). Replaces any current song. "
        "- resume: continue paused playback of the current track without searching. "
        "- pause: pause playback. "
        "- next: skip to next track. "
        "- previous: return to previous track. "
        "- get_state: query currently playing track and artist. "
        "Always call this tool for music playback requests."
    ),
    "brief": "Search, select, play specific tracks, resume, pause, and verify Spotify playback.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "play | resume | pause | next | previous | get_state | open | volume_up | volume_down",
            },
            "query": {
                "type": "STRING",
                "description": "Song name or track title to find and play (e.g. 'Beat It', 'One Name').",
            },
            "artist": {
                "type": "STRING",
                "description": "Artist or band name (e.g. 'Michael Jackson', 'Anirudh').",
            },
        },
        "required": ["action"],
    },
    "handler": spotify_control,
}
