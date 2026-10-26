"""SQLite 连接与建表。纯标准库。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
-- 键值状态：好感度、上次说话时间、累计轮数等
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);

-- 每一轮对话全量落库，可随时回放「为什么扣分」
CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT    NOT NULL,
    user_text  TEXT    NOT NULL DEFAULT '',
    user_norm  TEXT    NOT NULL DEFAULT '',
    ja         TEXT    NOT NULL DEFAULT '',
    zh         TEXT    NOT NULL DEFAULT '',
    act        TEXT    NOT NULL DEFAULT '',
    mood       TEXT    NOT NULL DEFAULT 'calm',
    aff_delta  INTEGER NOT NULL DEFAULT 0,
    aff_after  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_turns_ts   ON turns(ts);
CREATE INDEX IF NOT EXISTS idx_turns_norm ON turns(user_norm);

-- 长期记忆：事实、偏好、事件、每日摘要
CREATE TABLE IF NOT EXISTS memory (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts   TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'fact',
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory(kind);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
