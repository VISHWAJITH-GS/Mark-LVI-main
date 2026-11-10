"""
core/global_input.py — Global Windows Input Controller for MARK LVI / JARVIS

Provides system-wide keyboard shortcuts and mouse gestures that work regardless
of which application currently has foreground focus (Edge, Chrome, VS Code,
WhatsApp, Spotify, LeetCode, File Explorer, Desktop, etc.), including when
JARVIS is minimized or in the background.

Features:
- Global Listening Toggle: default Ctrl + Alt + J
- Global Emergency Speech Interruption: default Ctrl + Alt + K (and Esc when focused)
- Global Simultaneous Mouse Gesture: Left Mouse Button + Right Mouse Button (configurable)
- Debounced and latched state machines to prevent repeat firing while held
- Clean shutdown and thread-safe dispatch to the voice state manager
- Extremely low CPU overhead (< 0.1% CPU via efficient 50Hz polling loop)
"""

from __future__ import annotations

import logging
import platform
import threading
import time
from typing import Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"

# ─────────────────────────────────────────────────────────────────────────────
# Virtual Key Mapping (Windows)
# ─────────────────────────────────────────────────────────────────────────────

_VK_MAP: Dict[str, int] = {
    # Modifiers
    "ctrl": 0x11,
    "control": 0x11,
    "shift": 0x10,
    "alt": 0x12,
    "menu": 0x12,
    "win": 0x5B,
    "windows": 0x5B,
    # Standard Keys
    "space": 0x20,
    "esc": 0x1B,
    "escape": 0x1B,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "backspace": 0x08,
    "insert": 0x2D,
    "delete": 0x2E,
    "capslock": 0x14,
    # Mouse Buttons
    "lbutton": 0x01,
    "rbutton": 0x02,
    "mbutton": 0x04,
}

# Add Letters A-Z
for c in range(ord('a'), ord('z') + 1):
    _VK_MAP[chr(c)] = ord(chr(c).upper())

# Add Digits 0-9
for d in range(10):
    _VK_MAP[str(d)] = 0x30 + d

# Add Function Keys F1-F12
for f in range(1, 13):
    _VK_MAP[f"f{f}"] = 0x70 + (f - 1)


def parse_chord(chord_def: str | list | tuple) -> List[int]:
    """Parse a chord string (e.g. 'ctrl+alt+j') or list into Windows virtual-key codes."""
    if isinstance(chord_def, str):
        keys = [k.strip().lower() for k in chord_def.replace("+", " ").replace("-", " ").split()]
    else:
        keys = [str(k).strip().lower() for k in chord_def]

    vk_codes = []
    for k in keys:
        if k in _VK_MAP:
            vk_codes.append(_VK_MAP[k])
        else:
            logger.warning("Unknown key in chord: '%s'", k)
    return vk_codes


# ─────────────────────────────────────────────────────────────────────────────
# Global Input Controller
# ─────────────────────────────────────────────────────────────────────────────

class GlobalInputController:
    """
    Monitors global keyboard shortcuts and mouse gestures across Windows applications.
    Dispatches thread-safe callbacks to the central voice controller.
    """

    def __init__(
        self,
        on_toggle_listening: Optional[Callable[[], None]] = None,
        on_emergency_stop: Optional[Callable[[], None]] = None,
        listening_chord: str | tuple = ("ctrl", "alt", "j"),
        emergency_chord: str | tuple = ("ctrl", "alt", "k"),
        mouse_gesture_enabled: bool = True,
        poll_hz: float = 50.0,
    ):
        self.on_toggle_listening = on_toggle_listening
        self.on_emergency_stop = on_emergency_stop

        self._listening_codes = parse_chord(listening_chord)
        self._emergency_codes = parse_chord(emergency_chord)
        self._mouse_gesture_enabled = mouse_gesture_enabled
        self._poll_hz = poll_hz

        self._listening_chord_name = "+".join(listening_chord) if isinstance(listening_chord, (list, tuple)) else str(listening_chord)
        self._emergency_chord_name = "+".join(emergency_chord) if isinstance(emergency_chord, (list, tuple)) else str(emergency_chord)

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # State latches to prevent repeated firing while held
        self._listening_latched = False
        self._emergency_latched = False
        self._mouse_latched = False
        self._listening_down_time = 0.0
        self._emergency_down_time = 0.0
        self._mouse_down_time = 0.0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def mouse_gesture_enabled(self) -> bool:
        return self._mouse_gesture_enabled

    @mouse_gesture_enabled.setter
    def mouse_gesture_enabled(self, value: bool) -> None:
        self._mouse_gesture_enabled = bool(value)

    def set_shortcuts(self, listening_chord: str | tuple, emergency_chord: str | tuple) -> None:
        """Update shortcuts at runtime."""
        with self._lock:
            self._listening_codes = parse_chord(listening_chord)
            self._emergency_codes = parse_chord(emergency_chord)
            self._listening_chord_name = "+".join(listening_chord) if isinstance(listening_chord, (list, tuple)) else str(listening_chord)
            self._emergency_chord_name = "+".join(emergency_chord) if isinstance(emergency_chord, (list, tuple)) else str(emergency_chord)
        logger.info("[GlobalInput] Shortcuts updated: Toggle=%s, Stop=%s", self._listening_chord_name, self._emergency_chord_name)

    def start(self) -> bool:
        """Start the global input listener thread."""
        if not _IS_WINDOWS:
            logger.warning("[GlobalInput] Global Windows input hook is only supported on Windows.")
            return False

        with self._lock:
            if self.is_running:
                return True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._monitor_loop,
                name="jarvis-global-input",
                daemon=True,
            )
            self._thread.start()
            logger.info("[GlobalInput] Started global input listener (Toggle: %s, Stop: %s, MouseGesture: %s)",
                        self._listening_chord_name, self._emergency_chord_name, self._mouse_gesture_enabled)
            return True

    def stop(self) -> None:
        """Stop the global input listener thread."""
        self._stop_event.set()
        t = self._thread
        self._thread = None
        if t and t.is_alive():
            t.join(timeout=1.0)
        logger.info("[GlobalInput] Stopped global input listener.")

    # ── Input Loop ───────────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        import ctypes
        user32 = ctypes.windll.user32
        period = 1.0 / max(10.0, self._poll_hz)

        VK_LBUTTON = 0x01
        VK_RBUTTON = 0x02

        while not self._stop_event.is_set():
            try:
                # 1. Check Global Listening Toggle (Ctrl + Alt + J)
                if self._listening_codes and len(self._listening_codes) >= 2:
                    listening_down = all(bool(user32.GetAsyncKeyState(vk) & 0x8000) for vk in self._listening_codes)
                    now = time.monotonic()
                    if listening_down:
                        if not self._listening_latched:
                            if self._listening_down_time == 0.0:
                                self._listening_down_time = now
                            elif now - self._listening_down_time >= 0.04:
                                self._listening_latched = True
                                self._dispatch(self.on_toggle_listening, "Global Listening Toggle")
                    else:
                        self._listening_latched = False
                        self._listening_down_time = 0.0

                # 2. Check Global Emergency Stop Speaking (Ctrl + Alt + K)
                if self._emergency_codes and len(self._emergency_codes) >= 1:
                    emergency_down = all(bool(user32.GetAsyncKeyState(vk) & 0x8000) for vk in self._emergency_codes)
                    now = time.monotonic()
                    if emergency_down:
                        if not self._emergency_latched:
                            if self._emergency_down_time == 0.0:
                                self._emergency_down_time = now
                            elif now - self._emergency_down_time >= 0.04:
                                self._emergency_latched = True
                                self._dispatch(self.on_emergency_stop, "Global Emergency Stop")
                    else:
                        self._emergency_latched = False
                        self._emergency_down_time = 0.0

                # 3. Check Optional Mouse Gesture (Left + Right Click simultaneously)
                if self._mouse_gesture_enabled:
                    l_down = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
                    r_down = bool(user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000)

                    if l_down and r_down:
                        now = time.monotonic()
                        if not self._mouse_latched:
                            if self._mouse_down_time == 0.0:
                                self._mouse_down_time = now
                            # Short debounce (~40ms) to ensure it is a deliberate simultaneous click
                            elif now - self._mouse_down_time >= 0.04:
                                self._mouse_latched = True
                                self._dispatch(self.on_toggle_listening, "Mouse Gesture (L+R Click)")
                    else:
                        # Unlatch ONLY when BOTH buttons have been released
                        if not l_down and not r_down:
                            self._mouse_latched = False
                            self._mouse_down_time = 0.0

            except Exception as e:
                logger.debug("[GlobalInput] Monitor tick exception: %s", e)

            self._stop_event.wait(period)

    def _dispatch(self, callback: Optional[Callable[[], None]], name: str) -> None:
        if not callback:
            return
        logger.info("[GlobalInput] Triggered: %s", name)
        # Dispatch on worker thread to prevent blocking the input monitor loop
        def _run():
            try:
                callback()
            except Exception as e:
                logger.error("[GlobalInput] Error in callback for %s: %s", name, e)
        threading.Thread(target=_run, name=f"input-dispatch-{name}", daemon=True).start()

