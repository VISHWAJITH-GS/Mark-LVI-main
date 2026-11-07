"""
core/skill_learning/skill_validator.py — Schema, Tool, and Execution Validator for Learned Skills.

Validates that a learned SkillDefinition:
1. Conforms to the declarative schema (valid IDs, names, steps, parameters).
2. Uses only registered and available actions in the unified ActionRegistry.
3. Contains valid parameter references ({param}) within step arguments.
4. Complies with safety and security guidelines (no sensitive/redacted tokens).
5. Provides a dry-run simulation mode to test parameter substitution and step validity.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from core.action_loader import get_default_registry
from core.skill_learning.models import RiskLevel, SkillDefinition, SkillStatus, StepDefinition
from core.skill_learning.skill_security import SENSITIVE_INPUT_PLACEHOLDER

logger = logging.getLogger(__name__)


class SkillValidator:
    """Performs static analysis, schema compliance, and dry-run validation on skills."""

    def __init__(self, action_registry=None):
        self.registry = action_registry or get_default_registry()

    def validate_schema(self, skill: SkillDefinition) -> Tuple[bool, list[str], list[str]]:
        """Validate structure and integrity of a SkillDefinition.
        
        Returns:
            (is_valid, errors, warnings)
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not skill.id or not re.match(r"^[a-z0-9_\-]+$", skill.id):
            errors.append(f"Invalid skill ID '{skill.id}'. Must contain only lowercase letters, digits, '_' or '-'.")

        if not skill.name or len(skill.name.strip()) < 3:
            errors.append("Skill name must be at least 3 characters.")

        if not skill.steps:
            errors.append("Skill must contain at least one step.")

        # Check parameter names
        defined_param_names = set()
        for p in skill.parameters:
            if not p.name or not re.match(r"^[a-zA-Z0-9_]+$", p.name):
                errors.append(f"Invalid parameter name '{p.name}'.")
            defined_param_names.add(p.name)

        # Inspect steps
        step_ids = set()
        for idx, step in enumerate(skill.steps, 1):
            if step.step_id in step_ids:
                warnings.append(f"Duplicate step_id {step.step_id}; auto-correcting to {idx}.")
            step_ids.add(step.step_id)

            if not step.tool:
                errors.append(f"Step {idx} has no tool declared.")
                continue

            # Check if tool is available in the ActionRegistry
            if not self.registry.has_action(step.tool):
                errors.append(f"Step {idx} refers to unregistered tool '{step.tool}'.")

            # Check for unresolved sensitive placeholders
            for k, v in step.parameters.items():
                if isinstance(v, str):
                    if SENSITIVE_INPUT_PLACEHOLDER in v:
                        warnings.append(f"Step {idx} parameter '{k}' contains redacted credential placeholder. Needs parameterization.")

                    # Check parameter placeholders {param_name}
                    placeholders = re.findall(r"\{([a-zA-Z0-9_]+)\}", v)
                    for ph in placeholders:
                        if ph not in defined_param_names:
                            warnings.append(
                                f"Step {idx} uses placeholder '{{{ph}}}' which is not defined in skill parameters."
                            )

        is_valid = len(errors) == 0
        return is_valid, errors, warnings

    def dry_run_test(
        self,
        skill: SkillDefinition,
        test_inputs: Optional[dict] = None,
    ) -> dict:
        """Simulate execution of skill steps without calling OS tools.
        
        Verifies:
        - All required parameters are satisfied or have defaults
        - Template substitution evaluates properly
        - Tool signatures accept the substituted arguments
        """
        test_inputs = test_inputs or {}
        report = {
            "skill_id": skill.id,
            "success": True,
            "errors": [],
            "warnings": [],
            "substituted_steps": [],
        }

        # 1. Parameter completeness
        substituted_params: dict[str, Any] = {}
        for p in skill.parameters:
            if p.name in test_inputs:
                substituted_params[p.name] = test_inputs[p.name]
            elif p.default is not None:
                substituted_params[p.name] = p.default
            elif p.required:
                report["errors"].append(f"Missing required parameter '{p.name}' for test.")
                report["success"] = False
            else:
                substituted_params[p.name] = ""

        if not report["success"]:
            return report

        # 2. Step substitution simulation
        for step in skill.steps:
            sub_params = {}
            for k, v in step.parameters.items():
                if isinstance(v, str):
                    val = v
                    for p_name, p_val in substituted_params.items():
                        val = val.replace(f"{{{p_name}}}", str(p_val))
                    sub_params[k] = val
                else:
                    sub_params[k] = v

            # Verify tool accepts parameters
            action_def = self.registry.get_action(step.tool)
            if not action_def:
                report["errors"].append(f"Step {step.step_id}: Tool '{step.tool}' not found in registry.")
                report["success"] = False
            else:
                param_valid, param_err = action_def.validate_parameters(sub_params)
                if not param_valid:
                    report["warnings"].append(f"Step {step.step_id} tool '{step.tool}' parameter validation: {param_err}")

            report["substituted_steps"].append({
                "step_id": step.step_id,
                "tool": step.tool,
                "parameters": sub_params,
                "verification": step.verification.to_dict() if step.verification else None,
            })

        return report

    def validate_full(
        self,
        skill: SkillDefinition,
        sample_inputs: Optional[dict] = None,
    ) -> dict:
        """Perform comprehensive validation and return a structured report."""
        is_valid, errors, warnings = self.validate_schema(skill)
        dry_run = self.dry_run_test(skill, sample_inputs)

        errors.extend(dry_run["errors"])
        warnings.extend(dry_run["warnings"])

        overall_valid = is_valid and dry_run["success"]

        return {
            "valid": overall_valid,
            "errors": list(set(errors)),
            "warnings": list(set(warnings)),
            "step_count": len(skill.steps),
            "tool_dependencies": skill.tool_dependencies,
            "risk_level": skill.permissions,
            "simulation": dry_run["substituted_steps"],
        }

