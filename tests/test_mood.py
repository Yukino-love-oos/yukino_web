# -*- coding: utf-8 -*-
"""心情衰减 + 记忆召回的单元测试。

这两块都直接影响「她像不像人」：
  · 心情不衰减 → 昨天生的气今天还挂着脸
  · 记忆不分新鲜度/重要度 → 三年前的闲话和昨天的偏好同权
"""

from __future__ import annotations

import datetime as _dt
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.state import memory, mood  # noqa: E402


class TestMoodDecay(unittest.TestCase):
    NOW = 1_700_000_000.0

    def test_fresh_mood_is_full_intensity(self):
        st = mood.decay("angry", self.NOW, now=self.NOW)
        self.assertEqual(st["mood"], "angry")
        self.assertAlmostEqual(st["intensity"], 1.0, places=3)
        self.assertFalse(st["expired"])

    def test_one_half_life_halves_intensity(self):
        hl = mood.half_life("angry")  # 1.5h
        st = mood.decay("angry", self.NOW - hl * 3600, now=self.NOW)
        self.assertEqual(st["mood"], "angry")
        self.assertAlmostEqual(st["intensity"], 0.5, places=2)

    def test_long_gap_falls_back_to_calm(self):
        # 3 小时后：0.5^2 = 0.25 < 0.35，应回到 calm
        st = mood.decay("angry", self.NOW - 3 * 3600, now=self.NOW)
        self.assertEqual(st["mood"], "calm")
        self.assertTrue(st["expired"])

    def test_persistent_mood_lasts_longer_than_transient(self):
        # 同样的 2.5 小时：soft(半衰期 3h) 强度约 0.56 还在，
        # angry(半衰期 1.5h) 强度约 0.31 已低于阈值，回落到 calm
        soft = mood.decay("soft", self.NOW - 2.5 * 3600, now=self.NOW)
        angry = mood.decay("angry", self.NOW - 2.5 * 3600, now=self.NOW)
        self.assertEqual(soft["mood"], "soft")
        self.assertEqual(angry["mood"], "calm")

    def test_no_timestamp_means_decayed(self):
        st = mood.decay("shy", 0.0, now=self.NOW)
        self.assertEqual(st["mood"], "calm")

    def test_unknown_mood_is_calm(self):
        self.assertEqual(mood.decay("doge", self.NOW, now=self.NOW)["mood"], "calm")

    def test_blend_same_mood_refills(self):
        st = mood.blend({"mood": "angry", "mood_at": self.NOW - 3600}, "angry", now=self.NOW)
        self.assertEqual(st["mood"], "angry")
        self.assertAlmostEqual(st["intensity"], 1.0, places=3)

    def test_blend_different_mood_keeps_some_inertia(self):
        st = mood.blend({"mood": "angry", "mood_at": self.NOW}, "shy", now=self.NOW)
        self.assertEqual(st["mood"], "shy")
        self.assertGreaterEqual(st["intensity"], 0.6)
        self.assertLessEqual(st["intensity"], 1.0)

    def test_blend_to_calm_is_plain_calm(self):
        st = mood.blend({"mood": "angry", "mood_at": self.NOW}, "calm", now=self.NOW)
        self.assertEqual(st["mood"], "calm")

    def test_describe_is_human_readable(self):
        text = mood.describe({"mood": "angry", "intensity": 0.9})
        self.assertIsInstance(text, str)
        self.assertTrue(text)
        self.assertIn("平静", mood.describe({"mood": "calm"}))


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE memory (
          id   INTEGER PRIMARY KEY AUTOINCREMENT,
          ts   TEXT NOT NULL,
          kind TEXT NOT NULL DEFAULT 'fact',
          text TEXT NOT NULL
        )
        """
    )
    return conn


def _add(conn, ts, kind, text):
    conn.execute("INSERT INTO memory(ts, kind, text) VALUES(?,?,?)", (ts, kind, text))
    conn.commit()


class TestMemoryRecall(unittest.TestCase):
    NOW = _dt.datetime(2026, 10, 30, 20, 0, 0)

    def _ts(self, days_ago: float) -> str:
        return (self.NOW - _dt.timedelta(days=days_ago)).isoformat(timespec="seconds")

    def test_importance_breaks_ties(self):
        # 同样新鲜、同样命中：fact(9) 应该排在 mood(4) 前面
        conn = _fresh_conn()
        _add(conn, self._ts(0), "mood", "猫 今天心情不错")
        _add(conn, self._ts(0), "fact", "猫 她其实很喜欢猫")
        hits = memory.recall(conn, "猫", k=2, now=self.NOW)
        self.assertEqual(hits[0]["kind"], "fact")

    def test_freshness_breaks_ties(self):
        # 同类型、同样命中：新的排在旧的前面
        conn = _fresh_conn()
        _add(conn, self._ts(200), "event", "下雨 那天聊了下雨")
        _add(conn, self._ts(1), "event", "下雨 昨天又下雨了")
        hits = memory.recall(conn, "下雨", k=2, now=self.NOW)
        self.assertIn("昨天", hits[0]["text"])

    def test_unrelated_memory_not_recalled(self):
        conn = _fresh_conn()
        _add(conn, self._ts(0), "fact", "她喜欢猫")
        self.assertEqual(memory.recall(conn, "政治", k=3, now=self.NOW), [])

    def test_duplicates_collapse(self):
        conn = _fresh_conn()
        # 注意关键词要能独立成分词（"猫" 单独出现），否则 2-gram 不会命中
        _add(conn, self._ts(1), "fact", "猫 她喜欢猫")
        _add(conn, self._ts(0), "fact", "猫 她喜欢猫")
        hits = memory.recall(conn, "猫", k=5, now=self.NOW)
        self.assertEqual(len(hits), 1)

    def test_empty_query(self):
        conn = _fresh_conn()
        _add(conn, self._ts(0), "fact", "她喜欢猫")
        self.assertEqual(memory.recall(conn, "", k=3), [])

    def test_block_respects_char_budget(self):
        mems = [{"text": "x" * 100} for _ in range(20)]
        block = memory.build_memory_block(mems, max_chars=250)
        self.assertLessEqual(len(block), 250 + 10)
        self.assertTrue(block)

    def test_block_empty(self):
        self.assertEqual(memory.build_memory_block([]), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
