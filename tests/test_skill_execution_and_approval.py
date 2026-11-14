"""
tests/test_skill_execution_and_approval.py — Verification for Skill Learner & Execution Engine.

Tests:
1. Tool schema includes 'approve_skill' property.
2. Robust parsing of malformed / compound action strings (e.g. 'execute_skill, approve_skill:True').
3. Confirmation gate integration with ConfirmationManager.
4. Word-boundary risk classification (preventing 'skillrack' from matching 'kill').
5. Observable step-by-step reporting with credential redaction.
"""
import unittest
from unittest.mock import MagicMock, patch

from actions.skill_learner import TOOL, skill_learner
from core.confirm import ConfirmationManager
from core.outcome import STATUS_NEEDS_CONFIRMATION
from core.skill_learning.models import RiskLevel, SkillDefinition, StepDefinition
from core.skill_learning.skill_security import classify_skill_risk, sanitize_text_and_credentials


class TestSkillExecutionAndApproval(unittest.TestCase):

    def setUp(self):
        ConfirmationManager.discard_pending("test setup")

    def tearDown(self):
        ConfirmationManager.discard_pending("test teardown")

    def test_tool_schema_contains_approve_skill(self):
        """Tool parameter schema must expose approve_skill to prevent model parameter jamming."""
        props = TOOL["parameters"]["properties"]
        self.assertIn("approve_skill", props)
        self.assertEqual(props["approve_skill"]["type"], "BOOLEAN")

    def test_compound_action_parsing(self):
        """When model provides 'execute_skill, approve_skill:True', action is correctly extracted."""
        with patch("actions.skill_learner.get_default_learning_manager") as mock_mgr_getter:
            mock_mgr = MagicMock()
            mock_mgr_getter.return_value = mock_mgr

            skill_learner({
                "action": "execute_skill, approve_skill:True",
                "skill_id": "test_skill",
            })

            # execute_skill should have been called with approve_skill set in parameters
            self.assertTrue(mock_mgr.execute_skill.called)
            call_kwargs = mock_mgr.execute_skill.call_args[1]
            params = call_kwargs.get("parameters", {})
            self.assertTrue(params.get("approve_skill"))
            self.assertTrue(params.get("__user_confirmed__"))

    def test_word_boundary_risk_classification(self):
        """'Skillrack Login' should NOT be classified as DESTRUCTIVE due to 'kill' in 'skillrack'."""
        steps = [
            StepDefinition(step_id=1, tool="open_app", thought="Open Chrome browser"),
            StepDefinition(step_id=2, tool="browser_control", thought="Navigate to Skillrack portal"),
            StepDefinition(step_id=3, tool="windows_uia", thought="Enter username"),
            StepDefinition(step_id=4, tool="windows_uia", thought="Enter password"),
            StepDefinition(step_id=5, tool="windows_uia", thought="Click Login button"),
        ]
        risk = classify_skill_risk(steps, description="Skillrack Login workflow")
        # Should be SENSITIVE because of 'login' and 'password', NOT DESTRUCTIVE
        self.assertEqual(risk, RiskLevel.SENSITIVE)
        self.assertNotEqual(risk, RiskLevel.DESTRUCTIVE)

    def test_confirmation_gate_parks_action_when_unconfirmed(self):
        """Executing a sensitive skill without approval must park in ConfirmationManager."""
        from core.skill_learning.skill_executor import SkillExecutor

        mock_registry = MagicMock()
        executor = SkillExecutor(action_registry=mock_registry)

        sensitive_skill = SkillDefinition(
            id="skillrack_login",
            name="Skillrack Login",
            description="Login to Skillrack",
            category="web",
            status=RiskLevel.NORMAL.value, # or SkillStatus.APPROVED.value
            permissions=RiskLevel.SENSITIVE.value,
            steps=[
                StepDefinition(step_id=1, tool="open_app", thought="Open Chrome", is_sensitive=True),
            ],
        )
        sensitive_skill.status = "approved"

        res = executor.execute(sensitive_skill, parameters={})
        self.assertEqual(res.status, STATUS_NEEDS_CONFIRMATION)

        # Must have parked in ConfirmationManager
        pending = ConfirmationManager.get_pending()
        self.assertIsNotNone(pending)
        self.assertIn("Skillrack Login", pending.title)

    def test_confirmation_resolution_executes_skill(self):
        """User voice approval ('confirm') must resolve and execute the parked skill."""
        from core.skill_learning.skill_executor import SkillExecutor

        mock_registry = MagicMock()
        mock_registry.execute.return_value = MagicMock(is_success=lambda: True, status="done", message="Done", verified=True)

        executor = SkillExecutor(action_registry=mock_registry)
        sensitive_skill = SkillDefinition(
            id="skillrack_login",
            name="Skillrack Login",
            description="Login to Skillrack",
            category="web",
            status=RiskLevel.NORMAL.value, # or SkillStatus.APPROVED.value
            permissions=RiskLevel.SENSITIVE.value,
            steps=[
                StepDefinition(step_id=1, tool="open_app", thought="Open Chrome", is_sensitive=True),
            ],
        )
        sensitive_skill.status = "approved"

        # Park action
        executor.execute(sensitive_skill, parameters={})
        self.assertIsNotNone(ConfirmationManager.get_pending())

        # Resolve via user confirmation
        resolved = ConfirmationManager.resolve(True)
        self.assertTrue(resolved)

        # Pending should now be cleared to prevent re-execution
        self.assertIsNone(ConfirmationManager.get_pending())

    def test_explicit_approval_executes_immediately(self):
        """When approve_skill=True is passed upfront, execution proceeds without confirmation park."""
        from core.skill_learning.skill_executor import SkillExecutor

        mock_registry = MagicMock()
        mock_registry.execute.return_value = MagicMock(is_success=lambda: True, status="done", message="Done", verified=True)

        executor = SkillExecutor(action_registry=mock_registry)
        sensitive_skill = SkillDefinition(
            id="skillrack_login",
            name="Skillrack Login",
            description="Login to Skillrack",
            category="web",
            status=RiskLevel.NORMAL.value, # or SkillStatus.APPROVED.value
            permissions=RiskLevel.SENSITIVE.value,
            steps=[
                StepDefinition(step_id=1, tool="open_app", thought="Open Chrome", is_sensitive=True),
            ],
        )
        sensitive_skill.status = "approved"

        res = executor.execute(sensitive_skill, parameters={"approve_skill": True})
        self.assertTrue(res.is_success())
        self.assertIsNone(ConfirmationManager.get_pending())

    def test_credential_redaction_in_logging(self):
        """Sensitive credentials like passwords must be redacted in traces and logs."""
        raw_text = "Entered password secretPass123 into password field"
        sanitized = sanitize_text_and_credentials(raw_text)
        self.assertNotIn("secretPass123", sanitized)
        self.assertIn("[REDACTED_CREDENTIAL]", sanitized)


if __name__ == "__main__":
    unittest.main()
