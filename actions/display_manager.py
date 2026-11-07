"""
actions/display_manager.py — Full Desktop Perception & Multi-Monitor Display Subsystem.

Provides:
  1. Multi-monitor enumeration with DPI, physical origins, and negative coordinate support.
  2. Full desktop & per-monitor screenshot capture using MSS (preserving native resolution).
  3. Optimized preview generation for Gemini multimodal inspection without loss of original.
  4. High-resolution localized crop extraction around points of interest or cursor.
  5. Bidirectional coordinate transformation between physical desktop space, full-res screenshots,
     preview dimensions, and crops.
  6. Mouse cursor position retrieval and active window bounding rectangle tracking.
"""
from __future__ import annotations

import io
import os
import platform
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_IS_WINDOWS = platform.system() == "Windows"

# Imports
try:
    import mss
    import mss.tools
    _MSS_AVAILABLE = True
except ImportError:
    _MSS_AVAILABLE = False

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes
    try:
        import win32gui
        import win32process
        import win32api
        import win32con
        _WIN32_AVAILABLE = True
    except ImportError:
        _WIN32_AVAILABLE = False
else:
    _WIN32_AVAILABLE = False


@dataclass
class MonitorInfo:
    index: int
    left: int
    top: int
    width: int
    height: int
    right: int = 0
    bottom: int = 0
    is_primary: bool = False
    name: str = ""
    scale_factor: float = 1.0

    def __post_init__(self):
        self.right = self.left + self.width
        self.bottom = self.top + self.height

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
            "right": self.right,
            "bottom": self.bottom,
            "is_primary": self.is_primary,
            "name": self.name,
            "scale_factor": self.scale_factor,
        }


@dataclass
class CapturedScreenshot:
    image_bytes: bytes
    format: str  # "png" | "jpeg"
    width: int
    height: int
    origin_x: int  # Desktop X coordinate corresponding to image (0, 0)
    origin_y: int  # Desktop Y coordinate corresponding to image (0, 0)
    monitor_index: int  # 0 for all monitors combined
    timestamp: float = field(default_factory=time.time)
    scale_w: float = 1.0  # preview scale factor if downscaled
    scale_h: float = 1.0

    def desktop_to_image(self, x: int, y: int) -> Tuple[int, int]:
        """Convert desktop screen coordinate to this image coordinate."""
        ix = int((x - self.origin_x) * self.scale_w)
        iy = int((y - self.origin_y) * self.scale_h)
        return ix, iy

    def image_to_desktop(self, ix: int, iy: int) -> Tuple[int, int]:
        """Convert image coordinate back to desktop physical coordinate."""
        x = int(ix / self.scale_w + self.origin_x)
        y = int(iy / self.scale_h + self.origin_y)
        return x, y


class DisplayManager:
    """Central display and monitor perception manager."""

    def __init__(self):
        self._last_capture: Optional[CapturedScreenshot] = None
        self._last_original: Optional[CapturedScreenshot] = None

    def get_monitors(self) -> List[MonitorInfo]:
        """Enumerate all connected monitors with origin, dimensions, and DPI scale."""
        if not _MSS_AVAILABLE:
            return [
                MonitorInfo(index=1, left=0, top=0, width=1920, height=1080, is_primary=True, name="Default")
            ]

        with mss.mss() as sct:
            raw_monitors = sct.monitors

        scale_factor = self.get_primary_scale_factor()
        monitors: List[MonitorInfo] = []

        # raw_monitors[0] is all combined; 1..n are individual monitors
        if len(raw_monitors) > 1:
            for idx, m in enumerate(raw_monitors[1:], start=1):
                is_pri = bool(m.get("is_primary") or (m["left"] == 0 and m["top"] == 0))
                monitors.append(
                    MonitorInfo(
                        index=idx,
                        left=m["left"],
                        top=m["top"],
                        width=m["width"],
                        height=m["height"],
                        is_primary=is_pri,
                        name=m.get("name", f"Display {idx}"),
                        scale_factor=scale_factor,
                    )
                )
        elif raw_monitors:
            m = raw_monitors[0]
            monitors.append(
                MonitorInfo(
                    index=1,
                    left=m["left"],
                    top=m["top"],
                    width=m["width"],
                    height=m["height"],
                    is_primary=True,
                    name="Primary Display",
                    scale_factor=scale_factor,
                )
            )

        return monitors

    def get_virtual_bounds(self) -> Dict[str, int]:
        """Return the bounding box of the virtual desktop encompassing all monitors."""
        monitors = self.get_monitors()
        if not monitors:
            return {"left": 0, "top": 0, "width": 1920, "height": 1080, "right": 1920, "bottom": 1080}

        min_left = min(m.left for m in monitors)
        min_top = min(m.top for m in monitors)
        max_right = max(m.right for m in monitors)
        max_bottom = max(m.bottom for m in monitors)

        return {
            "left": min_left,
            "top": min_top,
            "width": max_right - min_left,
            "height": max_bottom - min_top,
            "right": max_right,
            "bottom": max_bottom,
        }

    def get_primary_scale_factor(self) -> float:
        """Get DPI scaling factor of the primary display."""
        if not _IS_WINDOWS:
            return 1.0
        try:
            percent = ctypes.windll.shcore.GetScaleFactorForDevice(0) or 100
            return percent / 100.0
        except Exception:
            return 1.0

    def get_cursor_position(self) -> Tuple[int, int]:
        """Get the current cursor position in desktop coordinates."""
        if _IS_WINDOWS and _WIN32_AVAILABLE:
            try:
                pt = win32gui.GetCursorPos()
                return int(pt[0]), int(pt[1])
            except Exception:
                pass
        if _IS_WINDOWS:
            try:
                class POINT(ctypes.Structure):
                    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
                pt = POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                return int(pt.x), int(pt.y)
            except Exception:
                pass
        return 0, 0

    def get_active_window_info(self) -> dict:
        """Get title, process name, handle, and bounding box of active window."""
        if not (_IS_WINDOWS and _WIN32_AVAILABLE):
            return {"title": "", "handle": 0, "rect": None, "process_name": ""}

        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd or not win32gui.IsWindow(hwnd):
                return {"title": "", "handle": 0, "rect": None, "process_name": ""}

            title = win32gui.GetWindowText(hwnd).strip()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            rect = win32gui.GetWindowRect(hwnd)  # (left, top, right, bottom)
            proc_name = ""
            try:
                import psutil
                proc_name = psutil.Process(pid).name()
            except Exception:
                pass

            bounds = {
                "left": rect[0],
                "top": rect[1],
                "right": rect[2],
                "bottom": rect[3],
                "width": rect[2] - rect[0],
                "height": rect[3] - rect[1],
            }

            return {
                "title": title,
                "handle": hwnd,
                "pid": pid,
                "process_name": proc_name,
                "bounds": bounds,
            }
        except Exception as e:
            return {"title": "", "handle": 0, "rect": None, "process_name": "", "error": str(e)}

    def capture_screenshot(
        self,
        monitor_index: int = 0,
        region: Optional[Dict[str, int]] = None,
    ) -> CapturedScreenshot:
        """
        Capture native full-resolution screenshot without permanent downscaling.
        monitor_index=0 captures the entire virtual desktop (all monitors).
        """
        if not _MSS_AVAILABLE:
            raise RuntimeError("MSS is not installed. Run: pip install mss")
        if not _PIL_AVAILABLE:
            raise RuntimeError("PIL (Pillow) is not installed. Run: pip install pillow")

        with mss.mss() as sct:
            if region:
                # Custom region bbox {left, top, width, height}
                monitor_dict = {
                    "left": int(region["left"]),
                    "top": int(region["top"]),
                    "width": int(region["width"]),
                    "height": int(region["height"]),
                }
                origin_x = monitor_dict["left"]
                origin_y = monitor_dict["top"]
            elif monitor_index == 0:
                # All monitors combined
                monitor_dict = sct.monitors[0]
                origin_x = monitor_dict["left"]
                origin_y = monitor_dict["top"]
            else:
                idx = min(monitor_index, len(sct.monitors) - 1)
                monitor_dict = sct.monitors[idx]
                origin_x = monitor_dict["left"]
                origin_y = monitor_dict["top"]

            raw_shot = sct.grab(monitor_dict)
            pil_img = Image.frombytes("RGB", raw_shot.size, raw_shot.bgra, "raw", "BGRX")

            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            img_bytes = buf.getvalue()

            captured = CapturedScreenshot(
                image_bytes=img_bytes,
                format="png",
                width=pil_img.width,
                height=pil_img.height,
                origin_x=origin_x,
                origin_y=origin_y,
                monitor_index=monitor_index,
            )
            self._last_original = captured
            return captured

    def generate_preview(
        self,
        original: Optional[CapturedScreenshot] = None,
        max_width: int = 1440,
        max_height: int = 900,
        quality: int = 85,
    ) -> CapturedScreenshot:
        """
        Generate an optimized preview for Gemini multimodal inference while preserving coordinate mapping.
        """
        if not _PIL_AVAILABLE:
            raise RuntimeError("Pillow is required for preview generation.")

        orig = original or self._last_original or self.capture_screenshot()
        pil_img = Image.open(io.BytesIO(orig.image_bytes)).convert("RGB")

        orig_w, orig_h = pil_img.width, pil_img.height

        # Calculate aspect ratio scaling
        scale = min(max_width / orig_w, max_height / orig_h, 1.0)
        target_w = max(1, int(orig_w * scale))
        target_h = max(1, int(orig_h * scale))

        if scale < 1.0:
            resized_img = pil_img.resize((target_w, target_h), Image.Resampling.BILINEAR)
        else:
            resized_img = pil_img

        buf = io.BytesIO()
        resized_img.save(buf, format="JPEG", quality=quality)
        preview_bytes = buf.getvalue()

        preview = CapturedScreenshot(
            image_bytes=preview_bytes,
            format="jpeg",
            width=target_w,
            height=target_h,
            origin_x=orig.origin_x,
            origin_y=orig.origin_y,
            monitor_index=orig.monitor_index,
            scale_w=target_w / orig_w,
            scale_h=target_h / orig_h,
        )
        self._last_capture = preview
        return preview

    def crop_region(
        self,
        center_x: int,
        center_y: int,
        width: int = 400,
        height: int = 300,
        original: Optional[CapturedScreenshot] = None,
    ) -> CapturedScreenshot:
        """
        Generate a high-resolution localized crop around a specific desktop coordinate (e.g. around cursor).
        """
        if not _PIL_AVAILABLE:
            raise RuntimeError("Pillow is required for image cropping.")

        orig = original or self._last_original or self.capture_screenshot()
        pil_img = Image.open(io.BytesIO(orig.image_bytes)).convert("RGB")

        # Desktop center to image coords
        ix = center_x - orig.origin_x
        iy = center_y - orig.origin_y

        crop_left = max(0, ix - width // 2)
        crop_top = max(0, iy - height // 2)
        crop_right = min(pil_img.width, crop_left + width)
        crop_bottom = min(pil_img.height, crop_top + height)

        cropped_img = pil_img.crop((crop_left, crop_top, crop_right, crop_bottom))

        buf = io.BytesIO()
        cropped_img.save(buf, format="PNG")

        return CapturedScreenshot(
            image_bytes=buf.getvalue(),
            format="png",
            width=cropped_img.width,
            height=cropped_img.height,
            origin_x=orig.origin_x + crop_left,
            origin_y=orig.origin_y + crop_top,
            monitor_index=orig.monitor_index,
            scale_w=1.0,
            scale_h=1.0,
        )


_DEFAULT_DISPLAY_MANAGER: Optional[DisplayManager] = None


def get_display_manager() -> DisplayManager:
    global _DEFAULT_DISPLAY_MANAGER
    if _DEFAULT_DISPLAY_MANAGER is None:
        _DEFAULT_DISPLAY_MANAGER = DisplayManager()
    return _DEFAULT_DISPLAY_MANAGER

