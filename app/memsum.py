"""记忆写回：把对话压缩成关于用户的长期事实。

为什么要单独跑
--------------
写回要调一次 LLM，而且只应该处理「已经聊完、不会再变」的内容。
放在对话主链路里会让每条回复都多一次 API 调用、多几百毫秒延迟；
做成离线任务就没有这个代价 —— 聊天时完全不感知。

增量策略
--------
meta 表里记 `memory_summary_until_id`，每次只处理 id 比它大的轮次：

  turns:  ... id=100 (已摘要到 100) ... id=101..130 (待处理)
                                   ↑
                         下一批从这里开始

所以重复跑不会产生重复记忆，跑得再勤也只是空转。

用法
----
    python -m app.memsum                 # 有足够新对话就摘要，否则什么都不做
    python -m app.memsum --dry-run       # 只看会喂什么给模型，不写库
    python -m app.memsum --force         # 不到阈值也摘要（调试用）
    python -m app.memsum --min-turns 10  # 至少 10 轮才动机

推荐调度：每 2 小时跑一次（后台计划任务 YukinoMemory）。
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import load_settings
from .state import db, memory, userprofile

log = logging.getLogger("yukino.memsum")


def _transcript(turns: list[dict]) -> str:
    lines = []
    for t in turns:
        lines.append("用户：%s" % (t.get("user_text") or ""))
        lines.append("雪乃：%s" % (t.get("zh") or t.get("ja") or ""))
    return "\n".join(lines)


def run(*, min_turns: int = 6, batch: int = 30, force: bool = False,
        dry_run: bool = False, max_batches: int = 5) -> int:
    settings = load_settings()
    conn = db.connect(settings.db_path)
    db.init_schema(conn)
    # 这个任务和机器人服务同时访问同一个 SQLite 文件。
    # 默认 busy timeout 只有 5 秒，聊得正嗨时写回可能刚好撞上，加长一点更稳。
    try:
        conn.execute("PRAGMA busy_timeout = 15000")
    except Exception:  # noqa: BLE001
        pass

    cursor = memory.last_summarized_id(conn)
    total_rows = conn.execute("SELECT COUNT(*) AS n FROM turns").fetchone()["n"]
    log.info("已摘要到 turn id=%s，共 %s 轮对话", cursor, total_rows)

    pending = memory.pending_turns(conn, cursor, limit=batch * max_batches)
    if not pending:
        log.info("没有新对话，无需写回")
        return 0

    if len(pending) < max(1, min_turns) and not force:
        log.info("新对话只有 %s 轮（阈值 %s），这次先不摘要", len(pending), min_turns)
        return 0

    if dry_run:
        log.info("[dry-run] 待处理 %s 轮，下面是把要喂给模型的内容（截断 1200 字）", len(pending))
        print(_transcript(pending)[:1200])
        return 0

    from .llm.client import build_client

    llm = build_client(settings)
    written_total = 0
    for bi in range(max_batches):
        chunk = pending[bi * batch:(bi + 1) * batch]
        if not chunk:
            break
        res = memory.summarize_turns(conn, llm, chunk)
        status = res.get("status")
        got = res.get("written") or []

        if status in ("error", "unparsed"):
            # 模型没给出可用的结论：不推进游标，下次重试这一批
            log.warning("第 %s 批未产出（status=%s），游标不推进，下次重试", bi + 1, status)
            log.warning("  模型原始输出（前 200 字）：%r", (res.get("raw") or "")[:200])
            break

        # status == ok / nothing：结论有效，无论有没有写到新记忆都推进游标
        cursor = int(chunk[-1]["id"])
        memory.set_last_summarized_id(conn, cursor)
        written_total += len(got)
        # 顺手把「关于用户的事实/偏好」补进用户档案，聊得越久档案越完整
        try:
            facts = [w["text"] for w in got if w.get("kind") in ("fact", "preference")]
            if facts:
                added = userprofile.append_facts(settings.user_profile_path, facts)
                for a in added:
                    log.info("  → 档案 + %s", a)
        except Exception as exc:  # noqa: BLE001
            log.warning("补充用户档案失败：%r", exc)
        if status == "nothing":
            log.info("第 %s 批（%s 轮）：模型判断没有值得记的信息，游标推进到 id=%s",
                     bi + 1, len(chunk), cursor)
        else:
            log.info("第 %s 批（%s 轮）写入 %s 条记忆，游标推进到 id=%s",
                     bi + 1, len(chunk), len(got), cursor)
            for item in got:
                log.info("  + [%s] %s", item["kind"], item["text"])

    log.info("本次共写入 %s 条记忆", written_total)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="雪乃 · 记忆写回（把对话压成长期事实）")
    ap.add_argument("--min-turns", type=int, default=6, help="至少积累多少轮才摘要（默认 6）")
    ap.add_argument("--batch", type=int, default=30, help="每批处理多少轮（默认 30）")
    ap.add_argument("--max-batches", type=int, default=5, help="单次最多跑几批（默认 5）")
    ap.add_argument("--force", action="store_true", help="不到阈值也摘要")
    ap.add_argument("--dry-run", action="store_true", help="只看不写")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    return run(min_turns=args.min_turns, batch=args.batch, force=args.force,
               dry_run=args.dry_run, max_batches=args.max_batches)


if __name__ == "__main__":
    raise SystemExit(main())
