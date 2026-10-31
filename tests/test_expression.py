# -*- coding: utf-8 -*-
"""输出模式（语音 / 文字）的测试。

规则是「心情越外放越愿意开口」：
  soft/shy 高概率语音，angry/tired 高概率文字。
这里把概率单调性、强制模式、随机分布都钉住。
"""

from __future__ import annotations

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.state import expression  # noqa: E402


class TestVoiceProbability(unittest.TestCase):
    def test_soft_and_shy_prefer_voice(self):
        for mood in ("soft", "shy"):
            p = expression.voice_probability({"mood": mood, "intensity": 0.0}, 0)
            self.assertGreater(p, 0.5, mood)

    def test_angry_and_tired_prefer_text(self):
        for mood in ("angry", "tired"):
            p = expression.voice_probability({"mood": mood, "intensity": 0.0}, 0)
            self.assertLess(p, 0.4, mood)

    def test_calm_is_in_the_middle(self):
        # 好感度 -100 时不加「亲近加成」，应当正好等于基础概率
        p = expression.voice_probability({"mood": "calm", "intensity": 0.0}, -100)
        self.assertAlmostEqual(p, expression.VOICE_PROBABILITY["calm"], places=3)

    def test_intensity_pushes_away_from_middle(self):
        low = expression.voice_probability({"mood": "soft", "intensity": 0.0}, 0)
        high = expression.voice_probability({"mood": "soft", "intensity": 1.0}, 0)
        self.assertGreater(high, low)

    def test_affinity_raises_probability(self):
        low = expression.voice_probability({"mood": "calm"}, -100)
        high = expression.voice_probability({"mood": "calm"}, 100)
        self.assertGreater(high, low)

    def test_probability_always_in_range(self):
        for mood in ("calm", "soft", "shy", "angry", "tired", "tsundere", "unknown"):
            for aff in (-100, 0, 100):
                p = expression.voice_probability({"mood": mood, "intensity": 1.0}, aff)
                self.assertGreaterEqual(p, 0.0)
                self.assertLessEqual(p, 1.0)

    def test_handles_none_and_garbage(self):
        self.assertIsInstance(expression.voice_probability(None, 0), float)
        self.assertIsInstance(expression.voice_probability({"mood": None}, "abc"), float)
        self.assertIsInstance(expression.voice_probability({"mood": "calm", "intensity": "x"}, 0), float)


class TestChooseMode(unittest.TestCase):
    def test_force_voice(self):
        d = expression.choose_mode({"mood": "angry"}, 0, force="voice")
        self.assertEqual(d["mode"], "voice")

    def test_force_text(self):
        d = expression.choose_mode({"mood": "shy"}, 100, force="text")
        self.assertEqual(d["mode"], "text")

    def test_random_is_reproducible_with_seed(self):
        a = expression.choose_mode({"mood": "calm"}, 0, rng=random.Random(7))
        b = expression.choose_mode({"mood": "calm"}, 0, rng=random.Random(7))
        self.assertEqual(a["mode"], b["mode"])

    def test_distribution_follows_mood(self):
        # soft 应该比 angry 更常选语音
        def voices(mood, trials=400):
            rng = random.Random(1234)
            n = 0
            for _ in range(trials):
                if expression.choose_mode({"mood": mood, "intensity": 0.8}, 50, rng=rng)["mode"] == "voice":
                    n += 1
            return n

        self.assertGreater(voices("soft"), voices("angry"))

    def test_reason_is_human_readable(self):
        d = expression.choose_mode({"mood": "calm"}, 0, rng=random.Random(1))
        self.assertIn("心情", d["reason"])
        self.assertIn("概率", d["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
