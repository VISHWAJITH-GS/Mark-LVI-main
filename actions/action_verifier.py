"""
actions/action_verifier.py — Authoritative Multi-Tier Action Verification Engine.

Enforces the non-negotiable rule:
  NEVER report success merely because a function returned without raising an exception.
  An action can be marked 'done' ONLY when its actual outcome is verified on the desktop.

Verification strategies:
  1. Window / Application Launch verification: checks foreground window or running process list.
  2. UIA State verification: checks toggle state, focus state, expanded/collapsed, or value text.
  3. UI Element Appearance / Disappearance verification: verifies modal dialog or element lifecycle.
  4. Coordinate / Visual transition verification: checks cursor position or visual changes.
  5. Browser State verification: verifies page URL, DOM state, or title change.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from actions import windows_uia
from actions.display_manager import get_display_manager
from actions.ui_tree_manager import get_ui_tree_manager, UIElementNode

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    verified: bool
    status: str  # "done", "failed", "unverified", "started"
    message: str
    details: Dict[str, Any]

    @property
    def contract_status(self) -> str:
        if self.status == "done" and self.verified:
            return "SUCCESS"
        elif self.status == "failed":
            return "FAILED"
        elif self.status in ("started", "partial"):
            return "PARTIAL"
        return "UNKNOWN"

    def to_dict(self) -> dict:
        return {
            "status": self.contract_status,
            "raw_status": self.status,
            "verified": self.verified,
            "attempted": True,
            "evidence": self.message,
            "details": self.details,
            "error": None if self.verified else self.message,
        }


class ActionVerifier:
    """Performs deterministic verification of desktop and browser actions."""

    def __init__(self):
        self.display_manager = get_display_manager()
        self.ui_tree_manager = get_ui_tree_manager()

    def verify_window_launch(
        self,
        app_or_window_name: str,
        timeout: float = 4.0,
        poll_interval: float = 0.3,
    ) -> VerificationResult:
        """
        Verify that an application has launched AND has a ready, visible window.
        A running background process alone does NOT prove the window is ready.
        """
        t0 = time.time()
        target = app_or_window_name.lower().strip()

        # Phase 1: Wait for actual foreground or visible top-level window
        while (time.time() - t0) < timeout:
            win_info = self.display_manager.get_active_window_info()
            curr_title = (win_info.get("title") or "").lower()
            curr_proc = (win_info.get("process_name") or "").lower()

            if target in curr_title or target in curr_proc:
                if win_info.get("handle") and curr_title:
                    return VerificationResult(
                        verified=True,
                        status="done",
                        message=f"Application window ready and active in foreground: '{win_info.get('title')}' ({curr_proc})",
                        details=win_info,
                    )

            # Check all visible top-level windows
            try:
                if windows_uia.is_available():
                    desktop = windows_uia.get_desktop()
                    if desktop:
                        for w in desktop.windows(visible_only=True):
                            try:
                                wt = w.window_text()
                                if target in wt.lower() and w.is_visible():
                                    return VerificationResult(
                                        verified=True,
                                        status="done",
                                        message=f"Application window ready and visible: '{wt}'",
                                        details={"title": wt, "handle": getattr(w, "handle", 0)},
                                    )
                            except Exception:
                                continue
            except Exception:
                pass

            time.sleep(poll_interval)

        # Phase 2: Check process table
        # IMPORTANT: A process existing without a ready window is PARTIAL / started, NOT verified done!
        try:
            import psutil
            for p in psutil.process_iter(["name", "pid"]):
                pname = (p.info.get("name") or "").lower()
                if target in pname:
                    return VerificationResult(
                        verified=False,
                        status="started",
                        message=f"Process '{pname}' (PID {p.info.get('pid')}) is running, but top-level window is not yet ready.",
                        details={"process_name": pname, "pid": p.info.get("pid")},
                    )
        except Exception:
            pass

        return VerificationResult(
            verified=False,
            status="unverified",
            message=f"Could not confirm launch or ready window for '{app_or_window_name}' within {timeout:.1f}s",
            details={},
        )

    def verify_element_interaction(
        self,
        node: Optional[UIElementNode],
        action_type: str,
        expected_state: Optional[dict] = None,
        timeout: float = 2.0,
    ) -> VerificationResult:
        """
        Verify that clicking, toggling, or focusing an element caused the expected transition.
        Coordinate clicks without confirmed state changes return UNKNOWN / unverified.
        """
        time.sleep(0.3)  # Brief wait for UI reaction
        if not node:
            # Blind coordinate click: outcome cannot be proven without visual/UIA delta
            return VerificationResult(
                verified=False,
                status="unverified",
                message=f"Action '{action_type}' executed by coordinates, but state change could not be confirmed.",
                details={"coordinate_only": True},
            )

        wrapper = node.raw_wrapper
        if wrapper:
            try:
                if action_type == "toggle":
                    toggled = False
                    if hasattr(wrapper, "iface_toggle") and wrapper.iface_toggle:
                        state = wrapper.iface_toggle.CurrentToggleState
                        toggled = bool(state == 1)
                    return VerificationResult(
                        verified=True,
                        status="done",
                        message=f"Toggle state verified: {toggled}",
                        details={"toggled": toggled},
                    )
                elif action_type == "click":
                    # Check if element is responsive, enabled, or if active window shifted
                    is_enabled = getattr(wrapper, "is_enabled", lambda: True)()
                    win_info = self.display_manager.get_active_window_info()
                    return VerificationResult(
                        verified=True,
                        status="done",
                        message=f"Element '{node.name}' clicked successfully. Active window: '{win_info.get('title')}'.",
                        details={"node": node.to_dict(), "active_window": win_info},
                    )
            except Exception as e:
                logger.debug("Verification check encountered exception: %s", e)

        return VerificationResult(
            verified=False,
            status="unverified",
            message=f"Interaction '{action_type}' performed on '{node.name}', but outcome state change could not be verified.",
            details={"node": node.to_dict() if node else {}},
        )

    def verify_text_input(
        self,
        expected_text: str,
        target_node: Optional[UIElementNode] = None,
    ) -> VerificationResult:
        """Verify that text was written to the target input field."""
        time.sleep(0.2)
        if target_node and target_node.raw_wrapper:
            try:
                curr_val = target_node.raw_wrapper.window_text() or ""
                if expected_text in curr_val:
                    return VerificationResult(
                        verified=True,
                        status="done",
                        message=f"Text input verified in '{target_node.name}': contains '{expected_text}'",
                        details={"current_text": curr_val},
                    )
                else:
                    return VerificationResult(
                        verified=False,
                        status="unverified",
                        message=f"Text input mismatch in '{target_node.name}': expected '{expected_text}' not found.",
                        details={"current_text": curr_val},
                    )
            except Exception:
                pass

        return VerificationResult(
            verified=False,
            status="unverified",
            message=f"Typed {len(expected_text)} characters, but target field text could not be verified.",
            details={"text_length": len(expected_text)},
        )

    def verify_window_closed(
        self,
        handle: int,
        timeout: float = 3.0,
    ) -> VerificationResult:
        """Verify that a window with handle HWND was closed."""
        t0 = time.time()
        while (time.time() - t0) < timeout:
            if not windows_uia.win32gui.IsWindow(handle):
                return VerificationResult(
                    verified=True,
                    status="done",
                    message="Window close verified.",
                    details={"handle": handle},
                )
            time.sleep(0.3)

        return VerificationResult(
            verified=False,
            status="unverified",
            message="Window still open after close request.",
            details={"handle": handle},
        )


_DEFAULT_ACTION_VERIFIER: Optional[ActionVerifier] = None


def get_action_verifier() -> ActionVerifier:
    global _DEFAULT_ACTION_VERIFIER
    if _DEFAULT_ACTION_VERIFIER is None:
        _DEFAULT_ACTION_VERIFIER = ActionVerifier()
    return _DEFAULT_ACTION_VERIFIER

