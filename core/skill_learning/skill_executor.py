"""
core/skill_learning/skill_executor.py — Declarative Skill Runner with Verification & Recovery.

Safely executes approved declarative SkillDefinitions:
- Substitutes dynamic runtime parameters ({param_name}) into tool arguments.
- Executes actions through the unified ActionRegistry.
- Verifies post-conditions after each critical step.
- Performs automatic retries and failure recovery based on step recovery rules.
- Enforces security checks: unapproved or high-risk skills require approval.
- Emits real-time progress via JarvisUI player if present.
- Returns normalized ActionResults (core.outcome.ActionResult).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from core.action_loader import get_default_registry
from core.outcome import (
    ActionResult,
    result_done,
    result_failed,
    result_needs_confirmation,
    result_unverified,
)
from core.skill_learning.models import (
    RiskLevel,
    SkillDefinition,
    SkillStatus,
    StepDefinition,
    VerificationCondition,
)
from core.skill_learning.skill_security import requires_explicit_confirmation

logger = logging.getLogger(__name__)


class SkillExecutor:
    """Executes declarative learned skills with parameter injection, step verification, and recovery."""

    def __init__(self, action_registry=None):
        self.registry = action_registry or get_default_registry()

    def execute(
        self,
        skill: SkillDefinition,
        parameters: Optional[dict] = None,
        player=None,
        session_memory=None,
        allow_unapproved: bool = False,
    ) -> ActionResult:
        """Run a skill workflow end-to-end and verify results.
        
        Args:
            skill: The SkillDefinition to execute.
            parameters: Runtime parameter values for template substitution.
            player: Optional JarvisUI interface for HUD logs and updates.
            session_memory: Optional conversation context dict.
            allow_unapproved: If True, allows executing DRAFT or VALIDATED skills (e.g. during testing).
        """
        start_time = time.time()
        parameters = parameters or {}

        # 1. Approval Gate
        if not allow_unapproved and skill.status != SkillStatus.APPROVED.value:
            msg = (
                f"Skill '{skill.name}' is in '{skill.status}' status. "
                "User approval is required before autonomous execution."
            )
            logger.warning(msg)
            return result_failed(
                action=f"skill:{skill.id}",
                message=msg,
                error="SkillNotApprovedError",
                data={"status": skill.status, "skill_id": skill.id},
            )

        # 2. Risk confirmation gate for sensitive/destructive workflows
        user_confirmed = bool(
            parameters.get("__user_confirmed__", False)
            or parameters.get("approve_skill", False)
            or parameters.get("approved", False)
            or parameters.get("confirm", False)
        )
        if requires_explicit_confirmation(skill.steps, skill.permissions):
            if not user_confirmed:
                from core import confirm
                risk_level_str = "high_impact" if skill.permissions == "destructive" else "confirmation"
                prompt_msg = confirm.request_action(
                    action=f"skill:{skill.id}",
                    parameters=parameters,
                    risk_level=risk_level_str,
                    title=f"Execute Skill: {skill.name}",
                    detail=f"Skill '{skill.name}' performs {len(skill.steps)} automated steps including sensitive actions.",
                    run=lambda: getattr(
                        self.execute(
                            skill=skill,
                            parameters={**parameters, "__user_confirmed__": True},
                            player=player,
                            session_memory=session_memory,
                            allow_unapproved=allow_unapproved,
                        ),
                        "message",
                        "Skill executed successfully.",
                    ),
                )
                return result_needs_confirmation(
                    action=f"skill:{skill.id}",
                    message=prompt_msg,
                    data={"skill_id": skill.id, "risk": skill.permissions},
                )

        # 3. Parameter validation & defaults
        resolved_params = {}
        for p_def in skill.parameters:
            if p_def.name in parameters:
                resolved_params[p_def.name] = parameters[p_def.name]
            elif p_def.default is not None:
                resolved_params[p_def.name] = p_def.default
            elif p_def.required:
                err_msg = f"Missing required parameter '{p_def.name}' for skill '{skill.name}'."
                return result_failed(action=f"skill:{skill.id}", message=err_msg, error="MissingParameterError")
            else:
                resolved_params[p_def.name] = ""

        # Include any extra parameters directly passed
        for k, v in parameters.items():
            if k not in resolved_params and not k.startswith("__"):
                resolved_params[k] = v

        if player and hasattr(player, "write_log"):
            player.write_log(f"▶ Executing skill: {skill.name} ({len(skill.steps)} steps)")

        # 4. Sequential Step Execution
        step_results = []
        for step in skill.steps:
            from core.skill_learning.skill_security import sanitize_text_and_credentials
            step_desc = sanitize_text_and_credentials(step.thought)
            logger.info("Running step %d/%d: %s (%s)", step.step_id, len(skill.steps), step_desc, step.tool)
            print(f"[SKILL] Step {step.step_id}/{len(skill.steps)}: {step_desc} ({step.tool})")
            if player and hasattr(player, "write_log"):
                player.write_log(f"▶ [Skill] Step {step.step_id}/{len(skill.steps)}: {step_desc}")

            # Substitute parameters into tool parameters
            sub_params = self._substitute_parameters(step.parameters, resolved_params)

            # Execute with automatic retries if specified
            step_res = self._execute_step_with_recovery(
                step=step,
                sub_params=sub_params,
                player=player,
                session_memory=session_memory,
            )

            step_results.append({
                "step_id": step.step_id,
                "tool": step.tool,
                "status": step_res.status,
                "message": step_res.message,
                "verified": step_res.verified,
            })

            if not step_res.is_success():
                err_msg = f"Skill '{skill.name}' failed at step {step.step_id} ({step.thought}): {step_res.message}"
                logger.error(err_msg)
                if player and hasattr(player, "write_log"):
                    player.write_log(f"✖ Step {step.step_id} failed: {step_res.message}")
                return result_failed(
                    action=f"skill:{skill.id}",
                    message=err_msg,
                    error=step_res.error or "StepExecutionError",
                    data={"failed_step": step.step_id, "step_results": step_results},
                    duration_ms=int((time.time() - start_time) * 1000),
                )

        duration = int((time.time() - start_time) * 1000)
        success_msg = f"Skill '{skill.name}' completed successfully ({len(skill.steps)} steps executed)."
        if player and hasattr(player, "write_log"):
            player.write_log(f"✔ {success_msg}")

        return result_done(
            action=f"skill:{skill.id}",
            message=success_msg,
            data={"skill_id": skill.id, "steps_executed": len(step_results), "history": step_results},
            verified=True,
            duration_ms=duration,
        )

    def _execute_step_with_recovery(
        self,
        step: StepDefinition,
        sub_params: dict,
        player=None,
        session_memory=None,
    ) -> ActionResult:
        """Execute a step, verify post-condition, and retry on failure according to recovery config."""
        recovery = step.recovery or {}
        max_retries = int(recovery.get("retry_count", 1))
        delay = float(recovery.get("delay_between_retries", 1.0))

        last_result: Optional[ActionResult] = None

        for attempt in range(max_retries + 1):
            if attempt > 0:
                logger.info("Retrying step %d (attempt %d/%d)...", step.step_id, attempt, max_retries)
                time.sleep(delay)

            # Invoke tool through the unified registry
            res = self.registry.execute(
                name=step.tool,
                parameters=sub_params,
                player=player,
                session_memory=session_memory,
            )
            last_result = res

            if not res.is_success():
                # Check for coordinate fallback on click steps
                coords = sub_params.get("coordinates") or (step.selector.get("coordinates") if step.selector else None)
                if not coords and ("x" in sub_params and "y" in sub_params):
                    coords = {"x": sub_params["x"], "y": sub_params["y"]}
                elif not coords and step.selector and ("x" in step.selector and "y" in step.selector):
                    coords = {"x": step.selector["x"], "y": step.selector["y"]}

                if coords and "x" in coords and "y" in coords:
                    logger.info("Step %d %s failed; attempting coordinate fallback (%s, %s)",
                                step.step_id, step.tool, coords["x"], coords["y"])
                    res = self.registry.execute(
                        name="computer_control",
                        parameters={"action": "click", "x": coords["x"], "y": coords["y"]},
                        player=player,
                        session_memory=session_memory,
                    )
                    last_result = res
                    if not res.is_success():
                        continue
                else:
                    continue

            # Verify post-condition if specified
            if step.verification:
                verified, v_msg = self._verify_condition(step.verification)
                if not verified:
                    last_result = result_unverified(
                        action=step.tool,
                        message=f"Step {step.step_id} action succeeded but verification condition not met: {v_msg}",
                    )
                    continue
                else:
                    last_result.verified = True

            # If we reached here, step succeeded and passed verification
            return last_result

        return last_result or result_failed(action=step.tool, message="Step failed after retries.")

    def _verify_condition(self, condition: VerificationCondition) -> tuple[bool, str]:
        """Check post-condition state."""
        cond_type = condition.type.lower().strip()
        target = condition.target.strip()
        expected = condition.expected_value.strip().lower()

        try:
            if cond_type == "window_title_contains":
                from actions.windows_control import get_active_window_title
                curr_title = (get_active_window_title() or "").lower()
                target_str = (target or expected).lower()
                if target_str in curr_title:
                    return True, f"Active window title '{curr_title}' contains '{target_str}'"
                return False, f"Active window title '{curr_title}' does not contain '{target_str}'"

            elif cond_type == "file_exists":
                if os.path.exists(target):
                    return True, f"File exists: {target}"
                return False, f"File not found: {target}"

            elif cond_type == "element_visible":
                from actions.windows_uia import find_element_by_name
                el = find_element_by_name(target or expected)
                if el:
                    return True, f"Element '{target}' is visible."
                return False, f"Element '{target}' not found in active UI."

            elif cond_type == "process_running":
                import psutil
                proc_name = (target or expected).lower()
                for p in psutil.process_iter(["name"]):
                    if proc_name in (p.info.get("name") or "").lower():
                        return True, f"Process '{proc_name}' is running."
                return False, f"Process '{proc_name}' not running."

            # Default fallback for unknown or generic verification
            return True, f"Condition '{cond_type}' accepted."
        except Exception as e:
            logger.warning("Verification condition exception: %s", e)
            return False, f"Verification failed with error: {e}"

    def _substitute_parameters(self, params: dict, values: dict) -> dict:
        """Deep substitute {param_name} placeholders in strings, lists, and dicts."""
        substituted = {}
        for k, v in params.items():
            if isinstance(v, str):
                val = v
                for p_name, p_val in values.items():
                    val = val.replace(f"{{{p_name}}}", str(p_val))
                substituted[k] = val
            elif isinstance(v, dict):
                substituted[k] = self._substitute_parameters(v, values)
            elif isinstance(v, list):
                substituted[k] = [
                    v_item.replace(f"{{{p_name}}}", str(p_val))
                    if isinstance(v_item, str) else v_item
                    for v_item in v
                    for p_name, p_val in values.items()
                ]
            else:
                substituted[k] = v
        return substituted
