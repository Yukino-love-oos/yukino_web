# -*- coding: utf-8 -*-
"""多层记忆 + 艾宾浩斯曲线 + 用户档案 + 好感度锁定的测试。

这几块的共同点是「看不见但影响很大」：
  · 遗忘曲线算错 → 要么全忘、要么永远不忘
  · 复习升格算错 → 长期/短期不分，prompt 分层失去意义
  · 档案去重算错 → 同一句「喜欢饺子」重复几十遍把 prompt 撑爆
  · 好感度锁定失效 → 用户说「对我满好感度」却被一句玩笑扣分
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.state import ebbinghaus, memory, userprofile  # noqa: E402


# ---------------------------------------------------------------- 艾宾浩斯
class TestEbbinghaus(unittest.TestCase):
    def test_retention_at_zero_is_one(self):
        self.assertAlmostEqual(ebbinghaus.retention(0, 90), 1.0, places=3)

    def test_retention_at_one_stability_is_inv_e(self):
        self.assertAlmostEqual(ebbinghaus.retention(90, 90), 0.3679, places=3)

    def test_longer_stability_decays_slower(self):
        self.assertGreater(ebbinghaus.retention(30, 60), ebbinghaus.retention(30, 20))

    def test_initial_stability_by_kind(self):
        self.assertGreater(ebbinghaus.initial_stability("fact"),
                           ebbinghaus.initial_stability("event"))
        self.assertEqual(ebbinghaus.initial_stability("unknown"), ebbinghaus.DEFAULT_STABILITY)

    def test_review_boosts_stability(self):
        out = ebbinghaus.after_review(20.0, 0)
        self.assertAlmostEqual(out["stability"], 32.0, places=2)
        self.assertEqual(out["review_count"], 1)

    def test_third_review_promotes_to_long(self):
        self.assertEqual(ebbinghaus.after_review(20.0, 0)["layer"], "short")
        self.assertEqual(ebbinghaus.after_review(32.0, 1)["layer"], "short")
        third = ebbinghaus.after_review(51.2, 2)
        self.assertEqual(third["layer"], "long")
        self.assertGreaterEqual(third["stability"], 60.0)

    def test_stability_is_capped(self):
        out = ebbinghaus.after_review(300.0, 9)
        self.assertLessEqual(out["stability"], ebbinghaus.MAX_STABILITY)

    def test_dormant_when_retention_low(self):
        # 30 天没想起、稳定度只有 7 天 → R≈0.014，应判为淡忘
        self.assertTrue(ebbinghaus.is_dormant(30, 7))
        self.assertFalse(ebbinghaus.is_dormant(0, 7))

    def test_rotation_increases_over_time(self):
        s = 20.0
        seen = []
        for n in range(4):
            seen.append(round(ebbinghaus.retention(10, s), 3))
            s = ebbinghaus.after_review(s, n)["stability"]
        # 同样间隔 10 天，随着复习次数增加，保持率应该越来越高
        self.assertLess(seen[0], seen[-1])


# ---------------------------------------------------------------- 记忆层
def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE memory (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT NOT NULL,
          kind TEXT NOT NULL DEFAULT 'fact',
          text TEXT NOT NULL
        );
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
        """
    )
    from app.state import db as dbtools

    dbtools.ensure_memory_columns(conn)
    return conn


class TestMemoryLayers(unittest.TestCase):
    def test_add_sets_initial_layer_and_stability(self):
        conn = _conn()
        memory.add(conn, "fact", "猫 用户很喜欢猫")
        row = conn.execute("SELECT layer, stability, review_count FROM memory").fetchone()
        self.assertEqual(row["layer"], "short")
        self.assertAlmostEqual(row["stability"], ebbinghaus.initial_stability("fact"), places=1)
        self.assertEqual(row["review_count"], 0)

    def test_recall_reinforces(self):
        conn = _conn()
        memory.add(conn, "event", "猫 上周聊到猫")
        memory.recall(conn, "猫", k=1)
        row = conn.execute("SELECT review_count, stability FROM memory").fetchone()
        self.assertEqual(row["review_count"], 1)
        self.assertGreater(row["stability"], ebbinghaus.initial_stability("event"))

    def test_three_recalls_promote_to_long(self):
        conn = _conn()
        memory.add(conn, "fact", "猫 用户很喜欢猫")
        for _ in range(3):
            memory.recall(conn, "猫", k=1)
        row = conn.execute("SELECT layer, review_count FROM memory").fetchone()
        self.assertEqual(row["layer"], "long")
        self.assertGreaterEqual(row["review_count"], 3)

    def test_dormant_memory_not_returned(self):
        conn = _conn()
        old_ts = (dt.datetime.now() - dt.timedelta(days=200)).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO memory(ts, kind, text, layer, stability, review_count) VALUES(?,?,?,?,?,?)",
            (old_ts, "event", "猫 很久以前的猫", "short", 5.0, 0),
        )
        conn.commit()
        self.assertEqual(memory.recall(conn, "猫", k=3), [])

    def test_fresh_memory_returned(self):
        conn = _conn()
        memory.add(conn, "fact", "猫 用户很喜欢猫")
        hits = memory.recall(conn, "猫", k=3)
        self.assertEqual(len(hits), 1)
        self.assertGreater(hits[0]["_retention"], 0.9)

    def test_block_groups_by_layer(self):
        mems = [
            {"text": "喜欢猫", "layer": "long"},
            {"text": "上周聊到猫", "layer": "short"},
        ]
        block = memory.build_memory_block(mems)
        self.assertIn("长期记住的事", block)
        self.assertIn("近期的事", block)
        self.assertLess(block.index("长期记住的事"), block.index("近期的事"))

    def test_block_respects_budget(self):
        mems = [{"text": "x" * 100, "layer": "short"} for _ in range(20)]
        block = memory.build_memory_block(mems, max_chars=250)
        self.assertLessEqual(len(block), 300)


# ---------------------------------------------------------------- 用户档案
class TestUserProfile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="yukino-profile-"))
        self.path = self.tmp / "user_profile.json"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_missing_returns_empty(self):
        self.assertEqual(userprofile.load(self.path), {})

    def test_render_contains_fields(self):
        profile = {
            "name": "马佳豪",
            "age": 20,
            "school": "贵州大学",
            "grade": "大三",
            "likes": ["吃饺子"],
            "notes": ["是雪乃最在意的人"],
            "facts": ["最近在学日语"],
        }
        block = userprofile.render_block(profile)
        for token in ("马佳豪", "20", "贵州大学", "吃饺子", "最在意", "学日语"):
            self.assertIn(token, block)

    def test_append_dedupes(self):
        self.assertEqual(userprofile.append_facts(self.path, ["喜欢饺子"]), ["喜欢饺子"])
        self.assertEqual(userprofile.append_facts(self.path, ["喜欢饺子"]), [])
        self.assertEqual(len(userprofile.load(self.path)["facts"]), 1)

    def test_append_caps_length(self):
        userprofile.append_facts(self.path, ["事实%d" % i for i in range(50)], max_facts=10)
        facts = userprofile.load(self.path)["facts"]
        self.assertEqual(len(facts), 10)
        self.assertIn("事实49", facts)      # 保留最新的
        self.assertNotIn("事实0", facts)    # 丢掉最早的

    def test_render_respects_budget(self):
        block = userprofile.render_block({"facts": ["y" * 200] * 20}, max_chars=300)
        self.assertLessEqual(len(block), 340)


# ---------------------------------------------------------------- 好感度锁定
class TestAffinityLock(unittest.TestCase):
    class StubPersona:
        affinity = {"range": [-100, 100]}

    def _session(self, lock):
        from app.state.session import Session

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        return Session(conn, self.StubPersona(), affinity_lock=lock)

    def test_lock_returns_fixed_value(self):
        s = self._session(100)
        self.assertEqual(s.affinity, 100)
        s.affinity = 30                      # 被锁定时写入无效
        self.assertEqual(s.affinity, 100)

    def test_no_lock_behaves_normally(self):
        s = self._session(None)
        s.affinity = 42
        self.assertEqual(s.affinity, 42)

    def test_lock_zero_is_respected(self):
        # 0 是合法锁定值，不能被当成「未设置」
        self.assertEqual(self._session(0).affinity, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
