"""
core/skill_learning/workflow_builder.py — Declarative Workflow & Step Assembler.

Converts analyzed demonstration data into structured StepDefinitions,
attaches verification conditions, configures recovery parameters, and calculates risk tiers.
"""
from __future__ import annotations

from typing import Any, Dict, List

from core.skill_learning.models import (
    ParameterDefinition,
    RiskLevel,
    SkillDefinition,
    SkillStatus,
    StepDefinition,
    VerificationCondition,
)
from core.skill_learning.skill_security import classify_skill_risk


class WorkflowBuilder:
    """Builds authoritative SkillDefinition instances from semantic analysis."""

    def build_skill_definition(self, analysis_data: dict, author: str = "user_demonstration") -> SkillDefinition:
        """Construct a validated SkillDefinition object from analysis output."""
        skill_id = str(analysis_data.get("skill_id") or "unnamed_skill").lower().strip()
        name = str(analysis_data.get("skill_name") or skill_id.replace("_", " ").title())
        desc = str(analysis_data.get("description") or f"Learned workflow for {name}")
        app = str(analysis_data.get("application") or "Desktop")

        # Parameters
        params = []
        for p in analysis_data.get("parameters", []):
            if isinstance(p, dict):
                params.append(ParameterDefinition(
                    name=p.get("name", "param"),
                    type=p.get("type", "string"),
                    description=p.get("description", ""),
                    required=p.get("required", True),
                    default=p.get("default"),
                    example=p.get("example"),
                ))

        # Steps
        steps = []
        tool_deps = set()
        for idx, s in enumerate(analysis_data.get("steps", []), 1):
            if not isinstance(s, dict):
                continue
            tool_name = str(s.get("tool") or "windows_uia")
            tool_deps.add(tool_name)

            # Verification condition
            v_cond = None
            raw_v = s.get("verification")
            if isinstance(raw_v, dict) and raw_v.get("type"):
                v_cond = VerificationCondition(
                    type=raw_v.get("type", "window_title_contains"),
                    target=raw_v.get("target", ""),
                    expected_value=raw_v.get("expected_value", ""),
                    timeout_seconds=float(raw_v.get("timeout_seconds", 8.0)),
                )

            # Step definition with recovery rule
            recovery = s.get("recovery") or {
                "retry_count": 2,
                "delay_between_retries": 1.2,
                "reobserve_window": True,
            }

            steps.append(StepDefinition(
                step_id=s.get("step_id", idx),
                thought=s.get("thought", f"Execute {tool_name}"),
                tool=tool_name,
                parameters=dict(s.get("parameters", {})),
                selector=s.get("selector"),
                verification=v_cond,
                recovery=recovery,
                is_sensitive=bool(s.get("is_sensitive", False)),
            ))

        # Security classification
        risk = classify_skill_risk(steps, description=desc)

        return SkillDefinition(
            id=skill_id,
            name=name,
            description=desc,
            category="application" if app and app != "Desktop" else "user",
            application=app,
            version="1.0.0",
            intent_examples=analysis_data.get("intent_examples", [name]),
            parameters=params,
            preconditions=analysis_data.get("preconditions", []),
            steps=steps,
            tool_dependencies=sorted(list(tool_deps)),
            permissions=risk.value,
            status=SkillStatus.DRAFT.value,
            author=author,
        )

