"""
core/skill_learning/models.py — Data Models for the Learning-by-Demonstration Skill Engine.

Defines all structured types for:
- Learning sessions & recording lifecycle
- UI events & state captures
- Declarative skill definitions, parameters, steps, and verification rules
- Security risk levels & approval lifecycle statuses
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
import json
from typing import Any, Dict, List, Optional


class SkillStatus(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVED = "approved"
    DISABLED = "disabled"


class RiskLevel(str, Enum):
    READ_ONLY = "read_only"
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    DESTRUCTIVE = "destructive"


@dataclass
class UIEvent:
    """Captured user action during demonstration."""
    timestamp: float
    event_type: str  # click | double_click | type_text | hotkey | window_activate | browser_navigate | file_dialog | state_change
    app_name: str
    window_title: str
    element_name: str = ""
    control_type: str = ""
    automation_id: str = ""
    role: str = ""
    selector: dict = field(default_factory=dict)
    action: str = ""
    input_value: str = ""  # NEVER passwords or credentials
    bounding_box: Optional[dict] = None
    before_state: Optional[dict] = None
    after_state: Optional[dict] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> UIEvent:
        return cls(**data)


@dataclass
class UIState:
    """Snapshot of active window and accessible controls."""
    timestamp: float
    app_name: str
    window_title: str
    process_name: str
    elements: list[dict] = field(default_factory=list)
    focused_element: Optional[dict] = None
    is_browser: bool = False
    url: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> UIState:
        return cls(**data)


@dataclass
class ParameterDefinition:
    """Dynamic parameter extracted from demonstration."""
    name: str
    type: str = "string"  # string | number | boolean | file_path | url
    description: str = ""
    required: bool = True
    default: Any = None
    example: Any = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ParameterDefinition:
        return cls(**data)


@dataclass
class VerificationCondition:
    """Expected post-condition for validating a skill step."""
    type: str  # window_title_contains | element_visible | element_value_equals | file_exists | process_running | custom_rule
    target: str = ""
    expected_value: str = ""
    timeout_seconds: float = 8.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> VerificationCondition:
        return cls(**data)


@dataclass
class StepDefinition:
    """Single atomic step in a declarative skill workflow."""
    step_id: int
    thought: str
    tool: str  # Name of registered tool: open_app, windows_uia, computer_control, browser_control, file_controller, etc.
    parameters: dict = field(default_factory=dict)
    selector: Optional[dict] = None
    verification: Optional[VerificationCondition] = None
    recovery: Optional[dict] = None  # e.g. {"retry_count": 2, "alternative_action": ...}
    is_sensitive: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.verification:
            d["verification"] = self.verification.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> StepDefinition:
        d = dict(data)
        if d.get("verification") and isinstance(d["verification"], dict):
            d["verification"] = VerificationCondition.from_dict(d["verification"])
        return cls(**d)


@dataclass
class SkillDefinition:
    """Authoritative declarative definition of a learned desktop skill."""
    id: str  # Slug, e.g. "claude_document_analysis"
    name: str
    description: str
    category: str = "user"  # application | workflow | user
    application: str = ""
    version: str = "1.0.0"
    intent_examples: list[str] = field(default_factory=list)
    parameters: list[ParameterDefinition] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    steps: list[StepDefinition] = field(default_factory=list)
    tool_dependencies: list[str] = field(default_factory=list)
    permissions: str = RiskLevel.NORMAL.value
    status: str = SkillStatus.DRAFT.value
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_validated_at: Optional[str] = None
    last_used_at: Optional[str] = None
    author: str = "user_demonstration"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["parameters"] = [p.to_dict() for p in self.parameters]
        d["steps"] = [s.to_dict() for s in self.steps]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> SkillDefinition:
        d = dict(data)
        if d.get("parameters"):
            d["parameters"] = [
                ParameterDefinition.from_dict(p) if isinstance(p, dict) else p
                for p in d["parameters"]
            ]
        if d.get("steps"):
            d["steps"] = [
                StepDefinition.from_dict(s) if isinstance(s, dict) else s
                for s in d["steps"]
            ]
        return cls(**d)


@dataclass
class LearningSession:
    """Active demonstration recording session."""
    session_id: str
    goal: str
    status: str = "idle"  # idle | recording | paused | analyzing | review_ready | completed | cancelled
    started_at: float = field(default_factory=lambda: datetime.now().timestamp())
    events: list[UIEvent] = field(default_factory=list)
    generated_skill: Optional[SkillDefinition] = None
    validation_report: Optional[dict] = None

    def to_dict(self) -> dict:
        d = {
            "session_id": self.session_id,
            "goal": self.goal,
            "status": self.status,
            "started_at": self.started_at,
            "event_count": len(self.events),
            "generated_skill_id": self.generated_skill.id if self.generated_skill else None,
            "validation_report": self.validation_report,
        }
        return d
