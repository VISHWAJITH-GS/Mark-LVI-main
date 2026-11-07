"""
core/skill_learning — Learning-by-Demonstration Skill Engine for MARK LVI.

Enables MARK LVI to observe manual user workflows on Windows desktop and browser,
synthesize declarative skills using Google Gemini AI, validate tool safety,
and execute verified skills without manual Python scripting.
"""
from core.skill_learning.demonstration_analyzer import DemonstrationAnalyzer
from core.skill_learning.learning_manager import LearningManager, get_default_learning_manager
from core.skill_learning.models import (
    LearningSession,
    ParameterDefinition,
    RiskLevel,
    SkillDefinition,
    SkillStatus,
    StepDefinition,
    UIEvent,
    UIState,
    VerificationCondition,
)
from core.skill_learning.observation_engine import ObservationEngine
from core.skill_learning.skill_executor import SkillExecutor
from core.skill_learning.skill_generator import SkillGenerator
from core.skill_learning.skill_registry import SkillRegistry, get_default_skill_registry
from core.skill_learning.skill_storage import (
    delete_skill,
    list_skills,
    load_skill,
    save_skill,
    update_skill_status,
)
from core.skill_learning.skill_validator import SkillValidator
from core.skill_learning.workflow_builder import WorkflowBuilder

__all__ = [
    "DemonstrationAnalyzer",
    "LearningManager",
    "get_default_learning_manager",
    "LearningSession",
    "ParameterDefinition",
    "RiskLevel",
    "SkillDefinition",
    "SkillStatus",
    "StepDefinition",
    "UIEvent",
    "UIState",
    "VerificationCondition",
    "ObservationEngine",
    "SkillExecutor",
    "SkillGenerator",
    "SkillRegistry",
    "get_default_skill_registry",
    "SkillValidator",
    "WorkflowBuilder",
    "delete_skill",
    "list_skills",
    "load_skill",
    "save_skill",
    "update_skill_status",
]

