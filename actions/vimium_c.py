"""
actions/vimium_c.py — Hybrid Browser Automation & Vimium C Keyboard Interaction Engine.

Combines Playwright browser automation with the Vimium C extension:
  1. Detects and loads Vimium C into Chromium / Edge browsers.
  2. Activates Link Hints mode ('f' for current tab, 'F' for new tab).
  3. Pierces closed shadow roots via Chrome DevTools Protocol (CDP) to extract
     visible keyboard hint labels, coordinates, and underlying element descriptors.
  4. Resolves user targets by text, role, aria-label, fuzzy match, or Vision Grounding.
  5. Dispatches keyboard hint sequences and verifies navigation or interaction.
  6. Supports full Vimium keyboard navigation shortcuts:
     - 'j' / 'k' (scroll down / up)
     - 'd' / 'u' (half page down / up)
     - 'gg' / 'G' (scroll top / bottom)
     - 't' (open tab)
     - 'x' / 'X' (close tab / restore tab)
     - 'J' / 'K' (next / previous tab)
     - '?' (show shortcuts)
  7. Robust fallback to Playwright DOM selectors and vision when Vimium C is inactive.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import outcome

logger = logging.getLogger(__name__)
_OS = platform.system()


@dataclass
class VimiumHint:
    hint_code: str
    x: int
    y: int
    tag: str = ""
    element_text: str = ""
    href: str = ""
    role: str = ""
    aria_label: str = ""
    title: str = ""
    placeholder: str = ""

    def summary(self) -> str:
        desc = self.element_text or self.aria_label or self.title or self.href or self.tag
        return f"[{self.hint_code}] ({self.tag.lower()}) '{desc[:40]}'"


def get_vimium_extension_path() -> Optional[str]:
    """
    Locates the Vimium C extension directory.
    Checks bundled repo copy first, then searches user Edge and Chrome AppData extensions.
    """
    # 1. Bundled repo path
    repo_base = Path(__file__).resolve().parent.parent
    bundled = repo_base / "extensions" / "vimium_c"
    if bundled.exists() and (bundled / "manifest.json").exists():
        return str(bundled.resolve())

    # 2. User AppData (Windows / Linux / macOS)
    ext_id = "hfjbmagddngcpeloejdejnfgbamkjaeg"
    candidates = []
    if _OS == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            candidates.extend([
                Path(local) / "Microsoft" / "Edge" / "User Data" / "Default" / "Extensions" / ext_id,
                Path(local) / "Google" / "Chrome" / "User Data" / "Default" / "Extensions" / ext_id,
                Path(local) / "BraveSoftware" / "Brave-Browser" / "User Data" / "Default" / "Extensions" / ext_id,
            ])
    elif _OS == "Darwin":
        home = Path.home()
        candidates.extend([
            home / "Library" / "Application Support" / "Microsoft Edge" / "Default" / "Extensions" / ext_id,
            home / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "Extensions" / ext_id,
        ])
    else:
        home = Path.home()
        candidates.extend([
            home / ".config" / "google-chrome" / "Default" / "Extensions" / ext_id,
            home / ".config" / "microsoft-edge" / "Default" / "Extensions" / ext_id,
        ])

    for c in candidates:
        if c.exists():
            # Directory contains version subdirectories (e.g. 2.12.2_0)
            versions = [p for p in c.iterdir() if p.is_dir() and (p / "manifest.json").exists()]
            if versions:
                versions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                return str(versions[0].resolve())

    return None


def is_vimium_installed() -> bool:
    """Returns True if Vimium C unpacked extension files are found."""
    return get_vimium_extension_path() is not None


class VimiumCController:
    """Manages Vimium C Link Hints and keyboard navigation over Playwright pages."""

    def __init__(self):
        self._cdp_sessions: dict[Any, Any] = {}

    async def _get_cdp(self, page) -> Any:
        try:
            if page in self._cdp_sessions:
                return self._cdp_sessions[page]
            cdp = await page.context.new_cdp_session(page)
            await cdp.send("DOM.enable")
            self._cdp_sessions[page] = cdp
            return cdp
        except Exception as e:
            logger.debug(f"[VimiumC] CDP session error: {e}")
            return None

    async def is_available(self, page) -> bool:
        """Checks if Vimium C service worker or content script is present."""
        try:
            ctx = getattr(page, "context", None)
            if ctx and hasattr(ctx, "service_workers"):
                for sw in ctx.service_workers:
                    if "hfjbmagddngcpeloejdejnfgbamkjaeg" in getattr(sw, "url", ""):
                        return True
            # Also check DOM injection presence
            has_dom = await page.evaluate("""() => {
                return Array.from(document.documentElement.children).some(
                    c => c.tagName === 'DIV' && c.className && c.className.includes('R')
                ) || document.querySelector('.vimiumReset') !== null;
            }""")
            return bool(has_dom or is_vimium_installed())
        except Exception:
            return is_vimium_installed()

    async def activate_hints(self, page, new_tab: bool = False, timeout: float = 1.5) -> bool:
        """
        Activates Vimium C link hint mode.
        Ensures input fields are blurred first (Escape), then sends 'f' or 'F'.
        """
        try:
            # Blur any focused text area / input element so 'f' is not eaten as text
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.12)

            key = "F" if new_tab else "f"
            await page.keyboard.press(key)
            await asyncio.sleep(0.35)

            # Check if hints appeared
            hints = await self.get_visible_hints(page, max_hints=5)
            if hints:
                return True

            # Retry once with explicit focus on body
            await page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); }")
            await page.keyboard.press(key)
            await asyncio.sleep(0.35)
            hints = await self.get_visible_hints(page, max_hints=5)
            return len(hints) > 0
        except Exception as e:
            logger.debug(f"[VimiumC] Error activating hints: {e}")
            return False

    async def cancel_hints(self, page) -> None:
        """Exits hint mode."""
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.1)
        except Exception:
            pass

    async def get_visible_hints(self, page, max_hints: int = 150) -> List[VimiumHint]:
        """
        Extracts active link hints from Vimium C's closed shadow DOM using CDP.
        Resolves each hint's underlying DOM element properties.
        """
        cdp = await self._get_cdp(page)
        if not cdp:
            return []

        try:
            doc = await cdp.send("DOM.getFlattenedDocument", {"depth": -1, "pierce": True})
            nodes = doc.get("nodes", [])
            hint_nodes = []

            for n in nodes:
                attrs = n.get("attributes", [])
                if "LH" in attrs:
                    hint_nodes.append(n)
                if len(hint_nodes) >= max_hints:
                    break

            if not hint_nodes:
                return []

            results: List[VimiumHint] = []
            for n in hint_nodes:
                node_id = n.get("nodeId")
                try:
                    outer = (await cdp.send("DOM.getOuterHTML", {"nodeId": node_id})).get("outerHTML", "")
                except Exception:
                    continue

                # Parse hint characters (e.g. <span>S</span>S -> "SS")
                clean_text = re.sub(r"<[^>]+>", "", outer).strip().upper()
                if not clean_text:
                    continue

                # Parse position
                pos_m = re.search(r"left:\s*(-?\d+)px;\s*top:\s*(-?\d+)px", outer)
                x = int(pos_m.group(1)) if pos_m else 0
                y = int(pos_m.group(2)) if pos_m else 0

                # Query underlying DOM element at (x, y) including parent video/article cards
                el_info = await page.evaluate(f"""() => {{
                    let el = document.elementFromPoint({x}, {y});
                    if (!el) return {{}};
                    let a = el.closest('a') || el.closest('button') || el.closest('[role="button"]') || el.closest('[role="link"]') || el;
                    let card = el.closest('ytd-compact-video-renderer, ytd-rich-item-renderer, ytd-video-renderer, ytd-grid-video-renderer, ytd-playlist-video-renderer, article, li, tr, [role="row"], [role="listitem"], .video-card, div#dismissible');
                    let cardTitle = '';
                    let cardText = '';
                    if (card) {{
                        let tEl = card.querySelector('#video-title, #title, [id*="title"], h1, h2, h3, h4, h5, a[title]');
                        if (tEl) {{
                            cardTitle = (tEl.getAttribute('title') || tEl.getAttribute('aria-label') || tEl.innerText || tEl.textContent || '').trim();
                        }}
                        cardText = (card.innerText || card.textContent || '').trim().slice(0, 200);
                    }}
                    let elText = (a.innerText || a.textContent || '').trim().slice(0, 120);
                    let fullText = [elText, cardTitle, cardText].filter(Boolean).join(' ');
                    return {{
                        tag: a.tagName || '',
                        text: fullText.slice(0, 250),
                        href: a.getAttribute('href') || (card && card.querySelector('a') ? card.querySelector('a').getAttribute('href') : '') || '',
                        role: a.getAttribute('role') || '',
                        aria: (a.getAttribute('aria-label') || cardTitle).slice(0, 150),
                        title: (a.getAttribute('title') || cardTitle).slice(0, 150),
                        placeholder: a.getAttribute('placeholder') || ''
                    }};
                }}""")

                vh = VimiumHint(
                    hint_code=clean_text,
                    x=x,
                    y=y,
                    tag=el_info.get("tag", ""),
                    element_text=el_info.get("text", ""),
                    href=el_info.get("href", ""),
                    role=el_info.get("role", ""),
                    aria_label=el_info.get("aria", ""),
                    title=el_info.get("title", ""),
                    placeholder=el_info.get("placeholder", ""),
                )
                results.append(vh)

            return results
        except Exception as e:
            logger.debug(f"[VimiumC] Error getting visible hints: {e}")
            return []

    def match_hint(self, target_query: str, hints: List[VimiumHint]) -> Optional[VimiumHint]:
        """
        Matches a target query against a list of hints using:
          0. Direct hint code match (e.g. 'A', 'JK', 'GP').
          1. Quoted terms extraction (e.g. 'PLACEMENTS' or 'Putaapu Gang Song').
          2. Exact or substring match on text, aria-label, title, placeholder, or href.
          3. Distinctive keyword token matching (ignoring conversational stopwords).
        """
        if not target_query or not hints:
            return None

        q_clean = target_query.strip()
        q_lower = q_clean.lower()

        # ── Pass 0: Direct hint code match ───────────────────────────────────
        for h in hints:
            if h.hint_code.lower() == q_lower:
                return h

        # ── Pass 1: Quoted term extraction ───────────────────────────────────
        # When target_query is e.g. "Click the 'PLACEMENTS' project folder..."
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", target_query)
        for q_term in quoted:
            term_lower = q_term.lower().strip()
            if len(term_lower) >= 2:
                for h in hints:
                    haystack = f"{h.element_text} {h.aria_label} {h.title} {h.placeholder} {h.href}".lower()
                    if term_lower in haystack or haystack in term_lower:
                        return h

        # ── Pass 2: Exact or substring match ─────────────────────────────────
        for h in hints:
            for field_val in (h.element_text, h.aria_label, h.title, h.placeholder):
                fv = field_val.lower().strip()
                if fv and (q_lower == fv or q_lower in fv or fv in q_lower):
                    return h

        # Check href matching (e.g. "wiki/Main_Page", "contact", "login")
        for h in hints:
            if h.href and q_lower in h.href.lower():
                return h

        # ── Pass 3: Distinctive keyword token matching ───────────────────────
        STOP_WORDS = {
            "click", "the", "button", "link", "item", "folder", "in", "on", "sidebar",
            "it", "is", "below", "above", "pinned", "please", "video", "song", "tab",
            "to", "and", "or", "a", "an", "at", "by", "for", "from", "of", "with",
            "can", "you", "see", "side", "put", "up"
        }
        all_tokens = set(re.findall(r"\w+", q_lower))
        distinct_tokens = [tok for tok in all_tokens if tok not in STOP_WORDS and len(tok) >= 3]
        if not distinct_tokens:
            distinct_tokens = [tok for tok in all_tokens if len(tok) >= 3]

        best_hint = None
        best_score = 0
        for h in hints:
            haystack = f"{h.element_text} {h.aria_label} {h.title} {h.placeholder} {h.role} {h.href}".lower()
            h_tokens = set(re.findall(r"\w+", haystack))
            score = 0
            for tok in distinct_tokens:
                if tok in h_tokens:
                    score += len(tok) * 2  # exact token hit
                elif any(tok in ht for ht in h_tokens if len(ht) >= 3):
                    score += len(tok)      # partial token hit (e.g. 'putaapu' vs 'put')
            if score > best_score:
                best_score = score
                best_hint = h

        if best_hint and best_score >= 4:
            return best_hint

        return None

    async def find_hint_for_target(
        self,
        page,
        target_query: str,
        new_tab: bool = False,
    ) -> Optional[VimiumHint]:
        """
        Finds the matching Vimium C hint for a target description or query.
        Uses multi-pass matching:
          0-2. Code, text, aria, token matching via match_hint().
          3. Visual Grounding fallback for icon/image elements.
        """
        active = await self.activate_hints(page, new_tab=new_tab)
        if not active:
            return None

        hints = await self.get_visible_hints(page)
        if not hints:
            await self.cancel_hints(page)
            return None

        matched = self.match_hint(target_query, hints)
        if matched:
            return matched

        # ── Pass 3: Visual Grounding fallback ────────────────────────────────
        try:
            from actions.visual_grounding import VisualGroundingSubsystem
            import tempfile

            vg = VisualGroundingSubsystem()
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                tmp_shot = tf.name
            await page.screenshot(path=tmp_shot)

            from actions.display_manager import CapturedScreenshot
            from PIL import Image

            img = Image.open(tmp_shot)
            captured = CapturedScreenshot(image=img, monitor_index=0, physical_rect=(0, 0, img.width, img.height))
            targets = vg.ground_element(target_query, screenshot=captured)

            try:
                os.remove(tmp_shot)
            except Exception:
                pass

            if targets:
                t = targets[0]
                tx, ty = t.center_point()
                # Find the hint closest to the grounded target center
                closest = min(
                    hints,
                    key=lambda h: (h.x - tx) ** 2 + (h.y - ty) ** 2
                )
                return closest
        except Exception as e:
            logger.debug(f"[VimiumC] Visual grounding fallback error: {e}")

        # Target could not be resolved
        await self.cancel_hints(page)
        return None

    async def execute_hint(self, page, hint: VimiumHint, wait_ms: int = 1500) -> dict:
        """
        Sends the hint characters sequentially to trigger the link or button.
        Verifies that navigation, DOM change, or input focus took place.
        """
        url_before = page.url
        title_before = await page.title()
        pages_count_before = len(page.context.pages)

        # Send hint characters
        for ch in hint.hint_code:
            await page.keyboard.press(ch.lower())
            await asyncio.sleep(0.08)

        # Wait for page response
        await asyncio.sleep(min(3.0, wait_ms / 1000.0))
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=2500)
        except Exception:
            pass

        url_after = page.url
        title_after = await page.title()
        pages_count_after = len(page.context.pages)

        navigated = url_after != url_before or title_after != title_before
        new_tab_opened = pages_count_after > pages_count_before

        verified = navigated or new_tab_opened or hint.tag.lower() in ("input", "textarea", "button")

        return {
            "success": True,
            "hint_code": hint.hint_code,
            "target": hint.summary(),
            "url_before": url_before,
            "url_after": url_after,
            "navigated": navigated,
            "new_tab_opened": new_tab_opened,
            "verified": verified,
        }

    # ── Quick Keyboard Navigation Shortcuts ──────────────────────────────────

    async def scroll(self, page, direction: str = "down", amount: int = 1) -> str:
        """Scrolls using Vimium C keys ('j' for down, 'k' for up, 'gg' for top, 'G' for bottom)."""
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.1)

        d = direction.lower().strip()
        if d in ("top", "home"):
            await page.keyboard.press("g")
            await page.keyboard.press("g")
            return "Scrolled to top via Vimium C ('gg')."
        elif d in ("bottom", "end"):
            await page.keyboard.press("G")
            return "Scrolled to bottom via Vimium C ('G')."
        elif d in ("up", "pageup"):
            for _ in range(max(1, amount)):
                await page.keyboard.press("k")
                await asyncio.sleep(0.05)
            return f"Scrolled up {amount} steps via Vimium C ('k')."
        else:
            for _ in range(max(1, amount)):
                await page.keyboard.press("j")
                await asyncio.sleep(0.05)
            return f"Scrolled down {amount} steps via Vimium C ('j')."

    async def next_tab(self, page) -> str:
        """Switches to the next tab ('J' or 'gt')."""
        await page.keyboard.press("Escape")
        await page.keyboard.press("J")
        return "Switched to next tab via Vimium C ('J')."

    async def prev_tab(self, page) -> str:
        """Switches to the previous tab ('K' or 'gT')."""
        await page.keyboard.press("Escape")
        await page.keyboard.press("K")
        return "Switched to previous tab via Vimium C ('K')."

    async def close_tab(self, page) -> str:
        """Closes the current tab ('x')."""
        await page.keyboard.press("Escape")
        await page.keyboard.press("x")
        return "Closed current tab via Vimium C ('x')."

    async def restore_tab(self, page) -> str:
        """Restores the last closed tab ('X')."""
        await page.keyboard.press("Escape")
        await page.keyboard.press("X")
        return "Restored closed tab via Vimium C ('X')."

    async def focus_input(self, page) -> str:
        """Focuses the first interactive input field ('gi')."""
        await page.keyboard.press("Escape")
        await page.keyboard.press("g")
        await page.keyboard.press("i")
        return "Focused input field via Vimium C ('gi')."


_controller: Optional[VimiumCController] = None

def get_vimium_controller() -> VimiumCController:
    global _controller
    if _controller is None:
        _controller = VimiumCController()
    return _controller
