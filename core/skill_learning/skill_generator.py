"""
core/skill_learning/skill_generator.py — High-Level Skill Generator.

Coordinates the demonstration analyzer and workflow builder to generate
authoritative, declarative, and validated SkillDefinitions from captured
user demonstrations.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.skill_learning.demonstration_analyzer import DemonstrationAnalyzer
from core.skill_learning.models import LearningSession, SkillDefinition, UIEvent
from core.skill_learning.workflow_builder import WorkflowBuilder

logger = logging.getLogger(__name__)


class SkillGenerator:
    """Orchestrates demonstration analysis and declarative workflow assembly."""

    def __init__(
        self,
        analyzer: Optional[DemonstrationAnalyzer] = None,
        builder: Optional[WorkflowBuilder] = None,
    ):
        self.analyzer = analyzer or DemonstrationAnalyzer()
        self.builder = builder or WorkflowBuilder()

    def generate_from_events(
        self,
        goal: str,
        events: list[UIEvent],
        trace_summary: str = "",
        author: str = "user_demonstration",
    ) -> SkillDefinition:
        """Analyze recorded events and construct a complete declarative SkillDefinition."""
        if not trace_summary and events:
            # Generate fallback summary if empty
            lines = [f"- {e.event_type} on '{e.element_name or e.window_title}' ({e.app_name})" for e in events[:20]]
            trace_summary = "\n".join(lines)

        logger.info("Analyzing demonstration with %d events for goal: '%s'", len(events), goal)
        analysis = self.analyzer.analyze_demonstration(
            goal=goal,
            events=events,
            trace_summary=trace_summary,
        )

        skill = self.builder.build_skill_definition(analysis, author=author)
        logger.info("Generated skill '%s' (ID: %s) with %d steps", skill.name, skill.id, len(skill.steps))
        return skill

    def generate_from_session(self, session: LearningSession) -> SkillDefinition:
        """Generate a SkillDefinition directly from a completed LearningSession."""
        from core.skill_learning.observation_engine import ObservationEngine
        engine = ObservationEngine()
        trace_summary = engine.get_trace_summary(session.events)
        skill = self.generate_from_events(
            goal=session.goal,
            events=session.events,
            trace_summary=trace_summary,
        )
        session.generated_skill = skill
        return skill

