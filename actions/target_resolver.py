"""
actions/target_resolver.py — Intelligent Hybrid Target Resolver.

Combines Semantic Windows UI Automation with Gemini Visual Grounding:
  1. Priority 1: Semantic UI Automation (UIA) tree matching (Invoke/Select/Toggle/Focus patterns).
  2. Priority 2: Visual Grounding via Gemini Vision when UIA is unavailable, custom rendered,
     or ambiguous.
  3. Spatial & Relative Reference parsing ("button on the left", "field below username", etc.).
  4. Cursor-centric resolution ("Click here") via point-in-element and localized crop analysis.
  5. Multi-monitor physical coordinate validation and bounds checking.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from actions import windows_uia
from actions.display_manager import get_display_manager
from actions.ui_tree_manager import get_ui_tree_manager, UIElementNode
from actions.visual_grounding import get_visual_grounding, GroundedTarget

logger = logging.getLogger(__name__)


@dataclass
class ResolvedTarget:
    target_name: str
    resolution_type: str  # "uia_pattern" | "uia_coordinate" | "visual_grounding" | "cursor_point"
    desktop_x: int
    desktop_y: int
    bounding_box: Dict[str, int]
    confidence: float
    uia_node: Optional[UIElementNode] = None
    grounded_target: Optional[GroundedTarget] = None
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "target_name": self.target_name,
            "resolution_type": self.resolution_type,
            "desktop_x": self.desktop_x,
            "desktop_y": self.desktop_y,
            "bounding_box": self.bounding_box,
            "confidence": self.confidence,
            "notes": self.notes,
        }


class TargetResolver:
    """Authoritative target resolver prioritizing semantic UIA and verified visual fallback."""

    def __init__(self):
        self.display_manager = get_display_manager()
        self.ui_tree_manager = get_ui_tree_manager()
        self.visual_grounding = get_visual_grounding()

    def resolve_target(
        self,
        query: str,
        control_type: Optional[str] = None,
        automation_id: Optional[str] = None,
        parent_name: Optional[str] = None,
        prefer_visual: bool = False,
        confidence_threshold: float = 0.65,
    ) -> Tuple[Optional[ResolvedTarget], str]:
        """
        Resolve a natural language element query into an actionable desktop target.
        """
        clean_query = query.strip()
        logger.info("Resolving target for query: '%s' (control_type=%s)", clean_query, control_type)

        # 1. Check for spatial/relative keywords (e.g. "on the left", "top-right", "bottom")
        spatial_hint = self._extract_spatial_hint(clean_query)

        # 2. Priority 1: Semantic UI Automation resolution (unless prefer_visual is forced)
        if not prefer_visual and self.ui_tree_manager.is_available():
            node, reason = self.ui_tree_manager.resolve_target_element(
                name=clean_query,
                control_type=control_type,
                automation_id=automation_id,
                parent_name=parent_name,
            )
            if node and node.is_enabled and node.is_visible and not node.is_offscreen:
                cx, cy = node.center_point()
                # Validate physical coordinates against multi-monitor bounds
                valid_x, valid_y, ok, bounds_reason = windows_uia.validate_coordinates(
                    cx, cy, bounding_box=node.bounding_rectangle
                )
                if ok:
                    has_pattern = bool(node.supported_patterns)
                    res_type = "uia_pattern" if has_pattern else "uia_coordinate"
                    return ResolvedTarget(
                        target_name=node.name or clean_query,
                        resolution_type=res_type,
                        desktop_x=valid_x,
                        desktop_y=valid_y,
                        bounding_box=node.bounding_rectangle,
                        confidence=0.98,
                        uia_node=node,
                        notes=f"Resolved via UIA: {reason}",
                    ), "Success (UIA)"

        # 3. Priority 2: Gemini Visual Grounding
        logger.info("Semantic UIA did not yield high-confidence target. Engaging visual grounding...")
        try:
            grounded_list = self.visual_grounding.ground_element(
                query=clean_query,
                context_hint=f"Spatial hint: {spatial_hint}" if spatial_hint else "",
            )
            if grounded_list:
                # Filter by confidence
                viable = [g for g in grounded_list if g.confidence >= confidence_threshold]
                if viable:
                    best = viable[0]
                    # Select based on spatial hint if multiple exist
                    if len(viable) > 1 and spatial_hint:
                        best = self._pick_by_spatial_hint(viable, spatial_hint)

                    cx, cy = best.center_point()
                    # Validate against physical desktop
                    valid_x, valid_y, ok, v_reason = windows_uia.validate_coordinates(cx, cy, best.approximate_bounds)
                    if ok:
                        # Check if a UIA element aligns with this visual point
                        matching_uia = None
                        point_elem = windows_uia.find_element_at_point(valid_x, valid_y)
                        if point_elem and point_elem.get("element"):
                            matching_uia = self.ui_tree_manager.node_from_wrapper(point_elem["element"])

                        return ResolvedTarget(
                            target_name=best.target,
                            resolution_type="visual_grounding",
                            desktop_x=valid_x,
                            desktop_y=valid_y,
                            bounding_box=best.approximate_bounds,
                            confidence=best.confidence,
                            uia_node=matching_uia,
                            grounded_target=best,
                            notes=f"Visually grounded: {best.visual_description} (confidence: {best.confidence:.2f})",
                        ), "Success (Visual Grounding)"
        except Exception as e:
            logger.warning("Visual grounding attempt failed: %s", e)

        return None, f"Could not resolve interactive target for '{query}'"

    def resolve_cursor_position(self) -> ResolvedTarget:
        """
        Special resolution for 'Click here' / cursor-targeted interactions:
          1. Capture current cursor coordinates.
          2. Inspect UI Automation element directly under cursor.
          3. If UIA cannot identify it, capture localized screenshot crop and inspect.
        """
        cur_x, cur_y = self.display_manager.get_cursor_position()

        # Step 2: UIA element under cursor
        uia_node = None
        point_elem = windows_uia.find_element_at_point(cur_x, cur_y)
        if point_elem and point_elem.get("element"):
            uia_node = self.ui_tree_manager.node_from_wrapper(point_elem["element"])

        if uia_node and (uia_node.name or uia_node.control_type):
            return ResolvedTarget(
                target_name=uia_node.name or f"{uia_node.control_type} at cursor",
                resolution_type="cursor_point",
                desktop_x=cur_x,
                desktop_y=cur_y,
                bounding_box=uia_node.bounding_rectangle,
                confidence=0.99,
                uia_node=uia_node,
                notes=f"Identified control under cursor via UIA: [{uia_node.control_type}] '{uia_node.name}'",
            )

        # Step 3: Localized visual inspection around cursor
        crop_desc = self.visual_grounding.inspect_visual_crop(cur_x, cur_y)

        return ResolvedTarget(
            target_name=f"Point at ({cur_x}, {cur_y})",
            resolution_type="cursor_point",
            desktop_x=cur_x,
            desktop_y=cur_y,
            bounding_box={"left": cur_x - 10, "top": cur_y - 10, "width": 20, "height": 20},
            confidence=0.85,
            notes=f"Visual crop under cursor: {crop_desc}",
        )

    def _extract_spatial_hint(self, query: str) -> Optional[str]:
        q = query.lower()
        hints = [
            ("bottom-right", "bottom_right"),
            ("bottom right", "bottom_right"),
            ("top-right", "top_right"),
            ("top right", "top_right"),
            ("top-left", "top_left"),
            ("top left", "top_left"),
            ("bottom-left", "bottom_left"),
            ("bottom left", "bottom_left"),
            ("on the left", "left"),
            ("to the left", "left"),
            ("on the right", "right"),
            ("to the right", "right"),
            ("at the top", "top"),
            ("at the bottom", "bottom"),
            ("in the center", "center"),
        ]
        for phrase, tag in hints:
            if phrase in q:
                return tag
        return None

    def _pick_by_spatial_hint(self, targets: List[GroundedTarget], hint: str) -> GroundedTarget:
        """Select the target closest to the requested spatial sector."""
        v_bounds = self.display_manager.get_virtual_bounds()
        vw, vh = v_bounds["width"], v_bounds["height"]

        def score(t: GroundedTarget) -> float:
            cx, cy = t.center_point()
            if hint == "left":
                return cx
            elif hint == "right":
                return -cx
            elif hint == "top":
                return cy
            elif hint == "bottom":
                return -cy
            elif hint == "top_right":
                return (vw - cx) + cy
            elif hint == "bottom_right":
                return (vw - cx) + (vh - cy)
            elif hint == "top_left":
                return cx + cy
            elif hint == "bottom_left":
                return cx + (vh - cy)
            return 0.0

        return min(targets, key=score)


_DEFAULT_TARGET_RESOLVER: Optional[TargetResolver] = None


def get_target_resolver() -> TargetResolver:
    global _DEFAULT_TARGET_RESOLVER
    if _DEFAULT_TARGET_RESOLVER is None:
        _DEFAULT_TARGET_RESOLVER = TargetResolver()
    return _DEFAULT_TARGET_RESOLVER

