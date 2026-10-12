"""
Проверки работы со звуком.

Проверяются только чистые вычисления: пересэмплирование и перевод
форматов. Звуковое устройство не требуется.
"""

import unittest

import numpy as np

from core.audio import (
    FRAME_BYTES,
    FRAME_SAMPLES,
    SAMPLE_RATE,
    from_pcm16,
    probe_duration,
    resample,
    to_pcm16,
)


class TestConstants(unittest.TestCase):
    def test_frame_layout(self):
        self.assertEqual(SAMPLE_RATE, 16000)
        self.assertEqual(FRAME_SAMPLES, 480)          # 30 мс
        self.assertEqual(FRAME_BYTES, 960)           # 480 отсчётов по 2 байта


class TestResample(unittest.TestCase):
    def test_same_rate_is_noop(self):
        data = np.linspace(-1, 1, 100, dtype=np.float32)
        result = resample(data, 16000)
        self.assertEqual(result.size, 100)
        self.assertEqual(result.dtype, np.float32)

    def test_downsampling_halves_length(self):
        data = np.zeros(44100, dtype=np.float32)
        self.assertEqual(resample(data, 44100, 16000).size, 16000)

    def test_upsampling_doubles_length(self):
        data = np.zeros(8000, dtype=np.float32)
        self.assertEqual(resample(data, 8000, 16000).size, 16000)

    def test_duration_preserved(self):
        """Пересэмплирование не должно менять длительность записи."""
        time = np.arange(44100, dtype=np.float32) / 44100
        wave = np.sin(2 * np.pi * 220 * time).astype(np.float32)

        result = resample(wave, 44100, 16000)
        before = wave.size / 44100
        after = result.size / 16000
        self.assertAlmostEqual(before, after, places=3)

    def test_tone_frequency_survives(self):
        """Частота сигнала не должна «уплывать» при пересэмплировании."""
        frequency = 440.0
        time = np.arange(44100, dtype=np.float32) / 44100
        wave = np.sin(2 * np.pi * frequency * time).astype(np.float32)

        result = resample(wave, 44100, 16000)

        # считаем пересечения нуля — их частота равна частоте сигнала
        sign = np.sign(result)
        crossings = np.count_nonzero(np.diff(sign) != 0)
        measured = crossings / 2 / (result.size / 16000)

        self.assertAlmostEqual(measured, frequency, delta=5.0)

    def test_empty_input(self):
        self.assertEqual(resample(np.array([], dtype=np.float32), 44100).size, 0)


class TestPcmConversion(unittest.TestCase):
    def test_roundtrip(self):
        data = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)
        restored = from_pcm16(to_pcm16(data))
        np.testing.assert_allclose(restored, data, atol=1e-4)

    def test_bytes_are_int16(self):
        raw = to_pcm16(np.array([0.0, 1.0], dtype=np.float32))
        self.assertEqual(len(raw), 4)
        self.assertEqual(raw[:2], b"\x00\x00")

    def test_clipping(self):
        """Значения вне -1..1 обрезаются, а не переполняются."""
        raw = to_pcm16(np.array([5.0, -5.0], dtype=np.float32))
        restored = from_pcm16(raw)
        self.assertTrue(-1.001 <= restored[0] <= 1.0)
        self.assertTrue(-1.0 <= restored[1] <= -0.999)

    def test_length_preserved(self):
        data = np.zeros(480, dtype=np.float32)
        self.assertEqual(len(from_pcm16(to_pcm16(data))), 480)


class TestDemoFile(unittest.TestCase):
    """Демонстрационная дорожка, если она уже создана."""

    def test_demo_wav_readable(self):
        from pathlib import Path

        from core.audio import load_audio

        path = Path(__file__).resolve().parent.parent / "assets" / "demo" / "демо.wav"
        if not path.exists():
            self.skipTest("демонстрационная дорожка не создана "
                          "(python tools/make_demo_audio.py)")

        data, rate = load_audio(path)
        self.assertEqual(rate, 16000)
        self.assertGreater(data.size, 16000)          # больше секунды
        self.assertLess(abs(float(data.max())), 1.01)

        self.assertGreater(probe_duration(path), 10.0)


if __name__ == "__main__":
    unittest.main()