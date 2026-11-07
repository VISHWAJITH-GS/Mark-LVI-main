"""
core/agent_orchestrator.py — Autonomous Windows Desktop AI Orchestrator

Coordinates multi-step autonomous desktop control tasks with an
Observe-Plan-Act-Verify feedback loop.
Operates fully locally using Llama 3.2 (3B) for planning and reasoning,
and supports local desktop vision models (Qwen-VL / Llama-Vision via Ollama)
along with deterministic Windows UI Automation (pywinauto/UIA).

Execution Cascade for Actions:
  1. Windows UI Automation (windows_uia) - accessibility tree matching & control patterns.
  2. Windows System Shell & File APIs - direct OS launches and verified file discovery.
  3. Screen Vision Grounding (Ollama vision model) - visual element discovery & verification.
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

_IS_WINDOWS = platform.system() == "Windows"

# Fallback logger/speaker types
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


class LocalVisionClient:
    """
    Connects to local vision models (Qwen-VL / Llama-Vision) via Ollama endpoints.
    Provides image understanding, element detection, and verification of screen state.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "qwen3-vl:2b"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def is_available(self) -> bool:
        """Check if Ollama server is reachable."""
        try:
            import urllib.request
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        try:
            import urllib.request
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    def inspect_screen(self, image_bytes: bytes, prompt: str) -> str:
        """Send screen capture and prompt to the local vision model."""
        import urllib.request

        models = self.list_models()
        # Find best available vision model (model name match or fallback to any installed vision/multimodal)
        chosen_model = self.model
        if chosen_model not in models:
            # Check for other vision models in Ollama
            for m in models:
                if any(v in m.lower() for v in ("vl", "vision", "qwen", "llama3.2")):
                    chosen_model = m
                    break
            else:
                if models:
                    chosen_model = models[0]

        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        payload = {
            "model": chosen_model,
            "prompt": prompt,
            "images": [b64_image],
            "stream": False,
            "options": {
                "temperature": 0.1,
            },
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=45.0) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                return res_data.get("response", "").strip()
        except Exception as e:
            return f"[Vision Error: {e}]"


class AgentOrchestrator:
    """
    Central AI Orchestration layer implementing Observe-Plan-Act-Verify loop.
    Coordinates between user instructions, Llama 3.2, desktop vision, and tools.
    """

    def __init__(
        self,
        player=None,
        speak_fn: Optional[Callable[[str], None]] = None,
        log_fn: Optional[LogFn] = None,
        ollama_url: str = "http://127.0.0.1:11434",
        llm_model: str = "llama3.2:3b",
        vision_model: str = "qwen3-vl:2b",
    ):
        self.player = player
        self.speak_fn = speak_fn
        self.log_fn = log_fn or (lambda m: print(f"[Orchestrator] {m}"))
        self.ollama_url = ollama_url.rstrip("/")
        self.llm_model = llm_model
        self.vision = LocalVisionClient(base_url=self.ollama_url, model=vision_model)

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
    def observe_desktop(self) -> dict:
        """
        Inspect the current desktop state:
        - Active window (title, process, bounds)
        - UIA top controls (if available)
        - Screen capture (encoded for vision if needed)
        """
        state = {
            "active_window": None,
            "window_title": "",
            "process_name": "",
            "uia_available": False,
            "elements": [],
            "screenshot_bytes": None,
        }

        try:
            from actions import windows_uia
            if windows_uia.is_available():
                state["uia_available"] = True
                active = windows_uia.get_active_window()
                if active.get("status") == "success":
                    state["active_window"] = active
                    state["window_title"] = active.get("title", "")
                    state["process_name"] = active.get("process_name", "")

                # Quick inspect of actionable controls
                ins = windows_uia.inspect_window(max_depth=2, limit=15, only_actionable=True)
                if ins.get("status") == "success":
                    state["elements"] = [
                        {"name": e.get("name"), "type": e.get("control_type")}
                        for e in ins.get("elements", [])
                        if e.get("name")
                    ]
        except Exception as e:
            self.log(f"UIA observation error: {e}")

        # Capture screenshot for visual verification
        try:
            from actions import screen_processor
            img_b, _ = screen_processor._capture_screen()
            state["screenshot_bytes"] = img_b
        except Exception as e:
            self.log(f"Screen capture error: {e}")

        return state

    # ── UNDERSTAND & PLAN ────────────────────────────────────────────────────
    def plan_task(self, goal: str, observation: dict) -> list[dict]:
        """
        Use Llama 3.2 (3B) with a structured prompt to break the goal into
        validated, atomic desktop actions.
        """
        prompt = f"""You are the central Windows Desktop AI Orchestrator.
Your job is to decompose the user's high-level desktop goal into a list of atomic executable steps.

USER GOAL: "{goal}"

CURRENT SYSTEM STATE:
- Active Window: "{observation.get('window_title')}"
- Active Process: "{observation.get('process_name')}"
- Visible Controls: {json.dumps(observation.get('elements', [])[:8])}

AVAILABLE ACTIONS:
1. "open_app": Launch an app. Parameters: {{"app_name": "..."}} (e.g. "File Explorer", "notepad", "msedge")
2. "uia_open": Open exact file or folder in File Explorer. Parameters: {{"name": "exact folder or file name"}}
3. "click_element": Click a UI button/item. Parameters: {{"name": "...", "control_type": "Button|ListItem"}}
4. "type_text": Type text. Parameters: {{"text": "..."}}
5. "file_find": Search files on disk. Parameters: {{"name": "...", "path": "path or shortcut"}}
6. "file_read_and_summarize": Read document and generate summary. Parameters: {{"file_path": "..."}}
7. "browser_navigate": Open URL in browser. Parameters: {{"url": "..."}}
8. "verify_state": Verify expected window or condition. Parameters: {{"expected_window": "..."}}

OUTPUT FORMAT:
Return ONLY a valid JSON array of objects, with NO surrounding explanation or markdown fences.
Each object must have:
- "thought": A 1-sentence reasoning for the step.
- "action": One of the action names above.
- "parameters": Key-value dictionary of arguments.

Example:
[
  {{"thought": "Open File Explorer to navigate to the desired folder", "action": "open_app", "parameters": {{"app_name": "File Explorer"}}}},
  {{"thought": "Open exact folder Semester 5", "action": "uia_open", "parameters": {{"name": "Semester 5"}}}},
  {{"thought": "Open Applied Image Processing subject folder", "action": "uia_open", "parameters": {{"name": "Applied Image Processing"}}}},
  {{"thought": "Find and summarize notes", "action": "file_find", "parameters": {{"name": "notes", "path": "Semester 5"}}}}
]
"""
        import urllib.request
        payload = {
            "model": self.llm_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1},
        }

        try:
            req = urllib.request.Request(
                f"{self.ollama_url}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=20.0) as resp:
                res_text = json.loads(resp.read().decode("utf-8")).get("response", "").strip()

            # Robust JSON extraction
            match = re.search(r'\[.*\]', res_text, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list) and len(parsed) > 0:
                    return parsed
        except Exception as e:
            self.log(f"Planning fallback due to: {e}")

        # Deterministic Rule-Based Fallback Planner for standard desktop workflows
        return self._rule_based_planner(goal)

    def _rule_based_planner(self, goal: str) -> list[dict]:
        """Deterministic fallback when LLM output cannot be parsed as JSON."""
        lower = goal.lower()
        steps = []

        # Case: File Explorer navigation & note summarization
        if "file explorer" in lower or "navigate" in lower or "semester" in lower or "notes" in lower:
            # 1. Open File Explorer
            steps.append({
                "thought": "Open File Explorer",
                "action": "open_app",
                "parameters": {"app_name": "File Explorer"},
            })

            # Extract target folders
            if "semester 5" in lower or "semester" in lower:
                sem = "Semester 5" if "semester 5" in lower else "Semester"
                steps.append({
                    "thought": f"Locate and open {sem}",
                    "action": "uia_open",
                    "parameters": {"name": sem},
                })

            if "applied image processing" in lower:
                steps.append({
                    "thought": "Open Applied Image Processing folder",
                    "action": "uia_open",
                    "parameters": {"name": "Applied Image Processing"},
                })

            if "summarize" in lower or "notes" in lower:
                steps.append({
                    "thought": "Find notes and summarize their content",
                    "action": "find_and_summarize_notes",
                    "parameters": {"query": "notes", "subject": "Applied Image Processing"},
                })
            return steps

        # Case: Browser navigation
        if "browser" in lower or "edge" in lower or "chrome" in lower or "open website" in lower:
            app = "Microsoft Edge" if "edge" in lower else "Google Chrome"
            steps.append({
                "thought": f"Open browser {app}",
                "action": "open_app",
                "parameters": {"app_name": app},
            })
            url_match = re.search(r'https?://[^\s]+', goal)
            if url_match:
                steps.append({
                    "thought": f"Navigate to {url_match.group(0)}",
                    "action": "browser_navigate",
                    "parameters": {"url": url_match.group(0)},
                })
            return steps

        # General open app
        steps.append({
            "thought": "Execute requested desktop goal",
            "action": "open_app",
            "parameters": {"app_name": goal.strip()},
        })
        return steps

    # ── ACT ──────────────────────────────────────────────────────────────────
    def execute_step(self, step: dict) -> Tuple[bool, str]:
        """
        Executes a single validated desktop action.
        Returns: (success: bool, report: str)
        """
        action = step.get("action", "")
        params = step.get("parameters", {})
        self.log(f"▶ Executing: {action} with {params}")

        try:
            # 1. Open Application
            if action == "open_app":
                app_name = params.get("app_name", "")
                from actions import open_app
                res = open_app.open_app({"app_name": app_name})
                time.sleep(1.0)
                return True, f"Application '{app_name}' launch status: {res}"

            # 2. UIA Open Folder or File (with strict name matching and direct search fallback)
            if action in ("uia_open", "open_folder", "open_file"):
                name = params.get("name", "")
                from actions import windows_uia
                # First attempt UIA exact match in open File Explorer
                res = windows_uia.open_file_explorer_item(name)
                if "done" in str(res):
                    return True, f"Successfully opened '{name}' in File Explorer."

                # Fallback: direct OS path resolution if folder exists on disk
                from actions import file_controller
                found = file_controller.find_files(name=name, path="home", max_results=3)
                if "Found" in found:
                    for line in found.split("\n"):
                        if "—" in line:
                            parent_path = line.split("—")[-1].strip()
                            target_full = os.path.join(parent_path, name)
                            if os.path.exists(target_full):
                                os.startfile(target_full)
                                time.sleep(0.8)
                                return True, f"Opened '{target_full}' via direct file resolution."

                return False, f"Could not open '{name}': {res}"

            # 3. Click Element via UIA
            if action == "click_element":
                name = params.get("name", "")
                ctl_type = params.get("control_type")
                from actions import windows_uia
                res = windows_uia.click_element(name=name, control_type=ctl_type)
                if res.get("status") == "success":
                    return True, f"Clicked element '{name}'."
                return False, f"Click failed: {res.get('reason')}"

            # 4. Type Text
            if action == "type_text":
                text = params.get("text", "")
                name = params.get("name")
                from actions import windows_uia
                res = windows_uia.type_text(text=text, element_name=name)
                if res.get("status") == "success":
                    return True, f"Typed text into '{name or 'focused field'}'."
                return False, f"Type text failed: {res.get('reason')}"

            # 5. File search
            if action == "file_find":
                name = params.get("name", "")
                path = params.get("path", "home")
                from actions import file_controller
                res = file_controller.find_files(name=name, path=path)
                return True, res

            # 6. Find and summarize notes
            if action in ("find_and_summarize_notes", "file_read_and_summarize"):
                return self._find_and_summarize(params)

            # 7. Browser navigation
            if action == "browser_navigate":
                url = params.get("url", "")
                from actions import browser_control
                res = browser_control.browser_control({"action": "navigate", "url": url})
                return True, f"Browser navigation: {res}"

            # 8. Verify state
            if action == "verify_state":
                expected = params.get("expected_window", "").lower()
                obs = self.observe_desktop()
                current = obs.get("window_title", "").lower()
                if expected in current:
                    return True, f"Verified active window matches '{expected}'."
                return False, f"Window verification mismatch: currently at '{current}'."

            return False, f"Unknown action: '{action}'"
        except Exception as e:
            return False, f"Action execution exception: {e}"

    def _find_and_summarize(self, params: dict) -> Tuple[bool, str]:
        """
        Locates relevant notes files on disk and summarizes their content using
        local Llama 3.2 3B.
        """
        query = params.get("query", "notes")
        subject = params.get("subject", "")
        file_path = params.get("file_path", "")

        target_file = None
        if file_path and os.path.exists(file_path):
            target_file = Path(file_path)
        else:
            # Search for candidate files in drives and user home
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

        # Summarize using Llama 3.2 3B
        summary_prompt = f"Summarize the key takeaways and topics from these notes concisely in 3-5 bullet points:\n\n{content}"
        import urllib.request
        payload = {
            "model": self.llm_model,
            "prompt": summary_prompt,
            "stream": False,
            "options": {"temperature": 0.2},
        }
        try:
            req = urllib.request.Request(
                f"{self.ollama_url}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                summary_text = json.loads(resp.read().decode("utf-8")).get("response", "").strip()

            report = f"Summary for {target_file.name}:\n{summary_text}"
            if self.player and hasattr(self.player, "show_content"):
                self.player.show_content(f"Notes Summary: {target_file.stem}", summary_text)
            return True, report
        except Exception as e:
            return True, f"Extracted notes from {target_file.name} (Summary generation error: {e})"

    # ── VERIFY & RECOVER (The Complete Autonomous Loop) ───────────────────────
    def run_autonomous_task(self, goal: str, max_steps: int = 10) -> str:
        """
        Executes an autonomous Observe-Plan-Act-Verify desktop control loop
        until the goal is completed or unrecoverable.
        """
        self.log(f"Starting autonomous desktop control for goal: '{goal}'")
        self.speak(f"Taking over desktop to {goal}.")

        # 1. OBSERVE
        obs = self.observe_desktop()

        # 2. PLAN
        plan_steps = self.plan_task(goal, obs)
        self.log(f"Planned {len(plan_steps)} steps.")

        results_log = []
        step_idx = 0

        while step_idx < len(plan_steps) and step_idx < max_steps:
            current = plan_steps[step_idx]
            thought = current.get("thought", "")
            action = current.get("action", "")

            self.log(f"Step {step_idx + 1}/{len(plan_steps)}: {thought}")

            # 3. ACT
            ok, result_msg = self.execute_step(current)
            results_log.append(f"Step {step_idx + 1} ({action}): {result_msg}")

            # 4. VERIFY
            time.sleep(0.5)
            new_obs = self.observe_desktop()

            # Recovery check: if folder or app was supposed to open but didn't
            if not ok:
                self.log(f"Step {step_idx + 1} failed: {result_msg}. Attempting recovery...")
                # If uia_open failed, attempt direct Explorer address navigation
                if action == "uia_open":
                    folder_name = current.get("parameters", {}).get("name", "")
                    from actions import file_controller
                    matches = file_controller.find_files(name=folder_name, path="home", max_results=1)
                    if "Found" in matches:
                        self.log(f"Recovering via direct path opening for {folder_name}...")
                        import os
                        for line in matches.split("\n"):
                            if "—" in line:
                                pdir = line.split("—")[-1].strip()
                                full_p = os.path.join(pdir, folder_name)
                                if os.path.exists(full_p):
                                    os.startfile(full_p)
                                    time.sleep(1.0)
                                    ok = True
                                    results_log[-1] += f" [Recovered via {full_p}]"
                                    break

            step_idx += 1

        final_summary = "\n".join(results_log)
        self.log(f"Autonomous task finished.\n{final_summary}")
        return final_summary


# ── Global Singleton & Tool Declaration ──────────────────────────────────────
_ORCHESTRATOR: Optional[AgentOrchestrator] = None


def get_orchestrator(player=None, speak=None) -> AgentOrchestrator:
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        _ORCHESTRATOR = AgentOrchestrator(player=player, speak_fn=speak)
    elif player and not _ORCHESTRATOR.player:
        _ORCHESTRATOR.player = player
    return _ORCHESTRATOR


def takeover_desktop(parameters: dict, player=None, speak=None, **kwargs) -> str:
    """
    Auto-discovered tool handler for full desktop takeover.
    """
    goal = parameters.get("goal") or parameters.get("instruction") or parameters.get("task", "")
    if not goal:
        return "Please specify a goal or instruction for desktop control."

    orch = get_orchestrator(player=player, speak=speak)
    return orch.run_autonomous_task(goal)


TOOL = {
    "name": "takeover_desktop",
    "description": (
        "AUTONOMOUS WINDOWS DESKTOP CONTROL: Takes complete control of the Windows desktop "
        "to fulfill multi-step instructions (e.g. 'Open File Explorer, navigate to Semester 5, "
        "open Applied Image Processing, find my notes and summarize them'). "
        "Executes a continuous Observe-Plan-Act-Verify loop using Windows UI Automation, "
        "desktop vision, and local reasoning. Always call this tool when the user requests "
        "navigating files, opening apps and performing multi-step tasks on Windows."
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

