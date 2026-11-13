import unittest
from unittest.mock import MagicMock, patch

from core import gemini
from memory import config_manager


class TestProviderFallback(unittest.TestCase):

    def setUp(self):
        config_manager.set_provider_fallback_enabled(False)
        gemini._prov_cache = (-1.0, "", False)

    def tearDown(self):
        config_manager.set_provider_fallback_enabled(False)
        gemini._prov_cache = (-1.0, "", False)

    def test_fallback_disabled_by_default(self):
        self.assertFalse(config_manager.get_provider_fallback_enabled())
        rungs = gemini._provider_rungs(gemini.FAST)
        self.assertEqual(rungs, ())

    def test_fallback_enabled_adds_configured_provider(self):
        config_manager.set_provider_fallback_enabled(True)
        with patch("memory.config_manager.get_sidecar_provider", return_value="ollama_mock"):
            rungs = gemini._provider_rungs(gemini.FAST)
            self.assertEqual(rungs, ("prov:ollama_mock",))

    def test_fallback_respects_english_only_instruction(self):
        config_manager.set_provider_fallback_enabled(True)

        mock_prov = MagicMock()
        mock_prov.complete.return_value = ("Hello from fallback", [])

        with patch("memory.config_manager.get_sidecar_provider", return_value="ollama_mock"), \
             patch("memory.config_manager.get_provider_config", return_value={}), \
             patch("providers.registry.build", return_value=mock_prov):

            res = gemini._provider_call("prov:ollama_mock", "What is the capital of France?", None, 5000)
            self.assertEqual(res.text, "Hello from fallback")

            # Verify English-only rule was injected into messages
            called_messages, _ = mock_prov.complete.call_args[0][0], mock_prov.complete.call_args[1]
            sys_msgs = [m["content"] for m in called_messages if m.get("role") == "system"]
            self.assertTrue(len(sys_msgs) >= 1)
            self.assertIn("Always understand the user as speaking English", sys_msgs[0])

    def test_fallback_triggered_on_429(self):
        config_manager.set_provider_fallback_enabled(True)

        mock_prov = MagicMock()
        mock_prov.complete.return_value = ("Recovered via fallback", [])

        # Mock gemini client to throw 429 quota error on all Gemini models
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("429 RESOURCE_EXHAUSTED: Quota exceeded")

        with patch("memory.config_manager.get_sidecar_provider", return_value="ollama_mock"), \
             patch("memory.config_manager.get_provider_config", return_value={}), \
             patch("providers.registry.build", return_value=mock_prov), \
             patch("core.gemini.client", return_value=mock_client), \
             patch("core.gemini.api_key", return_value="fake_key"):

            res = gemini.call("test prompt", tier=gemini.FAST)
            self.assertIsNotNone(res)
            self.assertEqual(res.text, "Recovered via fallback")


if __name__ == "__main__":
    unittest.main()
