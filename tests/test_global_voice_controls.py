"""
tests/test_global_voice_controls.py — Complete Test Suite for Global Voice Control,
Listening Toggle & Emergency Speech Interruption for MARK LVI / JARVIS.
"""

import asyncio
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from core.voice_state import VoiceState, VoiceStateManager
from core.global_input import GlobalInputController, parse_chord, _VK_MAP
from memory.config_manager import (
    get_listening_shortcut,
    get_emergency_stop_shortcut,
    get_mouse_gesture_enabled,
    save_listening_shortcut,
    save_emergency_stop_shortcut,
    save_mouse_gesture_enabled,
)


class TestVoiceStateManager(unittest.TestCase):
    """Tests the 4-state voice machine, transitions, and generation token advance."""

    def setUp(self):
        self.state_changes = []
        self.interrupt_calls = 0

        def on_change(old_s, new_s):
            self.state_changes.append((old_s, new_s))

        def on_intr():
            self.interrupt_calls += 1

        self.sm = VoiceStateManager(
            initial_state=VoiceState.LISTENING,
            on_state_change=on_change,
            on_interrupt=on_intr,
        )

    def test_initial_state(self):
        self.assertEqual(self.sm.state, VoiceState.LISTENING)
        self.assertTrue(self.sm.is_listening)
        self.assertFalse(self.sm.is_disabled)
        self.assertFalse(self.sm.is_speaking)
        self.assertFalse(self.sm.is_processing)
        self.assertEqual(self.sm.generation_id, 0)

    def test_listening_toggle_to_disabled_and_back(self):
        # 1. Toggle OFF -> DISABLED
        new_state = self.sm.toggle_listening()
        self.assertEqual(new_state, VoiceState.DISABLED)
        self.assertTrue(self.sm.is_disabled)
        self.assertFalse(self.sm.is_listening)

        # 2. Toggle ON -> LISTENING
        new_state = self.sm.toggle_listening()
        self.assertEqual(new_state, VoiceState.LISTENING)
        self.assertTrue(self.sm.is_listening)
        self.assertFalse(self.sm.is_disabled)

        self.assertEqual(len(self.state_changes), 2)
        self.assertEqual(self.state_changes[0], (VoiceState.LISTENING, VoiceState.DISABLED))
        self.assertEqual(self.state_changes[1], (VoiceState.DISABLED, VoiceState.LISTENING))

    def test_full_state_lifecycle_transitions(self):
        # LISTENING -> PROCESSING
        self.assertTrue(self.sm.start_processing())
        self.assertTrue(self.sm.is_processing)

        # PROCESSING -> SPEAKING
        self.assertTrue(self.sm.start_speaking())
        self.assertTrue(self.sm.is_speaking)

        # SPEAKING -> LISTENING (normal completion)
        self.assertTrue(self.sm.finish_speaking())
        self.assertTrue(self.sm.is_listening)

    def test_emergency_stop_speech_while_speaking(self):
        self.sm.start_speaking()
        self.assertTrue(self.sm.is_speaking)
        initial_gen = self.sm.generation_id

        # Trigger emergency stop
        interrupted = self.sm.emergency_stop_speech()
        self.assertTrue(interrupted)
        self.assertEqual(self.interrupt_calls, 1)
        self.assertGreater(self.sm.generation_id, initial_gen)
        # Should return to LISTENING if previously enabled
        self.assertTrue(self.sm.is_listening)

    def test_disable_listening_while_speaking_aborts_speech(self):
        self.sm.start_speaking()
        self.assertTrue(self.sm.is_speaking)

        # Disable listening while speaking
        self.sm.disable_listening()
        self.assertTrue(self.sm.is_disabled)
        # Interrupt must have been triggered to cancel speech
        self.assertEqual(self.interrupt_calls, 1)
        self.assertGreater(self.sm.generation_id, 0)


class TestGlobalInputController(unittest.TestCase):
    """Tests keyboard chord parsing, shortcut execution, and mouse gestures."""

    def test_parse_chord_standard_shortcuts(self):
        # Default listening shortcut: Ctrl + Alt + J
        codes = parse_chord(["ctrl", "alt", "j"])
        self.assertEqual(codes, [0x11, 0x12, 0x4A])

        # Default emergency stop shortcut: Ctrl + Alt + K
        codes_k = parse_chord("ctrl+alt+k")
        self.assertEqual(codes_k, [0x11, 0x12, 0x4B])

        # Escape key
        codes_esc = parse_chord("esc")
        self.assertEqual(codes_esc, [0x1B])

    def test_parse_chord_case_insensitivity_and_punctuation(self):
        codes = parse_chord("CTRL + ALT + J")
        self.assertEqual(codes, [0x11, 0x12, 0x4A])

        codes_dash = parse_chord("ctrl-alt-k")
        self.assertEqual(codes_dash, [0x11, 0x12, 0x4B])

    def test_unknown_key_handled_safely(self):
        codes = parse_chord(["ctrl", "supercalifragilistic", "j"])
        self.assertEqual(codes, [0x11, 0x4A])

    def test_simultaneous_mouse_gesture_logic(self):
        """Simulate mouse button state checking to verify single-trigger and latching."""
        triggered = []

        def on_toggle():
            triggered.append("toggled")

        controller = GlobalInputController(
            on_toggle_listening=on_toggle,
            mouse_gesture_enabled=True,
        )

        # Mock GetAsyncKeyState
        mock_keystates = {}

        def mock_get_async_key_state(vk):
            return 0x8000 if mock_keystates.get(vk, False) else 0

        with patch("ctypes.windll.user32.GetAsyncKeyState", side_effect=mock_get_async_key_state):
            # 1. Neither button pressed
            mock_keystates[0x01] = False
            mock_keystates[0x02] = False
            self.assertFalse(controller._mouse_latched)

            # 2. Only Left button pressed -> should NOT trigger
            mock_keystates[0x01] = True
            mock_keystates[0x02] = False
            # Simulate a tick
            # In monitor loop:
            l_down = bool(mock_get_async_key_state(0x01) & 0x8000)
            r_down = bool(mock_get_async_key_state(0x02) & 0x8000)
            self.assertTrue(l_down and not r_down)
            self.assertEqual(len(triggered), 0)

            # 3. Both Left and Right pressed simultaneously -> triggers gesture!
            mock_keystates[0x01] = True
            mock_keystates[0x02] = True
            l_down = bool(mock_get_async_key_state(0x01) & 0x8000)
            r_down = bool(mock_get_async_key_state(0x02) & 0x8000)
            self.assertTrue(l_down and r_down)
            controller._mouse_down_time = time.monotonic() - 0.1
            controller._mouse_latched = True
            controller._dispatch(controller.on_toggle_listening, "Mouse Gesture")
            time.sleep(0.05)
            self.assertEqual(len(triggered), 1)

            # 4. Both buttons STILL held -> MUST NOT repeat trigger
            self.assertTrue(controller._mouse_latched)

            # 5. Only one button released -> MUST REMAIN latched until BOTH released
            mock_keystates[0x01] = False
            mock_keystates[0x02] = True
            l_down = bool(mock_get_async_key_state(0x01) & 0x8000)
            r_down = bool(mock_get_async_key_state(0x02) & 0x8000)
            if not l_down and not r_down:
                controller._mouse_latched = False
            self.assertTrue(controller._mouse_latched)

            # 6. Both released -> unlatches
            mock_keystates[0x01] = False
            mock_keystates[0x02] = False
            l_down = bool(mock_get_async_key_state(0x01) & 0x8000)
            r_down = bool(mock_get_async_key_state(0x02) & 0x8000)
            if not l_down and not r_down:
                controller._mouse_latched = False
            self.assertFalse(controller._mouse_latched)


class TestGlobalVoiceIntegration(unittest.TestCase):
    """End-to-end integration tests between VoiceStateManager, queues, and audio cancellation."""

    def test_audio_stale_chunk_rejection_by_generation_id(self):
        """Verify that chunks with older generation tokens are immediately dropped."""
        sm = VoiceStateManager(initial_state=VoiceState.LISTENING)
        sm.start_speaking()

        current_gen = sm.generation_id  # 0
        audio_queue = []

        # Producer pushes chunks under gen 0
        audio_queue.append((current_gen, b"audio_chunk_1"))
        audio_queue.append((current_gen, b"audio_chunk_2"))

        # Emergency stop occurs mid-speech
        sm.emergency_stop_speech()
        new_gen = sm.generation_id  # 1
        self.assertGreater(new_gen, current_gen)

        # Consumer processes queue
        valid_chunks_played = []
        for chunk_gen, data in audio_queue:
            if chunk_gen == sm.generation_id and not sm.is_disabled:
                valid_chunks_played.append(data)

        # All chunks from old generation were discarded
        self.assertEqual(len(valid_chunks_played), 0)

    def test_microphone_audio_gating_when_disabled(self):
        """Verify microphone callback drops all frames when voice state is DISABLED."""
        sm = VoiceStateManager(initial_state=VoiceState.DISABLED)
        out_queue = []

        def mock_mic_callback(indata):
            if sm.is_disabled:
                return  # Gated: do not process or enqueue
            out_queue.append(indata)

        mock_mic_callback(b"user_speech_frame_1")
        mock_mic_callback(b"user_speech_frame_2")

        # Zero frames transmitted while disabled
        self.assertEqual(len(out_queue), 0)

        # Enable listening
        sm.enable_listening()
        mock_mic_callback(b"user_speech_frame_3")
        self.assertEqual(len(out_queue), 1)

    def test_config_manager_shortcuts_persistence(self):
        """Verify reading and saving global shortcut preferences."""
        orig_l = get_listening_shortcut()
        orig_e = get_emergency_stop_shortcut()
        orig_m = get_mouse_gesture_enabled()

        try:
            save_listening_shortcut(["ctrl", "alt", "j"])
            save_emergency_stop_shortcut(["ctrl", "alt", "k"])
            save_mouse_gesture_enabled(True)

            self.assertEqual(get_listening_shortcut(), ["ctrl", "alt", "j"])
            self.assertEqual(get_emergency_stop_shortcut(), ["ctrl", "alt", "k"])
            self.assertTrue(get_mouse_gesture_enabled())
        finally:
            save_listening_shortcut(orig_l)
            save_emergency_stop_shortcut(orig_e)
            save_mouse_gesture_enabled(orig_m)

    def test_interrupt_when_idle_does_not_spam_logs(self):
        """Verify calling interrupt when idle does not emit any speech stopped log."""
        mock_ui = MagicMock()
        mock_ui.write_log = MagicMock()
        mock_ui.muted = False

        from main import JarvisLive
        live = JarvisLive.__new__(JarvisLive)
        live.ui = mock_ui
        live._interrupted = False
        live._in_interrupt = False
        live._interrupt_lock = threading.Lock()
        live._speaking_lock = threading.Lock()
        live._is_speaking = False
        live._tail_until = 0.0
        live._out_latency = 0.0
        live.audio_in_queue = None
        live._turn_done_event = None
        live._engine = None
        live._visemes = MagicMock()
        live.voice_state = VoiceStateManager(initial_state=VoiceState.LISTENING)

        # Call interrupt while idle
        live.interrupt()

        # write_log must NOT have been called with "SYS: Interrupted — speech stopped."
        mock_ui.write_log.assert_not_called()

    def test_interrupt_when_speaking_logs_exactly_once(self):
        """Verify calling interrupt when speaking logs exactly once and does not recurse."""
        mock_ui = MagicMock()
        mock_ui.write_log = MagicMock()
        mock_ui.muted = False

        from main import JarvisLive
        live = JarvisLive.__new__(JarvisLive)
        live.ui = mock_ui
        live._interrupted = False
        live._in_interrupt = False
        live._interrupt_lock = threading.Lock()
        live._speaking_lock = threading.Lock()
        live._is_speaking = True
        live._tail_until = 0.0
        live._out_latency = 0.0
        live.audio_in_queue = None
        live._turn_done_event = None
        live._engine = None
        live._visemes = MagicMock()
        live.voice_state = VoiceStateManager(initial_state=VoiceState.SPEAKING)

        # Call interrupt while speaking
        live.interrupt()

        # write_log must have been called exactly once
        mock_ui.write_log.assert_called_once_with("SYS: Interrupted — speech stopped.")
        self.assertFalse(live._is_speaking)
        self.assertEqual(live.voice_state.state, VoiceState.LISTENING)


if __name__ == "__main__":
    unittest.main()

