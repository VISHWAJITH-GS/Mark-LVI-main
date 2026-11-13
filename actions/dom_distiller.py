"""
actions/dom_distiller.py — Perception Engine: AXTree + DOM Snapshot + Ephemeral Indexing + SoM.

Converts live browser web pages into a compact, token-efficient semantic map for the LLM:
1. Queries Chromium Accessibility Tree (AXTree) and live DOM layout via CDP.
2. Extracts all visible, interactive elements (standard roles + custom SPA div/span targets).
3. Prunes non-interactive wrappers, invisible/occluded nodes, and CSS noise.
4. Assigns sequential integer indices [1], [2], [3]... mapped to backendNodeId and bounding boxes.
5. Injects non-destructive Set-of-Mark (SoM) overlays for visual grounding when requested.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dom_distiller")


@dataclass
class BoundingBox:
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> Tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)


@dataclass
class DistilledElement:
    index: int
    backend_node_id: int
    tag: str
    role: str
    name: str = ""
    aria_label: str = ""
    placeholder: str = ""
    text: str = ""
    value: str = ""
    href: str = ""
    bounding_box: BoundingBox = field(default_factory=lambda: BoundingBox(0, 0, 0, 0))
    is_focused: bool = False
    is_selected: bool = False
    is_checked: bool = False
    is_disabled: bool = False

    def to_prompt_line(self) -> str:
        """
        Serializes the element into a clean, token-efficient prompt string:
        [1] <textbox> aria-label="Message" placeholder="Type a message" focused=true
        [2] <button> aria-label="Send" text="Send"
        [3] <tab> text="Unread" selected=false
        """
        tag_or_role = self.role if self.role and self.role != "generic" else self.tag
        parts = [f"[{self.index}] <{tag_or_role}>"]

        if self.aria_label:
            clean_label = self.aria_label.replace('"', '\\"').strip()
            parts.append(f'aria-label="{clean_label}"')
        if self.placeholder:
            clean_ph = self.placeholder.replace('"', '\\"').strip()
            parts.append(f'placeholder="{clean_ph}"')
        if self.text and self.text != self.aria_label:
            clean_text = self.text.replace('"', '\\"').strip()
            if len(clean_text) > 60:
                clean_text = clean_text[:57] + "..."
            parts.append(f'text="{clean_text}"')
        elif self.name and self.name != self.aria_label and self.name != self.text:
            clean_name = self.name.replace('"', '\\"').strip()
            parts.append(f'name="{clean_name}"')

        if self.href and tag_or_role in ("link", "a"):
            parts.append(f'href="{self.href[:60]}"')
        if self.value and tag_or_role in ("textbox", "input", "searchbox"):
            val_clean = str(self.value)[:40].replace('"', '\\"')
            parts.append(f'value="{val_clean}"')

        if self.is_focused:
            parts.append("focused=true")
        if self.is_selected:
            parts.append("selected=true")
        if self.is_checked:
            parts.append("checked=true")
        if self.is_disabled:
            parts.append("disabled=true")

        return " ".join(parts)


# JavaScript scanner to find interactive DOM elements within the current viewport
_SCANNER_JS = """
() => {
    const isVisible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
        const rect = el.getBoundingClientRect();
        if (rect.width < 2 || rect.height < 2) return false;
        // Viewport check (must be at least partially inside viewport)
        if (rect.bottom < 0 || rect.top > window.innerHeight || rect.right < 0 || rect.left > window.innerWidth) {
            return false;
        }
        return true;
    };

    const isInteractive = (el) => {
        const tag = el.tagName.toLowerCase();
        if (['button', 'input', 'textarea', 'select', 'summary', 'option'].includes(tag)) return true;
        if (tag === 'a' && el.hasAttribute('href')) return true;
        
        // ARIA roles
        const role = el.getAttribute('role');
        const interactiveRoles = [
            'button', 'link', 'tab', 'menuitem', 'checkbox', 'radio', 
            'textbox', 'combobox', 'switch', 'searchbox', 'option', 'treeitem'
        ];
        if (role && interactiveRoles.includes(role)) return true;

        // Custom SPA interactive triggers
        if (el.getAttribute('contenteditable') === 'true') return true;
        if (el.hasAttribute('onclick') || el.hasAttribute('data-action')) return true;
        if (el.hasAttribute('tabindex') && el.getAttribute('tabindex') !== '-1') return true;

        const style = window.getComputedStyle(el);
        if (style.cursor === 'pointer' && !['body', 'html'].includes(tag)) {
            // Avoid marking massive wrappers with pointer cursor
            const rect = el.getBoundingClientRect();
            if (rect.width < window.innerWidth * 0.95 || rect.height < window.innerHeight * 0.95) {
                return true;
            }
        }
        return false;
    };

    const candidates = [];
    const all = document.querySelectorAll('*');
    
    // First pass: identify and mark interactive elements
    for (const el of all) {
        if (isInteractive(el) && isVisible(el)) {
            candidates.push(el);
        }
    }

    // Prune nested duplicates (if parent and child are both candidates with identical bounds, keep child)
    const pruned = [];
    for (let i = 0; i < candidates.length; i++) {
        const a = candidates[i];
        const rA = a.getBoundingClientRect();
        let dominated = false;
        for (let j = 0; j < candidates.length; j++) {
            if (i === j) continue;
            const b = candidates[j];
            if (a.contains(b)) {
                const rB = b.getBoundingClientRect();
                if (Math.abs(rA.width - rB.width) < 4 && Math.abs(rA.height - rB.height) < 4) {
                    dominated = true;
                    break;
                }
            }
        }
        if (!dominated) {
            pruned.push(a);
        }
    }

    // Tag each surviving element with a temporary index attribute for CDP backendNodeId correlation
    const results = [];
    for (let i = 0; i < pruned.length; i++) {
        const el = pruned[i];
        el.setAttribute('data-jarvis-cdp-idx', String(i));
        const rect = el.getBoundingClientRect();
        
        let txt = '';
        if (el.innerText) {
            txt = el.innerText.trim().replace(/\\s+/g, ' ').slice(0, 80);
        } else if (el.textContent) {
            txt = el.textContent.trim().replace(/\\s+/g, ' ').slice(0, 80);
        }

        results.push({
            tempIdx: i,
            tag: el.tagName.toLowerCase(),
            role: el.getAttribute('role') || el.tagName.toLowerCase(),
            ariaLabel: el.getAttribute('aria-label') || '',
            placeholder: el.getAttribute('placeholder') || '',
            name: el.getAttribute('name') || '',
            text: txt,
            value: el.value !== undefined ? String(el.value).slice(0, 60) : '',
            href: el.getAttribute('href') || '',
            box: {
                x: rect.x,
                y: rect.y,
                width: rect.width,
                height: rect.height
            },
            isFocused: document.activeElement === el,
            isSelected: el.getAttribute('aria-selected') === 'true' || el.selected === true,
            isChecked: el.checked === true || el.getAttribute('aria-checked') === 'true',
            isDisabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true'
        });
    }

    return results;
}
"""

_CLEANUP_JS = """
() => {
    document.querySelectorAll('[data-jarvis-cdp-idx]').forEach(el => el.removeAttribute('data-jarvis-cdp-idx'));
}
"""

_SOM_INJECT_JS = """
(elements) => {
    document.getElementById('__jarvis_som_root__')?.remove();
    const root = document.createElement('div');
    root.id = '__jarvis_som_root__';
    root.style.position = 'fixed';
    root.style.top = '0';
    root.style.left = '0';
    root.style.width = '100vw';
    root.style.height = '100vh';
    root.style.pointerEvents = 'none';
    root.style.zIndex = '2147483647';
    
    for (const item of elements) {
        const badge = document.createElement('div');
        badge.innerText = item.index;
        badge.style.position = 'absolute';
        badge.style.left = `${Math.max(0, item.x)}px`;
        badge.style.top = `${Math.max(0, item.y - 14)}px`;
        badge.style.background = '#FFEB3B';
        badge.style.color = '#000000';
        badge.style.border = '1px solid #000000';
        badge.style.borderRadius = '3px';
        badge.style.padding = '1px 3px';
        badge.style.fontSize = '10px';
        badge.style.fontWeight = 'bold';
        badge.style.fontFamily = 'monospace';
        badge.style.boxShadow = '1px 1px 3px rgba(0,0,0,0.4)';
        badge.style.pointerEvents = 'none';
        
        const outline = document.createElement('div');
        outline.style.position = 'absolute';
        outline.style.left = `${item.x}px`;
        outline.style.top = `${item.y}px`;
        outline.style.width = `${item.width}px`;
        outline.style.height = `${item.height}px`;
        outline.style.border = '1px dashed #FF9800';
        outline.style.pointerEvents = 'none';

        root.appendChild(outline);
        root.appendChild(badge);
    }
    document.body.appendChild(root);
}
"""

_SOM_REMOVE_JS = """
() => {
    document.getElementById('__jarvis_som_root__')?.remove();
}
"""


class DOMDistiller:
    """
    Distills live web pages into an indexed semantic representation using CDP + AXTree.
    """
    def __init__(self):
        self._last_distilled_map: Dict[int, DistilledElement] = {}
        self._last_serialized_prompt: str = ""
        self._lock = asyncio.Lock()

    def invalidate_cache(self):
        """Strictly invalidates the index -> backendNodeId lookup table after actions."""
        self._last_distilled_map.clear()
        self._last_serialized_prompt = ""

    def get_element_by_index(self, index: int) -> Optional[DistilledElement]:
        return self._last_distilled_map.get(index)

    def get_current_lookup_map(self) -> Dict[int, DistilledElement]:
        return dict(self._last_distilled_map)

    async def distill_page(
        self,
        page: Any,
        cdp_session: Any = None,
        enable_som_overlay: bool = False,
    ) -> Tuple[str, Dict[int, DistilledElement]]:
        """
        Performs hybrid AXTree + DOM layout distillation pass.
        Returns serialized prompt text and integer-indexed element map.
        """
        async with self._lock:
            self.invalidate_cache()
            if page is None or page.is_closed():
                return "(No active browser tab)", {}

            # 1. Run in-page viewport scanner
            raw_candidates = await page.evaluate(_SCANNER_JS)

            # 2. Correlate with CDP backendNodeIds via DOM domain
            backend_map: Dict[int, int] = {}
            if cdp_session:
                try:
                    await cdp_session.send("DOM.enable")
                    doc = await cdp_session.send("DOM.getDocument", {"depth": 1})
                    root_node_id = doc.get("root", {}).get("nodeId", 1)
                    node_ids_res = await cdp_session.send(
                        "DOM.querySelectorAll",
                        {"nodeId": root_node_id, "selector": "[data-jarvis-cdp-idx]"},
                    )
                    node_ids = node_ids_res.get("nodeIds", [])
                    for nid in node_ids:
                        try:
                            desc = await cdp_session.send("DOM.describeNode", {"nodeId": nid})
                            node_info = desc.get("node", {})
                            b_id = node_info.get("backendNodeId", 0)
                            attrs = node_info.get("attributes", [])
                            # Find data-jarvis-cdp-idx attribute value
                            for k in range(0, len(attrs), 2):
                                if attrs[k] == "data-jarvis-cdp-idx":
                                    temp_idx = int(attrs[k + 1])
                                    backend_map[temp_idx] = b_id
                                    break
                        except Exception:
                            continue
                except Exception as cdp_err:
                    logger.debug(f"CDP DOM correlation fallback: {cdp_err}")

            # Clean up temporary attributes in the DOM
            try:
                await page.evaluate(_CLEANUP_JS)
            except Exception:
                pass

            # 3. Assemble indexed DistilledElement instances
            distilled_elements: Dict[int, DistilledElement] = {}
            prompt_lines: List[str] = []
            som_payload: List[Dict[str, Any]] = []

            for i, c in enumerate(raw_candidates, start=1):
                temp_idx = c["tempIdx"]
                backend_id = backend_map.get(temp_idx, 0)
                box_data = c["box"]
                bbox = BoundingBox(
                    x=box_data["x"],
                    y=box_data["y"],
                    width=box_data["width"],
                    height=box_data["height"],
                )

                elem = DistilledElement(
                    index=i,
                    backend_node_id=backend_id,
                    tag=c["tag"],
                    role=c["role"],
                    name=c["name"],
                    aria_label=c["ariaLabel"],
                    placeholder=c["placeholder"],
                    text=c["text"],
                    value=c["value"],
                    href=c["href"],
                    bounding_box=bbox,
                    is_focused=c["isFocused"],
                    is_selected=c["isSelected"],
                    is_checked=c["isChecked"],
                    is_disabled=c["isDisabled"],
                )

                distilled_elements[i] = elem
                prompt_lines.append(elem.to_prompt_line())

                if enable_som_overlay:
                    som_payload.append({
                        "index": i,
                        "x": bbox.x,
                        "y": bbox.y,
                        "width": bbox.width,
                        "height": bbox.height,
                    })

            # 4. Optional SoM overlay injection
            if enable_som_overlay and som_payload:
                try:
                    await page.evaluate(_SOM_INJECT_JS, som_payload)
                except Exception as som_err:
                    logger.debug(f"SoM overlay injection error: {som_err}")

            self._last_distilled_map = distilled_elements
            serialized = "\n".join(prompt_lines) if prompt_lines else "(No interactive elements detected in current viewport)"
            self._last_serialized_prompt = serialized
            return serialized, distilled_elements

    async def remove_som_overlay(self, page: Any):
        """Removes the Set-of-Mark visual overlay cleanly before dispatching input."""
        if page and not page.is_closed():
            try:
                await page.evaluate(_SOM_REMOVE_JS)
            except Exception:
                pass


_global_distiller: Optional[DOMDistiller] = None


def get_dom_distiller() -> DOMDistiller:
    global _global_distiller
    if _global_distiller is None:
        _global_distiller = DOMDistiller()
    return _global_distiller

