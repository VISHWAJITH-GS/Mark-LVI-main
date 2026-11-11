"""
actions/display_window.py — Named floating media & text displays (images, videos, and texts).

Allows JARVIS to:
1. Open images, videos, and texts/documents in new named floating display windows (e.g. Display 1, Display 2).
2. Focus any display window to the center of the screen ("focus display 2").
3. List currently active displays.
4. Close a specific display or all displays.
"""
from __future__ import annotations

import os
import re
import urllib.request
from pathlib import Path


def _resolve_image_source(source: str, title: str) -> str:
    """Resolve image source: cleans Wikimedia URLs, verifies reachability,
    or falls back to web image search if the URL returns 400/404 or is just a query."""
    if not source:
        return ""
    if os.path.exists(source):
        return source

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    }

    # If it is a URL, test if valid
    if source.startswith(("http://", "https://")):
        test_url = source
        # Fix Wikipedia thumbnail restrictions: convert thumb url to direct commons image url
        if "upload.wikimedia.org" in test_url and "/thumb/" in test_url:
            m = re.match(r"(https?://upload\.wikimedia\.org/wikipedia/[^/]+)/thumb/([^/]+/[^/]+/[^/]+)/.*", test_url)
            if m:
                test_url = f"{m.group(1)}/{m.group(2)}"
        try:
            req = urllib.request.Request(test_url, headers=headers)
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    return test_url
        except Exception:
            pass

    # If not reachable or source was a search query, search DDGS for images
    query = title or source
    if query.startswith(("http://", "https://")):
        query = title or Path(query.split("?")[0]).stem.replace("_", " ")

    if query:
        try:
            from actions.web_search import _get_ddgs
            DDGS = _get_ddgs()
            with DDGS() as ddgs:
                for r in ddgs.images(query, max_results=3):
                    img = r.get("image")
                    if img:
                        try:
                            req = urllib.request.Request(img, headers=headers)
                            with urllib.request.urlopen(req, timeout=3) as resp:
                                if resp.status == 200:
                                    return img
                        except Exception:
                            continue
        except Exception as e:
            print(f"[display_window] DDG image search fallback error: {e}")

    return source


def display_window(parameters: dict = None, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = (params.get("action") or "show_image").strip().lower()
    source = (params.get("path_or_url") or params.get("source") or params.get("text") or params.get("content") or "").strip()
    display_id = params.get("display_id")
    title = (params.get("title") or "").strip()

    if player is None:
        return "Sir, no desktop display manager is currently available."

    # ── Focus display ────────────────────────────────────────────────────────
    if action in ("focus", "focus_display", "center"):
        if display_id is None:
            for text in (source, title):
                m = re.search(r"\b(?:display\s*)?(\d+)\b", str(text), re.I)
                if m:
                    display_id = int(m.group(1))
                    break
        if display_id is None:
            return "Please specify which display to bring to the center, sir."
        ok = player.focus_display(int(display_id))
        if ok:
            return f"Display {display_id} is now front and center, sir."
        return f"Sir, I could not find Display {display_id}."

    # ── Close display ────────────────────────────────────────────────────────
    if action in ("close", "close_display", "hide"):
        target_id = int(display_id) if display_id is not None else 0
        player.close_display_window(target_id)
        if target_id == 0:
            return "Closed all extra display windows, sir."
        return f"Closed Display {target_id}, sir."

    # ── List displays ────────────────────────────────────────────────────────
    if action in ("list", "list_displays"):
        displays = player.get_open_displays()
        if not displays:
            return "There are no extra media displays open right now, sir."
        summary = ", ".join(f"{d['name']} ({d['title']})" for d in displays)
        return f"Open displays: {summary}."

    # ── Show image, video, or text ───────────────────────────────────────────
    if action in ("show_text", "text", "display_text") or (params.get("text") and action not in ("show_image", "show_video")):
        content_type = "text"
    elif action == "show_video" or source.lower().endswith((".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv")):
        content_type = "video"
    else:
        content_type = "image"

    if not source:
        curr_file = getattr(player, "current_file", None)
        if curr_file and os.path.exists(curr_file):
            source = curr_file
        else:
            return f"Sir, please provide the content or path for the {content_type} display."

    if not title:
        if content_type == "text":
            title = Path(source).name if os.path.exists(source) else "Document"
        else:
            title = Path(source).name if os.path.exists(source) else f"{content_type.capitalize()} Media"

    # For images, pre-resolve source if URL is broken or needs search
    if content_type == "image":
        source = _resolve_image_source(source, title)

    disp_id = player.open_display(content_type, source, title)
    return f"Opened {title} in Display {disp_id}, sir."


TOOL = {
    "name": "display_window",
    "description": (
        "Controls multi-display floating windows for images, videos, and texts. "
        "Call this to show images, videos, or texts/articles/stories/code in a new named display window (e.g. Display 1, Display 2), "
        "or to focus and bring a specific display window to the front in the center of the screen "
        "(e.g. 'focus display 2'), list open displays, or close a display."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "The display action: 'show_image', 'show_video', 'show_text', 'focus', 'close', or 'list'",
                "enum": ["show_image", "show_video", "show_text", "focus", "close", "list"]
            },
            "path_or_url": {
                "type": "STRING",
                "description": "Path or URL of the image or video to display, or file path for text"
            },
            "text": {
                "type": "STRING",
                "description": "Text, article, story, code, or document content to display in the window"
            },
            "display_id": {
                "type": "INTEGER",
                "description": "The display number to focus or close (e.g. 2 for Display 2)"
            },
            "title": {
                "type": "STRING",
                "description": "Optional title for the display header"
            }
        },
        "required": ["action"]
    },
    "handler": display_window,
}
