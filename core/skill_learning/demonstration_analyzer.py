"""
core/skill_learning/demonstration_analyzer.py — Gemini AI Demonstration Analyzer.

Interprets raw observation event traces using Google Gemini API:
- Infers workflow purpose and user intent
- Identifies participating desktop/web applications
- Discovers and abstracts dynamic parameters (file paths, prompts, URLs)
- Maps interaction steps to declarative local tools and semantic selectors
- Defines verification rules and failure recovery paths
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from core import gemini as _gemini
from core.skill_learning.models import (
    ParameterDefinition,
    StepDefinition,
    UIEvent,
    VerificationCondition,
)


class DemonstrationAnalyzer:
    """Analyzes demonstrated user interactions and extracts semantic workflows using Gemini."""

    def analyze_demonstration(
        self,
        goal: str,
        events: list[UIEvent],
        trace_summary: str,
    ) -> dict:
        """Analyze demonstration trace and return structured workflow analysis."""
        if not events:
            return self._empty_analysis(goal)

        prompt = f"""You are MARK LVI's Senior AI Skill Architect.
Analyze the following recorded user demonstration of a Windows desktop workflow and convert it into a reusable declarative skill.

USER GOAL / INTENT: "{goal}"

DEMONSTRATION EVENT TRACE:
{trace_summary}

INSTRUCTIONS:
1. Identify the primary application (e.g. "Claude", "File Explorer", "Chrome", "Notepad", "Gmail").
2. Extract dynamic reusable parameters. If the user selected a specific file, abstract it to parameter "file_path". If they typed a prompt, abstract it to "prompt". If they navigated to a specific URL, abstract to "url". Never hardcode the user's specific test files or personal prompts.
3. Convert the events into a sequence of atomic, verified steps using standard local tools:
   - "open_app": Launch application (app_name: str)
   - "windows_uia": Semantic element interaction (action: "click" | "type_text" | "open_item", name: str, control_type: str, automation_id: str, text: str)
   - "browser_control": Browser automation (action: "navigate" | "click" | "type", url: str, selector: str)
   - "computer_control": OS inputs (action: "hotkey" | "press", key: str)
   - "file_controller": Local file operations
4. Define a verification condition for each important step (e.g. type="window_title_contains" or "element_visible").

OUTPUT FORMAT:
Return ONLY a valid JSON object matching this exact schema:
{{
  "skill_name": "Claude Document Analysis",
  "skill_id": "claude_document_analysis",
  "application": "Claude",
  "description": "Uploads a document to Claude and generates an analysis from a prompt.",
  "intent_examples": ["Analyze this document in Claude", "Use Claude to summarize my file"],
  "parameters": [
    {{"name": "file_path", "type": "file_path", "description": "Path to document", "required": true}},
    {{"name": "prompt", "type": "string", "description": "Prompt instruction for Claude", "required": true}}
  ],
  "preconditions": ["Claude desktop app or web browser is accessible"],
  "steps": [
    {{
      "step_id": 1,
      "thought": "Launch Claude application",
      "tool": "open_app",
      "parameters": {{"app_name": "Claude"}},
      "verification": {{"type": "window_title_contains", "target": "Claude", "expected_value": "Claude"}}
    }},
    {{
      "step_id": 2,
      "thought": "Type user prompt into Claude message input",
      "tool": "windows_uia",
      "parameters": {{"action": "type_text", "text": "{{prompt}}", "name": "Message Claude"}},
      "verification": {{"type": "element_visible", "target": "Send"}}
    }}
  ]
}}
"""
        try:
            # Smart Gemini tier for workflow reasoning
            raw = _gemini.text(prompt, tier=_gemini.SMART)
            if raw:
                clean = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
                clean = re.sub(r"\s*```$", "", clean.strip(), flags=re.MULTILINE)
                parsed = json.loads(clean)
                if isinstance(parsed, dict) and "steps" in parsed:
                    return parsed
        except Exception as e:
            print(f"[DemonstrationAnalyzer] Gemini analysis fallback due to: {e}")

        # Deterministic rule-based fallback if offline
        return self._deterministic_fallback_analysis(goal, events)

    def _deterministic_fallback_analysis(self, goal: str, events: list[UIEvent]) -> dict:
        """Deterministic workflow extraction for offline or test execution."""
        apps = {e.app_name for e in events if e.app_name and e.app_name != "Unknown"}
        primary_app = list(apps)[0] if apps else "Desktop"
        slug = re.sub(r"[^a-z0-9_]+", "_", goal.lower().strip())[:40].strip("_") or "demonstrated_skill"

        steps = []
        parameters = []
        step_id = 1

        for e in events:
            if e.event_type == "window_activate":
                steps.append({
                    "step_id": step_id,
                    "thought": f"Switch to window '{e.window_title}'",
                    "tool": "open_app",
                    "parameters": {"app_name": e.app_name},
                    "verification": {"type": "window_title_contains", "target": e.app_name, "expected_value": e.app_name},
                })
                step_id += 1
            elif e.event_type in ("click", "double_click"):
                elem_name = e.element_name or e.selector.get("name", "Button")
                steps.append({
                    "step_id": step_id,
                    "thought": f"Click '{elem_name}' in {e.app_name}",
                    "tool": "windows_uia",
                    "parameters": {
                        "action": "click",
                        "name": elem_name,
                        "control_type": e.control_type or "Button",
                        "element_name": elem_name,
                    },
                    "verification": {"type": "element_visible", "target": elem_name},
                })
                step_id += 1
            elif e.event_type == "type_text" and e.input_value:
                # Abstract input into a dynamic parameter if longer than 3 words or path-like
                param_name = "input_text"
                if "\\" in e.input_value or "/" in e.input_value or "." in e.input_value:
                    param_name = "file_path"
                    parameters.append({
                        "name": param_name,
                        "type": "file_path",
                        "description": "Target file path",
                        "required": True,
                    })
                else:
                    param_name = "prompt"
                    parameters.append({
                        "name": param_name,
                        "type": "string",
                        "description": "User instruction text",
                        "required": True,
                    })

                steps.append({
                    "step_id": step_id,
                    "thought": f"Enter {param_name} into {e.element_name}",
                    "tool": "windows_uia",
                    "parameters": {
                        "action": "type_text",
                        "text": f"{{{param_name}}}",
                        "element_name": e.element_name,
                    },
                    "verification": {"type": "element_visible", "target": e.element_name},
                })
                step_id += 1

        return {
            "skill_name": goal.capitalize(),
            "skill_id": slug,
            "application": primary_app,
            "description": f"Workflow learned from demonstration: {goal}",
            "intent_examples": [goal, f"Run {goal}", f"Execute {goal}"],
            "parameters": parameters,
            "preconditions": [f"{primary_app} is installed and available"],
            "steps": steps,
        }

    def _empty_analysis(self, goal: str) -> dict:
        return {
            "skill_name": goal,
            "skill_id": "empty_skill",
            "application": "Desktop",
            "description": "No events recorded.",
            "intent_examples": [goal],
            "parameters": [],
            "preconditions": [],
            "steps": [],
        }
