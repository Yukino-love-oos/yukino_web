"""会话状态：好感度持久化 + 代码级防刷分。

原实现的问题：
  · `affinity = 0` 是模块级全局变量，重启归零
  · CLI 版和 Web 版各维护一份，协议还不一样（`[AFFINITY_CHANGE: N]` vs `| N`）
  · 「防刷好感度」只写在提示词里求模型自律，模型不听就失效
  · 解析失败被 `except: pass` 静默吞掉

现在的规则（全部由代码强制执行，不信模型自觉）：
  1. 单轮变化幅度 ≤ persona.per_turn_max
  2. 同一句话在最近 N 轮内重复出现 → 直接扣分（repeated_topic_penalty）
  3. 每日净增上限 persona.daily_gain_cap
  4. 好感度、每一轮、每次分数变化全部落 SQLite，可回溯
"""

from __future__ import annotations

import datetime as _dt
import re
import sqlite3
import unicodedata

from ..llm.protocol import Turn
from . import mood as mood_mod

_PUNCT = re.compile(r"[\s\u3000、。．，,.！？!?…「」『』（）()【】\[\]<>《》~〜ー\-—・:：;；\"'`*#]+")


def normalize(text: str) -> str:
    """归一化用户输入，用于判重（去标点、去空白、统一全半角）。"""
    t = unicodedata.normalize("NFKC", text or "").lower()
    return _PUNCT.sub("", t)


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return _dt.date.today().isoformat()


class Session:
    """一个会话 = 一份持久化的好感度 + 一轮轮的对话记录。"""

    def __init__(self, conn: sqlite3.Connection, persona, user_id: str = "default") -> None:
        self.conn = conn
        self.persona = persona
        self.user_id = user_id

    # ---------------- 键值状态 ----------------
    def get(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
        return row["v"] if row else default

    def set(self, key: str, value) -> None:
        self.conn.execute(
            "INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (key, str(value)),
        )
        self.conn.commit()

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get(key, str(default)))
        except ValueError:
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.get(key, str(default)))
        except ValueError:
            return default

    # ---------------- 心情（带衰减） ----------------
    def set_mood(self, mood: str, *, now: float | None = None) -> dict:
        """记录本轮情绪。存的是「起点」，读取时再按时间衰减。"""
        prev = {"mood": self.get("mood", "calm"), "mood_at": self.get_float("mood_at", 0.0)}
        state = mood_mod.blend(prev, mood, now=now)
        self.set("mood", state["mood"])
        self.set("mood_at", state["mood_at"])
        self.set("mood_intensity", state["intensity"])
        return state

    def current_mood(self, *, now: float | None = None) -> dict:
        """当前残余情绪。距上次设定越久强度越低，低于阈值自动回到 calm。"""
        return mood_mod.decay(self.get("mood", "calm"),
                              self.get_float("mood_at", 0.0), now=now)

    def mood_hint(self) -> str:
        return mood_mod.describe(self.current_mood())

    # ---------------- 好感度 ----------------
    @property
    def affinity(self) -> int:
        return self.get_int("affinity", 0)

    @affinity.setter
    def affinity(self, value: int) -> None:
        lo, hi = self.persona.affinity.get("range", [-100, 100])
        self.set("affinity", max(lo, min(hi, int(value))))

    @property
    def total_turns(self) -> int:
        return self.get_int("total_turns", 0)

    def today_gain(self) -> int:
        """今天已经净增了多少（只统计正分）。"""
        if self.get("gain_date", "") != _today():
            return 0
        return self.get_int("gain_today", 0)

    def _add_gain(self, amount: int) -> None:
        if amount <= 0:
            return
        if self.get("gain_date", "") != _today():
            self.set("gain_date", _today())
            self.set("gain_today", amount)
        else:
            self.set("gain_today", self.today_gain() + amount)

    # ---------------- 判重 ----------------
    def recent_user_norm(self, n: int = 5) -> list[str]:
        rows = self.conn.execute(
            "SELECT user_norm FROM turns WHERE user_norm <> '' ORDER BY id DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [r["user_norm"] for r in rows]

    def _is_repeat(self, user_text: str) -> bool:
        norm = normalize(user_text)
        if not norm:
            return False
        window = int(self.persona.anti_farming.get("repeated_topic_window", 5))
        return norm in self.recent_user_norm(window)

    # ---------------- 核心：应用好感度变化 ----------------
    def apply_affinity(self, model_delta: int, user_text: str) -> tuple[int, str]:
        """把模型给的分值过一遍规则，返回 (实际生效分值, 说明)。"""
        cap = self.persona.per_turn_max
        reason = []

        delta = int(model_delta or 0)
        lo, hi = self.persona.affinity.get("range", [-100, 100])
        current = self.affinity

        # 规则 1：单轮幅度上限
        if abs(delta) > cap:
            delta = cap if delta > 0 else -cap
            reason.append(f"单轮幅度限制为 ±{cap}")

        # 规则 2：复读惩罚（代码强制，不依赖模型）
        if self._is_repeat(user_text):
            penalty = int(self.persona.anti_farming.get("repeated_topic_penalty", -2))
            delta = min(delta, 0) + penalty
            reason.append(f"检测到重复讨好话题，额外 {penalty}")

        # 规则 3：每日净增上限
        if delta > 0:
            room = self.persona.daily_gain_cap - self.today_gain()
            if room <= 0:
                reason.append("今日好感度增长已达上限")
                delta = 0
            elif delta > room:
                delta = room
                reason.append(f"受每日增长上限限制，本轮到 +{room}")

        # 边界裁剪
        new_value = max(lo, min(hi, current + delta))
        delta = new_value - current

        if delta != 0:
            self.affinity = new_value
            self._add_gain(delta)

        return delta, "；".join(reason)

    # ---------------- 落库 ----------------
    def record_turn(self, user_text: str, turn: Turn, delta: int) -> int:
        aff_after = self.affinity
        cur = self.conn.execute(
            """INSERT INTO turns(ts, user_text, user_norm, ja, zh, act, mood, aff_delta, aff_after)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                _now(),
                user_text,
                normalize(user_text),
                turn.ja,
                turn.zh,
                turn.act,
                turn.mood,
                delta,
                aff_after,
            ),
        )
        self.conn.execute(
            "INSERT INTO meta(k, v) VALUES('total_turns', '1') "
            "ON CONFLICT(k) DO UPDATE SET v = CAST(CAST(v AS INTEGER) + 1 AS TEXT)"
        )
        self.set("last_seen", _now())
        self.conn.commit()
        return int(cur.lastrowid)

    def recent_turns(self, n: int = 10) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM turns ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def history_messages(self, n: int = 6) -> list[dict]:
        """转成 LLM 的 messages 格式。

        关键：assistant 的历史**必须还原成标签协议格式**。
        早先这里只放纯日语台词，结果模型从历史里学到「先裸说一句再补标签」，
        本地小模型会因此漏掉 <ja> 开标签，导致整句台词被丢掉。
        """
        msgs: list[dict] = []
        for row in self.recent_turns(n):
            if row["user_text"]:
                msgs.append({"role": "user", "content": row["user_text"]})
            if row["ja"]:
                parts = [f"<ja>{row['ja']}</ja>"]
                if row["zh"]:
                    parts.append(f"<zh>{row['zh']}</zh>")
                if row["act"]:
                    parts.append(f"<act>{row['act']}</act>")
                if row["mood"]:
                    parts.append(f"<mood>{row['mood']}</mood>")
                msgs.append({"role": "assistant", "content": "\n".join(parts)})
        return msgs

    def stats(self) -> dict:
        return {
            "affinity": self.affinity,
            "band": self.persona.band(self.affinity).get("label", ""),
            "total_turns": self.total_turns,
            "today_gain": self.today_gain(),
            "last_seen": self.get("last_seen", ""),
        }
