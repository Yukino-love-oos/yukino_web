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

# 记忆类型 -> 重要度（0~10）。写得越像"长期事实"越重要，
# 这样同样关键词命中时，「她说过讨厌下雨」比「今天聊了天气」更优先被想起。
_KIND_IMPORTANCE = {
    "fact": 9,
    "preference": 8,
    "pref": 8,
    "event": 6,
    "summary": 5,
    "mood": 4,
}
_DEFAULT_IMPORTANCE = 5

# 记忆新鲜度半衰期（天）：30 天前的记忆权重减半，但不会归零
FRESHNESS_HALF_LIFE_DAYS = 30.0


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


def _age_days(ts: str, *, now: _dt.datetime | None = None) -> float:
    """记忆距今多少天；解析不了就当成很旧（但不归零）。"""
    now = now or _dt.datetime.now()
    try:
        t = _dt.datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return 365.0
    if t.tzinfo is not None:
        t = t.replace(tzinfo=None)
    return max(0.0, (now - t).total_seconds() / 86400.0)


def _freshness(ts: str, *, now: _dt.datetime | None = None) -> float:
    """新鲜度 0.5 ** (age_days / 半衰期)：30 天前的记忆权重减半。"""
    age = _age_days(ts, now=now)
    return 0.5 ** (age / FRESHNESS_HALF_LIFE_DAYS)


def recall(conn: sqlite3.Connection, query: str, k: int = 3, *,
           now: _dt.datetime | None = None) -> list[dict]:
    """按「关键词相关度 × 新鲜度 × 重要度」召回记忆。

    原来只按关键词重叠度排，问题有两个：
      · 三年前的一句闲话和昨天说的偏好权重一样
      · 「她讨厌下雨」这种事实和「今天聊了天气」这种流水账同权
    加入新鲜度（半衰期 30 天）和类型重要度后，召回顺序明显更像人。
    """
    q = _tokens(query)
    if not q:
        return []
    rows = conn.execute("SELECT id, ts, kind, text FROM memory ORDER BY id DESC LIMIT 200").fetchall()
    scored: list[tuple[float, dict]] = []
    seen: set[str] = set()
    for r in rows:
        text = r["text"] or ""
        key = normalize(text)[:60]
        if key in seen:          # 近似重复的记忆只留分数最高那条
            continue
        toks = _tokens(text)
        if not toks:
            continue
        inter = len(q & toks)
        if inter == 0:
            continue
        overlap = inter / (len(q) ** 0.5 * len(toks) ** 0.5)
        fresh = _freshness(r["ts"], now=now)
        importance = _KIND_IMPORTANCE.get((r["kind"] or "").lower(), _DEFAULT_IMPORTANCE)
        score = overlap * (0.55 + 0.45 * fresh) * (0.60 + 0.40 * (importance / 10.0))
        seen.add(key)
        item = dict(r)
        item["_score"] = round(score, 4)
        item["_fresh"] = round(fresh, 3)
        scored.append((score, item))
    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored[:k]]


def build_memory_block(memories: list[dict], *, max_chars: int = 800) -> str:
    """把召回结果渲染成 system prompt 里的一段（带字数预算）。

    预算很重要：记忆条数不限的话，长对话后 system prompt 会被记忆撑爆，
    token 成本和延迟都会涨。默认最多 800 字，超了就丢分数最低的。
    """
    if not memories:
        return ""
    lines: list[str] = []
    used = 0
    for m in memories:
        text = (m.get("text") or "").strip()
        if not text:
            continue
        line = f"  - {text}"
        if used + len(line) > max_chars and lines:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


# ---------------------------------------------------------------- 增量写回
# 「记忆写回」= 把对话压缩成关于用户的稳定事实存下来。
# 三个必须做对的点：
#   1. 增量：记录已经摘要到哪一条 turn，不重复摘要（否则记忆越攒越多重复）
#   2. 分批：一次几十轮，prompt 不会太长，失败也只丢一批
#   3. 带类型：事实/偏好/事件分开存，直接喂给上面的重要度打分

_SUMMARY_KEY = "memory_summary_until_id"

# 类型标记 -> memory.kind
_SUMMARY_KINDS = {
    "事实": "fact",
    "偏好": "preference",
    "事件": "event",
    "摘要": "summary",
}

_SUMMARY_PROMPT = (
    "下面是一段对话记录。请只从里面提取关于「用户」的**稳定信息**"
    "（长期成立的事实、明确的喜好、发生过的重要事件）。\n"
    "每条一行，严格用这个格式：\n"
    "事实|一句话\n"
    "偏好|一句话\n"
    "事件|一句话\n"
    "最多 4 条。没有值得记的就输出「无」。只输出这些行，不要解释、不要编号。\n\n"
)


def last_summarized_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT v FROM meta WHERE k = ?", (_SUMMARY_KEY,)).fetchone()
    try:
        return int(row["v"]) if row else 0
    except (TypeError, ValueError):
        return 0


def set_last_summarized_id(conn: sqlite3.Connection, turn_id: int) -> None:
    conn.execute(
        "INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
        (_SUMMARY_KEY, str(int(turn_id))),
    )
    conn.commit()


def pending_turns(conn: sqlite3.Connection, after_id: int = 0, limit: int = 200) -> list[dict]:
    """取还没摘要过的对话轮次（按 id 升序，保证时间顺序）。"""
    rows = conn.execute(
        "SELECT id, ts, user_text, ja, zh FROM turns WHERE id > ? ORDER BY id ASC LIMIT ?",
        (int(after_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def _parse_summary_lines(raw: str) -> list[tuple[str, str]]:
    """把模型输出解析成 [(kind, text), ...]。认不出的行直接丢弃。"""
    out: list[tuple[str, str]] = []
    for line in (raw or "").splitlines():
        line = line.strip().strip("-*·").strip()
        if not line or line in ("无", "沒有", "没有"):
            continue
        if "|" not in line:
            continue
        tag, _, text = line.partition("|")
        tag = tag.strip().strip("：:").strip()
        text = text.strip()
        if not text:
            continue
        kind = _SUMMARY_KINDS.get(tag)
        if kind is None:
            # 允许模型写成 fact/preference/event
            low = tag.lower()
            kind = low if low in ("fact", "preference", "event", "summary") else None
        if kind is None:
            continue
        out.append((kind, text))
    return out


def _exists_similar(conn: sqlite3.Connection, text: str) -> bool:
    key = normalize(text)[:40]
    if not key:
        return False
    for r in conn.execute("SELECT text FROM memory ORDER BY id DESC LIMIT 400").fetchall():
        if normalize(r["text"])[:40] == key:
            return True
    return False


_NOTHING_MARKERS = ("无", "沒有", "没有", "none", "nothing", "n/a")


def is_nothing_reply(raw: str) -> bool:
    """模型明确表示「没什么值得记的」。

    这跟「输出跑偏了」是两回事，必须分开：
      · 无  -> 是有效结论，游标要推进（否则每 2 小时重试同一批空对话，白烧 API）
      · 乱  -> 不推进，下次重试
    """
    text = (raw or "").strip().strip("。.").strip().lower()
    return text in _NOTHING_MARKERS


def summarize_turns(conn: sqlite3.Connection, llm, turns: list[dict], *,
                    max_items: int = 4) -> dict:
    """把一批对话压成记忆写入。

    llm 需要提供 `complete(messages) -> str`。

    返回 {"written": [...], "status": "ok"|"nothing"|"unparsed"|"error", "raw": str}
    调用方据 status 决定要不要推进游标：
      ok / nothing -> 推进（结论有效，只是可能没东西可记）
      unparsed / error -> 不推进，下次重试
    """
    if not turns:
        return {"written": [], "status": "nothing", "raw": ""}
    transcript = "\n".join(
        "用户：%s\n雪乃：%s" % (t.get("user_text") or "", t.get("zh") or t.get("ja") or "")
        for t in turns
    )
    prompt = _SUMMARY_PROMPT + transcript
    try:
        raw = (llm.complete([{"role": "user", "content": prompt}]) or "").strip()
    except Exception:  # noqa: BLE001
        return {"written": [], "status": "error", "raw": ""}

    items = _parse_summary_lines(raw)
    if not items:
        status = "nothing" if is_nothing_reply(raw) else "unparsed"
        return {"written": [], "status": status, "raw": raw}

    written: list[dict] = []
    for kind, text in items[:max_items]:
        if _exists_similar(conn, text):
            continue
        mid = add(conn, kind, text)
        if mid:
            written.append({"id": mid, "kind": kind, "text": text})
    # 解析成功但全被判为重复 —— 也算有效结论（这批确实没新信息）
    return {"written": written, "status": "ok", "raw": raw}


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
