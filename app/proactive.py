"""主动关心：链式 timer + 临时回访。

为什么要做这个
--------------
之前她只会在你说话之后才出现 —— 永远等着，像个客服。
真人不是这样的：她会记得你说「等下要去开会」，一小时后问一句开完没；
也会在自己闲着的时候主动找你，说点没头没尾的话。

两种机制各司其职
----------------
  · **链式 timer**：长周期持续运行，是「主动关心」的底线 —— 保证她不会太久不出现。
    每次触发后重建下一个，间隔由她自己按时间段决定（白天 10~60 分钟，深夜 2~7 小时）。
  · **临时回访**：短周期灵活触发，是「主动性的体现」——
    每次聊完她想一想：要不要过一会再问一句？比如：
      你说累了     → 15 分钟后看看好点没
      你去开会     → 1 小时后问开完没
      你说去吃饭   → 40 分钟后问吃了什么
      你说等下试   → 5 分钟后再问一句

关键设计：**不用固定文案**
--------------------------
定时器只负责「到点了，该想一想了」，**真正说什么每次重新判**：
把当前时间 + 最近对话 + 上次设 timer 时的意图一起给模型，让它自己决定
「要不要发、发什么」，不想发就回 NO_REPLY。

另一条容易被忽略的规则（原设计里也强调了）：
**如果他主动回来告诉你结果了，就把对应的 timer 取消** ——
他已经说「我开完会了」，你还追着问「开完会没」，显得不走心。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import random
import re
import sqlite3

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- 时间段
# 白天：08:00 ~ 次日 01:00；深夜：01:00 ~ 08:00
NIGHT_START_H = 1
NIGHT_END_H = 8

DAY_DELAY = (10 * 60, 60 * 60)        # 白天 10~60 分钟
NIGHT_DELAY = (2 * 3600, 7 * 3600)    # 深夜 2~7 小时
# 触发时间落在深夜时，顺延到早上这个点数再发
NIGHT_WAKE_H = 8
NIGHT_WAKE_JITTER_MIN = 0

# recall 延迟的随机抖动比例（±20%），避免每次都精准到分
RECALL_JITTER = 0.2

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def is_night(now: dt.datetime) -> bool:
    return NIGHT_START_H <= now.hour < NIGHT_END_H


def delay_bounds(now: dt.datetime) -> tuple[int, int]:
    return NIGHT_DELAY if is_night(now) else DAY_DELAY


def pick_chain_delay(now: dt.datetime, rng: random.Random | None = None) -> int:
    lo, hi = delay_bounds(now)
    return (rng or random).randint(lo, hi)


def shift_out_of_night(when: dt.datetime) -> dt.datetime:
    """落在深夜（1:00~8:00）的时间点顺延到早上 8 点之后。"""
    if not is_night(when):
        return when
    wake = when.replace(hour=NIGHT_WAKE_H, minute=0, second=0, microsecond=0)
    if wake <= when:
        wake += dt.timedelta(days=1)
    return wake


def describe_now(now: dt.datetime) -> str:
    """给模型看的「现在」：日期 + 星期 + 时间 + 时段。"""
    h = now.hour
    if h < 6:
        part = "凌晨"
    elif h < 11:
        part = "上午"
    elif h < 13:
        part = "中午"
    elif h < 18:
        part = "下午"
    elif h < 23:
        part = "晚上"
    else:
        part = "深夜"
    return "%d月%d日 %s %s %02d:%02d" % (
        now.month, now.day, WEEKDAYS[now.weekday()], part, now.hour, now.minute,
    )


# ---------------------------------------------------------------- 临时回访规则
# (正则, 延迟秒数, 备注模板)  —— 备注里的 {word} 会被替换成命中的词
RECALL_RULES = (
    (r"(好累|累死|太累|有点累|困死|好困|没精神|疲惫)", 15 * 60,
     "他刚才说「{word}」，过一会儿问问他缓过来没有"),
    (r"(开会|会议|上课|考试|面试|答辩|汇报|讲座)", 60 * 60,
     "他去「{word}」了，问一句结束了没有"),
    (r"(去吃饭|吃饭了|吃个饭|点外卖|外卖到了|去食堂)", 40 * 60,
     "他刚才去吃饭了，问问他吃了什么"),
    (r"(等下|一会儿|等一下|待会儿|稍后|先忙|忙完)", 5 * 60,
     "他说「{word}」，过几分钟问一句忙完没有"),
    (r"(加班|赶工|ddl|deadline|赶稿|通宵)", 90 * 60,
     "他在加班/赶工，提醒他歇一会儿"),
    (r"(午休|躺一会|去睡了|睡觉了|眯一会)", 60 * 60,
     "他说要睡一会儿，醒了之后问一句状态"),
    (r"(回家|地铁上|公交上|在路上|车上|堵车)", 30 * 60,
     "他在路上，问一句到家没有"),
    (r"(去洗澡|洗澡|做饭|收拾一下|去收拾)", 25 * 60,
     "他去做「{word}」了，过一会儿问一句"),
    (r"(去医院|看医生|拿药|不舒服|头疼|胃疼)", 45 * 60,
     "他身体不太舒服，问一句好点没有"),
)

# 用户回来说「结果」时，用来判断要不要取消对应 timer 的关键词
RESOLVE_KEYWORDS = (
    "开完", "结束", "上完课", "考完", "面完", "忙完", "弄完", "搞定", "完事",
    "吃完了", "吃完", "吃过了", "吃了",
    "到家", "到了", "回来了", "回来了",
    "洗完", "洗好", "做好了",
    "睡醒", "醒了", "起来",
    "好多了", "好点", "缓过来", "没事了",
)


def detect_recall(user_text: str) -> dict | None:
    """从用户这句话里判断要不要设临时回访。返回 {"delay_seconds","note","word"}。"""
    text = (user_text or "").strip()
    if not text:
        return None
    for pattern, delay, note_tpl in RECALL_RULES:
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        word = m.group(1) if m.groups() else m.group(0)
        return {"delay_seconds": int(delay), "note": note_tpl.format(word=word), "word": word}
    return None


def looks_resolved(user_text: str) -> bool:
    """用户是不是在报告「事情办完了」——用来取消 pending 的回访。"""
    text = (user_text or "").strip()
    return any(k in text for k in RESOLVE_KEYWORDS)


# ---------------------------------------------------------------- 存储
class TimerStore:
    """timers 表读写。一条 timer = 到点后请模型重新判断一次。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, kind: str, due: dt.datetime, *, note: str = "", why: str = "",
            now: dt.datetime | None = None) -> int:
        now = now or dt.datetime.now()
        cur = self.conn.execute(
            "INSERT INTO timers(kind, created_ts, due_ts, status, note, why) VALUES(?,?,?,?,?,?)",
            (kind, _iso(now), _iso(due), "pending", note, why),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def pending(self, kind: str | None = None) -> list[dict]:
        if kind:
            rows = self.conn.execute(
                "SELECT * FROM timers WHERE status='pending' AND kind=? ORDER BY due_ts", (kind,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM timers WHERE status='pending' ORDER BY due_ts"
            ).fetchall()
        return [dict(r) for r in rows]

    def due(self, now: dt.datetime, *, limit: int = 3) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM timers WHERE status='pending' AND due_ts<=? ORDER BY due_ts LIMIT ?",
            (_iso(now), int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark(self, timer_id: int, status: str, *, result: str = "",
             now: dt.datetime | None = None) -> None:
        now = now or dt.datetime.now()
        self.conn.execute(
            "UPDATE timers SET status=?, result=?, fired_ts=? WHERE id=?",
            (status, result, _iso(now), int(timer_id)),
        )
        self.conn.commit()

    def cancel_recalls(self, *, note_like: str | None = None) -> int:
        """取消 pending 的临时回访。note_like 为 None 时取消全部。"""
        if note_like:
            cur = self.conn.execute(
                "UPDATE timers SET status='cancelled', result='user_resolved' "
                "WHERE status='pending' AND kind='recall' AND note LIKE ?",
                ("%" + note_like + "%",),
            )
        else:
            cur = self.conn.execute(
                "UPDATE timers SET status='cancelled', result='user_resolved' "
                "WHERE status='pending' AND kind='recall'"
            )
        self.conn.commit()
        return cur.rowcount or 0

    def ensure_chain(self, now: dt.datetime, rng: random.Random | None = None) -> str:
        """保证「链式 timer」只有一个 pending：没有就建下一个。"""
        existing = self.pending("chain")
        if existing:
            return "exists"
        delay = pick_chain_delay(now, rng)
        due = shift_out_of_night(now + dt.timedelta(seconds=delay))
        self.add(
            "chain", due,
            note="链式主动关心：到点自己判断要不要主动找他",
            why="上次间隔 %d 分钟" % (delay // 60),
            now=now,
        )
        return "created"


def _iso(t: dt.datetime) -> str:
    return t.replace(microsecond=0).isoformat(sep=" ")


def _from_iso(s: str) -> dt.datetime:
    try:
        return dt.datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return dt.datetime.now()


# ---------------------------------------------------------------- outbox（发不出去的话）
def add_outbox(conn: sqlite3.Connection, text: str, *, channel: str = "buffered",
               timer_id: int | None = None, now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now()
    cur = conn.execute(
        "INSERT INTO proactive_outbox(ts, text, delivered, channel, timer_id) VALUES(?,?,0,?,?)",
        (_iso(now), text, channel, timer_id),
    )
    conn.commit()
    return int(cur.lastrowid)


def read_pending_topics(conn: sqlite3.Connection, *, limit: int = 3) -> list[dict]:
    rows = conn.execute(
        "SELECT id, ts, text FROM proactive_outbox WHERE delivered=0 ORDER BY id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def consume_pending_topics(conn: sqlite3.Connection, ids: list[int]) -> None:
    if not ids:
        return
    conn.execute(
        "UPDATE proactive_outbox SET delivered=1 WHERE id IN (%s)" % ",".join("?" * len(ids)),
        [int(i) for i in ids],
    )
    conn.commit()


def read_web_messages(conn: sqlite3.Connection, *, limit: int = 10) -> list[dict]:
    """网页端取「她主动发的消息」（未取走的）。"""
    rows = conn.execute(
        "SELECT id, ts, text FROM proactive_outbox WHERE channel='web' AND delivered=0 "
        "ORDER BY id LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 决策
NO_REPLY = "NO_REPLY"

_DECISION_SYSTEM = """你在扮演一个真实的人，不是客服。
现在是「主动关心」的触发时刻：没有人在跟你说话，是你在决定要不要主动联系他。

规则：
1. 先看现在几点、上次聊到哪、你当时打算做什么，再判断该不该发。
2. 深夜（01:00~08:00）除非他明确说过会熬夜/在等结果，否则不要发。
3. 如果上次对话刚结束不久、或者没什么新东西可问，就回复 {no_reply}。
4. 要发就只写**一句**自然的短消息，用中文，不加括号动作、不加表情符号，不要写成问候模板。
5. 不要重复你上次已经问过的问题；也不要追问他已经回答过的事。
6. 语气符合你们的关系（亲密度见下方），冷淡或温柔都可以，但不要突然热情。

输出格式（只输出其中一种）：
  {no_reply}
  或者直接输出那句话本身。"""


def parse_decision(raw: str) -> dict:
    """把模型输出解析成 {"send": bool, "text": str}。"""
    text = (raw or "").strip()
    if not text:
        return {"send": False, "text": ""}
    # 去掉代码块围栏 / 引号 / 多余标签
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    text = re.sub(r"^<[^>]{1,12}>", "", text).strip()
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    if first.upper().startswith(NO_REPLY) or text.upper().startswith(NO_REPLY):
        return {"send": False, "text": ""}
    # 模型可能写成 <ja>…</ja><zh>…</zh>，优先取中文那段（QQ 文字发的是中文）
    zh = re.search(r"<zh>(.*?)</zh>", text, re.S)
    if zh:
        text = zh.group(1).strip()
    # 去掉可能带上的日文标签，只留正文
    text = re.sub(r"<[^>]{1,12}>", "", text).strip()
    text = text.strip('"“”\'').strip()
    # 只取第一句非空行，避免模型写小作文
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {"send": False, "text": ""}
    out = lines[0].strip("「」\"'")
    if not out or out.upper().startswith(NO_REPLY):
        return {"send": False, "text": ""}
    return {"send": True, "text": out[:120]}


# ---------------------------------------------------------------- 引擎
class Proactive:
    """把 timer、决策、投递串起来。

    pipeline 只用到三个属性：session（拿 conn）、llm（complete）、persona；
    sender 需要一个 send(text, timer) -> dict 方法（投递成功/失败）。
    """

    def __init__(self, pipeline, *, sender=None, now_fn=None, rng=None,
                 enabled: bool = True):
        self.pipeline = pipeline
        self.store = TimerStore(pipeline.session.conn)
        self.sender = sender
        self.now_fn = now_fn or dt.datetime.now
        self.rng = rng or random.Random()
        self.enabled = enabled

    # ---------------- 每轮对话结束 ----------------
    def on_turn_end(self, user_text: str, reply: str = "") -> dict:
        if not self.enabled:
            return {"cancelled": 0, "recall": None, "chain": "disabled"}
        now = self.now_fn()

        # 1) 他已经主动报告结果 -> 取消对应回访（“不走心”的坑）
        cancelled = 0
        if looks_resolved(user_text):
            cancelled = self.store.cancel_recalls()

        # 2) 新的一句话里是否埋了「过一会要回访」的线索
        recall = None
        hit = detect_recall(user_text)
        if hit:
            delay = int(hit["delay_seconds"] * (1 + self.rng.uniform(-RECALL_JITTER, RECALL_JITTER)))
            due = shift_out_of_night(now + dt.timedelta(seconds=max(60, delay)))
            tid = self.store.add(
                "recall", due, note=hit["note"],
                why="他说「%s」，%d 分钟后回访" % (hit["word"], delay // 60),
                now=now,
            )
            recall = {"id": tid, "due": _iso(due), "note": hit["note"]}
            log.info("设了临时回访：%s（%s）", hit["note"], _iso(due))

        # 3) 保证链式 timer 存在
        chain = self.store.ensure_chain(now, self.rng)
        return {"cancelled": cancelled, "recall": recall, "chain": chain}

    # ---------------- 到点处理 ----------------
    def tick(self, *, limit: int = 3) -> list[dict]:
        if not self.enabled:
            return []
        now = self.now_fn()
        out: list[dict] = []
        for timer in self.store.due(now, limit=limit):
            try:
                out.append(self._fire(timer, now))
            except Exception as exc:  # noqa: BLE001
                log.warning("处理 timer #%s 失败：%r", timer.get("id"), exc)
                self.store.mark(timer["id"], "skipped", result="error:%s" % exc, now=now)
        return out

    def _fire(self, timer: dict, now: dt.datetime) -> dict:
        decision = self._decide(timer, now)
        kind = timer.get("kind") or "chain"
        if not decision["send"]:
            self.store.mark(timer["id"], "skipped", result="no_reply", now=now)
            log.info("timer #%s（%s）判断为不用发", timer["id"], kind)
        else:
            result = self._deliver(decision["text"], timer)
            self.store.mark(timer["id"], "sent", result=result.get("result", "sent"), now=now)
            log.info("timer #%s（%s）已发：%s", timer["id"], kind, decision["text"][:40])
        # 链式 timer 每次触发后都要重建
        if kind == "chain":
            self.store.ensure_chain(now, self.rng)
        return {"id": timer["id"], "kind": kind, **decision, "result": "ok"}

    def _deliver(self, text: str, timer: dict) -> dict:
        if self.sender is None:
            add_outbox(self.pipeline.session.conn, text, channel="buffered",
                       timer_id=timer.get("id"), now=self.now_fn())
            return {"delivered": False, "result": "buffered"}
        try:
            r = self.sender.send(text, timer) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("主动消息投递失败：%r", exc)
            r = {"delivered": False, "reason": repr(exc)}
        if not r.get("delivered"):
            add_outbox(self.pipeline.session.conn, text, channel="buffered",
                       timer_id=timer.get("id"), now=self.now_fn())
            return {"delivered": False, "result": "buffered"}
        return {"delivered": True, "result": r.get("channel", "sent")}

    def _decide(self, timer: dict, now: dt.datetime) -> dict:
        msgs = [
            {"role": "system", "content": _DECISION_SYSTEM.format(no_reply=NO_REPLY)},
            {"role": "user", "content": self._decision_prompt(timer, now)},
        ]
        try:
            raw = self.pipeline.llm.complete(msgs)
        except Exception as exc:  # noqa: BLE001
            log.warning("主动关心决策失败：%r", exc)
            return {"send": False, "text": ""}
        return parse_decision(raw)

    def _decision_prompt(self, timer: dict, now: dt.datetime) -> str:
        parts = ["【现在】" + describe_now(now)]
        parts.append("【这次触发】%s（%s）" % (
            timer.get("note") or "链式主动关心", timer.get("why") or ""))
        history = self._recent_history(4)
        if history:
            parts.append("【最近对话】\n" + history)
        aff = 0
        try:
            aff = int(self.pipeline.session.affinity)
        except Exception:  # noqa: BLE001
            pass
        parts.append("【关系】好感度 %d/100" % aff)
        parts.append("【任务】现在要不要主动找他？要就写一句，不要就只输出 %s。" % NO_REPLY)
        return "\n".join(parts)

    def _recent_history(self, n: int) -> str:
        try:
            rows = self.pipeline.session.recent_turns(n)
        except Exception:  # noqa: BLE001
            return ""
        lines = []
        for r in rows:
            u = (r.get("user_text") or "").strip()
            z = (r.get("zh") or "").strip()
            if u:
                lines.append("他：" + u[:80])
            if z:
                lines.append("你：" + z[:80])
        return "\n".join(lines)


# ---------------------------------------------------------------- 去重辅助
def dedupe_against_recent(text: str, recent_texts: list[str]) -> bool:
    """这句主动消息是不是和最近说过的太像了（简单字符重合度）。"""
    if not text or not recent_texts:
        return False
    t = set(text)
    for other in recent_texts:
        o = set(other or "")
        if not o:
            continue
        inter = len(t & o)
        if inter / max(1, len(t | o)) > 0.75:
            return True
    return False
