"""
core/skill_learning/observation_engine.py — Observation Lifecycle & Event Filtering Engine.

Orchestrates the recording lifecycle and cleans raw events:
- Filters out duplicate/redundant focus events
- Consolidates sequential typing into cohesive input blocks
- Synthesizes semantic interaction traces ready for Gemini demonstration analysis
- Redacts sensitive credentials
"""
from __future__ import annotations

import time
from typing import Callable, List, Optional

from core.skill_learning.event_recorder import EventRecorder
from core.skill_learning.models import UIEvent


class ObservationEngine:
    """Manages demonstration observation lifecycle and event trace consolidation."""

    def __init__(self, on_event_cb: Optional[Callable[[UIEvent], None]] = None):
        self.recorder = EventRecorder(on_event_cb=on_event_cb)
        self.is_active = False

    def start_observation(self) -> None:
        """Start capturing demonstration."""
        self.recorder.start()
        self.is_active = True

    start_recording = start_observation

    def pause_observation(self) -> None:
        self.recorder.pause()

    pause = pause_observation

    def resume_observation(self) -> None:
        self.recorder.resume()

    resume = resume_observation

    def stop_observation(self) -> list[UIEvent]:
        """Stop capturing and return consolidated events."""
        raw_events = self.recorder.stop()
        self.is_active = False
        return self.consolidate_events(raw_events)

    stop_recording = stop_observation

    def cancel(self) -> None:
        """Cancel recording and discard events."""
        self.recorder.stop()
        self.is_active = False

    def consolidate_events(self, raw_events: list[UIEvent]) -> list[UIEvent]:
        """
        Clean and consolidate raw events:
        - Drops immediate duplicate window activations
        - Merges sequential keystrokes on the same element
        - Eliminates jitter
        """
        if not raw_events:
            return []

        consolidated: list[UIEvent] = []
        for evt in raw_events:
            if not consolidated:
                consolidated.append(evt)
                continue

            last = consolidated[-1]

            # Merge duplicate window activations
            if evt.event_type == "window_activate" and last.event_type == "window_activate":
                if evt.window_title == last.window_title and evt.app_name == last.app_name:
                    continue

            # Merge sequential text input into the same control
            if evt.event_type == "type_text" and last.event_type == "type_text":
                if (
                    evt.app_name == last.app_name
                    and evt.element_name == last.element_name
                    and evt.selector == last.selector
                    and (evt.timestamp - last.timestamp) < 3.0
                ):
                    # Concatenate or replace typed input
                    last.input_value = evt.input_value if evt.input_value.startswith(last.input_value) else f"{last.input_value} {evt.input_value}".strip()
                    last.timestamp = evt.timestamp
                    continue

            consolidated.append(evt)

        return consolidated

    def format_trace_for_analysis(self, events: list[UIEvent]) -> str:
        """Format event trace as clean readable YAML/markdown for Gemini interpretation."""
        lines = []
        for idx, e in enumerate(events, 1):
            ts_str = f"+{e.timestamp - events[0].timestamp:.1f}s" if events else "0.0s"
            desc = f"Step {idx} [{ts_str}]: Event='{e.event_type}' on App='{e.app_name}' (Window='{e.window_title}')"
            if e.element_name:
                desc += f" -> Element='{e.element_name}' [Type='{e.control_type}']"
            if e.input_value:
                desc += f" -> Typed='{e.input_value}'"
            if e.selector:
                desc += f" (Selector: {e.selector})"
            lines.append(desc)
        return "\n".join(lines)

    debounce_events = consolidate_events
    get_trace_summary = format_trace_for_analysis
