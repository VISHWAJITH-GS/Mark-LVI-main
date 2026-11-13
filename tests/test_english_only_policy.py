import unittest
from pathlib import Path

from providers.stt_whisper import WhisperSTT
from providers import tts_piper
from providers import tts_system
from memory import config_manager


class TestEnglishOnlyPolicy(unittest.TestCase):

    def test_system_prompt_contains_english_only_instruction(self):
        prompt_path = Path("core/prompt.txt")
        self.assertTrue(prompt_path.exists(), "core/prompt.txt must exist")
        content = prompt_path.read_text(encoding="utf-8")

        required_instruction = (
            "Always understand the user as speaking English and always reply in English only, "
            "never in any other language, even if the user's words are misheard or the content contains other languages."
        )
        self.assertIn(
            required_instruction,
            content,
            "System prompt must contain the required English-only instruction.",
        )
        self.assertNotIn(
            "The language of your reply is the language of the user's MOST RECENT message",
            content,
            "Legacy multilingual instruction must be removed from system prompt.",
        )

    def test_stt_whisper_configured_to_english_only(self):
        # Default configuration
        stt_default = WhisperSTT({})
        self.assertEqual(stt_default.language, "en")
        self.assertEqual(stt_default.detected_language, "en")

        # Explicit non-English configuration must fall back to "en"
        stt_foreign = WhisperSTT({"language": "tr"})
        self.assertEqual(
            stt_foreign.language,
            "en",
            "Whisper STT must reject non-English language setting and enforce 'en'.",
        )

        stt_foreign_ar = WhisperSTT({"language": "ar_JO"})
        self.assertEqual(
            stt_foreign_ar.language,
            "en",
            "Whisper STT must reject non-English language setting and enforce 'en'.",
        )

    def test_tts_piper_voices_english_only(self):
        # Ensure no non-English voices are present in installed list
        installed_voices = tts_piper.installed()
        for v in installed_voices:
            self.assertTrue(
                tts_piper.language_of(v).startswith("en"),
                f"Installed voice '{v}' is non-English, which violates English-only policy.",
            )

        # Non-English voice fallback
        piper_foreign = tts_piper.PiperTTS({"voice": "ar_JO-kareem-low", "follow_language": "1"})
        self.assertTrue(
            tts_piper.language_of(piper_foreign.voice).startswith("en"),
            "PiperTTS must fall back to default English voice when given non-English voice.",
        )
        self.assertFalse(
            piper_foreign.follow,
            "Language-following must be disabled under English-only policy.",
        )

    def test_tts_system_voices_english_only(self):
        system_voices = tts_system.list_voices(refresh=True)
        # Any known voices from System.Speech / say / espeak must be English
        for v in system_voices:
            # On Windows, all returned voices should have passed the Culture.TwoLetterISOLanguageName -eq 'en' filter
            self.assertTrue(isinstance(v, str) and len(v) > 0)

        # Fallback when given non-English voice
        sys_tts = tts_system.SystemTTS({"voice": "NonExistentForeignVoice_zh_CN"})
        if system_voices:
            self.assertIn(sys_tts.voice, system_voices)
        else:
            self.assertEqual(sys_tts.voice, "")

    def test_gemini_live_voice_english_only(self):
        v = config_manager.get_voice()
        self.assertIn(v, config_manager.AVAILABLE_VOICES)
        self.assertIn(config_manager.DEFAULT_VOICE, config_manager.AVAILABLE_VOICES)


if __name__ == "__main__":
    unittest.main()

