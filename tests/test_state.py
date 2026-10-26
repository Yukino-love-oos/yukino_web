"""状态层单元测试：好感度持久化、防刷分、每日上限、记忆召回。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.llm.persona import load_persona  # noqa: E402
from app.llm.protocol import Turn  # noqa: E402
from app.state import db, memory  # noqa: E402
from app.state.session import Session, normalize  # noqa: E402


class StateTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = db.connect(self.db_path)
        db.init_schema(self.conn)
        self.persona = load_persona(ROOT / "persona" / "yukino.json")
        self.session = Session(self.conn, self.persona)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()


class TestAffinityRules(StateTestBase):
    def test_starts_at_zero(self):
        self.assertEqual(self.session.affinity, 0)
        self.assertEqual(self.session.total_turns, 0)

    def test_per_turn_cap(self):
        """单轮幅度不能超过 persona 里配置的上限（默认 3）。"""
        delta, reason = self.session.apply_affinity(99, "你好")
        self.assertEqual(delta, self.persona.per_turn_max)
        self.assertEqual(self.session.affinity, self.persona.per_turn_max)
        self.assertIn("幅度", reason)

    def test_negative_symmetric(self):
        delta, _ = self.session.apply_affinity(-99, "你好")
        self.assertEqual(delta, -self.persona.per_turn_max)

    def test_persists_across_sessions(self):
        """关键回归：原实现是模块级全局变量，重启就归零。"""
        self.session.apply_affinity(3, "a")
        self.assertEqual(self.session.affinity, 3)

        same = Session(self.conn, self.persona)
        self.assertEqual(same.affinity, 3)

        # 真的重开数据库连接也不行丢
        self.conn.close()
        self.conn = db.connect(self.db_path)
        db.init_schema(self.conn)
        again = Session(self.conn, self.persona)
        self.assertEqual(again.affinity, 3)

    def test_daily_gain_cap(self):
        cap = self.persona.daily_gain_cap
        total = 0
        for i in range(cap):
            delta, _ = self.session.apply_affinity(3, f"不同的输入{i}")
            total += delta
        self.assertEqual(total, cap)
        delta, reason = self.session.apply_affinity(3, "又来一句")
        self.assertEqual(delta, 0)
        self.assertIn("上限", reason)

    def test_lower_bound_clamped(self):
        for i in range(50):
            self.session.apply_affinity(-3, f"无礼发言{i}")
        lo, _hi = self.persona.affinity["range"]
        self.assertEqual(self.session.affinity, lo)


class TestAntiFarming(StateTestBase):
    def test_repeat_is_penalized(self):
        """复读同一句讨好 → 代码强制扣分，不依赖模型自觉。"""
        user = "你喜欢猫对吧"
        p = self.persona.anti_farming.get("repeated_topic_penalty", -2)

        self.session.record_turn(user, Turn(ja="……そう。", zh="……是呢。"), 0)
        delta, reason = self.session.apply_affinity(3, user)

        self.assertEqual(delta, p)
        self.assertLess(self.session.affinity, 0)
        self.assertIn("重复", reason)

    def test_punctuation_variants_count_as_repeat(self):
        self.session.record_turn("你喜欢猫", Turn(ja="……"), 0)
        delta, reason = self.session.apply_affinity(3, "你喜欢猫！！")
        self.assertLessEqual(delta, 0)
        self.assertIn("重复", reason)

    def test_different_input_not_penalized(self):
        self.session.record_turn("你喜欢猫", Turn(ja="……"), 0)
        delta, reason = self.session.apply_affinity(3, "我今天考砸了")
        self.assertEqual(delta, 3)
        self.assertNotIn("重复", reason)

    def test_repeat_outside_window_is_fine(self):
        window = int(self.persona.anti_farming.get("repeated_topic_window", 5))
        self.session.record_turn("老话题", Turn(ja="……"), 0)
        for i in range(window):
            self.session.record_turn(f"填充{i}", Turn(ja="……"), 0)
        delta, _ = self.session.apply_affinity(3, "老话题")
        self.assertEqual(delta, 3)

    def test_normalize(self):
        self.assertEqual(normalize("你喜欢猫！"), normalize("你喜欢猫"))
        self.assertEqual(normalize("ＡＢＣ"), normalize("abc"))


class TestTurnRecording(StateTestBase):
    def test_record_and_history(self):
        turn = Turn(ja="それはどうかしら。", zh="那可不好说吧。", act="挑眉", mood="tsundere", aff=-2)
        self.session.apply_affinity(turn.aff, "随便你吧")
        self.session.record_turn("随便你吧", turn, -2)

        self.assertEqual(self.session.total_turns, 1)

        recent = self.session.recent_turns(5)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["ja"], "それはどうかしら。")
        self.assertEqual(recent[0]["mood"], "tsundere")

        msgs = self.session.history_messages(5)
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        # assistant 历史必须还原成标签协议，否则小模型会漏掉 <ja> 开标签
        self.assertIn("<ja>それはどうかしら。</ja>", msgs[1]["content"])
        self.assertIn("<zh>那可不好说吧。</zh>", msgs[1]["content"])
        self.assertIn("<mood>tsundere</mood>", msgs[1]["content"])

    def test_stats(self):
        self.session.apply_affinity(3, "hello")
        st = self.session.stats()
        self.assertEqual(st["affinity"], 3)
        self.assertEqual(st["band"], "平常")
        self.assertEqual(st["total_turns"], 0)


class TestBand(StateTestBase):
    def test_bands_cover_range(self):
        for value in (-100, -60, -59, -20, -19, 0, 19, 20, 59, 60, 89, 90, 100):
            band = self.persona.band(value)
            self.assertIn("label", band, f"好感度 {value} 没有对应分段")
            self.assertTrue(band["label"])


class TestMemory(StateTestBase):
    def test_add_and_recent(self):
        memory.add(self.conn, "fact", "用户是计算机专业的学生")
        memory.add(self.conn, "preference", "用户喜欢黑咖啡")
        got = memory.recent(self.conn, 10)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[-1]["text"], "用户喜欢黑咖啡")

    def test_recall_matches_related(self):
        memory.add(self.conn, "fact", "用户正在准备期末考试")
        memory.add(self.conn, "fact", "用户养了一只叫小黑的猫")
        hits = memory.recall(self.conn, "考试怎么样了", k=2)
        self.assertTrue(hits)
        self.assertIn("考试", hits[0]["text"])

    def test_recall_no_match(self):
        memory.add(self.conn, "fact", "用户喜欢黑咖啡")
        self.assertEqual(memory.recall(self.conn, "量子力学", k=3), [])

    def test_empty_text_ignored(self):
        self.assertEqual(memory.add(self.conn, "fact", "   "), 0)

    def test_build_block(self):
        block = memory.build_memory_block(
            [{"text": "用户喜欢黑咖啡"}, {"text": "用户在准备考试"}]
        )
        self.assertIn("黑咖啡", block)
        self.assertIn("考试", block)
        self.assertEqual(memory.build_memory_block([]), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
