"""时间轮：让她的年龄随时间真实增长。

为什么不用写死的年龄
--------------------
写死「18 岁」的话，一年后她还是 18 岁 —— 一开口就露馅。
这里只存**生日**和**出生年**，年龄每次按当天现算：

    age = 当年 - 出生年   （若今年生日还没到，再减 1）

于是每过一个生日她自动长一岁，代码里没有任何需要手动更新的地方。

另外提供两个「今天是什么日子」的信号，让她的回复可以有温度：
  · is_birthday   今天正是她的生日
  · days_to_birthday  离下一个生日还有几天（用来做「快到我生日了」的铺垫）

纯 stdlib（datetime），不引依赖。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass


def _parse_birthday(text: str) -> tuple[int, int] | None:
    """接受 "01-03" / "1-3" / "01/03" 这几种写法。"""
    raw = (text or "").strip().replace("/", "-").replace(".", "-")
    if not raw:
        return None
    parts = raw.split("-")
    if len(parts) != 2:
        return None
    try:
        month, day = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return month, day


@dataclass(frozen=True)
class Life:
    """她的时间线参数：生日 + 出生年。"""

    birthday: tuple[int, int]
    birth_year: int

    @classmethod
    def from_dict(cls, data: dict | None) -> "Life | None":
        if not data:
            return None
        bd = _parse_birthday(str(data.get("birthday") or ""))
        try:
            year = int(data.get("birth_year"))
        except (TypeError, ValueError):
            return None
        if bd is None or year < 1900:
            return None
        return cls(birthday=bd, birth_year=year)

    # ---------------- 年龄 ----------------
    def age_on(self, day: _dt.date) -> int:
        """在指定日期她多少岁（生日当天算长一岁）。"""
        age = day.year - self.birth_year
        if (day.month, day.day) < self.birthday:
            age -= 1
        return age

    def next_birthday(self, day: _dt.date) -> _dt.date:
        """下一个（含当天的）生日。"""
        month, dom = self.birthday
        try:
            this_year = _dt.date(day.year, month, dom)
        except ValueError:      # 2-29 在平年
            this_year = _dt.date(day.year, month, 28)
        if this_year >= day:
            return this_year
        try:
            next_year = _dt.date(day.year + 1, month, dom)
        except ValueError:
            next_year = _dt.date(day.year + 1, month, 28)
        return next_year

    def snapshot(self, today: _dt.date | None = None) -> dict:
        today = today or _dt.date.today()
        month, dom = self.birthday
        is_birthday = (today.month, today.day) == (month, dom)
        nxt = self.next_birthday(today)
        return {
            "age": self.age_on(today),
            "is_birthday": is_birthday,
            "next_birthday": nxt.isoformat(),
            "next_age": self.age_on(nxt),
            "days_to_birthday": (nxt - today).days,
            "birthday_label": "%d 月 %d 日" % (month, dom),
            "today": today.isoformat(),
        }


def render_age_hint(snap: dict) -> str:
    """给 system prompt 用的一句话：「你现在几岁、离生日多久」。"""
    age = snap.get("age")
    label = snap.get("birthday_label", "")
    if snap.get("is_birthday"):
        return "今天 %s 是你的生日 —— 从今天起你 %s 岁了。" % (label, age)
    days = snap.get("days_to_birthday")
    if isinstance(days, int) and 0 < days <= 10:
        return "你现在 %s 岁。还有 %s 天就是你的生日（%s）。" % (age, days, label)
    return "你现在 %s 岁。" % age


def render_life_block(life: dict | None, *, max_chars: int = 1600) -> str:
    """把人生经历渲染成 prompt 里的一段（带字数上限）。

    结构上分成「年表 / 家庭 / 喜欢 / 讨厌 / 不愿提的事」，
    全部压缩成一行一条，避免 prompt 膨胀。
    """
    if not life:
        return ""
    parts: list[str] = []

    summary = (life.get("summary") or "").strip()
    if summary:
        parts.append(summary)

    timeline = life.get("timeline") or []
    if timeline:
        lines = []
        for item in timeline:
            try:
                age = int(item.get("age"))
            except (TypeError, ValueError):
                continue
            text = (item.get("event") or "").strip()
            if text:
                lines.append("  · %s 岁：%s" % (age, text))
        if lines:
            parts.append("【年表】\n" + "\n".join(lines))

    family = (life.get("family") or "").strip()
    if family:
        parts.append("【家庭】\n" + family)

    likes = [x for x in (life.get("likes") or []) if x]
    dislikes = [x for x in (life.get("dislikes") or []) if x]
    if likes:
        parts.append("【喜欢】" + "、".join(likes))
    if dislikes:
        parts.append("【讨厌】" + "、".join(dislikes))

    private = [x for x in (life.get("private") or []) if x]
    if private:
        parts.append("【不会主动说、但被问到会承认的事】\n" + "\n".join("  · " + x for x in private))

    note = (life.get("note") or "").strip()
    if note:
        parts.append("【关于这段过去的要求】" + note)

    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n  …（其余略）"
    return text
