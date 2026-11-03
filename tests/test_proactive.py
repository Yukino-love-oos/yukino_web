# -*- coding: utf-8 -*-
"""主动关心模块测试：链式 timer + 临时回访。

这块最容易出错的地方：
  · 深夜主动发消息把人吵醒
  · 他已经说「开完会了」还追着问 —— 显得不走心
  · 定时器变成「机械发固定文案」，而不是每次重新判断
  · QQ 主动消息配额打爆（官方每用户每月约 4 条）
"""

from __future__ import annotations

import datetime as dt
import os
import random
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import proactive  # noqa: E402
from app.state import db  # noqa: E402


# ---------------------------------------------------------------- 桩
class FakeSession:
    def __init__(self, conn):
        self.conn = conn
        self.affinity = 100
        self._turns: list[dict] = []

    def recent_turns(self, n=10):
        return self._turns[-n:]


class FakeLLM:
    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls: list[list[dict]] = []

    def complete(self, msgs):
        self.calls.append(msgs)
        return self.replies.pop(0) if self.replies else proactive.NO_REPLY


class FakeSender:
    def __init__(self, *, ok=True, channel="fake"):
        self.ok = ok
        self.channel = channel
        self.sent: list[str] = []

    def send(self, text, timer=None):
        if not self.ok:
            return {"delivered": False, "reason": "quota"}
        self.sent.append(text)
        return {"delivered": True, "channel": self.channel}


class FakePipeline:
    def __init__(self, conn, llm=None):
        self.session = FakeSession(conn)
        self.llm = llm or FakeLLM()


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)
    return conn


NOW = dt.datetime(2026, 10, 31, 21, 35)


# ---------------------------------------------------------------- 时间段
class TestWindows(unittest.TestCase):
    def test_night_boundaries(self):
        self.assertTrue(proactive.is_night(dt.datetime(2026, 10, 31, 1, 0)))
        self.assertTrue(proactive.is_night(dt.datetime(2026, 10, 31, 7, 59)))
        self.assertFalse(proactive.is_night(dt.datetime(2026, 10, 31, 8, 0)))
        self.assertFalse(proactive.is_night(dt.datetime(2026, 10, 31, 23, 59)))
        self.assertFalse(proactive.is_night(dt.datetime(2026, 10, 31, 0, 30)))

    def test_day_delay_in_range(self):
        rng = random.Random(1)
        for _ in range(50):
            d = proactive.pick_chain_delay(NOW, rng)
            self.assertGreaterEqual(d, 10 * 60)
            self.assertLessEqual(d, 60 * 60)

    def test_night_delay_in_range(self):
        rng = random.Random(2)
        night = NOW.replace(hour=3)
        for _ in range(50):
            d = proactive.pick_chain_delay(night, rng)
            self.assertGreaterEqual(d, 2 * 3600)
            self.assertLessEqual(d, 7 * 3600)

    def test_night_trigger_shifts_to_morning(self):
        # 03:10 触发的 timer 不该在凌晨发，要顺延到 08:00 之后
        out = proactive.shift_out_of_night(dt.datetime(2026, 10, 31, 3, 10))
        self.assertGreaterEqual(out.hour, 8)
        self.assertFalse(proactive.is_night(out))

    def test_daytime_not_shifted(self):
        t = dt.datetime(2026, 10, 31, 21, 35)
        self.assertEqual(proactive.shift_out_of_night(t), t)

    def test_describe_now_has_weekday_and_clock(self):
        text = proactive.describe_now(NOW)
        self.assertIn("21:35", text)
        self.assertIn("周六", text)      # 2026-10-31 是周六


# ---------------------------------------------------------------- 回访识别
class TestRecallDetect(unittest.TestCase):
    def test_tired(self):
        hit = proactive.detect_recall("我今天好累啊")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["delay_seconds"], 15 * 60)

    def test_meeting(self):
        hit = proactive.detect_recall("等下去开会")
        self.assertEqual(hit["delay_seconds"], 60 * 60)

    def test_eating(self):
        hit = proactive.detect_recall("先去吃饭了")
        self.assertEqual(hit["delay_seconds"], 40 * 60)

    def test_short_wait(self):
        hit = proactive.detect_recall("等下试试")
        self.assertEqual(hit["delay_seconds"], 5 * 60)

    def test_normal_sentence_no_recall(self):
        self.assertIsNone(proactive.detect_recall("今天天气不错"))
        self.assertIsNone(proactive.detect_recall(""))

    def test_resolved_keywords(self):
        for text in ("我开完会了", "吃完了", "到家了", "睡醒了", "好多了"):
            self.assertTrue(proactive.looks_resolved(text), text)
        self.assertFalse(proactive.looks_resolved("今天天气不错"))


# ---------------------------------------------------------------- 决策解析
class TestParseDecision(unittest.TestCase):
    def test_no_reply(self):
        for raw in ("NO_REPLY", "no_reply", "  NO_REPLY\n", "```\nNO_REPLY\n```"):
            self.assertFalse(proactive.parse_decision(raw)["send"], raw)

    def test_plain_sentence(self):
        out = proactive.parse_decision("都快十一点了，你还不睡？")
        self.assertTrue(out["send"])
        self.assertIn("还不睡", out["text"])

    def test_prefers_chinese_tag(self):
        out = proactive.parse_decision("<ja>まだ起きてるの</ja><zh>你还没睡？</zh>")
        self.assertEqual(out["text"], "你还没睡？")

    def test_quotes_stripped(self):
        out = proactive.parse_decision("「该起来了吧」")
        self.assertEqual(out["text"], "该起来了吧")

    def test_empty(self):
        self.assertFalse(proactive.parse_decision("")["send"])
        self.assertFalse(proactive.parse_decision(None)["send"])

    def test_long_output_truncated_to_first_line(self):
        out = proactive.parse_decision("你还没睡？\n第二行不该出现")
        self.assertEqual(out["text"], "你还没睡？")


# ---------------------------------------------------------------- 存储
class TestTimerStore(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.store = proactive.TimerStore(self.conn)

    def test_add_and_pending(self):
        self.store.add("recall", NOW + dt.timedelta(minutes=15), note="问问他")
        rows = self.store.pending()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "recall")
        self.assertEqual(rows[0]["note"], "问问他")

    def test_due_only_returns_ripe(self):
        self.store.add("recall", NOW - dt.timedelta(minutes=1))
        self.store.add("recall", NOW + dt.timedelta(hours=1))
        self.assertEqual(len(self.store.due(NOW)), 1)

    def test_mark_done_removes_from_pending(self):
        tid = self.store.add("recall", NOW - dt.timedelta(minutes=1))
        self.store.mark(tid, "sent", result="ok", now=NOW)
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(len(self.store.due(NOW)), 0)

    def test_cancel_recalls(self):
        self.store.add("recall", NOW + dt.timedelta(minutes=5), note="他吃饭")
        self.store.add("recall", NOW + dt.timedelta(minutes=5), note="他开会")
        self.store.add("chain", NOW + dt.timedelta(minutes=20))
        self.assertEqual(self.store.cancel_recalls(), 2)
        left = self.store.pending()
        self.assertEqual(len(left), 1)
        self.assertEqual(left[0]["kind"], "chain")

    def test_ensure_chain_is_unique(self):
        rng = random.Random(3)
        self.assertEqual(self.store.ensure_chain(NOW, rng), "created")
        self.assertEqual(self.store.ensure_chain(NOW, rng), "exists")
        self.assertEqual(len(self.store.pending("chain")), 1)


# ---------------------------------------------------------------- 引擎
class TestProactiveEngine(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.sender = FakeSender()
        self.llm = FakeLLM(["你还没睡？"])
        self.pipe = FakePipeline(self.conn, self.llm)
        self.rng = random.Random(11)
        self.p = proactive.Proactive(
            self.pipe, sender=self.sender, now_fn=lambda: NOW, rng=self.rng
        )

    def _recall_timers(self):
        return [t for t in self.p.store.pending("recall")]

    def test_turn_end_creates_recall_and_chain(self):
        out = self.p.on_turn_end("我今天好累啊")
        self.assertIsNotNone(out["recall"])
        self.assertEqual(out["chain"], "created")
        recalls = self._recall_timers()
        self.assertEqual(len(recalls), 1)
        # 延迟应当接近 15 分钟（±20% 抖动）
        due = dt.datetime.fromisoformat(recalls[0]["due_ts"])
        delta = (due - NOW).total_seconds()
        self.assertGreaterEqual(delta, 15 * 60 * 0.8 - 5)
        self.assertLessEqual(delta, 15 * 60 * 1.2 + 5)

    def test_turn_end_without_trigger_only_chain(self):
        out = self.p.on_turn_end("今天天气不错")
        self.assertIsNone(out["recall"])
        self.assertEqual(out["chain"], "created")
        self.assertEqual(self._recall_timers(), [])

    def test_resolved_cancels_pending_recall(self):
        self.p.on_turn_end("先去吃饭了")
        self.assertEqual(len(self._recall_timers()), 1)
        out = self.p.on_turn_end("吃完了，很好吃")
        self.assertGreaterEqual(out["cancelled"], 1)
        self.assertEqual(self._recall_timers(), [])

    def test_disabled_does_nothing(self):
        p = proactive.Proactive(self.pipe, sender=self.sender,
                                now_fn=lambda: NOW, enabled=False)
        out = p.on_turn_end("我好累")
        self.assertIsNone(out["recall"])
        self.assertEqual(p.tick(), [])

    def test_tick_sends_when_llm_says_yes(self):
        self.p.store.add("recall", NOW - dt.timedelta(minutes=1), note="他刚才说累了")
        out = self.p.tick()
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["send"])
        self.assertEqual(self.sender.sent, ["你还没睡？"])
        # 触发过的 timer 不应再 pending
        self.assertEqual(self.p.store.due(NOW), [])

    def test_tick_skips_when_no_reply(self):
        self.llm.replies = ["NO_REPLY"]
        self.p.store.add("recall", NOW - dt.timedelta(minutes=1), note="随便看看")
        out = self.p.tick()
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0]["send"])
        self.assertEqual(self.sender.sent, [])
        rows = self.conn.execute("SELECT status, result FROM timers").fetchall()
        self.assertEqual(rows[0]["status"], "skipped")

    def test_tick_rebuilds_chain(self):
        self.p.on_turn_end("嗯")               # 建了 chain
        chain = self.p.store.pending("chain")[0]
        # 把 chain 改到过去，模拟到点
        self.conn.execute("UPDATE timers SET due_ts=? WHERE id=?",
                          (proactive._iso(NOW - dt.timedelta(minutes=1)), chain["id"]))
        self.conn.commit()
        self.p.tick()
        # 触发后应该又建了一个新的 chain
        self.assertEqual(len(self.p.store.pending("chain")), 1)
        self.assertNotEqual(self.p.store.pending("chain")[0]["id"], chain["id"])

    def test_failed_delivery_buffers_to_outbox(self):
        self.sender.ok = False
        self.p.store.add("recall", NOW - dt.timedelta(minutes=1), note="他吃饭")
        self.p.tick()
        pending = proactive.read_pending_topics(self.conn)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["text"], "你还没睡？")

    def test_decision_prompt_has_time_and_history(self):
        self.pipe.session._turns = [
            {"user_text": "我去吃饭了", "zh": "去吧，别吃太快"},
        ]
        self.p.store.add("recall", NOW - dt.timedelta(minutes=1), note="问他吃了什么")
        self.p.tick()
        sent_msgs = self.llm.calls[0]
        joined = "\n".join(m["content"] for m in sent_msgs)
        self.assertIn("21:35", joined)
        self.assertIn("我去吃饭了", joined)
        self.assertIn("NO_REPLY", joined)


# ---------------------------------------------------------------- outbox
class TestOutbox(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_add_and_read(self):
        proactive.add_outbox(self.conn, "在吗")
        proactive.add_outbox(self.conn, "该睡了")
        rows = proactive.read_pending_topics(self.conn)
        self.assertEqual([r["text"] for r in rows], ["该睡了", "在吗"])  # 新的在前

    def test_consume_marks_delivered(self):
        proactive.add_outbox(self.conn, "在吗")
        rows = proactive.read_pending_topics(self.conn)
        proactive.consume_pending_topics(self.conn, [r["id"] for r in rows])
        self.assertEqual(proactive.read_pending_topics(self.conn), [])

    def test_web_messages_only_web_channel(self):
        proactive.add_outbox(self.conn, "web 的", channel="web")
        proactive.add_outbox(self.conn, "缓冲的", channel="buffered")
        rows = proactive.read_web_messages(self.conn)
        self.assertEqual([r["text"] for r in rows], ["web 的"])


class TestDedupe(unittest.TestCase):
    def test_identical_text_flagged(self):
        self.assertTrue(proactive.dedupe_against_recent("你还没睡？", ["你还没睡？"]))

    def test_different_text_ok(self):
        self.assertFalse(proactive.dedupe_against_recent("该睡了", ["吃了吗"]))

    def test_empty_safe(self):
        self.assertFalse(proactive.dedupe_against_recent("", ["x"]))
        self.assertFalse(proactive.dedupe_against_recent("x", []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
