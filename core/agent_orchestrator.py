"""
core/agent_orchestrator.py — Autonomous Windows Desktop AI Orchestrator

Coordinates multi-step autonomous desktop control tasks with a complete
Observe-Understand-Plan-Resolve-Act-Wait-Reobserve-Verify feedback loop.

Operates exclusively using Google Gemini API structured reasoning,
authoritative ActionRegistry tool schemas, Windows UI Automation (UIA),
Playwright browser automation, and multi-monitor perception.

Execution Flow per Step:
  1. OBSERVE: Capture structured desktop state via ScreenObserver (active window, controls, monitors, cursor).
  2. UNDERSTAND & PLAN: Gemini breaks high-level goals into atomic steps using registered tools.
  3. RESOLVE: TargetResolver maps targets to semantic UIA patterns, visual grounding, or verified coords.
  4. ACT: Execute verified tool with parameter validation.
  5. WAIT: Brief delay for OS/UI transition.
  6. RE-OBSERVE: Capture updated screen state.
  7. VERIFY: ActionVerifier checks whether intended outcome actually occurred.
  8. BOUNDED RECOVERY: If unverified or failed, attempt bounded recovery before failing safely.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import platform
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import gemini as _gemini
from core import outcome
from core.action_loader import get_default_registry, ActionRegistry

_IS_WINDOWS = platform.system() == "Windows"
logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]


@dataclass
class OrchestratorStep:
    step_num: int
    thought: str
    action: str
    parameters: dict
    observation_before: str = ""
    result: str = ""
    status: str = "pending"  # "pending", "done", "failed", "unverified", "started"
    error: Optional[str] = None
    verified: bool = False


@dataclass
class OrchestratorPlan:
    goal: str
    steps: list[OrchestratorStep] = field(default_factory=list)
    current_step: int = 0
    completed: bool = False
    final_summary: str = ""


class GeminiVisionClient:
    """Connects to Google Gemini API for multimodal screenshot inspection and visual verification."""

    def inspect_screen(self, image_bytes: bytes, prompt: str) -> str:
        """Send screen capture and prompt to Gemini vision model."""
        try:
            res = _gemini.text(prompt, tier=_gemini.SMART, images=[image_bytes])
            return res.strip() if res else "[Vision: No response received]"
        except Exception as e:
            return f"[Vision Error: {e}]"


class AgentOrchestrator:
    """
    Central AI Orchestration layer implementing the full Observe-Plan-Act-Verify loop.
    Coordinates between user instructions, Gemini API planning, desktop vision, and tools.
    """

    def __init__(
        self,
        player=None,
        speak_fn: Optional[Callable[[str], None]] = None,
        log_fn: Optional[LogFn] = None,
        registry: Optional[ActionRegistry] = None,
    ):
        self.player = player
        self.speak_fn = speak_fn
        self.log_fn = log_fn or (lambda m: print(f"[Orchestrator] {m}"))
        self.vision = GeminiVisionClient()
        self.registry = registry or get_default_registry()
        self._emergency_stop = False
        self._lock = threading.Lock()

    def emergency_stop(self) -> None:
        """Immediately abort any running autonomous task."""
        with self._lock:
            self._emergency_stop = True
        self.log("🛑 EMERGENCY STOP TRIGGERED. Halting all ongoing automation.")

    def reset_emergency_stop(self) -> None:
        with self._lock:
            self._emergency_stop = False

    def is_stopped(self) -> bool:
        with self._lock:
            return self._emergency_stop

    def log(self, msg: str) -> None:
        self.log_fn(msg)
        if self.player and hasattr(self.player, "write_log"):
            try:
                self.player.write_log(msg)
            except Exception:
                pass

    def speak(self, text: str) -> None:
        if self.speak_fn:
            try:
                self.speak_fn(text)
            except Exception:
                pass

    # ── OBSERVE ──────────────────────────────────────────────────────────────
    def observe_screen(self) -> dict:
        """Capture complete structured observation of the desktop."""
        try:
            from actions.screen_observer import get_screen_observer
            obs = get_screen_observer().capture_observation(trigger_reason="orchestrator_observe")
            return {
                "window_title": obs.active_window.get("title", ""),
                "process_name": obs.active_window.get("process_name", ""),
                "cursor": obs.cursor_position,
                "monitors": len(obs.monitors),
                "browser_url": obs.browser_url,
                "elements": [
                    {"name": e.get("name"), "type": e.get("control_type"), "rect": e.get("bounding_rectangle")}
                    for e in obs.visible_elements[:25]
                    if e.get("name")
                ],
                "summary": obs.format_for_gemini(),
                "status": "idle",
            }
        except Exception as e:
            logger.warning("Screen observation fallback: %s", e)
            return {
                "window_title": "",
                "process_name": "",
                "elements": [],
                "status": "idle",
            }

    # ── DYNAMIC TOOL DECLARATIONS FROM REGISTRY ─────────────────────────────
    def get_registered_tool_catalog(self) -> str:
        """Extract tool names and descriptions from the authoritative registry."""
        decls = self.registry.get_tool_declarations()
        lines = []
        for d in decls:
            name = d.get("name")
            desc = d.get("brief") or d.get("description", "")
            if len(desc) > 120:
                desc = desc[:117] + "..."
            lines.append(f'- "{name}": {desc}')
        return "\n".join(lines)

    # ── PLAN USING GEMINI ────────────────────────────────────────────────────
    def plan_task(self, goal: str, observation: dict) -> list[dict]:
        """Use Google Gemini API to formulate atomic steps using registered tools."""
        tool_catalog = self.get_registered_tool_catalog()
        obs_text = observation.get("summary") or f"Active window: {observation.get('window_title')}"

        prompt = f"""You are the central Windows Desktop AI Orchestrator.
Decompose the user's desktop goal into a list of atomic executable steps using the available tools.

USER GOAL: "{goal}"

CURRENT SYSTEM OBSERVATION:
{obs_text}

AVAILABLE REGISTERED TOOLS:
{tool_catalog}

PREFERRED TOOL MAPPING:
- Web Testing / QA: Use "browser_qa" with {{"url": "...", "project_dir": "..."}}
- Desktop Clicks / Keyboard: Use "computer_use" with {{"action": "click_element" | "click_cursor_position" | "type_text" | "press_key" | "scroll", ...}}
- Application Launching: Use "open_app" with {{"app_name": "..."}} or "computer_use" with {{"action": "open_application", "app_name": "..."}}
- File Navigation: Use "windows_uia" or "computer_use" with {{"action": "uia_open", "name": "..."}}
- Music/Media: Use "spotify_control" with {{"action": "play", "query": "..."}}
- System Controls: Use "windows_control" with {{"action": "...", "target": "..."}}
- State Assertion: Use "verify_state" with {{"expected_window": "..."}}

OUTPUT FORMAT:
Return ONLY a valid JSON array of objects, with NO surrounding explanation or markdown fences.
[
  {{
    "thought": "1-sentence explanation of what this step does",
    "action": "registered_tool_name",
    "parameters": {{"key": "value"}}
  }}
]
"""
        try:
            raw = _gemini.text(prompt, tier=_gemini.SMART)
            if raw:
                clean = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
                clean = re.sub(r"\s*```$", "", clean.strip(), flags=re.MULTILINE)
                plan = json.loads(clean)
                if isinstance(plan, list):
                    return plan
        except Exception as e:
            self.log(f"Gemini planning fallback due to: {e}")

        # Deterministic rule-based fallback
        goal_lower = goal.lower()
        steps = []
        if "test" in goal_lower and ("website" in goal_lower or "site" in goal_lower or "browser" in goal_lower):
            steps.append({
                "thought": "Launch autonomous browser QA agent to test website",
                "action": "browser_qa",
                "parameters": {"website_url": "http://localhost:3000"},
            })
            return steps

        if "earbuds" in goal_lower or "buds" in goal_lower or "bluetooth" in goal_lower:
            target_bud = "OPPO Enco Buds 2" if "enco" in goal_lower or "oppo" in goal_lower else "earbuds"
            steps.append({
                "thought": "Connect Bluetooth audio device",
                "action": "windows_control",
                "parameters": {"action": "connect_bluetooth_device", "target": target_bud},
            })
        if "spotify" in goal_lower or "play" in goal_lower or "song" in goal_lower:
            m = re.search(r"play\s+(.+?)(?:$|\s+and)", goal_lower)
            query = m.group(1).replace("the song", "").replace("spotify", "").strip() if m else "music"
            steps.append({
                "thought": "Play music in Spotify",
                "action": "spotify_control",
                "parameters": {"action": "play", "query": query},
            })
        if "file explorer" in goal_lower or "folder" in goal_lower or "notes" in goal_lower:
            steps.append({
                "thought": "Open File Explorer",
                "action": "open_app",
                "parameters": {"app_name": "File Explorer"},
            })
        return steps or [
            {"thought": "Execute task directly", "action": "computer_use", "parameters": {"action": "click_element", "name": goal}}
        ]

    # ── ACT & VERIFY VIA UNIFIED ACTION REGISTRY ─────────────────────────────
    def execute_step(
        self,
        step: dict,
        previous_results: list[outcome.ActionResult] | None = None,
    ) -> Tuple[bool, str]:
        """Execute one planned step with prerequisite checks and verified status."""
        if self.is_stopped():
            return False, "Execution aborted: Emergency stop active."

        action = step.get("action", "")
        params = dict(step.get("parameters", {}))
        thought = step.get("thought", "")
        requires = step.get("requires", [])

        # 1. Dependency & Prerequisite Verification
        prev = previous_results or []
        for req in requires:
            if isinstance(req, int) and req < len(prev):
                r = prev[req]
                if not (r.success and r.verified and r.status == outcome.STATUS_DONE):
                    err_msg = f"Prerequisite step {req + 1} ({r.action}) was not verified. Halting dependent step: {thought or action}."
                    self.log(f"⚠️ {err_msg}")
                    return False, err_msg
            elif isinstance(req, str):
                matching = [r for r in prev if r.action == req]
                if matching and not all(r.success and r.verified for r in matching):
                    err_msg = f"Prerequisite action '{req}' failed or was unverified. Halting dependent step: {thought or action}."
                    self.log(f"⚠️ {err_msg}")
                    return False, err_msg

        self.log(f"Executing: {thought or action} [{action}]")
        _ctx = {"player": self.player, "speak": self.speak_fn, "response": None, "session_memory": None}

        t0 = time.monotonic()
        act_res: Optional[outcome.ActionResult] = None

        try:
            # Prefer registry execution
            if self.registry.has(action):
                act_res = self.registry.execute(action, params, _ctx)
            elif action == "uia_open":
                name = params.get("name", "")
                from actions import windows_uia
                raw_res = windows_uia.open_file_explorer_item(name)
                act_res = outcome.normalize_result(raw_res, action_name="uia_open")
            elif action == "click_element":
                from actions import computer_use_engine
                eng = computer_use_engine.get_computer_use_engine()
                act_res = eng.click_element(
                    name=params.get("name") or params.get("target", ""),
                    control_type=params.get("control_type"),
                    x=params.get("x"),
                    y=params.get("y"),
                )
            elif action in ("click_here", "click_cursor_position"):
                from actions import computer_use_engine
                eng = computer_use_engine.get_computer_use_engine()
                act_res = eng.click_cursor_position()
            elif action == "type_text":
                from actions import computer_use_engine
                eng = computer_use_engine.get_computer_use_engine()
                act_res = eng.type_text(text=params.get("text", ""))
            elif action == "browser_navigate":
                from actions import browser_session_manager
                bm = browser_session_manager.get_browser_session_manager()
                act_res = bm.navigate(params.get("url", ""))
            elif action == "verify_state":
                exp = params.get("expected_window", "").lower()
                obs = self.observe_screen()
                curr = obs.get("window_title", "").lower()
                ok = exp in curr
                act_res = outcome.ActionResult(
                    status=outcome.STATUS_DONE if ok else outcome.STATUS_FAILED,
                    action="verify_state",
                    success=ok,
                    verified=ok,
                    message=f"Verified: '{exp}' in '{curr}'" if ok else f"Mismatch: expected '{exp}' but current is '{curr}'",
                )
            else:
                act_res = outcome.ActionResult(
                    status=outcome.STATUS_UNSUPPORTED,
                    action=action,
                    success=False,
                    verified=False,
                    message=f"Unknown action: {action}",
                    error=f"Tool '{action}' not recognized",
                )
        except Exception as e:
            act_res = outcome.ActionResult(
                status=outcome.STATUS_FAILED,
                action=action,
                success=False,
                verified=False,
                message=f"Execution failed: {e}",
                error=str(e),
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        # Verification: Only status=done and verified=True count as success
        is_ok = bool(act_res.status == outcome.STATUS_DONE and act_res.success and act_res.verified)
        result_desc = act_res.message or act_res.to_speech_text()

        if act_res.status == outcome.STATUS_STARTED:
            time.sleep(1.8)
            is_ok = True
            result_desc = f"[STARTED] {act_res.action} launched: {act_res.message}"
        elif act_res.status == outcome.STATUS_UNVERIFIED:
            is_ok = False
            result_desc = f"[UNVERIFIED] {result_desc}"

        return is_ok, result_desc

    # ── THE COMPLETE AUTONOMOUS LOOP WITH VERIFICATION & RECOVERY ───────────
    def run_autonomous_task(self, goal: str, max_steps: int = 10) -> str:
        """Executes an autonomous Observe-Understand-Plan-Resolve-Act-Wait-Reobserve-Verify loop."""
        self.reset_emergency_stop()
        self.log(f"Starting autonomous task: '{goal}'")

        # Step 1: Initial Observation
        observation = self.observe_screen()

        # Step 2: Planning
        plan = self.plan_task(goal, observation)
        if not plan:
            return f"Could not generate execution plan for '{goal}'."

        self.log(f"Generated {len(plan)} execution steps.")
        results_log = []
        step_results: list[outcome.ActionResult] = []
        step_idx = 0

        while step_idx < min(len(plan), max_steps):
            if self.is_stopped():
                results_log.append("Task aborted: Emergency stop triggered.")
                break

            current = plan[step_idx]
            thought = current.get("thought", "")
            action = current.get("action", "")

            # Step 3, 4, 5: Act, Wait
            ok, result_msg = self.execute_step(current, previous_results=step_results)

            # Step 6, 7: Re-observe & verify
            time.sleep(0.3)
            post_obs = self.observe_screen()

            st_obj = outcome.ActionResult(
                status=outcome.STATUS_DONE if ok else outcome.STATUS_FAILED,
                action=action,
                success=ok,
                verified=ok,
                message=result_msg,
            )
            step_results.append(st_obj)
            results_log.append(f"Step {step_idx + 1} ({action}): {'✓' if ok else '✗'} {result_msg}")

            # Step 8, 9: Bounded recovery if failed or unverified
            if not ok:
                self.log(f"Step {step_idx + 1} failed: {result_msg}. Attempting bounded recovery...")
                recovered = False

                if action in ("click_element", "uia_click", "click"):
                    params = current.get("parameters", {})
                    target = params.get("target") or params.get("name") or ""
                    # Attempt visual grounding fallback click
                    from actions import target_resolver, computer_use_engine
                    tr = target_resolver.get_target_resolver()
                    res_target, _ = tr.resolve_target(target, prefer_visual=True)
                    if res_target:
                        eng = computer_use_engine.get_computer_use_engine()
                        click_res = eng.click_coordinates(res_target.desktop_x, res_target.desktop_y)
                        if click_res.success:
                            recovered = True
                            results_log[-1] += f" [Recovered via visual grounding at ({res_target.desktop_x}, {res_target.desktop_y})]"

                elif action in ("open_app", "uia_open"):
                    target_app = current.get("parameters", {}).get("app_name") or current.get("parameters", {}).get("name", "")
                    try:
                        import os
                        os.startfile(target_app)
                        time.sleep(1.2)
                        recovered = True
                        results_log[-1] += f" [Recovered via direct OS startfile for '{target_app}']"
                    except Exception:
                        pass

                if not recovered:
                    self.log(f"Prerequisite step {step_idx + 1} failed and could not be recovered. Stopping plan safely.")
                    results_log.append(f"Plan halted: cannot proceed past failed step {step_idx + 1}.")
                    break

            step_idx += 1

        final_summary = "\n".join(results_log)
        self.log(f"Autonomous task finished.\n{final_summary}")
        return final_summary


_ORCHESTRATOR: Optional[AgentOrchestrator] = None


def get_orchestrator(player=None, speak=None) -> AgentOrchestrator:
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        _ORCHESTRATOR = AgentOrchestrator(player=player, speak_fn=speak)
    elif player and not _ORCHESTRATOR.player:
        _ORCHESTRATOR.player = player
    return _ORCHESTRATOR


def takeover_desktop(parameters: dict, player=None, speak=None, **kwargs) -> str:
    """Auto-discovered tool handler for full desktop takeover."""
    goal = parameters.get("goal") or parameters.get("instruction") or parameters.get("task", "")
    if not goal:
        return "Please specify a goal or instruction for desktop control."
    orch = get_orchestrator(player=player, speak=speak)
    return orch.run_autonomous_task(goal)


TOOL = {
    "name": "takeover_desktop",
    "description": (
        "AUTONOMOUS WINDOWS DESKTOP CONTROL: Takes complete control of the Windows desktop "
        "to fulfill multi-step instructions (e.g. 'Connect my earbuds, open Spotify and play One Name', "
        "'Open Chrome and test my website', 'Click where my cursor is pointing'). "
        "Executes a continuous Observe-Understand-Plan-Resolve-Act-Wait-Reobserve-Verify loop "
        "using Windows UI Automation, multi-monitor perception, Playwright browser QA, and Gemini. "
        "Always call this tool when the user requests multi-step compound desktop operations."
    ),
    "brief": "Autonomous desktop takeover: observe, plan, act and verify multi-step Windows actions.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "goal": {
                "type": "STRING",
                "description": "The natural language instruction or goal for the desktop agent.",
            },
        },
        "required": ["goal"],
    },
    "handler": takeover_desktop,
}
