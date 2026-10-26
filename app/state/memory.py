"""长期记忆 —— 纯标准库（SQLite + 关键词召回）。

分三层，从便宜到贵：
  1. 全量落库：turns 表，永远可回放
  2. 事实/摘要：memory 表，由 LLM 在会话结束后压缩写入
  3. 召回：本轮先按关键词检索 top-k 拼进 system prompt

没有用向量检索，是因为标准库没有 embedding。等装了依赖可以平滑替换
`recall()` 的实现（保持函数签名不变即可），不必改调用方。
"""

from __future__ import annotations

import datetime as _dt
import re
import sqlite3

from .session import normalize

_WORD = re.compile(r"[\w\u3040-\u30ff\u4e00-\u9fff]+")


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def add(conn: sqlite3.Connection, kind: str, text: str) -> int:
    """写入一条记忆。kind: fact | preference | event | summary"""
    text = (text or "").strip()
    if not text:
        return 0
    cur = conn.execute(
        "INSERT INTO memory(ts, kind, text) VALUES(?,?,?)", (_now(), kind, text)
    )
    conn.commit()
    return int(cur.lastrowid)


def recent(conn: sqlite3.Connection, n: int = 8) -> list[dict]:
    rows = conn.execute(
        "SELECT ts, kind, text FROM memory ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _tokens(text: str) -> set[str]:
    """粗糙但够用的分词：日文/中文按 2-gram，其他按词。"""
    norm = normalize(text)
    toks: set[str] = set(_WORD.findall(text.lower()))
    for i in range(len(norm) - 1):
        toks.add(norm[i: i + 2])
    return {t for t in toks if t}


def recall(conn: sqlite3.Connection, query: str, k: int = 3) -> list[dict]:
    """按关键词重叠度召回最相关的记忆。"""
    q = _tokens(query)
    if not q:
        return []
    rows = conn.execute("SELECT id, ts, kind, text FROM memory ORDER BY id DESC LIMIT 200").fetchall()
    scored: list[tuple[float, dict]] = []
    for r in rows:
        toks = _tokens(r["text"])
        if not toks:
            continue
        inter = len(q & toks)
        if inter == 0:
            continue
        score = inter / (len(q) ** 0.5 * len(toks) ** 0.5)
        scored.append((score, dict(r)))
    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored[:k]]


def build_memory_block(memories: list[dict]) -> str:
    """把召回结果渲染成 system prompt 里的一段。"""
    if not memories:
        return ""
    lines = []
    for m in memories:
        lines.append(f"  - {m['text']}")
    return "\n".join(lines)


def summarize_day(conn: sqlite3.Connection, llm, day: str | None = None, limit: int = 60) -> str:
    """把一天（或最近 limit 轮）的对话压成 1~3 条事实写进 memory。

    llm 需要提供 `complete(messages) -> str`。
    """
    day = day or _dt.date.today().isoformat()
    rows = conn.execute(
        "SELECT user_text, ja, zh FROM turns WHERE ts LIKE ? ORDER BY id DESC LIMIT ?",
        (day + "%", limit),
    ).fetchall()
    if not rows:
        return ""

    transcript = "\n".join(
        f"用户：{r['user_text']}\n雪乃：{r['zh'] or r['ja']}" for r in reversed(rows)
    )
    prompt = (
        "下面是一段对话记录。请从中提取关于「用户」的稳定事实与偏好，"
        "每条一句话，最多 3 条，用中文。只输出条目本身，不要编号，不要解释。\n\n"
        + transcript
    )
    try:
        out = llm.complete([{"role": "user", "content": prompt}]).strip()
    except Exception:
        return ""
    for line in out.splitlines():
        line = line.strip(" -·、。0123456789.")
        if line:
            add(conn, "summary", line)
    return out
