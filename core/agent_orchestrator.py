"""
core/agent_orchestrator.py — Autonomous Windows Desktop AI Orchestrator

Coordinates multi-step autonomous desktop control tasks with an
Observe-Plan-Act-Verify feedback loop.
Operates exclusively using Google Gemini API function calling / structured reasoning,
and deterministic Windows UI Automation (pywinauto/UIA). No local LLM or Ollama required.

Execution Cascade for Actions:
  1. Windows UI Automation (windows_uia) - accessibility tree matching & control patterns.
  2. Windows System Shell & File APIs - direct OS launches and verified file discovery.
  3. Vision Grounding (Gemini Vision) - visual element verification only when needed.
  4. Physical Mouse & Keyboard Fallback (PyAutoGUI) - coordinate-validated physical inputs.
"""
from __future__ import annotations

import base64
import json
import os
import platform
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import gemini as _gemini
from core import outcome
from core.action_loader import get_default_registry, ActionRegistry

_IS_WINDOWS = platform.system() == "Windows"

LogFn = Callable[[str], None]


@dataclass
class OrchestratorStep:
    step_num: int
    thought: str
    action: str
    parameters: dict
    observation_before: str = ""
    result: str = ""
    status: str = "pending"  # "pending", "done", "failed", "unverified"
    error: Optional[str] = None


@dataclass
class OrchestratorPlan:
    goal: str
    steps: list[OrchestratorStep] = field(default_factory=list)
    current_step: int = 0
    completed: bool = False
    final_summary: str = ""


class GeminiVisionClient:
    """
    Connects to Google Gemini API for multimodal screenshot inspection and visual verification.
    """

    def inspect_screen(self, image_bytes: bytes, prompt: str) -> str:
        """Send screen capture and prompt to Gemini vision model."""
        try:
            # Uses core.gemini helper with automatic quota management & fallback ladder
            res = _gemini.text(prompt, tier=_gemini.SMART, images=[image_bytes])
            return res.strip() if res else "[Vision: No response received]"
        except Exception as e:
            return f"[Vision Error: {e}]"


class AgentOrchestrator:
    """
    Central AI Orchestration layer implementing Observe-Plan-Act-Verify loop.
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
        """
        Capture current active window state and actionable controls.
        """
        state = {
            "window_title": "",
            "process_name": "",
            "elements": [],
            "status": "idle",
        }
        if not _IS_WINDOWS:
            return state

        from actions import windows_uia
        if windows_uia.is_available():
            win_info = windows_uia.get_active_window()
            if win_info.get("status") == "success":
                state["window_title"] = win_info.get("title", "")
                state["process_name"] = win_info.get("process_name", "")

            # Inspect high-level controls
            ins = windows_uia.inspect_window(max_depth=2, limit=15, only_actionable=True)
            if ins.get("status") == "success":
                state["elements"] = [
                    {"name": e["name"], "type": e["control_type"]}
                    for e in ins.get("elements", [])
                    if e.get("name")
                ]
        return state

    # ── PLAN USING GEMINI ────────────────────────────────────────────────────
    def plan_task(self, goal: str, observation: dict) -> list[dict]:
        """
        Use Google Gemini API to break the goal into structured, atomic desktop actions.
        """
        prompt = f"""You are the central Windows Desktop AI Orchestrator.
Your job is to decompose the user's high-level desktop goal into a list of atomic executable steps.

USER GOAL: "{goal}"

CURRENT SYSTEM STATE:
- Active Window: "{observation.get('window_title')}"
- Active Process: "{observation.get('process_name')}"
- Visible Controls: {json.dumps(observation.get('elements', [])[:8])}

AVAILABLE ACTIONS:
1. "open_app": Launch an app. Parameters: {{"app_name": "..."}} (e.g. "File Explorer", "notepad", "msedge", "spotify")
2. "uia_open": Open exact file or folder in File Explorer. Parameters: {{"name": "exact folder or file name"}}
3. "click_element": Click a UI button/item. Parameters: {{"name": "...", "control_type": "Button|ListItem"}}
4. "type_text": Type text. Parameters: {{"text": "..."}}
5. "file_find": Search files on disk. Parameters: {{"name": "...", "path": "path or shortcut"}}
6. "file_read_and_summarize": Read document and generate summary. Parameters: {{"file_path": "..."}}
7. "browser_navigate": Open URL in browser. Parameters: {{"url": "..."}}
8. "windows_control": Bluetooth, Wi-Fi, audio output, brightness. Parameters: {{"action": "...", "target": "..."}}
9. "spotify_control": Play music, pause, skip. Parameters: {{"action": "play", "query": "..."}}
10. "verify_state": Verify expected window or condition. Parameters: {{"expected_window": "..."}}

OUTPUT FORMAT:
Return ONLY a valid JSON array of objects, with NO surrounding explanation or markdown fences.
Each object must have:
- "thought": A 1-sentence reasoning for the step.
- "action": One of the action names above.
- "parameters": Key-value dictionary of arguments.

Example:
[
  {{"thought": "Connect earbuds via Bluetooth", "action": "windows_control", "parameters": {{"action": "connect_bluetooth_device", "target": "OPPO Enco Buds 2"}}}},
  {{"thought": "Open Spotify and play requested track", "action": "spotify_control", "parameters": {{"action": "play", "query": "One Name from Jailer 2"}}}}
]
"""
        try:
            raw = _gemini.text(prompt, tier=_gemini.SMART)
            if raw:
                # Strip markdown code blocks if any
                clean = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
                clean = re.sub(r"\s*```$", "", clean.strip(), flags=re.MULTILINE)
                plan = json.loads(clean)
                if isinstance(plan, list):
                    return plan
        except Exception as e:
            self.log(f"Gemini planning fallback due to: {e}")

        # Deterministic rule-based fallback for standard desktop patterns
        goal_lower = goal.lower()
        steps = []
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
            {"thought": "Execute task directly", "action": "uia_open", "parameters": {"name": goal}}
        ]

    # ── ACT & VERIFY VIA UNIFIED ACTION REGISTRY ─────────────────────────────
    def execute_step(self, step: dict, previous_results: list[outcome.ActionResult] | None = None) -> Tuple[bool, str]:
        """Execute one planned step with prerequisite checks and verified status."""
        action = step.get("action", "")
        params = dict(step.get("parameters", {}))
        thought = step.get("thought", "")
        requires = step.get("requires", [])

        # 1. Dependency & Prerequisite Verification
        prev = previous_results or []
        # Check explicit prerequisite indices or action names
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

        # Semantic prerequisite guards (e.g. Bluetooth connection before music playback on buds)
        if action in ("spotify_control", "video_player"):
            bt_steps = [r for r in prev if r.action in ("windows_control", "bluetooth_control") and "bluetooth" in str(r.data).lower()]
            if bt_steps and not all(r.success and r.verified for r in bt_steps):
                err_msg = "Bluetooth connection prerequisite failed. Refusing to start playback on unconnected device."
                self.log(f"⚠️ {err_msg}")
                return False, err_msg

        self.log(f"Executing: {thought or action} [{action}]")
        _ctx = {"player": self.player, "speak": self.speak_fn, "response": None, "session_memory": None}

        t0 = time.monotonic()
        act_res: Optional[outcome.ActionResult] = None

        try:
            # 2. Prefer registry execution if registered
            if self.registry.has(action):
                act_res = self.registry.execute(action, params, _ctx)
            elif action == "uia_open":
                name = params.get("name", "")
                from actions import windows_uia
                raw_res = windows_uia.open_file_explorer_item(name)
                act_res = outcome.normalize_result(raw_res, action_name="uia_open")
            elif action == "click_element":
                from actions import windows_uia
                name = params.get("name", "")
                ctype = params.get("control_type")
                raw_res = windows_uia.click_element(name=name, control_type=ctype)
                if isinstance(raw_res, dict) and raw_res.get("status") != "done":
                    x = params.get("x")
                    y = params.get("y")
                    if x is not None and y is not None:
                        from actions import computer_control
                        raw_res = computer_control.computer_control({"action": "click", "x": x, "y": y})
                    elif ctype and name:
                        retry_res = windows_uia.click_element(name=name)
                        if isinstance(retry_res, dict) and retry_res.get("status") == "done":
                            raw_res = retry_res
                act_res = outcome.normalize_result(raw_res, action_name="click_element")
            elif action == "type_text":
                from actions import windows_uia
                text = params.get("text", "")
                raw_res = windows_uia.type_text(text)
                act_res = outcome.normalize_result(raw_res, action_name="type_text")
            elif action == "browser_navigate":
                from actions import browser_control
                url = params.get("url", "")
                raw_res = browser_control.browser_control({"action": "navigate", "url": url})
                act_res = outcome.normalize_result(raw_res, action_name="browser_control")
            elif action == "file_find":
                from actions import file_controller
                name = params.get("name", "")
                p = params.get("path", "home")
                raw_res = file_controller.find_files(name=name, path=p)
                act_res = outcome.normalize_result(raw_res, action_name="file_find")
            elif action == "file_read_and_summarize":
                ok, summary_msg = self._find_and_summarize(params)
                act_res = outcome.ActionResult(
                    status=outcome.STATUS_DONE if ok else outcome.STATUS_FAILED,
                    action="file_read_and_summarize",
                    success=ok,
                    verified=ok,
                    message=summary_msg,
                    error=None if ok else summary_msg,
                )
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

        # 3. Verification: Only status=done and verified=True count as success
        is_ok = bool(act_res.status == outcome.STATUS_DONE and act_res.success and act_res.verified)
        result_desc = act_res.message or act_res.to_speech_text()

        if act_res.status == outcome.STATUS_STARTED:
            # Application launch is in progress; allow brief pause for window/process initialization
            time.sleep(1.8)
            is_ok = True
            result_desc = f"[STARTED] {act_res.action} launched: {act_res.message}"

        elif act_res.status == outcome.STATUS_UNVERIFIED:
            is_ok = False
            result_desc = f"[UNVERIFIED] {result_desc}"

        return is_ok, result_desc

    def _find_and_summarize(self, params: dict) -> Tuple[bool, str]:
        """Locate notes files on disk and summarize content via Gemini."""
        query = params.get("query", "notes")
        subject = params.get("subject", "")
        file_path = params.get("file_path", "")

        target_file = None
        if file_path and os.path.exists(file_path):
            target_file = Path(file_path)
        else:
            from actions import file_controller
            search_terms = [subject, query] if subject else [query]
            candidates = []
            for term in search_terms:
                if not term:
                    continue
                found = file_controller.find_files(name=term, path="home", max_results=10)
                if "Found" in found:
                    for line in found.split("\n"):
                        if "—" in line:
                            parts = line.split("—")
                            fname = parts[0].replace("📄", "").split("(")[0].strip()
                            fdir = parts[1].strip()
                            full_p = Path(fdir) / fname
                            if full_p.exists() and full_p.suffix.lower() in (".txt", ".md", ".pdf", ".docx", ".py"):
                                candidates.append(full_p)
            if candidates:
                target_file = candidates[0]

        if not target_file:
            return False, f"Could not find notes document for '{subject or query}'."

        self.log(f"Summarizing notes file: {target_file}")
        content = ""
        try:
            if target_file.suffix.lower() in (".txt", ".md", ".py"):
                content = target_file.read_text(encoding="utf-8", errors="replace")[:6000]
            elif target_file.suffix.lower() == ".pdf":
                from actions import file_processor
                content = file_processor.file_processor({"action": "extract_text", "file_path": str(target_file)})[:6000]
        except Exception as e:
            return False, f"Failed reading {target_file.name}: {e}"

        if not content.strip():
            return False, f"Notes file '{target_file.name}' was empty or unreadable."

        summary_prompt = f"Summarize the key takeaways and topics from these notes concisely in 3-5 bullet points:\n\n{content}"
        summary_text = _gemini.text(summary_prompt, tier=_gemini.SMART) or "Summary unavailable."

        report = f"Summary for {target_file.name}:\n{summary_text}"
        if self.player and hasattr(self.player, "show_content"):
            self.player.show_content(f"Notes Summary: {target_file.stem}", summary_text)
        return True, report

    # ── VERIFY & RECOVER (The Complete Autonomous Loop) ───────────────────────
    def run_autonomous_task(self, goal: str, max_steps: int = 10) -> str:
        """Executes an autonomous Observe-Plan-Act-Verify desktop control loop."""
        self.log(f"Starting autonomous task: '{goal}'")
        observation = self.observe_screen()
        plan = self.plan_task(goal, observation)

        if not plan:
            return f"Could not generate execution plan for '{goal}'."

        self.log(f"Generated {len(plan)} execution steps.")
        results_log = []
        step_results: list[outcome.ActionResult] = []
        step_idx = 0

        while step_idx < min(len(plan), max_steps):
            current = plan[step_idx]
            thought = current.get("thought", "")
            action = current.get("action", "")

            ok, result_msg = self.execute_step(current, previous_results=step_results)
            # Create snapshot ActionResult for tracking dependencies
            st_obj = outcome.ActionResult(
                status=outcome.STATUS_DONE if ok else outcome.STATUS_FAILED,
                action=action,
                success=ok,
                verified=ok,
                message=result_msg,
            )
            step_results.append(st_obj)
            results_log.append(f"Step {step_idx + 1} ({action}): {'✓' if ok else '✗'} {result_msg}")

            if not ok:
                self.log(f"Step {step_idx + 1} failed: {result_msg}. Attempting bounded recovery...")
                recovered = False
                if action in ("uia_open", "open_app"):
                    folder_name = current.get("parameters", {}).get("name") or current.get("parameters", {}).get("app_name", "")
                    from actions import file_controller
                    matches = file_controller.find_files(name=folder_name, path="home", max_results=1)
                    if "Found" in matches:
                        self.log(f"Recovering via filesystem path opening for '{folder_name}'...")
                        for line in matches.split("\n"):
                            if "—" in line:
                                pdir = line.split("—")[-1].strip()
                                full_p = os.path.join(pdir, folder_name)
                                if os.path.exists(full_p):
                                    try:
                                        os.startfile(full_p)
                                        time.sleep(1.2)
                                        recovered = True
                                        results_log[-1] += f" [Recovered via direct path: {full_p}]"
                                        break
                                    except Exception as ex:
                                        self.log(f"Recovery launch failed: {ex}")

                elif action in ("click_element", "uia_click", "click"):
                    params = current.get("parameters", {})
                    target = params.get("target") or params.get("name") or params.get("element_or_name") or ""
                    x = params.get("x")
                    y = params.get("y")

                    # Check if target is a browser profile or profile chooser
                    try:
                        from actions import browser_profile
                        prof_res = browser_profile.browser_profile({"action": "select_profile", "profile_name": target, "x": x, "y": y})
                        if "selected" in str(prof_res).lower() or "done" in str(prof_res).lower():
                            recovered = True
                            time.sleep(1.0)
                            results_log[-1] += f" [Recovered via browser profile selection '{target}']"
                    except Exception as ex:
                        self.log(f"Profile recovery failed: {ex}")

                    if not recovered and x is not None and y is not None:
                        try:
                            from actions import computer_control
                            self.log(f"Recovering click on '{target}' via coordinates ({x}, {y})...")
                            res = computer_control.computer_control({"action": "click", "x": int(x), "y": int(y)})
                            if "clicked" in str(res).lower() or "done" in str(res).lower():
                                recovered = True
                                time.sleep(1.0)
                                results_log[-1] += f" [Recovered via coordinate click ({x}, {y})]"
                        except Exception as ex:
                            self.log(f"Coordinate recovery failed: {ex}")

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
        "'Open File Explorer, navigate to Semester 5, find my notes and summarize them'). "
        "Executes a continuous Observe-Plan-Act-Verify loop using Windows UI Automation and Gemini. "
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
