"""
actions/browser_session.py — Chrome DevTools Protocol (CDP) Browser Session Manager.

Attaches to an already-running Microsoft Edge instance with remote debugging enabled
(http://localhost:9222 by default or EDGE_CDP_URL).
Preserves existing authenticated sessions (Gmail, WhatsApp Web, Spotify, ChatGPT, Claude)
without re-logging in or launching isolated/unauthenticated browser profiles.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("browser_session")

DEFAULT_CDP_URL = "http://127.0.0.1:9222"


class CDPConnectionError(RuntimeError):
    """Raised when Microsoft Edge CDP endpoint is unreachable."""


@dataclass
class TabInfo:
    index: int
    title: str
    url: str
    target_id: str = ""
    is_active: bool = False


@dataclass
class FrameInfo:
    name: str
    url: str
    origin: str
    is_main: bool


@dataclass
class BrowserSessionState:
    is_connected: bool
    cdp_url: str
    active_title: str
    active_url: str
    tab_count: int
    browser_type: str = "edge_cdp"
    profile_dir: str = "live_edge_profile"
    console_errors_count: int = 0
    network_errors_count: int = 0
    tabs: List[TabInfo] = field(default_factory=list)
    error: Optional[str] = None


class BrowserSessionManager:
    """
    Authoritative CDP Browser Session Controller.
    Attaches to live Microsoft Edge session via Chrome DevTools Protocol.
    """
    _instance: Optional[BrowserSessionManager] = None
    _singleton_lock = threading.Lock()

    def __init__(self, cdp_url: Optional[str] = None):
        self.cdp_url = cdp_url or os.environ.get("EDGE_CDP_URL", DEFAULT_CDP_URL)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._loop_ready = threading.Event()
        self._lock = threading.Lock()

        # Playwright CDP handles
        self._pw = None
        self._browser = None
        self._context = None
        self._active_page = None
        self._cdp_session = None

        self._start_event_loop()

    @classmethod
    def get_instance(cls, cdp_url: Optional[str] = None) -> BrowserSessionManager:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls(cdp_url)
            return cls._instance

    # ── Background Asyncio Loop ────────────────────────────────────────────────

    def _start_event_loop(self):
        with self._lock:
            if self._thread and self._thread.is_alive() and self._loop:
                return
            self._loop_ready.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="JarvisEdgeCDPLoop",
            )
            self._thread.start()
            if not self._loop_ready.wait(timeout=5.0):
                raise RuntimeError("Failed to initialize background CDP event loop")

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_forever()

    def run_coroutine(self, coro, timeout: float = 30.0):
        """Thread-safe synchronous wrapper for CDP coroutines."""
        if not self._loop or not self._loop.is_running():
            self._start_event_loop()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ── Graceful Connection Guard ─────────────────────────────────────────────

    def check_connection(self, timeout: float = 2.0) -> Tuple[bool, str]:
        """
        Pings /json/version on the CDP endpoint to verify Edge is running with debugging enabled.
        Tries both configured URL and 127.0.0.1 fallback to prevent Windows IPv6 localhost delays.
        """
        urls_to_try = [self.cdp_url]
        if "localhost" in self.cdp_url:
            urls_to_try.append(self.cdp_url.replace("localhost", "127.0.0.1"))
        elif "127.0.0.1" in self.cdp_url:
            urls_to_try.append(self.cdp_url.replace("127.0.0.1", "localhost"))

        for url in urls_to_try:
            target = f"{url.rstrip('/')}/json/version"
            try:
                req = urllib.request.Request(target, headers={"User-Agent": "JARVIS-BrowserEngine"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        browser_name = data.get("Browser", "Chromium/Edge")
                        self.cdp_url = url
                        return True, f"Connected to {browser_name}"
            except Exception:
                continue

        err_msg = (
            f"[CDP Connection Error] Cannot connect to Microsoft Edge at {self.cdp_url}.\n"
            f"Please ensure Microsoft Edge is running with remote debugging enabled:\n"
            f"  1. Start Edge from Command Prompt / Run:\n"
            f"     msedge.exe --remote-debugging-port=9222\n"
            f"  2. Or navigate to edge://inspect/#remote-debugging in Edge and enable it.\n"
            f"  3. Set EDGE_CDP_URL environment variable if using a different port."
        )
        return False, err_msg

    # ── Live Edge / Chrome Attachment ──────────────────────────────────────────

    def _try_auto_launch(self) -> bool:
        """Attempt to auto-launch Chrome or Edge with remote debugging if not already running."""
        import subprocess
        candidates = [
            (r"C:\Program Files\Google\Chrome\Application\chrome.exe", os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\JarvisProfile")),
            (r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\JarvisProfile")),
            (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data\JarvisProfile")),
            (r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data\JarvisProfile")),
        ]
        for exe, profile in candidates:
            if os.path.exists(exe):
                try:
                    os.makedirs(profile, exist_ok=True)
                    cmd = [
                        exe,
                        "--remote-debugging-port=9222",
                        f"--user-data-dir={profile}",
                        "--remote-allow-origins=*",
                    ]
                    flags = subprocess.DETACHED_PROCESS if os.name == "nt" else 0
                    subprocess.Popen(cmd, creationflags=flags)
                    for _ in range(8):
                        time.sleep(0.5)
                        ok, _ = self.check_connection(timeout=0.5)
                        if ok:
                            logger.info(f"Auto-launched browser {exe} with remote debugging.")
                            return True
                except Exception as e:
                    logger.debug(f"Auto-launch attempt failed for {exe}: {e}")
        return False

    async def _ensure_attached(self):
        """
        Connects over CDP to the running Chrome / Edge instance.
        Attaches strictly to browser.contexts[0]. NEVER calls .launch() or creates a fresh context.
        """
        ok, err = self.check_connection(timeout=2.0)
        if not ok:
            if self._try_auto_launch():
                ok = True
        if not ok:
            raise CDPConnectionError(err)

        if self._browser and self._browser.is_connected() and self._context:
            # Check if active page is still alive
            if self._active_page and not self._active_page.is_closed():
                return
            # If active page closed, fall back to another existing tab
            pages = self._context.pages
            if pages:
                self._active_page = pages[0]
                self._cdp_session = None
                return

        # Start Playwright if needed
        if self._pw is None:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()

        # Connect over CDP
        self._browser = await self._pw.chromium.connect_over_cdp(self.cdp_url)

        # Attach strictly to browser.contexts[0]
        contexts = self._browser.contexts
        if not contexts:
            raise CDPConnectionError("Edge CDP connection established but no existing browser contexts found.")

        self._context = contexts[0]

        # Enumerate pages
        pages = self._context.pages
        if not pages:
            # If no open page exists in context, open one
            self._active_page = await self._context.new_page()
        else:
            self._active_page = pages[0]

        self._cdp_session = None
        logger.info(f"Attached to live Microsoft Edge context: {len(pages)} tab(s) detected.")

    async def _get_cdp_session(self):
        """Returns or creates a direct CDP session for the active page."""
        await self._ensure_attached()
        if self._cdp_session is None or self._active_page.is_closed():
            self._cdp_session = await self._context.new_cdp_session(self._active_page)
        return self._cdp_session

    def get_cdp_session(self):
        return self.run_coroutine(self._get_cdp_session())

    # ── Multi-Tab & Frame Management ───────────────────────────────────────────

    async def _list_tabs(self) -> List[TabInfo]:
        await self._ensure_attached()
        tabs: List[TabInfo] = []
        pages = self._context.pages
        for i, page in enumerate(pages):
            try:
                title = await page.title()
            except Exception:
                title = "(unknown)"
            url = page.url
            is_active = (page == self._active_page)
            tabs.append(TabInfo(index=i, title=title, url=url, is_active=is_active))
        return tabs

    def list_tabs(self) -> List[TabInfo]:
        return self.run_coroutine(self._list_tabs())

    async def _switch_tab(self, tab_index: int) -> TabInfo:
        await self._ensure_attached()
        pages = self._context.pages
        if tab_index < 0 or tab_index >= len(pages):
            raise IndexError(f"Tab index {tab_index} out of range (0..{len(pages)-1})")
        target_page = pages[tab_index]
        await target_page.bring_to_front()
        self._active_page = target_page
        self._cdp_session = None  # refresh CDP session for new tab
        title = await target_page.title()
        return TabInfo(index=tab_index, title=title, url=target_page.url, is_active=True)

    def switch_tab(self, tab_index: int) -> TabInfo:
        return self.run_coroutine(self._switch_tab(tab_index))

    async def _open_tab(self, url: str) -> TabInfo:
        await self._ensure_attached()
        new_page = await self._context.new_page()
        clean_url = url.strip()
        if clean_url:
            if "://" not in clean_url:
                clean_url = "https://" + clean_url
            await new_page.goto(clean_url, wait_until="domcontentloaded", timeout=30000)
        self._active_page = new_page
        self._cdp_session = None
        pages = self._context.pages
        idx = pages.index(new_page) if new_page in pages else len(pages) - 1
        title = await new_page.title()
        return TabInfo(index=idx, title=title, url=new_page.url, is_active=True)

    def open_tab(self, url: str) -> TabInfo:
        return self.run_coroutine(self._open_tab(url))

    async def _close_tab(self, tab_index: Optional[int] = None) -> str:
        await self._ensure_attached()
        pages = self._context.pages
        if not pages:
            return "No tabs to close"
        idx = tab_index if tab_index is not None else (pages.index(self._active_page) if self._active_page in pages else 0)
        if idx < 0 or idx >= len(pages):
            raise IndexError(f"Tab index {idx} out of range")
        closing_page = pages[idx]
        await closing_page.close()
        remaining = self._context.pages
        if remaining:
            self._active_page = remaining[min(idx, len(remaining) - 1)]
            self._cdp_session = None
        else:
            self._active_page = None
            self._cdp_session = None
        return f"Closed tab {idx}. {len(remaining)} tab(s) remaining."

    def close_tab(self, tab_index: Optional[int] = None) -> str:
        return self.run_coroutine(self._close_tab(tab_index))

    async def _get_all_frames(self) -> List[FrameInfo]:
        await self._ensure_attached()
        if not self._active_page:
            return []
        frames_out: List[FrameInfo] = []
        for f in self._active_page.frames:
            frames_out.append(FrameInfo(
                name=f.name or "(unnamed)",
                url=f.url,
                origin=f.url.split("/")[2] if "://" in f.url else "",
                is_main=(f == self._active_page.main_frame),
            ))
        return frames_out

    def get_all_frames(self) -> List[FrameInfo]:
        return self.run_coroutine(self._get_all_frames())

    # ── State Inspection ───────────────────────────────────────────────────────

    def get_session_state(self) -> BrowserSessionState:
        try:
            ok, msg = self.check_connection(timeout=1.0)
            if not ok:
                return BrowserSessionState(
                    is_connected=False,
                    cdp_url=self.cdp_url,
                    active_title="",
                    active_url="",
                    tab_count=0,
                    error=msg,
                )
            tabs = self.list_tabs()
            active_tab = next((t for t in tabs if t.is_active), tabs[0] if tabs else None)
            return BrowserSessionState(
                is_connected=True,
                cdp_url=self.cdp_url,
                active_title=active_tab.title if active_tab else "",
                active_url=active_tab.url if active_tab else "",
                tab_count=len(tabs),
                tabs=tabs,
            )
        except Exception as e:
            return BrowserSessionState(
                is_connected=False,
                cdp_url=self.cdp_url,
                active_title="",
                active_url="",
                tab_count=0,
                error=str(e),
            )

    @property
    def active_page(self):
        """Active Playwright page attached to live Edge."""
        return self._active_page


def get_browser_session_manager(cdp_url: Optional[str] = None) -> BrowserSessionManager:
    return BrowserSessionManager.get_instance(cdp_url)
