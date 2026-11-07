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
from pathlib import Path
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
        self.manager.delete_skill("skillrack_login")
        self.manager.delete_skill("skillrack_auto_login")

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


    def test_auto_generation_on_stop(self):
        """stop_learning should automatically generate and approve the demonstrated skill."""
        self.manager.start_learning("Learn how to log in to Skillrack")
        # Add mock events
        self.manager.current_session.events = [
            UIEvent(timestamp=1.0, event_type="click", app_name="Chrome", window_title="Skillrack", element_name="Login", selector={"x": 500, "y": 300}),
            UIEvent(timestamp=2.0, event_type="type_text", app_name="Chrome", window_title="Skillrack", input_value="user@tce.edu"),
        ]

        with patch.object(self.manager.generator.analyzer, "analyze_demonstration") as mock_ana:
            mock_ana.return_value = {
                "skill_name": "Skillrack Login",
                "skill_id": "skillrack_login",
                "application": "Chrome",
                "description": "Log into Skillrack",
                "steps": [
                    {"step_id": 1, "thought": "Click Login", "tool": "windows_uia", "parameters": {"action": "click", "name": "Login", "x": 500, "y": 300}},
                ],
            }
            res = self.manager.stop_learning()
            self.assertTrue(res["success"])
            self.assertEqual(res["generated_skill_id"], "skillrack_login")
            self.assertIn("Successfully generated and approved", res["message"])

            # Verify it is approved in the registry
            skill = self.manager.registry.get_skill("skillrack_login")
            self.assertIsNotNone(skill)
            self.assertEqual(skill.status, SkillStatus.APPROVED.value)

    def test_skill_id_semantic_fallback(self):
        """Even if model calls execute_skill with an imprecise/hallucinated ID, it resolves via semantic search or session."""
        test_skill = SkillDefinition(
            id="skillrack_auto_login",
            name="Skillrack Login",
            description="Log in to Skillrack student portal",
            application="Chrome",
            intent_examples=["login to skillrack", "skillrack login"],
            steps=[StepDefinition(step_id=1, thought="Click", tool="windows_uia", parameters={"action": "click", "name": "Login"})],
            status=SkillStatus.APPROVED.value,
        )
        self.manager.registry.save_and_register(test_skill)

        # Call with a hallucinated ID
        with patch.object(self.manager.executor, "execute") as mock_exec:
            mock_exec.return_value = ActionResult.done("skill:skillrack_auto_login", message="Success")
            res = self.manager.execute_skill("skill_chrome_skillrack_login")
            self.assertTrue(res.is_success())
            # Ensure it executed the resolved skill
            called_skill = mock_exec.call_args[1]["skill"]
            self.assertEqual(called_skill.id, "skillrack_auto_login")

    def test_coordinate_fallback_execution(self):
        """If windows_uia fails to find element by name, fallback to recorded coordinates via computer_control."""
        executor = SkillExecutor()
        coord_skill = SkillDefinition(
            id="coord_test_skill",
            name="Coordinate Fallback",
            description="Test coord fallback",
            steps=[
                StepDefinition(
                    step_id=1,
                    thought="Click student profile",
                    tool="windows_uia",
                    parameters={"action": "click", "name": "student.tce.edu", "x": 640, "y": 480},
                )
            ],
            status=SkillStatus.APPROVED.value,
        )

        with patch.object(executor.registry, "execute") as mock_exec:
            # First call (windows_uia) fails, second call (computer_control coordinate click) succeeds
            mock_exec.side_effect = [
                ActionResult.failed("windows_uia", message="Element not found"),
                ActionResult.done("computer_control", message="Clicked at (640, 480)"),
            ]
            res = executor.execute(coord_skill)
            self.assertTrue(res.is_success())
            self.assertEqual(mock_exec.call_count, 2)
            second_call_params = mock_exec.call_args_list[1][1]["parameters"]
            self.assertEqual(second_call_params["action"], "click")
            self.assertEqual(second_call_params["x"], 640)
            self.assertEqual(second_call_params["y"], 480)


class TestSkillLearnerToolAction(unittest.TestCase):
    """Test actions.skill_learner tool execution with ActionRegistry."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.tmp.name)

    def tearDown(self):
        try:
            from core.skill_learning.learning_manager import LearningManager
            LearningManager.get_instance().delete_skill("skillrack_student_login")
            LearningManager.get_instance().cancel_learning()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_skill_learner_registration_and_list(self):
        from core.action_loader import get_default_registry

        reg = get_default_registry()
        self.assertTrue(reg.has("skill_learner"))

        # Test execution through ActionRegistry
        res = reg.execute("skill_learner", {"action": "list_skills"})
        self.assertIsInstance(res, ActionResult)
        self.assertEqual(res.status, STATUS_DONE)

        # Test invalid action
        err_res = reg.execute("skill_learner", {"action": "non_existent_action_xyz"})
        self.assertEqual(err_res.status, STATUS_FAILED)

    def test_full_skillrack_lifecycle_mocked(self):
        """End-to-end lifecycle test for the Skillrack demonstration workflow."""
        from actions import skill_learner
        from core.skill_learning.learning_manager import LearningManager
        from core.skill_learning.models import UIEvent

        manager = LearningManager.get_instance()
        manager.cancel_learning()

        # 1. Start learning
        start_res = manager.start_learning(goal="Learn how to log in to Skillrack using student profile")
        self.assertIn("started", start_res.lower())
        self.assertIsNotNone(manager.current_session)

        # 2. Simulate captured events (including credentials)
        t = time.time()
        raw_events = [
            UIEvent(timestamp=t, event_type="window_activate", app_name="msedge", window_title="Microsoft Edge"),
            UIEvent(timestamp=t + 1, event_type="click", app_name="msedge", window_title="Microsoft Edge", element_name="Student", bounding_box={"x": 450, "y": 320, "w": 80, "h": 25}),
            UIEvent(timestamp=t + 2, event_type="type_text", app_name="msedge", window_title="Microsoft Edge", element_name="Address bar", input_value="skillrack.com"),
            UIEvent(timestamp=t + 3, event_type="click", app_name="msedge", window_title="Microsoft Edge", element_name="Login", bounding_box={"x": 800, "y": 200, "w": 60, "h": 25}),
            UIEvent(timestamp=t + 4, event_type="type_text", app_name="msedge", window_title="Microsoft Edge", element_name="Student ID", input_value="2403917714821034@tce"),
            UIEvent(timestamp=t + 5, event_type="type_text", app_name="msedge", window_title="Microsoft Edge", element_name="Password", input_value="forskillrack@2006"),
            UIEvent(timestamp=t + 6, event_type="click", app_name="msedge", window_title="Microsoft Edge", element_name="Submit", bounding_box={"x": 800, "y": 450, "w": 60, "h": 25}),
        ]
        manager.current_session.events = raw_events

        # 3. Stop learning -> triggers auto-analysis and generation
        with patch.object(manager.generator.analyzer, "analyze_demonstration") as mock_analyze:
            mock_analyze.return_value = {
                "skill_name": "Skillrack Student Login",
                "skill_id": "skillrack_student_login",
                "application": "msedge",
                "description": "Log into Skillrack student portal via Student profile",
                "intent_examples": ["Login into my skill rack", "Open skillrack student"],
                "parameters": [
                    {"name": "username", "type": "string", "description": "Student register ID", "required": True, "is_credential": True},
                    {"name": "password", "type": "string", "description": "Skillrack password", "required": True, "is_credential": True},
                ],
                "preconditions": ["Microsoft Edge is installed"],
                "steps": [
                    {"step_id": 1, "thought": "Open Edge", "tool": "open_app", "parameters": {"app_name": "msedge"}},
                    {"step_id": 2, "thought": "Select Student Profile", "tool": "browser_profile", "parameters": {"profile_name": "Student", "x": 450, "y": 320}},
                    {"step_id": 3, "thought": "Navigate to Skillrack", "tool": "browser_control", "parameters": {"action": "navigate", "url": "https://skillrack.com"}},
                    {"step_id": 4, "thought": "Enter credentials", "tool": "windows_uia", "parameters": {"action": "type_text", "text": "{password}"}},
                ],
            }
            stop_res = manager.stop_learning()
            self.assertTrue(stop_res["success"])
            skill_id = stop_res["generated_skill_id"]
            self.assertEqual(skill_id, "skillrack_student_login")

            # Verify persisted skill does NOT contain plaintext password
            stored_skill = manager.registry.get_skill("skillrack_student_login")
            self.assertIsNotNone(stored_skill)
            raw_json = json.dumps(stored_skill.to_dict())
            self.assertNotIn("forskillrack@2006", raw_json)
            self.assertNotIn("2403917714821034", raw_json)

            # 4. Test skill
            with patch.object(manager.executor.registry, "execute") as mock_exec:
                mock_exec.return_value = ActionResult.done("step", message="ok")
                test_res = manager.test_skill(skill_id, test_parameters={"username": "std1", "password": "secret"})
                self.assertTrue(test_res.is_success())

            # 5. Approve skill
            app_res = manager.approve_skill(skill_id)
            self.assertTrue(app_res.is_success())
            self.assertEqual(stored_skill.status, SkillStatus.APPROVED.value)

            # 6. Execute skill
            with patch.object(manager.executor.registry, "execute") as mock_exec:
                mock_exec.return_value = ActionResult.done("step", message="Logged in")
                exec_res = manager.execute_skill("skillrack_student_login", parameters={"username": "std1", "password": "secret", "__user_confirmed__": True})
                self.assertTrue(exec_res.is_success())

    def test_browser_profile_tool(self):
        """Test actions.browser_profile detection and profile selection dispatch."""
        from actions import browser_profile
        from core.action_loader import get_default_registry

        reg = get_default_registry()
        self.assertTrue(reg.has("browser_profile"))

        # Test detect action
        det_res = browser_profile.browser_profile({"action": "detect"})
        self.assertIn("status", det_res)

        # Test select_profile with missing profile
        fail_res = browser_profile.browser_profile({"action": "select_profile", "profile_name": ""})
        self.assertIn("failed", fail_res)

        # Test select_profile with coordinate fallback
        with patch("actions.computer_control.computer_control") as mock_cc:
            mock_cc.return_value = "clicked"
            with patch("actions.windows_uia.find_element") as mock_fe:
                mock_fe.return_value = {"status": "failed"}
                succ_res = browser_profile.select_browser_profile(profile_name="Student", x=500, y=300)
                self.assertIn("status=done", succ_res)
                self.assertIn("coordinates", succ_res)


if __name__ == "__main__":
    unittest.main()
