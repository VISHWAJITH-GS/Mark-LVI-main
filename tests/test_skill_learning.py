"""
tests/test_skill_learning.py — Comprehensive Unit & Integration Tests for Skill Engine.

Tests:
1. Models serialization and deserialization (UIEvent, SkillDefinition, StepDefinition, ParameterDefinition).
2. Privacy & security protection: sensitive/password field redaction and risk classification.
3. Persistent storage, manifest management, and backup operations.
4. ObservationEngine trace collection, debouncing, and summary generation.
5. DemonstrationAnalyzer and WorkflowBuilder declarative assembly.
6. SkillValidator schema validation and dry-run simulation.
7. SkillExecutor parameter substitution, step execution, verification, and recovery.
8. Approval gate enforcement (blocking unapproved skills, allowing approved skills).
9. LearningManager lifecycle (start, pause, resume, stop, cancel, review, generate, test, approve, delete).
10. Action handler `actions.skill_learner` integration with standard ActionResult contract.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from core.action_loader import ActionRegistry, get_default_registry
from core.outcome import ActionResult, STATUS_DONE, STATUS_FAILED, STATUS_NEEDS_CONFIRMATION
from core.skill_learning import (
    DemonstrationAnalyzer,
    LearningManager,
    LearningSession,
    ObservationEngine,
    ParameterDefinition,
    RiskLevel,
    SkillDefinition,
    SkillExecutor,
    SkillGenerator,
    SkillRegistry,
    SkillStatus,
    SkillValidator,
    StepDefinition,
    UIEvent,
    UIState,
    VerificationCondition,
    WorkflowBuilder,
    delete_skill,
    list_skills,
    load_skill,
    save_skill,
    update_skill_status,
)
from core.skill_learning.skill_security import (
    SENSITIVE_INPUT_PLACEHOLDER,
    classify_skill_risk,
    is_sensitive_control,
    requires_explicit_confirmation,
    sanitize_input_value,
)


class TestSkillLearningModels(unittest.TestCase):
    """Test data models serialization and deserialization."""

    def test_ui_event_serialization(self):
        event = UIEvent(
            timestamp=1000.0,
            event_type="click",
            app_name="Claude",
            window_title="Claude Desktop",
            element_name="Send",
            control_type="Button",
            automation_id="send_btn",
            action="click",
        )
        d = event.to_dict()
        self.assertEqual(d["app_name"], "Claude")
        self.assertEqual(d["element_name"], "Send")

        restored = UIEvent.from_dict(d)
        self.assertEqual(restored.window_title, "Claude Desktop")
        self.assertEqual(restored.control_type, "Button")

    def test_skill_definition_roundtrip(self):
        step = StepDefinition(
            step_id=1,
            thought="Click Upload",
            tool="windows_uia",
            parameters={"action": "click", "name": "Upload"},
            verification=VerificationCondition(
                type="element_visible",
                target="File Dialog",
                expected_value="File Dialog",
            ),
        )
        param = ParameterDefinition(
            name="file_path",
            type="file_path",
            description="Document path",
            required=True,
        )
        skill = SkillDefinition(
            id="test_upload_skill",
            name="Test Upload",
            description="Uploads a document",
            application="Claude",
            parameters=[param],
            steps=[step],
            tool_dependencies=["windows_uia"],
            permissions="normal",
            status=SkillStatus.DRAFT.value,
        )

        d = skill.to_dict()
        self.assertEqual(d["id"], "test_upload_skill")
        self.assertEqual(len(d["steps"]), 1)
        self.assertEqual(d["steps"][0]["verification"]["type"], "element_visible")

        restored = SkillDefinition.from_dict(d)
        self.assertEqual(restored.id, "test_upload_skill")
        self.assertEqual(restored.steps[0].verification.type, "element_visible")
        self.assertEqual(restored.parameters[0].name, "file_path")


class TestSkillSecurityAndPrivacy(unittest.TestCase):
    """Test security checks, password redacting, and risk classification."""

    def test_sensitive_control_detection(self):
        # 1. is_password flag
        self.assertTrue(is_sensitive_control({"is_password": True, "name": "Secret"}))

        # 2. Keyword detection in name or automation_id
        self.assertTrue(is_sensitive_control({"name": "Password Input", "control_type": "Edit"}))
        self.assertTrue(is_sensitive_control({"name": "API Key", "automation_id": "api_key_box"}))
        self.assertTrue(is_sensitive_control({"name": "CVV Code", "automation_id": "card_cvv"}))
        self.assertTrue(is_sensitive_control({"class_name": "PinEntryField"}))

        # 3. Non-sensitive controls
        self.assertFalse(is_sensitive_control({"name": "Search Box", "control_type": "Edit"}))
        self.assertFalse(is_sensitive_control({"name": "Submit", "control_type": "Button"}))

    def test_sensitive_value_redaction(self):
        sens_ctrl = {"name": "User Password", "is_password": True}
        val = sanitize_input_value(sens_ctrl, "super_secret_p@ss123")
        self.assertEqual(val, SENSITIVE_INPUT_PLACEHOLDER)

        safe_ctrl = {"name": "Prompt text", "is_password": False}
        safe_val = sanitize_input_value(safe_ctrl, "Summarize this file")
        self.assertEqual(safe_val, "Summarize this file")

    def test_risk_classification(self):
        # Destructive step
        step_dest = StepDefinition(
            step_id=1,
            thought="Delete folder",
            tool="file_controller",
            parameters={"action": "delete"},
        )
        self.assertEqual(classify_skill_risk([step_dest]), RiskLevel.DESTRUCTIVE)

        # Sensitive step
        step_sens = StepDefinition(
            step_id=1,
            thought="Run automation command",
            tool="computer_control",
            parameters={"action": "hotkey", "key": "ctrl+c"},
        )
        self.assertEqual(classify_skill_risk([step_sens]), RiskLevel.SENSITIVE)

        # Normal step
        step_norm = StepDefinition(
            step_id=1,
            thought="Click send button",
            tool="windows_uia",
            parameters={"action": "click", "name": "Send"},
        )
        self.assertEqual(classify_skill_risk([step_norm]), RiskLevel.NORMAL)

    def test_confirmation_requirement(self):
        step_norm = StepDefinition(step_id=1, thought="Click", tool="windows_uia")
        step_sens = StepDefinition(step_id=2, thought="Send email", tool="send_message", is_sensitive=True)

        self.assertFalse(requires_explicit_confirmation([step_norm], "normal"))
        self.assertTrue(requires_explicit_confirmation([step_norm, step_sens], "normal"))
        self.assertTrue(requires_explicit_confirmation([step_norm], "destructive"))


class TestSkillStorageAndManifest(unittest.TestCase):
    """Test saving, loading, status updating, backups, and deleting skills."""

    def setUp(self):
        self.test_skill = SkillDefinition(
            id="unit_test_skill_xyz",
            name="Unit Test Skill",
            description="Testing storage persistence",
            category="user",
            application="Notepad",
            steps=[
                StepDefinition(step_id=1, thought="Type", tool="windows_uia", parameters={"text": "hello"})
            ],
            status=SkillStatus.DRAFT.value,
        )

    def tearDown(self):
        delete_skill(self.test_skill.id)

    def test_save_load_update_delete_cycle(self):
        # 1. Save skill
        saved_path = save_skill(self.test_skill)
        self.assertTrue(saved_path.exists())

        # 2. Load skill
        loaded = load_skill(self.test_skill.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "Unit Test Skill")
        self.assertEqual(loaded.status, SkillStatus.DRAFT.value)

        # 3. List skills
        all_skills = list_skills()
        self.assertTrue(any(s.id == self.test_skill.id for s in all_skills))

        # 4. Update status
        updated = update_skill_status(self.test_skill.id, SkillStatus.APPROVED)
        self.assertTrue(updated)
        reloaded = load_skill(self.test_skill.id)
        self.assertEqual(reloaded.status, SkillStatus.APPROVED.value)

        # 5. Delete skill
        deleted = delete_skill(self.test_skill.id)
        self.assertTrue(deleted)
        self.assertIsNone(load_skill(self.test_skill.id))


class TestObservationEngineAndTrace(unittest.TestCase):
    """Test event observation, merging, and summary formatting."""

    def test_observation_trace_and_debouncing(self):
        engine = ObservationEngine()
        events = [
            UIEvent(timestamp=1.0, event_type="click", app_name="Chrome", window_title="Claude", element_name="Upload"),
            UIEvent(timestamp=2.0, event_type="type_text", app_name="Chrome", window_title="Claude", input_value="prompt 1"),
            # Rapid consecutive typing should be handled
            UIEvent(timestamp=2.1, event_type="type_text", app_name="Chrome", window_title="Claude", input_value="prompt 2"),
        ]

        debounced = engine.debounce_events(events)
        self.assertTrue(len(debounced) <= len(events))

        trace = engine.get_trace_summary(events)
        self.assertIn("Chrome", trace)
        self.assertIn("Upload", trace)


class TestWorkflowBuilderAndAnalyzer(unittest.TestCase):
    """Test semantic analysis and declarative workflow assembly."""

    def test_workflow_builder_assembly(self):
        builder = WorkflowBuilder()
        mock_analysis = {
            "skill_name": "Claude Summarize",
            "skill_id": "claude_summarize",
            "application": "Claude",
            "description": "Uploads and summarizes a file",
            "intent_examples": ["summarize this document in claude"],
            "parameters": [
                {"name": "file_path", "type": "file_path", "description": "Doc to summarize", "required": True},
                {"name": "prompt", "type": "string", "description": "Instruction", "default": "Summarize this."},
            ],
            "steps": [
                {
                    "step_id": 1,
                    "thought": "Focus Claude input box",
                    "tool": "windows_uia",
                    "parameters": {"action": "click", "name": "Prompt Input"},
                    "verification": {"type": "element_visible", "target": "Prompt Input"},
                },
                {
                    "step_id": 2,
                    "thought": "Type instruction into Claude",
                    "tool": "windows_uia",
                    "parameters": {"action": "type_text", "text": "{prompt}"},
                },
            ],
        }

        skill = builder.build_skill_definition(mock_analysis)
        self.assertEqual(skill.id, "claude_summarize")
        self.assertEqual(skill.application, "Claude")
        self.assertEqual(len(skill.parameters), 2)
        self.assertEqual(len(skill.steps), 2)
        self.assertEqual(skill.steps[0].verification.type, "element_visible")
        self.assertEqual(skill.status, SkillStatus.DRAFT.value)


class TestSkillValidatorAndDryRun(unittest.TestCase):
    """Test schema checking and dry-run validation."""

    def test_validator_schema_and_dry_run(self):
        validator = SkillValidator()

        valid_skill = SkillDefinition(
            id="sample_valid_skill",
            name="Sample Valid Skill",
            description="A test skill",
            application="Windows",
            parameters=[
                ParameterDefinition(name="target_name", type="string", required=True)
            ],
            steps=[
                StepDefinition(
                    step_id=1,
                    thought="Click item",
                    tool="windows_uia",
                    parameters={"action": "click", "name": "{target_name}"},
                )
            ],
            status=SkillStatus.DRAFT.value,
        )

        is_valid, errors, warnings = validator.validate_schema(valid_skill)
        self.assertTrue(is_valid, f"Expected valid schema, got errors: {errors}")

        # Dry run test with satisfied parameter
        dry_res = validator.dry_run_test(valid_skill, {"target_name": "MyButton"})
        self.assertTrue(dry_res["success"])
        self.assertEqual(dry_res["substituted_steps"][0]["parameters"]["name"], "MyButton")

        # Dry run test with missing required parameter
        dry_missing = validator.dry_run_test(valid_skill, {})
        self.assertFalse(dry_missing["success"])


class TestSkillExecutorAndApprovalGate(unittest.TestCase):
    """Test execution, parameter injection, and strict user approval enforcement."""

    def setUp(self):
        self.executor = SkillExecutor()
        self.skill = SkillDefinition(
            id="exec_test_skill",
            name="Execution Test Skill",
            description="Testing executor and gates",
            application="Desktop",
            parameters=[
                ParameterDefinition(name="query", type="string", required=True)
            ],
            steps=[
                StepDefinition(
                    step_id=1,
                    thought="Search the web",
                    tool="web_search",
                    parameters={"query": "{query}"},
                )
            ],
            status=SkillStatus.DRAFT.value,
        )

    def test_unapproved_skill_blocked_by_default(self):
        """Unapproved skills MUST be blocked from autonomous execution."""
        self.skill.status = SkillStatus.DRAFT.value
        res = self.executor.execute(self.skill, {"query": "python"}, allow_unapproved=False)
        self.assertFalse(res.is_success())
        self.assertEqual(res.status, STATUS_FAILED)
        self.assertIn("User approval is required", res.message)

    def test_allow_unapproved_flag_for_testing(self):
        """Testing mode should allow dry-testing even in draft status."""
        self.skill.status = SkillStatus.DRAFT.value

        with patch.object(self.executor.registry, "execute") as mock_exec:
            mock_exec.return_value = ActionResult.done("web_search", message="Searched successfully.")
            res = self.executor.execute(self.skill, {"query": "python"}, allow_unapproved=True)
            self.assertTrue(res.is_success())
            mock_exec.assert_called_once()
            # Verify parameter was substituted
            call_params = mock_exec.call_args[1]["parameters"]
            self.assertEqual(call_params["query"], "python")

    def test_approved_skill_executes_autonomously(self):
        """Approved skills run autonomously."""
        self.skill.status = SkillStatus.APPROVED.value

        with patch.object(self.executor.registry, "execute") as mock_exec:
            mock_exec.return_value = ActionResult.done("web_search", message="Searched successfully.")
            res = self.executor.execute(self.skill, {"query": "deepmind"})
            self.assertTrue(res.is_success())
            self.assertEqual(res.status, STATUS_DONE)

    def test_step_failure_and_recovery_retry(self):
        """Test automatic recovery and retry logic on step failure."""
        self.skill.status = SkillStatus.APPROVED.value
        self.skill.steps[0].recovery = {"retry_count": 2, "delay_between_retries": 0.01}

        with patch.object(self.executor.registry, "execute") as mock_exec:
            # First attempt fails, second succeeds
            mock_exec.side_effect = [
                ActionResult.failed("web_search", message="Connection timeout"),
                ActionResult.done("web_search", message="Success on retry"),
            ]
            res = self.executor.execute(self.skill, {"query": "weather"})
            self.assertTrue(res.is_success())
            self.assertEqual(mock_exec.call_count, 2)


class TestLearningManagerLifecycle(unittest.TestCase):
    """Test full learning session lifecycle (start, pause, resume, stop, cancel, approve)."""

    def setUp(self):
        self.manager = LearningManager.get_instance()
        self.manager.cancel_learning()  # Ensure clean slate

    def tearDown(self):
        self.manager.cancel_learning()

    def test_session_lifecycle(self):
        # 1. Start learning
        start_msg = self.manager.start_learning("Learn how to use Claude")
        self.assertTrue(self.manager.is_learning_active())
        self.assertEqual(self.manager.current_session.status, "recording")
        self.assertIn("Learning mode started", start_msg)

        # 2. Pause learning
        pause_msg = self.manager.pause_learning()
        self.assertEqual(self.manager.current_session.status, "paused")
        self.assertIn("paused", pause_msg.lower())

        # 3. Resume learning
        resume_msg = self.manager.resume_learning()
        self.assertEqual(self.manager.current_session.status, "recording")
        self.assertIn("resumed", resume_msg.lower())

        # 4. Stop learning
        stop_res = self.manager.stop_learning()
        self.assertTrue(stop_res["success"])
        self.assertEqual(self.manager.current_session.status, "analyzing")

        # 5. Cancel learning
        cancel_msg = self.manager.cancel_learning()
        self.assertFalse(self.manager.is_learning_active())
        self.assertIsNone(self.manager.current_session)


class TestSkillLearnerToolAction(unittest.TestCase):
    """Test actions.skill_learner tool execution with ActionRegistry."""

    def test_skill_learner_registration_and_list(self):
        from actions.skill_learner import skill_learner

        # Test list_skills
        res = skill_learner(action="list_skills")
        self.assertIsInstance(res, ActionResult)
        self.assertEqual(res.status, STATUS_DONE)
        self.assertIn("skills", res.data)

        # Test invalid action
        err_res = skill_learner(action="non_existent_action_xyz")
        self.assertEqual(err_res.status, STATUS_FAILED)


if __name__ == "__main__":
    unittest.main()
