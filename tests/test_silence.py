# -*- coding: utf-8 -*-
"""停顿压缩的单元测试。

这个后处理直接决定 QQ 语音听感（不压的话 50% 是静音，听起来一顿一顿），
所以要有回归测试保证它不改坏音频、不吞音、不留空洞。
"""

from __future__ import annotations

import io
import os
import struct
import sys
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tts.silence import pause_stats, squeeze_pauses  # noqa: E402

RATE = 24000


def make_wav(segments) -> bytes:
    """segments: [(ms, amplitude), ...] amplitude=0 表示静音。"""
    frames = bytearray()
    for ms, amp in segments:
        n = int(RATE * ms / 1000)
        for i in range(n):
            # 用正弦让"有声段"更像真实信号（RMS 明显非零）
            v = int(amp * 12000 * (1 if i % 2 else -1))
            frames += struct.pack("<h", max(-32768, min(32767, v)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    return buf.getvalue()


class TestSqueezePauses(unittest.TestCase):
    def test_long_gap_is_shortened(self):
        # 300ms 有声 + 800ms 静音 + 300ms 有声
        wav = make_wav([(300, 1.0), (800, 0), (300, 1.0)])
        before = pause_stats(wav)
        out = squeeze_pauses(wav)
        after = pause_stats(out)
        self.assertLess(after["duration"], before["duration"])
        self.assertGreater(after["duration"], 0.5)  # 别把有声段也切了
        self.assertEqual(after["long_gaps"], 0)

    def test_short_gap_untouched(self):
        wav = make_wav([(300, 1.0), (80, 0), (300, 1.0)])
        before = pause_stats(wav)
        out = squeeze_pauses(wav)
        after = pause_stats(out)
        # 80ms 属于自然停顿，应该基本不动
        self.assertAlmostEqual(after["duration"], before["duration"], delta=0.06)

    def test_trailing_silence_trimmed(self):
        wav = make_wav([(300, 1.0), (1200, 0)])
        before = pause_stats(wav)
        out = squeeze_pauses(wav)
        after = pause_stats(out)
        self.assertLess(after["duration"], before["duration"] - 0.5)

    def test_no_silence_returns_equivalent_audio(self):
        wav = make_wav([(600, 1.0)])
        out = squeeze_pauses(wav)
        before = pause_stats(wav)
        after = pause_stats(out)
        self.assertAlmostEqual(after["duration"], before["duration"], delta=0.05)

    def test_empty_and_garbage_are_safe(self):
        self.assertEqual(squeeze_pauses(b""), b"")
        garbage = b"not a wave file"
        self.assertEqual(squeeze_pauses(garbage), garbage)

    def test_output_is_valid_wav_mono_16bit(self):
        wav = make_wav([(200, 1.0), (600, 0), (200, 1.0)])
        out = squeeze_pauses(wav)
        with wave.open(io.BytesIO(out), "rb") as w:
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getsampwidth(), 2)
            self.assertEqual(w.getframerate(), RATE)
            self.assertGreater(w.getnframes(), 0)

    def test_stereo_input_is_handled(self):
        # 双声道 + 长静音：压缩后应输出单声道
        frames = bytearray()
        for _ in range(RATE // 2):
            frames += struct.pack("<hh", 6000, 6000)
        for _ in range(RATE):  # 1 秒静音
            frames += struct.pack("<hh", 0, 0)
        for _ in range(RATE // 2):
            frames += struct.pack("<hh", 6000, 6000)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(bytes(frames))
        out = squeeze_pauses(buf.getvalue())
        with wave.open(io.BytesIO(out), "rb") as w:
            self.assertEqual(w.getnchannels(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
