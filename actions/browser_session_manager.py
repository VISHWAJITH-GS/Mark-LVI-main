"""
actions/browser_session_manager.py — Authoritative Browser Automation & Session Manager.

Built on Playwright with robust lifecycle management:
  - Tracks browser type, instance, context, pages, active tab, process, and status.
  - Uses an isolated persistent testing profile (~/.mark_lvi_test_profile) by default
    so the user's personal Chrome/Edge profiles are never locked, modified, or corrupted.
  - Launches in HEADED mode so the user can visually watch the agent interact.
  - Semantic selector prioritization:
      page.get_by_role("button", name=...)
      page.get_by_label(...)
      page.get_by_placeholder(...)
      page.get_by_text(...)
      CSS selector / coordinate fallback.
  - Reconnects safely upon closed pages or browser crashes (never holds stale references).
  - Captures console errors and network request failures in real-time.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from core import outcome

logger = logging.getLogger(__name__)
_OS = platform.system()


@dataclass
class BrowserNetworkError:
    url: str
    method: str
    status: Optional[int]
    error_text: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class BrowserConsoleMessage:
    msg_type: str  # "error", "warning", "log"
    text: str
    location: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class BrowserSessionState:
    browser_type: str
    profile_dir: str
    is_connected: bool
    active_url: str
    tab_count: int
    console_errors_count: int
    network_errors_count: int


class BrowserSessionManager:
    """Authoritative Playwright Browser Session Controller."""

    _instance: Optional[BrowserSessionManager] = None

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

        self._pw = None
        self._context = None
        self._active_page = None
        self._browser_type: str = "chromium"
        self._profile_dir: Path = Path.home() / ".mark_lvi_test_profile"

        self.console_messages: List[BrowserConsoleMessage] = []
        self.network_errors: List[BrowserNetworkError] = []
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> BrowserSessionManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _ensure_event_loop(self):
        with self._lock:
            if self._thread and self._thread.is_alive() and self._loop:
                return

            self._ready.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="AuthoritativeBrowserLoop",
            )
            self._thread.start()
            self._ready.wait(timeout=20)

    def _run_loop(self):
        try:
            if _OS == "Windows":
                try:
                    loop = asyncio.ProactorEventLoop()
                except Exception:
                    loop = asyncio.new_event_loop()
            else:
                loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._ready.set()
            loop.run_forever()
        except Exception as e:
            logger.error("Browser event loop crashed: %s", e)
            self._ready.set()

    def run_sync(self, coro, timeout: float = 45.0) -> Any:
        self._ensure_event_loop()
        if not self._loop or not self._loop.is_running():
            try:
                coro.close()
            except Exception:
                pass
            raise RuntimeError("Browser event loop could not be started.")

        async def _run_with_timeout():
            return await asyncio.wait_for(coro, timeout=timeout)

        future = asyncio.run_coroutine_threadsafe(_run_with_timeout(), self._loop)
        try:
            return future.result(timeout=timeout + 5.0)
        except Exception:
            future.cancel()
            raise

    def launch_browser(
        self,
        browser_type: str = "chrome",
        headless: bool = False,
        isolated: bool = True,
    ) -> outcome.ActionResult:
        """
        Launch or attach to a visible headed browser session with an isolated testing profile.
        """
        t0 = time.monotonic()
        try:
            res_str = self.run_sync(
                self._async_launch(browser_type=browser_type, headless=headless, isolated=isolated)
            )
            return outcome.ActionResult(
                status=outcome.STATUS_DONE,
                action="launch_browser",
                success=True,
                verified=True,
                message=res_str,
                data=self.get_session_state().__dict__,
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            logger.exception("Failed to launch browser session")
            return outcome.ActionResult(
                status=outcome.STATUS_FAILED,
                action="launch_browser",
                success=False,
                verified=False,
                message=f"Browser launch failed: {e}",
                error=str(e),
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    async def _async_launch(
        self,
        browser_type: str = "chrome",
        headless: bool = False,
        isolated: bool = True,
    ) -> str:
        from playwright.async_api import async_playwright

        if self._pw is None:
            self._pw = await async_playwright().start()

        # Check if context already active
        if self._context is not None:
            try:
                pages = self._context.pages
                if pages:
                    self._active_page = pages[0]
                    return f"Reusing existing browser session on {self._active_page.url}"
            except Exception:
                self._context = None
                self._active_page = None

        if isolated:
            self._profile_dir = Path.home() / ".mark_lvi_test_profile" / browser_type
            self._profile_dir.mkdir(parents=True, exist_ok=True)
            profile_path = str(self._profile_dir)
        else:
            profile_path = str(Path.home() / ".jarvis_profiles" / browser_type)
            Path(profile_path).mkdir(parents=True, exist_ok=True)

        engine = "chromium"
        channel = None
        b_lower = browser_type.lower()
        if "chrome" in b_lower:
            channel = None  # Use installed Playwright Chromium for maximum reliability
        elif "edge" in b_lower or "msedge" in b_lower:
            channel = "msedge"
        elif "firefox" in b_lower:
            engine = "firefox"
        elif "webkit" in b_lower or "safari" in b_lower:
            engine = "webkit"

        engine_obj = getattr(self._pw, engine)

        launch_args = [
            "--no-first-run",
            "--disable-default-apps",
            "--no-default-browser-check",
            "--start-maximized",
        ]

        kwargs: dict = {
            "headless": headless,
            "viewport": None,
            "no_viewport": True,
            "args": launch_args,
            "timeout": 30_000,
        }
        if channel and engine == "chromium":
            kwargs["channel"] = channel

        try:
            self._context = await engine_obj.launch_persistent_context(profile_path, **kwargs)
        except Exception as e:
            # Fallback without channel if channel failed or if profile locked
            logger.warning("Primary browser launch failed (%s), attempting safe fallback...", e)
            kwargs.pop("channel", None)
            try:
                self._context = await engine_obj.launch_persistent_context(profile_path, **kwargs)
            except Exception as e2:
                logger.warning("Isolated profile locked (%s), creating fresh test instance...", e2)
                import uuid
                fresh_profile = str(Path.home() / ".mark_lvi_test_profile" / f"inst_{uuid.uuid4().hex[:6]}")
                Path(fresh_profile).mkdir(parents=True, exist_ok=True)
                self._context = await engine_obj.launch_persistent_context(fresh_profile, **kwargs)

        # Adopt or create page
        await asyncio.sleep(0.3)
        pages = self._context.pages
        self._active_page = pages[0] if pages else await self._context.new_page()

        # Wire console & network telemetry
        self._attach_telemetry(self._active_page)

        # Handle page close events
        self._context.on("page", lambda p: self._attach_telemetry(p))

        msg = f"Launched {browser_type.upper()} in {'HEADLESS' if headless else 'HEADED'} mode (Profile: {profile_path})"
        logger.info(msg)
        return msg

    def _attach_telemetry(self, page):
        """Monitor console messages and network errors on page."""
        try:
            page.on(
                "console",
                lambda msg: self.console_messages.append(
                    BrowserConsoleMessage(
                        msg_type=msg.type,
                        text=msg.text,
                        location=str(msg.location),
                    )
                ),
            )
            page.on(
                "requestfailed",
                lambda req: self.network_errors.append(
                    BrowserNetworkError(
                        url=req.url,
                        method=req.method,
                        status=None,
                        error_text=req.failure or "Failed request",
                    )
                ),
            )
        except Exception:
            pass

    async def _ensure_active_page(self):
        """Verify that _active_page is not stale or closed, recovering if necessary."""
        if self._context is None:
            await self._async_launch(browser_type="chrome", headless=False)

        if self._active_page is None or self._active_page.is_closed():
            pages = [p for p in self._context.pages if not p.is_closed()]
            if pages:
                self._active_page = pages[-1]
            else:
                self._active_page = await self._context.new_page()
                self._attach_telemetry(self._active_page)

        return self._active_page

    def navigate(self, url: str, wait_until: str = "load", timeout: float = 30.0) -> outcome.ActionResult:
        """Navigate to URL and verify page loaded."""
        t0 = time.monotonic()
        try:
            actual_url, title = self.run_sync(self._async_navigate(url, wait_until=wait_until, timeout=timeout))
            return outcome.ActionResult(
                status=outcome.STATUS_DONE,
                action="browser_navigate",
                success=True,
                verified=True,
                message=f"Navigated to '{actual_url}' (Title: '{title}').",
                data={"url": actual_url, "title": title},
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return outcome.ActionResult(
                status=outcome.STATUS_FAILED,
                action="browser_navigate",
                success=False,
                verified=False,
                message=f"Navigation to '{url}' failed: {e}",
                error=str(e),
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    async def _async_navigate(self, url: str, wait_until: str = "load", timeout: float = 30.0) -> Tuple[str, str]:
        page = await self._ensure_active_page()
        target_url = url.strip()
        if not target_url.startswith("http://") and not target_url.startswith("https://") and not target_url.startswith("file://"):
            target_url = f"https://{target_url}"

        await page.goto(target_url, wait_until=wait_until, timeout=int(timeout * 1000))
        await asyncio.sleep(0.5)
        title = await page.title()
        return page.url, title

    def click(
        self,
        text: Optional[str] = None,
        role: Optional[str] = None,
        name: Optional[str] = None,
        selector: Optional[str] = None,
        timeout: float = 8.0,
    ) -> outcome.ActionResult:
        """Click element prioritizing semantic selectors."""
        t0 = time.monotonic()
        try:
            target_desc = self.run_sync(
                self._async_click(text=text, role=role, name=name, selector=selector, timeout=timeout)
            )
            return outcome.ActionResult(
                status=outcome.STATUS_DONE,
                action="browser_click",
                success=True,
                verified=True,
                message=f"Clicked browser element: {target_desc}",
                data={"target": target_desc},
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return outcome.ActionResult(
                status=outcome.STATUS_FAILED,
                action="browser_click",
                success=False,
                verified=False,
                message=f"Browser click failed on '{name or text or selector}': {e}",
                error=str(e),
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    async def _async_click(
        self,
        text: Optional[str] = None,
        role: Optional[str] = None,
        name: Optional[str] = None,
        selector: Optional[str] = None,
        timeout: float = 8.0,
    ) -> str:
        page = await self._ensure_active_page()
        ms = int(timeout * 1000)

        # 1. Semantic role + name
        if role and name:
            loc = page.get_by_role(role, name=name)
            if await loc.count() > 0:
                await loc.first.click(timeout=ms)
                return f"get_by_role('{role}', name='{name}')"

        # 2. Semantic role only
        if role and not name:
            loc = page.get_by_role(role)
            if await loc.count() > 0:
                await loc.first.click(timeout=ms)
                return f"get_by_role('{role}')"

        # 3. Semantic text
        if text:
            loc = page.get_by_text(text, exact=False)
            if await loc.count() > 0:
                await loc.first.click(timeout=ms)
                return f"get_by_text('{text}')"

        # 4. Name as text fallback
        if name:
            loc = page.get_by_text(name, exact=False)
            if await loc.count() > 0:
                await loc.first.click(timeout=ms)
                return f"get_by_text('{name}')"

        # 5. CSS / XPath selector fallback
        if selector:
            loc = page.locator(selector)
            await loc.first.click(timeout=ms)
            return f"locator('{selector}')"

        raise RuntimeError(f"Could not locate element with given parameters: role={role}, name={name}, text={text}")

    def fill(
        self,
        value: str,
        label: Optional[str] = None,
        placeholder: Optional[str] = None,
        selector: Optional[str] = None,
        press_enter: bool = False,
        timeout: float = 8.0,
    ) -> outcome.ActionResult:
        """Fill input field prioritizing semantic label / placeholder selectors."""
        t0 = time.monotonic()
        try:
            target_desc = self.run_sync(
                self._async_fill(
                    value=value,
                    label=label,
                    placeholder=placeholder,
                    selector=selector,
                    press_enter=press_enter,
                    timeout=timeout,
                )
            )
            return outcome.ActionResult(
                status=outcome.STATUS_DONE,
                action="browser_fill",
                success=True,
                verified=True,
                message=f"Filled '{target_desc}' with value.",
                data={"target": target_desc},
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return outcome.ActionResult(
                status=outcome.STATUS_FAILED,
                action="browser_fill",
                success=False,
                verified=False,
                message=f"Failed to fill input field: {e}",
                error=str(e),
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    async def _async_fill(
        self,
        value: str,
        label: Optional[str] = None,
        placeholder: Optional[str] = None,
        selector: Optional[str] = None,
        press_enter: bool = False,
        timeout: float = 8.0,
    ) -> str:
        page = await self._ensure_active_page()
        ms = int(timeout * 1000)

        target = None
        desc = ""

        if label:
            loc = page.get_by_label(label, exact=False)
            if await loc.count() > 0:
                target = loc.first
                desc = f"get_by_label('{label}')"

        if not target and placeholder:
            loc = page.get_by_placeholder(placeholder, exact=False)
            if await loc.count() > 0:
                target = loc.first
                desc = f"get_by_placeholder('{placeholder}')"

        if not target and selector:
            loc = page.locator(selector)
            if await loc.count() > 0:
                target = loc.first
                desc = f"locator('{selector}')"

        if not target and (label or placeholder):
            # General text search for input beside label
            term = label or placeholder
            loc = page.locator(f"input[name*='{term}'], input[id*='{term}'], input[aria-label*='{term}']")
            if await loc.count() > 0:
                target = loc.first
                desc = f"fuzzy input selector for '{term}'"

        if not target:
            raise RuntimeError(f"Could not locate input field for label='{label}', placeholder='{placeholder}'")

        await target.fill(value, timeout=ms)
        if press_enter:
            await target.press("Enter")
        return desc

    def inspect_dom_structure(self) -> Dict[str, Any]:
        """Extract interactive components from page DOM: buttons, forms, inputs, links."""
        try:
            return self.run_sync(self._async_inspect_dom())
        except Exception as e:
            logger.warning("DOM inspection error: %s", e)
            return {"error": str(e), "buttons": [], "inputs": [], "links": [], "forms": []}

    async def _async_inspect_dom(self) -> Dict[str, Any]:
        page = await self._ensure_active_page()

        # Script to collect semantic interactive elements
        script = """
        () => {
            const data = {
                title: document.title,
                url: window.location.href,
                headings: [],
                buttons: [],
                inputs: [],
                links: [],
                forms: []
            };

            // Headings
            document.querySelectorAll('h1, h2, h3').forEach(h => {
                const t = h.innerText.trim();
                if (t) data.headings.push(t);
            });

            // Buttons
            document.querySelectorAll('button, [role="button"], input[type="submit"], input[type="button"]').forEach(b => {
                const text = b.innerText?.trim() || b.getAttribute('value') || b.getAttribute('aria-label') || '';
                const id = b.id || '';
                const isVisible = b.offsetWidth > 0 && b.offsetHeight > 0;
                if (isVisible && (text || id)) {
                    data.buttons.push({ text, id, tag: b.tagName.toLowerCase() });
                }
            });

            // Inputs
            document.querySelectorAll('input:not([type="hidden"]), textarea, select').forEach(i => {
                const isVisible = i.offsetWidth > 0 && i.offsetHeight > 0;
                if (isVisible) {
                    const label = i.getAttribute('aria-label') || i.getAttribute('placeholder') || i.name || i.id || '';
                    data.inputs.push({
                        type: i.getAttribute('type') || i.tagName.toLowerCase(),
                        name: i.name || '',
                        id: i.id || '',
                        placeholder: i.getAttribute('placeholder') || '',
                        label: label
                    });
                }
            });

            // Links (top navigation and routes)
            document.querySelectorAll('a[href]').forEach(a => {
                const text = a.innerText.trim();
                const href = a.getAttribute('href');
                const isVisible = a.offsetWidth > 0 && a.offsetHeight > 0;
                if (isVisible && text && href && !href.startsWith('javascript:')) {
                    data.links.push({ text, href });
                }
            });

            // Forms
            document.querySelectorAll('form').forEach(f => {
                data.forms.push({
                    id: f.id || '',
                    action: f.getAttribute('action') || '',
                    method: f.getAttribute('method') || 'GET',
                    fields_count: f.querySelectorAll('input, select, textarea').length
                });
            });

            return data;
        }
        """
        return await page.evaluate(script)

    def capture_screenshot(self, output_path: Optional[str] = None) -> bytes:
        """Capture screenshot of the active browser page."""
        return self.run_sync(self._async_screenshot(output_path=output_path))

    async def _async_screenshot(self, output_path: Optional[str] = None) -> bytes:
        page = await self._ensure_active_page()
        if output_path:
            p = Path(output_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            return await page.screenshot(path=str(p), full_page=False)
        return await page.screenshot(full_page=False)

    def assert_page_contains(self, text: str) -> bool:
        """Verify that expected text or substring is present in the active webpage body, title, or URL."""
        try:
            return bool(self.run_sync(self._async_assert_contains(text)))
        except Exception as e:
            logger.warning("assert_page_contains error: %s", e)
            return False

    async def _async_assert_contains(self, text: str) -> bool:
        page = await self._ensure_active_page()
        expected = text.lower().strip()
        body_text = (await page.inner_text("body") or "").lower()
        title = (await page.title() or "").lower()
        url = page.url.lower()
        return (expected in body_text) or (expected in title) or (expected in url)

    def get_session_state(self) -> BrowserSessionState:
        """Get live status of the browser session."""
        active_url = ""
        tab_count = 0
        is_conn = False

        if self._context:
            try:
                pages = [p for p in self._context.pages if not p.is_closed()]
                tab_count = len(pages)
                is_conn = True
                if self._active_page and not self._active_page.is_closed():
                    active_url = self._active_page.url
            except Exception:
                is_conn = False

        return BrowserSessionState(
            browser_type=self._browser_type,
            profile_dir=str(self._profile_dir),
            is_connected=is_conn,
            active_url=active_url,
            tab_count=tab_count,
            console_errors_count=len([m for m in self.console_messages if m.msg_type == "error"]),
            network_errors_count=len(self.network_errors),
        )

    def close(self):
        """Close browser context and cleanup gracefully."""
        if self._context:
            try:
                self.run_sync(self._async_close(), timeout=10.0)
            except Exception:
                pass

    async def _async_close(self):
        if self._context:
            await self._context.close()
            self._context = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
        self._active_page = None


_DEFAULT_BROWSER_MANAGER: Optional[BrowserSessionManager] = None


def get_browser_session_manager() -> BrowserSessionManager:
    global _DEFAULT_BROWSER_MANAGER
    if _DEFAULT_BROWSER_MANAGER is None:
        _DEFAULT_BROWSER_MANAGER = BrowserSessionManager.get_instance()
    return _DEFAULT_BROWSER_MANAGER
