"""
core/skill_learning/learning_manager.py — Demonstration & Learning Lifecycle Manager.

Orchestrates the entire learning-by-demonstration loop:
- Starting / pausing / resuming / stopping learning sessions
- Coordinating live UI observation and event recording
- Abstracting demonstrations into reusable declarative skills using Gemini
- Validating and dry-testing generated workflows
- Enforcing user approval gates before autonomous activation
- Managing skill execution and persistence
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from core.outcome import ActionResult, result_done, result_failed
from core.skill_learning.models import LearningSession, SkillDefinition, SkillStatus
from core.skill_learning.observation_engine import ObservationEngine
from core.skill_learning.skill_executor import SkillExecutor
from core.skill_learning.skill_generator import SkillGenerator
from core.skill_learning.skill_registry import get_default_skill_registry
from core.skill_learning.skill_storage import (
    delete_skill as storage_delete_skill,
    load_skill,
    save_skill,
    update_skill_status,
)
from core.skill_learning.skill_validator import SkillValidator

logger = logging.getLogger(__name__)


class LearningManager:
    """Central controller for learning-by-demonstration sessions."""

    _instance: Optional[LearningManager] = None

    def __init__(
        self,
        observation_engine: Optional[ObservationEngine] = None,
        generator: Optional[SkillGenerator] = None,
        validator: Optional[SkillValidator] = None,
        executor: Optional[SkillExecutor] = None,
    ):
        self.observation_engine = observation_engine or ObservationEngine()
        self.generator = generator or SkillGenerator()
        self.validator = validator or SkillValidator()
        self.executor = executor or SkillExecutor()
        self.registry = get_default_skill_registry()

        self.current_session: Optional[LearningSession] = None

    @classmethod
    def get_instance(cls) -> LearningManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def is_learning_active(self) -> bool:
        """Check if a learning session is currently recording or paused."""
        return self.current_session is not None and self.current_session.status in ("recording", "paused")

    def start_learning(self, goal: str, player=None) -> str:
        """Initiate a new demonstration recording session."""
        if self.is_learning_active():
            return f"A learning session is already active for: '{self.current_session.goal}'. Please stop or cancel it first."

        session_id = f"learn_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        self.current_session = LearningSession(
            session_id=session_id,
            goal=goal,
            status="recording",
            started_at=time.time(),
        )

        # Start live observation hooks
        self.observation_engine.start_recording()

        msg = (
            f"Learning mode started for: '{goal}'. "
            "Please perform the workflow on your desktop. Say 'Stop learning' when finished."
        )
        if player and hasattr(player, "write_log"):
            player.write_log(f"🔴 [LEARNING MODE] {msg}")

        logger.info("Learning session started: %s (goal: %s)", session_id, goal)
        return msg

    def pause_learning(self, player=None) -> str:
        """Temporarily pause event capture."""
        if not self.current_session or self.current_session.status != "recording":
            return "No active recording session to pause."

        self.observation_engine.pause()
        self.current_session.status = "paused"
        msg = "Learning session paused. Event recording is temporarily suspended."
        if player and hasattr(player, "write_log"):
            player.write_log(f"⏸ [LEARNING MODE] {msg}")
        return msg

    def resume_learning(self, player=None) -> str:
        """Resume recording after a pause."""
        if not self.current_session or self.current_session.status != "paused":
            return "No paused recording session to resume."

        self.observation_engine.resume()
        self.current_session.status = "recording"
        msg = "Learning session resumed. Event recording active."
        if player and hasattr(player, "write_log"):
            player.write_log(f"▶ [LEARNING MODE] {msg}")
        return msg

    def stop_learning(self, player=None) -> dict:
        """Stop observation, retrieve recorded events, and automatically generate and approve skill."""
        if not self.current_session:
            return {"success": False, "message": "No active learning session to stop."}

        # Stop recording and collect sanitized events
        events = self.observation_engine.stop_recording()
        self.current_session.events = events
        self.current_session.status = "analyzing"

        msg = f"Demonstration stopped. Captured {len(events)} user interaction events."
        if player and hasattr(player, "write_log"):
            player.write_log(f"⏹ [LEARNING MODE] {msg} Generating skill...")

        logger.info("Session %s stopped with %d events", self.current_session.session_id, len(events))

        gen_skill_id = None
        if events:
            try:
                gen_res = self.generate_skill(player=player)
                if gen_res.get("success"):
                    gen_skill_id = gen_res.get("skill_id")
                    # Auto-approve newly demonstrated skill
                    self.approve_skill(gen_skill_id, player=player)
                    msg += f" Successfully generated and approved skill '{gen_res.get('name')}' (ID: '{gen_skill_id}') with {len(gen_res.get('steps', []))} steps. Ready to test or execute!"
            except Exception as e:
                logger.warning("Auto skill generation failed on stop_learning: %s", e)

        return {
            "success": True,
            "session_id": self.current_session.session_id if self.current_session else "",
            "goal": self.current_session.goal if self.current_session else "",
            "event_count": len(events),
            "generated_skill_id": gen_skill_id,
            "message": msg,
        }

    def cancel_learning(self, player=None) -> str:
        """Discard the current demonstration session completely."""
        if not self.current_session:
            return "No active learning session to cancel."

        self.observation_engine.cancel()
        goal = self.current_session.goal
        self.current_session = None

        msg = f"Learning session for '{goal}' has been cancelled and discarded."
        if player and hasattr(player, "write_log"):
            player.write_log(f"✖ [LEARNING MODE] {msg}")
        return msg

    def review_demonstration(self) -> dict:
        """Inspect the events captured during the demonstration."""
        if not self.current_session:
            return {"error": "No active session to review."}

        events = self.current_session.events
        trace = self.observation_engine.get_trace_summary(events)
        return {
            "session_id": self.current_session.session_id,
            "goal": self.current_session.goal,
            "status": self.current_session.status,
            "event_count": len(events),
            "trace_summary": trace,
            "generated_skill": self.current_session.generated_skill.to_dict() if self.current_session.generated_skill else None,
        }

    def generate_skill(self, player=None) -> dict:
        """Analyze the demonstration using Gemini and generate a declarative SkillDefinition."""
        if not self.current_session:
            return {"success": False, "message": "No recorded demonstration available to generate skill from."}

        if not self.current_session.events:
            return {"success": False, "message": "No events were recorded during the demonstration."}

        if player and hasattr(player, "write_log"):
            player.write_log("⚙ Analyzing demonstration trace with Gemini AI...")

        try:
            skill = self.generator.generate_from_session(self.current_session)
            # Validate schema and tools
            val_report = self.validator.validate_full(skill)
            self.current_session.validation_report = val_report

            # Save draft skill to disk
            save_skill(skill)
            self.registry.save_and_register(skill)

            self.current_session.status = "review_ready"

            msg = (
                f"Generated draft skill '{skill.name}' (ID: {skill.id}) with {len(skill.steps)} steps. "
                "Skill is ready for testing and approval."
            )
            if player and hasattr(player, "write_log"):
                player.write_log(f"✔ {msg}")

            return {
                "success": True,
                "skill_id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "steps": [s.to_dict() for s in skill.steps],
                "parameters": [p.to_dict() for p in skill.parameters],
                "validation": val_report,
                "message": msg,
            }
        except Exception as e:
            logger.exception("Error generating skill from demonstration")
            return {"success": False, "message": f"Failed to generate skill: {e}"}

    def test_skill(
        self,
        skill_id: str,
        test_parameters: Optional[dict] = None,
        player=None,
    ) -> ActionResult:
        """Dry-test or test-run a learned skill before final approval."""
        skill = self.registry.get_skill(skill_id)
        if not skill:
            return result_failed(action=f"test_skill:{skill_id}", message=f"Skill '{skill_id}' not found.")

        if player and hasattr(player, "write_log"):
            player.write_log(f"🧪 Testing skill '{skill.name}'...")

        # Execute in test mode (allow_unapproved=True)
        result = self.executor.execute(
            skill=skill,
            parameters=test_parameters,
            player=player,
            allow_unapproved=True,
        )

        if result.is_success():
            # Mark validated in storage
            update_skill_status(skill.id, SkillStatus.VALIDATED)
            skill.status = SkillStatus.VALIDATED.value
            self.registry.save_and_register(skill)

        return result

    def approve_skill(self, skill_id: str, player=None) -> ActionResult:
        """User approval gate: activate skill for autonomous usage."""
        skill = self.registry.get_skill(skill_id)
        if not skill:
            return result_failed(action=f"approve_skill:{skill_id}", message=f"Skill '{skill_id}' not found.")

        update_skill_status(skill.id, SkillStatus.APPROVED)
        skill.status = SkillStatus.APPROVED.value
        self.registry.save_and_register(skill)

        msg = f"Skill '{skill.name}' ({skill.id}) is now APPROVED and available for autonomous use."
        if player and hasattr(player, "write_log"):
            player.write_log(f"★ {msg}")

        logger.info(msg)
        return result_done(
            action=f"approve_skill:{skill_id}",
            message=msg,
            data={"skill_id": skill.id, "status": skill.status},
        )

    def delete_skill(self, skill_id: str) -> ActionResult:
        """Permanently remove a learned skill."""
        success = self.registry.remove_skill(skill_id)
        if success:
            return result_done(action=f"delete_skill:{skill_id}", message=f"Skill '{skill_id}' deleted.")
        return result_failed(action=f"delete_skill:{skill_id}", message=f"Skill '{skill_id}' could not be deleted or not found.")

    def execute_skill(
        self,
        skill_id: str,
        parameters: Optional[dict] = None,
        player=None,
        session_memory=None,
    ) -> ActionResult:
        """Run an approved skill."""
        skill = self.registry.get_skill(skill_id)
        if not skill:
            return result_failed(action=f"execute_skill:{skill_id}", message=f"Skill '{skill_id}' not found.")

        return self.executor.execute(
            skill=skill,
            parameters=parameters,
            player=player,
            session_memory=session_memory,
            allow_unapproved=False,
        )


def get_default_learning_manager() -> LearningManager:
    return LearningManager.get_instance()
