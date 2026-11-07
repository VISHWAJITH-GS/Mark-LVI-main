"""
core/skill_learning/skill_security.py — Security, Sensitive Field Protection, and Permissions.

Strictly protects user privacy during demonstration:
- Detects password, token, PIN, and sensitive credential fields.
- Masks sensitive inputs so passwords are NEVER recorded or sent to Gemini.
- Classifies declarative skills by RiskLevel (READ_ONLY, NORMAL, SENSITIVE, DESTRUCTIVE).
- Enforces mandatory user confirmation for sensitive or destructive actions (emailing, purchasing, deleting).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from core.skill_learning.models import RiskLevel, SkillDefinition, StepDefinition

_SENSITIVE_KEYWORD_RE = re.compile(
    r"(pass(word)?|pin|secret|token|api_?key|auth|cvv|cvc|credit_?card|card_?number|ssn)",
    re.IGNORECASE,
)

_DESTRUCTIVE_ACTIONS = {
    "delete", "remove", "drop", "destroy", "format", "shutdown", "restart",
    "send_email", "publish", "purchase", "pay", "order", "transfer", "kill",
}

_SENSITIVE_ACTIONS = {
    "upload", "download", "login", "authenticate", "connect", "install",
    "modify_settings", "write_file", "dev_agent",
}

SENSITIVE_INPUT_PLACEHOLDER = "[REDACTED_SENSITIVE_CREDENTIAL]"


def is_sensitive_control(control_info: dict) -> bool:
    """Check if a UI control is a password, secret, or credential field."""
    if not isinstance(control_info, dict):
        return False

    # Check is_password attribute from accessibility tree
    if control_info.get("is_password") is True:
        return True

    # Check names, automation_ids, and roles
    name = str(control_info.get("name") or "")
    aid = str(control_info.get("automation_id") or "")
    ctype = str(control_info.get("control_type") or "")
    class_name = str(control_info.get("class_name") or "")

    for s in (name, aid, class_name):
        if _SENSITIVE_KEYWORD_RE.search(s):
            return True

    # Web input type password
    if control_info.get("type", "").lower() == "password":
        return True

    return False


def sanitize_input_value(control_info: dict, text_value: str) -> str:
    """Mask or redact input value if control is sensitive."""
    if is_sensitive_control(control_info):
        return "[REDACTED_SENSITIVE_CREDENTIAL]"
    from memory.config_manager import redact_secret
    return redact_secret(text_value)


def classify_skill_risk(steps: list[StepDefinition], description: str = "") -> RiskLevel:
    """Determine the effective security RiskLevel for a skill."""
    full_text = description.lower()
    for s in steps:
        full_text += " " + s.thought.lower() + " " + s.tool.lower() + " " + str(s.parameters).lower()

    for act in _DESTRUCTIVE_ACTIONS:
        if act in full_text:
            return RiskLevel.DESTRUCTIVE

    for act in _SENSITIVE_ACTIONS:
        if act in full_text:
            return RiskLevel.SENSITIVE

    # Check tools
    for s in steps:
        if s.tool in ("computer_control", "windows_control", "dev_agent"):
            return RiskLevel.SENSITIVE

    return RiskLevel.NORMAL


def requires_explicit_confirmation(steps: list[Any], permissions: str = "") -> bool:
    """Check whether a list of steps or risk tier requires explicit confirmation."""
    if permissions in (RiskLevel.DESTRUCTIVE.value, "destructive"):
        return True
    for s in steps:
        if getattr(s, "is_sensitive", False) or (isinstance(s, dict) and s.get("is_sensitive")):
            return True
    return False


def requires_skill_confirmation(skill: SkillDefinition, parameters: dict | None = None) -> bool:
    """Check whether executing this skill requires explicit user confirmation."""
    if skill.permissions == RiskLevel.DESTRUCTIVE.value:
        return True
    if any(s.is_sensitive for s in skill.steps):
        return True
    return False
