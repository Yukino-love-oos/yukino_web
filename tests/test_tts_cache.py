"""TTS 层单元测试：缓存键、读写、流式 WAV 头修补。不依赖真实服务器。"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.tts.cache import AudioCache, audio_report, fix_wav_header  # noqa: E402


def make_wav(n_frames: int = 3200, sample_rate: int = 32000, stale_header: bool = False) -> bytes:
    """造一个 44 字节头 + 静音数据的 WAV。"""
    payload = b"\x00\x00" * n_frames
    data_size = 0 if stale_header else len(payload)
    header = b"RIFF" + struct.pack("<I", 36 + len(payload)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", data_size)
    return header + payload


class TestCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = AudioCache(self.tmp.name, enabled=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_key_is_deterministic(self):
        p = {"top_k": 5, "speed_factor": 1.0}
        self.assertEqual(self.cache.key("そう。", p), self.cache.key("そう。", p))

    def test_key_changes_with_text(self):
        self.assertNotEqual(self.cache.key("そう。"), self.cache.key("違う。"))

    def test_key_changes_with_params(self):
        a = self.cache.key("そう。", {"speed_factor": 1.0})
        b = self.cache.key("そう。", {"speed_factor": 1.2})
        self.assertNotEqual(a, b)

    def test_key_ignores_param_order(self):
        a = self.cache.key("そう。", {"a": 1, "b": 2})
        b = self.cache.key("そう。", {"b": 2, "a": 1})
        self.assertEqual(a, b)

    def test_put_get_roundtrip(self):
        data = make_wav()
        self.assertIsNone(self.cache.get("そう。"))
        self.cache.put("そう。", {}, data)
        self.assertEqual(self.cache.get("そう。"), data)

    def test_disabled_cache_writes_nothing(self):
        off = AudioCache(Path(self.tmp.name) / "off", enabled=False)
        off.put("そう。", {}, make_wav())
        self.assertIsNone(off.get("そう。"))
        self.assertFalse((Path(self.tmp.name) / "off").exists())

    def test_stats(self):
        self.cache.put("そう。", {}, make_wav())
        st = self.cache.stats()
        self.assertTrue(st["enabled"])
        self.assertEqual(st["files"], 1)

    def test_clear(self):
        self.cache.put("a", {}, make_wav())
        self.cache.put("b", {}, make_wav())
        self.assertEqual(self.cache.clear(), 2)
        self.assertIsNone(self.cache.get("a"))


class TestFixWavHeader(unittest.TestCase):
    def test_repairs_zeroed_lengths(self):
        """回归：流式拼接的 WAV 头长度字段是 0，播放器会当成空音频。"""
        broken = make_wav(n_frames=1600, stale_header=True)
        self.assertEqual(audio_report(broken)["frames"], 0)

        fixed = fix_wav_header(broken)
        rep = audio_report(fixed)
        self.assertEqual(rep["format"], "wav")
        self.assertEqual(rep["frames"], 1600)
        self.assertAlmostEqual(rep["duration_s"], 0.05, places=2)
        self.assertEqual(rep["sample_rate"], 32000)

    def test_riff_size_field_correct(self):
        broken = make_wav(n_frames=1000, stale_header=True)
        fixed = fix_wav_header(broken)
        riff_size = int.from_bytes(fixed[4:8], "little")
        self.assertEqual(riff_size, len(fixed) - 8)

    def test_idempotent(self):
        good = make_wav(n_frames=500)
        self.assertEqual(fix_wav_header(good), fix_wav_header(fix_wav_header(good)))

    def test_non_wav_passthrough(self):
        ogg = b"OggS" + b"\x00" * 100
        self.assertEqual(fix_wav_header(ogg), ogg)
        self.assertEqual(fix_wav_header(b""), b"")

    def test_short_input_passthrough(self):
        self.assertEqual(fix_wav_header(b"RIFF"), b"RIFF")


class TestAudioReport(unittest.TestCase):
    def test_detects_silence(self):
        rep = audio_report(make_wav(n_frames=1000))
        self.assertTrue(rep["silent"])

    def test_reports_ogg(self):
        rep = audio_report(b"OggS" + b"\x00" * 50)
        self.assertEqual(rep["format"], "ogg")

    def test_handles_garbage(self):
        self.assertEqual(audio_report(b"")["format"], "unknown")
        self.assertEqual(audio_report(b"\x00\x01\x02")["format"], "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
