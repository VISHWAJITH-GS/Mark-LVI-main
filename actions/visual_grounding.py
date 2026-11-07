"""
actions/visual_grounding.py — Multimodal Visual Grounding via Google Gemini Vision.

Analyzes desktop screenshots with Google Gemini vision to ground interactive UI targets:
  - Buttons, text fields, dropdowns, menus, icons, tabs, links, checkboxes, dialogs, notifications.
  - Returns structured target descriptors (not arbitrary executable code).
  - Handles coordinate scaling and normalization from preview images to physical desktop coordinates.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core import gemini as _gemini
from actions.display_manager import get_display_manager, CapturedScreenshot

logger = logging.getLogger(__name__)


@dataclass
class GroundedTarget:
    target: str
    control_type: str
    visual_description: str
    approximate_bounds: Dict[str, int]  # {x, y, width, height} in physical desktop space
    confidence: float
    raw_preview_bounds: Optional[Dict[str, int]] = None

    def center_point(self) -> Tuple[int, int]:
        b = self.approximate_bounds
        cx = b.get("x", 0) + b.get("width", 0) // 2
        cy = b.get("y", 0) + b.get("height", 0) // 2
        return cx, cy

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "control_type": self.control_type,
            "visual_description": self.visual_description,
            "approximate_bounds": self.approximate_bounds,
            "confidence": self.confidence,
        }


class VisualGroundingSubsystem:
    """Interacts with Gemini multimodal models to locate UI elements visually."""

    def __init__(self):
        self.display_manager = get_display_manager()

    def ground_element(
        self,
        query: str,
        screenshot: Optional[CapturedScreenshot] = None,
        context_hint: str = "",
    ) -> List[GroundedTarget]:
        """
        Send screen image to Gemini vision to visually ground one or more target controls matching query.
        """
        # Ensure we have preview image and original
        preview = screenshot
        if preview is None:
            orig = self.display_manager.capture_screenshot(monitor_index=0)
            preview = self.display_manager.generate_preview(orig)

        prompt = f"""You are a precise Windows Desktop Visual Grounding Assistant.
Analyze this desktop screenshot and identify interactive UI elements matching the query.

QUERY: "{query}"
{f'ADDITIONAL CONTEXT: "{context_hint}"' if context_hint else ''}

SCREENSHOT DIMENSIONS: {preview.width} x {preview.height} pixels.

Instructions:
1. Locate the UI control (button, input, tab, icon, link, menu item, checkbox, etc.) described by the query.
2. Provide bounding box coordinates relative to THIS screenshot: x (left), y (top), width, height.
3. Assign a confidence score between 0.0 and 1.0.
4. Output ONLY valid JSON array with NO markdown fences, matching this schema:
[
  {{
    "target": "Element Name or Label",
    "control_type": "button | edit | hyperlink | tab | checkbox | menu_item | icon",
    "visual_description": "Precise visual appearance and spatial position",
    "approximate_bounds": {{
      "x": 100,
      "y": 150,
      "width": 80,
      "height": 30
    }},
    "confidence": 0.95
  }}
]
"""
        targets: List[GroundedTarget] = []

        try:
            raw_res = _gemini.text(
                prompt,
                tier=_gemini.SMART,
                images=[preview.image_bytes],
            )
            if not raw_res:
                logger.warning("Gemini vision returned empty response for visual grounding query: %s", query)
                return []

            # Clean markdown JSON fences
            clean = re.sub(r"^```(?:json)?\s*", "", raw_res.strip(), flags=re.MULTILINE)
            clean = re.sub(r"\s*```$", "", clean.strip(), flags=re.MULTILINE)

            parsed = json.loads(clean)
            if isinstance(parsed, dict):
                parsed = [parsed]

            if not isinstance(parsed, list):
                return []

            for item in parsed:
                bounds = item.get("approximate_bounds", {})
                px = bounds.get("x", 0)
                py = bounds.get("y", 0)
                pw = bounds.get("width", 0)
                ph = bounds.get("height", 0)

                # Transform preview image coordinates to physical desktop space
                # preview.scale_w is target_w / orig_w
                scale_w = preview.scale_w if preview.scale_w > 0 else 1.0
                scale_h = preview.scale_h if preview.scale_h > 0 else 1.0

                desktop_x = int(px / scale_w + preview.origin_x)
                desktop_y = int(py / scale_h + preview.origin_y)
                desktop_w = int(pw / scale_w)
                desktop_h = int(ph / scale_h)

                target = GroundedTarget(
                    target=str(item.get("target", query)),
                    control_type=str(item.get("control_type", "element")),
                    visual_description=str(item.get("visual_description", "")),
                    approximate_bounds={
                        "x": desktop_x,
                        "y": desktop_y,
                        "width": desktop_w,
                        "height": desktop_h,
                    },
                    confidence=float(item.get("confidence", 0.8)),
                    raw_preview_bounds={"x": px, "y": py, "width": pw, "height": ph},
                )
                targets.append(target)

        except Exception as e:
            logger.error("Visual grounding failed: %s", e)

        return targets

    def inspect_visual_crop(
        self,
        center_x: int,
        center_y: int,
        prompt: str = "Identify what UI control is at the center of this image.",
    ) -> str:
        """Analyze a high-resolution localized crop around a point of interest (e.g. cursor)."""
        try:
            crop = self.display_manager.crop_region(center_x, center_y, width=350, height=250)
            res = _gemini.text(
                f"{prompt}\nCrop size: {crop.width}x{crop.height}.",
                tier=_gemini.SMART,
                images=[crop.image_bytes],
            )
            return res.strip() if res else "No visual details recognized."
        except Exception as e:
            return f"Crop inspection failed: {e}"


_DEFAULT_VISUAL_GROUNDING: Optional[VisualGroundingSubsystem] = None


def get_visual_grounding() -> VisualGroundingSubsystem:
    global _DEFAULT_VISUAL_GROUNDING
    if _DEFAULT_VISUAL_GROUNDING is None:
        _DEFAULT_VISUAL_GROUNDING = VisualGroundingSubsystem()
    return _DEFAULT_VISUAL_GROUNDING

