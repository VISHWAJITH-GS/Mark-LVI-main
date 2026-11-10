"""
tests/test_audio_routing.py — Complete Unit and Real-Device Test Suite for
MARK LVI / JARVIS Audio Routing and Bluetooth Conflict Prevention.
"""

import time
import unittest
from unittest.mock import MagicMock, patch

from core import audio_devices
from core.audio_diagnostics import get_audio_diagnostics, generate_diagnostic_report


class TestAudioRoutingAndBluetooth(unittest.TestCase):
    """Test suite covering device enumeration, resolution, Bluetooth classification, and output routing."""

    def setUp(self):
        audio_devices.invalidate_cache()

    def test_directsound_output_never_selected(self):
        """Verify that DirectSound output devices are strictly rejected to prevent silent sink."""
        # Find any directsound output device if available
        import sounddevice as sd
        apis = [a.get("name", "").lower() for a in sd.query_hostapis()]
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_output_channels", 0) > 0:
                hostapi_id = dev.get("hostapi", -1)
                api_name = apis[hostapi_id] if 0 <= hostapi_id < len(apis) else ""
                if "directsound" in api_name:
                    usable = audio_devices._usable(idx, "output")
                    self.assertFalse(usable, f"DirectSound device #{idx} must NOT be marked usable for output!")

    def test_bluetooth_profile_classification(self):
        """Verify Bluetooth detection and profile warnings."""
        # OPPO Enco Buds 2 detection
        self.assertTrue(audio_devices.is_bluetooth_device("Headphones (OPPO Enco Buds2)"))
        self.assertTrue(audio_devices.is_bluetooth_device("Headset (OPPO Enco Buds2)"))
        self.assertFalse(audio_devices.is_bluetooth_device("Speakers (Realtek(R) Audio)"))
        self.assertFalse(audio_devices.is_bluetooth_device("Microphone Array (Realtek(R) Audio)"))

        # Status advisory
        bt_mic_status = audio_devices.get_device_status("Headset (OPPO Enco Buds2)", "input")
        self.assertTrue(bt_mic_status["is_bluetooth"])
        self.assertIn("HFP", bt_mic_status["profile"])
        self.assertIsNotNone(bt_mic_status["warning"])

        laptop_mic_status = audio_devices.get_device_status("Microphone Array (Realtek(R) Audio)", "input")
        self.assertFalse(laptop_mic_status["is_bluetooth"])
        self.assertIsNone(laptop_mic_status["warning"])

    def test_device_resolution_and_mme_selection_for_speakers(self):
        """Verify that output devices resolve to working MME indices and not silent sinks."""
        import sounddevice as sd
        apis = [a.get("name", "").lower() for a in sd.query_hostapis()]

        # Resolve Realtek speakers
        dev_spk = audio_devices.resolve("Speakers (Realtek(R) Audio)", "output")
        if dev_spk is not None:
            dev_info = sd.query_devices(dev_spk)
            api_name = apis[dev_info["hostapi"]]
            self.assertIn("mme", api_name, "Output device must use MME backend on Windows.")

        # Resolve OPPO earphones if present
        dev_oppo = audio_devices.resolve("Headphones (OPPO Enco Buds2)", "output")
        if dev_oppo is not None:
            dev_info = sd.query_devices(dev_oppo)
            api_name = apis[dev_info["hostapi"]]
            self.assertIn("mme", api_name, "OPPO earphones must use MME backend on Windows.")

    def test_play_tone_real_consumption(self):
        """Verify play_tone tests consumption and returns ok for active output."""
        res = audio_devices.play_tone("Speakers (Realtek(R) Audio)", seconds=0.3)
        self.assertTrue(res["ok"])
        self.assertIn("Chime played", res["verdict"])

    def test_diagnostics_report_generation(self):
        """Verify diagnostics report captures streams without raising exceptions."""
        diag = get_audio_diagnostics()
        self.assertIn("timestamp", diag)
        self.assertIn("resolved_streams", diag)
        self.assertIn("input", diag["resolved_streams"])
        self.assertIn("output", diag["resolved_streams"])

        report = generate_diagnostic_report()
        self.assertIn("# JARVIS Audio & Bluetooth Diagnostics Report", report)
        self.assertIn("## 1. Input Stream", report)
        self.assertIn("## 2. Output Stream", report)

    def test_cache_invalidation_rescans(self):
        """Verify invalidate_cache clears cached lists and chosen APIs."""
        audio_devices.list_devices("output")
        self.assertIsNotNone(audio_devices._cache)
        audio_devices.invalidate_cache()
        self.assertIsNone(audio_devices._cache)


if __name__ == "__main__":
    unittest.main()

