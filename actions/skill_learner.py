"""
actions/skill_learner.py — Unified Tool for Demonstration-Based Skill Learning & Execution.

Exposes the Learning-by-Demonstration Skill Engine to Gemini Live and the Autonomous Agent:
- Start, pause, resume, stop, and cancel demonstration recording
- Review captured events and semantic trace
- Generate declarative skills using Gemini AI
- Test and dry-run skills
- User approval gate for activating skills
- Execute learned skills with runtime parameters
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from core import outcome
from core.skill_learning.learning_manager import get_default_learning_manager
from core.skill_learning.models import SkillStatus
from core.skill_learning.skill_registry import get_default_skill_registry


def skill_learner(
    parameters: Optional[dict] = None,
    player=None,
    session_memory=None,
    **kwargs: Any,
) -> str:
    """Handle skill learning and execution actions."""
    # Unpack parameters whether passed as dict or direct kwargs
    p = dict(parameters or {})
    p.update(kwargs)

    action_raw = str(p.get("action", "")).strip()
    goal = p.get("goal")
    skill_id = p.get("skill_id")
    skill_params = p.get("parameters")

    # Detect explicit user approval flags passed in parameters or kwargs
    approve_skill_flag = bool(p.get("approve_skill") or p.get("approved") or p.get("confirm"))

    # Handle model hallucinations where parameters are combined into action string,
    # e.g. "execute_skill, approve_skill:True"
    if "approve_skill" in action_raw.lower() or "approve" in action_raw.lower():
        if "true" in action_raw.lower() or "1" in action_raw.lower():
            approve_skill_flag = True

    # Normalize primary action name: take first token before comma/semicolon/colon
    act = re.split(r"[,;:]", action_raw)[0].strip().lower()

    manager = get_default_learning_manager()

    if act in ("start_learning", "start", "learn"):
        if not goal:
            return outcome.failed("skill_learner", "A 'goal' describing the workflow to learn is required.")
        msg = manager.start_learning(goal=goal, player=player)
        return outcome.done("skill_learner", message=msg, data={"status": "recording", "goal": goal})

    elif act in ("pause_learning", "pause"):
        msg = manager.pause_learning(player=player)
        return outcome.done("skill_learner", message=msg)

    elif act in ("resume_learning", "resume"):
        msg = manager.resume_learning(player=player)
        return outcome.done("skill_learner", message=msg)

    elif act in ("stop_learning", "stop"):
        res = manager.stop_learning(player=player)
        if not res.get("success"):
            return outcome.failed("skill_learner", res.get("message", "Failed to stop learning."))
        return outcome.done("skill_learner", message=res["message"], data=res)

    elif act in ("cancel_learning", "cancel"):
        msg = manager.cancel_learning(player=player)
        return outcome.done("skill_learner", message=msg)

    elif act in ("review_demonstration", "review"):
        data = manager.review_demonstration()
        if "error" in data:
            return outcome.failed("skill_learner", data["error"])
        summary = (
            f"Session '{data.get('goal')}': {data.get('event_count')} events captured. "
            f"Trace: {data.get('trace_summary')[:300]}..."
        )
        return outcome.done("skill_learner", message=summary, data=data)

    elif act in ("generate_skill", "generate", "create_skill"):
        res = manager.generate_skill(player=player)
        if not res.get("success"):
            return outcome.failed("skill_learner", res.get("message", "Failed to generate skill."))
        return outcome.done("skill_learner", message=res["message"], data=res)

    elif act in ("test_skill", "test"):
        if not skill_id:
            # Fallback to session generated skill id if available
            if manager.current_session and manager.current_session.generated_skill:
                skill_id = manager.current_session.generated_skill.id
            else:
                return outcome.failed("skill_learner", "skill_id is required to test a skill.")

        # If parameters were passed as a JSON string, parse them
        params_dict = skill_params or {}
        if isinstance(params_dict, str):
            try:
                params_dict = json.loads(params_dict)
            except Exception:
                params_dict = {}

        return manager.test_skill(skill_id=skill_id, test_parameters=params_dict, player=player)

    elif act in ("approve_skill", "approve"):
        if not skill_id:
            if manager.current_session and manager.current_session.generated_skill:
                skill_id = manager.current_session.generated_skill.id
            else:
                return outcome.failed("skill_learner", "skill_id is required to approve a skill.")
        return manager.approve_skill(skill_id=skill_id, player=player)

    elif act in ("list_skills", "list"):
        reg = get_default_skill_registry()
        reg.reload()
        skills = reg.get_all_skills(approved_only=False)
        summaries = [
            {
                "id": s.id,
                "name": s.name,
                "status": s.status,
                "application": s.application,
                "steps": len(s.steps),
                "permissions": s.permissions,
            }
            for s in skills
        ]
        msg = f"Found {len(skills)} learned skills in library."
        return outcome.done("skill_learner", message=msg, data={"skills": summaries})

    elif act in ("execute_skill", "run_skill", "execute", "run"):
        target_id = skill_id or p.get("goal")
        if not target_id:
            if manager.current_session and manager.current_session.generated_skill:
                target_id = manager.current_session.generated_skill.id
            else:
                all_s = manager.registry.get_all_skills(approved_only=False)
                if all_s:
                    target_id = all_s[-1].id
                else:
                    return outcome.failed("skill_learner", "No skill ID provided and no skills exist in library.")

        params_dict = skill_params or {}
        if isinstance(params_dict, str):
            try:
                params_dict = json.loads(params_dict)
            except Exception:
                params_dict = {}

        if approve_skill_flag:
            params_dict["__user_confirmed__"] = True
            params_dict["approve_skill"] = True

        return manager.execute_skill(
            skill_id=target_id,
            parameters=params_dict,
            player=player,
            session_memory=session_memory,
        )

    elif act in ("delete_skill", "delete", "remove_skill"):
        if not skill_id:
            return outcome.failed("skill_learner", "skill_id is required to delete a skill.")
        return manager.delete_skill(skill_id=skill_id)

    return outcome.failed("skill_learner", f"Unknown skill_learner action: '{action_raw}'")


TOOL = {
    "name": "skill_learner",
    "description": (
        "Demonstration-based skill learning and execution system. "
        "Allows MARK LVI to observe user demonstrations on Windows desktop and web applications, "
        "synthesize reusable declarative skills using Gemini AI, test them safely, "
        "manage user approval gates, and autonomously execute learned skills. "
        "Actions: start_learning, pause_learning, resume_learning, stop_learning, "
        "cancel_learning, review_demonstration, generate_skill, test_skill, approve_skill, "
        "list_skills, execute_skill, delete_skill."
    ),
    "brief": "Demonstration-based skill learning, testing, approval, and execution.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "Exact action: start_learning | pause_learning | resume_learning | "
                    "stop_learning | cancel_learning | review_demonstration | generate_skill | "
                    "test_skill | approve_skill | list_skills | execute_skill | delete_skill"
                ),
            },
            "goal": {
                "type": "STRING",
                "description": "User intent or workflow goal description (for start_learning). E.g. 'Learn how to use Claude'",
            },
            "skill_id": {
                "type": "STRING",
                "description": "The unique ID of the skill to test, approve, execute, or delete.",
            },
            "approve_skill": {
                "type": "BOOLEAN",
                "description": "Explicit user approval to execute skills containing sensitive actions (set to true when user confirms execution).",
            },
            "parameters": {
                "type": "OBJECT",
                "description": "Dynamic runtime parameters for skill execution (e.g. {'file_path': 'C:/doc.pdf', 'prompt': 'Summarize'})",
            },
        },
        "required": ["action"],
    },
    "handler": skill_learner,
}
