"""
core/voice_state.py — Centralized Voice State Management for MARK LVI / JARVIS

Manages the 4 authoritative voice states:
- DISABLED:   Microphone capture stopped, wake-word stopped, no audio processing,
              voice output stopped, queues cleared.
- LISTENING:  Microphone capture active, wake-word active, waiting for speech.
- PROCESSING: Command received, thinking/synthesizing response.
- SPEAKING:   Audio response playing, cancellable immediately by emergency stop.

Enforces thread-safe transitions and dispatches event notifications to audio I/O,
UI elements, and the Gemini Live conversation loop.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class VoiceState(str, enum.Enum):
    DISABLED = "DISABLED"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"


class VoiceStateManager:
    """
    Central thread-safe voice state machine for JARVIS.
    Coordinates microphone gating, speech interruption, and UI feedback.
    """

    def __init__(
        self,
        initial_state: VoiceState = VoiceState.LISTENING,
        on_state_change: Optional[Callable[[VoiceState, VoiceState], None]] = None,
        on_interrupt: Optional[Callable[[], None]] = None,
    ):
        self._state = initial_state
        self._lock = threading.RLock()
        self._generation_id = 0
        self._on_state_change = on_state_change
        self._on_interrupt = on_interrupt
        self._in_interrupt = False
        self._last_state_change = time.monotonic()

    # ── State Inspection ─────────────────────────────────────────────────────

    @property
    def state(self) -> VoiceState:
        with self._lock:
            return self._state

    @property
    def is_disabled(self) -> bool:
        with self._lock:
            return self._state == VoiceState.DISABLED

    @property
    def is_listening(self) -> bool:
        with self._lock:
            return self._state == VoiceState.LISTENING

    @property
    def is_processing(self) -> bool:
        with self._lock:
            return self._state == VoiceState.PROCESSING

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self._state == VoiceState.SPEAKING

    @property
    def generation_id(self) -> int:
        """Generation counter incremented on speech interruption to discard stale chunks."""
        with self._lock:
            return self._generation_id

    # ── Transition Methods ───────────────────────────────────────────────────

    def enable_listening(self) -> bool:
        """
        Transition from DISABLED to LISTENING.
        Returns True if state changed, False if already enabled.
        """
        with self._lock:
            if self._state == VoiceState.DISABLED:
                self._transition_to(VoiceState.LISTENING)
                return True
            return False

    def disable_listening(self) -> bool:
        """
        Transition from any active state to DISABLED.
        Immediately halts microphone ingestion, cancels any active speech output,
        and clears audio pipelines.
        """
        with self._lock:
            if self._state == VoiceState.DISABLED:
                return False

            was_speaking = (self._state == VoiceState.SPEAKING or self._state == VoiceState.PROCESSING)
            if was_speaking:
                self._trigger_interrupt()

            self._transition_to(VoiceState.DISABLED)
            return True

    def toggle_listening(self) -> VoiceState:
        """
        Toggle between DISABLED and LISTENING.
        Returns the new state.
        """
        with self._lock:
            if self._state == VoiceState.DISABLED:
                self.enable_listening()
            else:
                self.disable_listening()
            return self._state

    def start_processing(self) -> bool:
        """
        Transition from LISTENING to PROCESSING when user speech is detected/submitted.
        """
        with self._lock:
            if self._state == VoiceState.LISTENING:
                self._transition_to(VoiceState.PROCESSING)
                return True
            return False

    def start_speaking(self) -> bool:
        """
        Transition from PROCESSING (or LISTENING) to SPEAKING when audio chunks begin playback.
        """
        with self._lock:
            if self._state in (VoiceState.PROCESSING, VoiceState.LISTENING):
                self._transition_to(VoiceState.SPEAKING)
                return True
            return False

    def finish_speaking(self) -> bool:
        """
        Transition from SPEAKING back to LISTENING when audio playback finishes normally.
        """
        with self._lock:
            if self._state == VoiceState.SPEAKING:
                self._transition_to(VoiceState.LISTENING)
                return True
            return False

    def emergency_stop_speech(self) -> bool:
        """
        Immediately interrupt speech playback and return to LISTENING if enabled.
        If DISABLED, remains DISABLED.
        Increments generation_id to invalidate queued/buffered chunks.
        """
        with self._lock:
            interrupted = False
            if self._state in (VoiceState.SPEAKING, VoiceState.PROCESSING):
                self._trigger_interrupt()
                interrupted = True
                self._transition_to(VoiceState.LISTENING)
            elif self._state == VoiceState.DISABLED:
                # Still ensure generation token advances and queues flush
                self._trigger_interrupt()
            return interrupted

    # ── Internal Helpers ─────────────────────────────────────────────────────

    def _trigger_interrupt(self) -> None:
        self._generation_id += 1
        if self._on_interrupt:
            try:
                self._on_interrupt()
            except Exception as e:
                logger.warning("Error in on_interrupt callback: %s", e)

    def _transition_to(self, new_state: VoiceState) -> None:
        old_state = self._state
        if old_state == new_state:
            return
        self._state = new_state
        self._last_state_change = time.monotonic()
        logger.info("[VoiceState] %s -> %s (gen=%d)", old_state.value, new_state.value, self._generation_id)
        if self._on_state_change:
            try:
                self._on_state_change(old_state, new_state)
            except Exception as e:
                logger.warning("Error in on_state_change callback: %s", e)

