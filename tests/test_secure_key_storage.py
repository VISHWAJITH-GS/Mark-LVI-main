"""
tests/test_secure_key_storage.py — Unit Tests for Secure API Key Storage & Redaction.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from memory import config_manager
from core.permissions import log_action_audit, get_audit_trail


class TestSecureKeyStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_cfg_dir = Path(self.temp_dir.name)
        self.test_cfg_file = self.test_cfg_dir / "api_keys.json"

        self.orig_dir = config_manager.CONFIG_DIR
        self.orig_file = config_manager.CONFIG_FILE
        config_manager.CONFIG_DIR = self.test_cfg_dir
        config_manager.CONFIG_FILE = self.test_cfg_file

    def tearDown(self):
        config_manager.CONFIG_DIR = self.orig_dir
        config_manager.CONFIG_FILE = self.orig_file
        self.temp_dir.cleanup()

    def test_keyring_save_load_roundtrip(self):
        """Verify save/load round-trip using mock keyring backend."""
        stored_keys = {}

        mock_keyring = MagicMock()
        mock_keyring.get_password.side_effect = lambda s, u: stored_keys.get(f"{s}:{u}")
        mock_keyring.set_password.side_effect = lambda s, u, p: stored_keys.update({f"{s}:{u}": p})

        with patch.object(config_manager, "_is_keyring_available", return_value=True):
            with patch.dict("sys.modules", {"keyring": mock_keyring}):
                test_key = "AIzaSyTestKeyRoundTrip1234567890ABC"
                config_manager.save_api_keys(test_key)

                # Keyring received key
                self.assertEqual(stored_keys.get(f"{config_manager._KEYRING_SERVICE}:{config_manager._KEYRING_USER}"), test_key)

                # JSON file does NOT contain plaintext key
                if self.test_cfg_file.exists():
                    raw = json.loads(self.test_cfg_file.read_text(encoding="utf-8"))
                    self.assertNotIn("gemini_api_key", raw)

                # get_gemini_key retrieves key from keyring
                loaded = config_manager.get_gemini_key()
                self.assertEqual(loaded, test_key)

    def test_auto_migration_from_json_to_keyring(self):
        """Verify existing plaintext key in JSON is migrated to keyring and stripped from file."""
        stored_keys = {}
        mock_keyring = MagicMock()
        mock_keyring.get_password.side_effect = lambda s, u: stored_keys.get(f"{s}:{u}")
        mock_keyring.set_password.side_effect = lambda s, u, p: stored_keys.update({f"{s}:{u}": p})

        # Seed JSON with plaintext key
        self.test_cfg_file.write_text(json.dumps({
            "gemini_api_key": "AIzaSyPreExistingKeyToMigrate999",
            "assistant_name": "JARVIS"
        }), encoding="utf-8")

        with patch.object(config_manager, "_is_keyring_available", return_value=True):
            with patch.dict("sys.modules", {"keyring": mock_keyring}):
                key = config_manager.get_gemini_key()
                self.assertEqual(key, "AIzaSyPreExistingKeyToMigrate999")

                # Migrated to keyring
                self.assertEqual(stored_keys.get(f"{config_manager._KEYRING_SERVICE}:{config_manager._KEYRING_USER}"), "AIzaSyPreExistingKeyToMigrate999")

                # Removed from json
                raw = json.loads(self.test_cfg_file.read_text(encoding="utf-8"))
                self.assertNotIn("gemini_api_key", raw)
                self.assertEqual(raw.get("assistant_name"), "JARVIS")

    def test_keyring_unavailable_fallback(self):
        """Verify graceful fallback with restricted permissions when keyring is unavailable."""
        with patch.object(config_manager, "_is_keyring_available", return_value=False):
            fallback_key = "AIzaSyFallbackKeyStoredInFile888"
            config_manager.save_api_keys(fallback_key)

            # File created with key
            self.assertTrue(self.test_cfg_file.exists())
            raw = json.loads(self.test_cfg_file.read_text(encoding="utf-8"))
            self.assertEqual(raw.get("gemini_api_key"), fallback_key)

            # Retrieval works
            loaded = config_manager.get_gemini_key()
            self.assertEqual(loaded, fallback_key)

    def test_redact_secret_everywhere(self):
        """Verify redact_secret masks keys and secrets from logs and audit trails."""
        secret = "AIzaSyVerySecretKey1234567890ABCDEF"
        with patch.object(config_manager, "get_gemini_key", return_value=secret):
            msg = f"Connecting with key {secret} and token sk-12345678901234567890"
            redacted = config_manager.redact_secret(msg)
            self.assertNotIn(secret, redacted)
            self.assertIn("[REDACTED_API_KEY]", redacted)
            self.assertIn("[REDACTED_SECRET]", redacted)

            # Check audit log redaction
            audit_entry = log_action_audit(
                action="test_action",
                parameters={"key": secret, "header": "Authorization: " + secret},
                permission_level="sensitive",
                status="done",
                verified=True,
            )
            self.assertNotIn(secret, audit_entry["parameters"])
            self.assertIn("[REDACTED_API_KEY]", audit_entry["parameters"])


if __name__ == "__main__":
    unittest.main()

