# -*- coding: utf-8 -*-
"""记忆写回（增量摘要）的单元测试。

写回最容易出的两个问题是：
  · 重复写同一批对话 → 记忆里全是重复条目
  · 模型输出格式跑偏 → 把解释文字当成记忆写进去
所以重点测：解析严格性、增量游标、去重。
"""

from __future__ import annotations

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.state import memory  # noqa: E402


class FakeLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0
        self.last_prompt = ""

    def complete(self, messages):
        self.calls += 1
        self.last_prompt = messages[-1]["content"] if messages else ""
        return self.reply


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE turns (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT NOT NULL,
          user_text TEXT NOT NULL DEFAULT '',
          ja TEXT NOT NULL DEFAULT '',
          zh TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE memory (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT NOT NULL,
          kind TEXT NOT NULL DEFAULT 'fact',
          text TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
    # 走一次真实迁移，保证测试表结构与生产一致（多层记忆新增了 layer/stability 等列）
    from app.state import db as dbtools

    dbtools.ensure_memory_columns(conn)
    return conn


def _add_turn(conn, user_text="你好", ja="こんにちは", zh="你好"):
    cur = conn.execute(
        "INSERT INTO turns(ts, user_text, ja, zh) VALUES('2026-10-30T20:00:00',?,?,?)",
        (user_text, ja, zh),
    )
    conn.commit()
    return int(cur.lastrowid)


class TestPendingTurns(unittest.TestCase):
    def test_cursor_starts_at_zero(self):
        conn = _conn()
        _add_turn(conn)
        self.assertEqual(memory.last_summarized_id(conn), 0)
        self.assertEqual(len(memory.pending_turns(conn, 0)), 1)

    def test_cursor_skips_old_turns(self):
        conn = _conn()
        a = _add_turn(conn, "第一句")
        b = _add_turn(conn, "第二句")
        self.assertEqual([t["id"] for t in memory.pending_turns(conn, a)], [b])

    def test_set_cursor_persists(self):
        conn = _conn()
        tid = _add_turn(conn)
        memory.set_last_summarized_id(conn, tid)
        self.assertEqual(memory.last_summarized_id(conn), tid)


class TestParseSummary(unittest.TestCase):
    def test_parses_typed_lines(self):
        raw = "事实|用户是软件工程师\n偏好|用户喜欢喝咖啡\n事件|用户换了工作"
        items = memory._parse_summary_lines(raw)
        self.assertEqual([k for k, _ in items], ["fact", "preference", "event"])
        self.assertIn("咖啡", items[1][1])

    def test_ignores_explanations_and_blanks(self):
        raw = "好的，我提取如下：\n事实|用户养了一只猫\n\n（以上为提取结果）"
        items = memory._parse_summary_lines(raw)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][0], "fact")

    def test_accepts_english_tags(self):
        items = memory._parse_summary_lines("fact|用户爱猫")
        self.assertEqual(items[0][0], "fact")

    def test_handles_none_marker(self):
        self.assertEqual(memory._parse_summary_lines("无"), [])
        self.assertEqual(memory._parse_summary_lines(""), [])

    def test_unknown_tag_dropped(self):
        self.assertEqual(memory._parse_summary_lines("随便|内容"), [])


class TestSummarizeTurns(unittest.TestCase):
    def test_writes_typed_memories(self):
        conn = _conn()
        _add_turn(conn, user_text="我是个程序员", ja="そう", zh="是吗")
        llm = FakeLLM("事实|用户是程序员\n偏好|用户喜欢猫")
        res = memory.summarize_turns(conn, llm, memory.pending_turns(conn, 0))
        self.assertEqual(res["status"], "ok")
        self.assertEqual(len(res["written"]), 2)
        rows = conn.execute("SELECT kind, text FROM memory ORDER BY id").fetchall()
        self.assertEqual([r["kind"] for r in rows], ["fact", "preference"])
        # 对话原文必须进了 prompt（否则等于让模型凭空编记忆）
        self.assertIn("程序员", llm.last_prompt)

    def test_duplicates_are_not_written_twice(self):
        conn = _conn()
        _add_turn(conn)
        llm = FakeLLM("事实|用户是程序员")
        memory.summarize_turns(conn, llm, memory.pending_turns(conn, 0))
        # 再来一次同样内容（模拟模型重复提取）
        res2 = memory.summarize_turns(conn, llm, memory.pending_turns(conn, 0))
        self.assertEqual(res2["status"], "ok")
        self.assertEqual(res2["written"], [])  # 全被判为重复
        n = conn.execute("SELECT COUNT(*) AS n FROM memory").fetchone()["n"]
        self.assertEqual(n, 1)

    def test_unparseable_reply_is_not_advanceable(self):
        # 模型输出跑偏 -> status=unparsed，调用方不能推进游标
        conn = _conn()
        _add_turn(conn)
        res = memory.summarize_turns(conn, FakeLLM("我无法提取"), memory.pending_turns(conn, 0))
        self.assertEqual(res["status"], "unparsed")
        self.assertEqual(res["written"], [])
        n = conn.execute("SELECT COUNT(*) AS n FROM memory").fetchone()["n"]
        self.assertEqual(n, 0)

    def test_nothing_reply_is_a_valid_conclusion(self):
        # 「无」是有效结论：这批确实没有值得记的信息，游标应当可以推进
        conn = _conn()
        _add_turn(conn)
        res = memory.summarize_turns(conn, FakeLLM("无"), memory.pending_turns(conn, 0))
        self.assertEqual(res["status"], "nothing")
        self.assertEqual(res["written"], [])

    def test_llm_exception_is_reported_as_error(self):
        class Boom:
            def complete(self, messages):
                raise RuntimeError("api down")

        conn = _conn()
        _add_turn(conn)
        res = memory.summarize_turns(conn, Boom(), memory.pending_turns(conn, 0))
        self.assertEqual(res["status"], "error")

    def test_empty_turn_list(self):
        conn = _conn()
        res = memory.summarize_turns(conn, FakeLLM("事实|x"), [])
        self.assertEqual(res["status"], "nothing")
        self.assertEqual(res["written"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
