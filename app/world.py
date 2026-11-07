"""场景系统：她现在在哪儿。

两层结构
--------
**第一层：时间线（硬约束）**
    平日照表走：07:00-09:00 通学、09:00-16:00 在学校、16:00-17:00 侍奉部活动、
    19:00-23:00 在家、23:00 以后在自己房间。
    这些时段不由随机决定 —— 她是个作息极规律的人，这也是人设的一部分。

**第二层：随机指数（软约束）**
    只有「放学后（17:00-19:00）」和「周末」这两段自由时间才掷骰子。
    权重 = 场景基础权重 × 心情权重 × 好感度修正：
      · 心情好（soft）→ 公园/咖啡店概率升
      · 心情差（angry/tired）→ 更可能一个人待着
      · 好感度高 → 更愿意待在「可能遇到你」的地方
      · 好感度低 → 更愿意躲起来

为什么要持久化
--------------
同一段时间内必须稳定。如果不落库，心情一变（她刚跟你聊完心情从 calm 变 soft），
场景就会在对话中途突然跳到别的地方，非常出戏。
所以每个时间片第一次算出结果后写进 meta，这一片时间之内不再变。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import random
from pathlib import Path

log = logging.getLogger(__name__)

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 自由时段的说明文案（写进 prompt，让她知道「为什么现在在这」）
FREE_REASONS = {
    "weekday_free": "放学后到回家前，这段时间她自己安排",
    "weekend": "周末，没有课，去哪儿由她自己决定",
    "weekend_evening": "周末的傍晚，一天快结束了",
}

SLOT_LABELS = {
    "school": "上课时间",
    "club": "侍奉部活动",
    "commute": "通学路上",
    "home": "在家",
    "night": "深夜",
    "free": "自由时间",
}


# ---------------------------------------------------------------- 加载
class World:
    def __init__(self, data: dict, *, store=None):
        self.data = data or {}
        self.scenes = self.data.get("scenes") or {}
        self.schedule = self.data.get("schedule") or {}
        self.mood_weights = self.data.get("mood_weights") or {}
        self.store = store           # Session：有 get/set 就用来持久化
        self._cache: dict[str, str] = {}

    # ---------------- 时间片 ----------------
    def is_weekend(self, now: dt.datetime) -> bool:
        return now.weekday() >= 5

    def _slots(self, now: dt.datetime) -> list[dict]:
        key = "weekend" if self.is_weekend(now) else "weekday"
        return list(self.schedule.get(key) or [])

    def slot(self, now: dt.datetime) -> dict:
        """返回当前时间片：kind / start / end / fixed scene。"""
        slots = self._slots(now)
        if not slots:
            return {"kind": "night", "scene": "home_room",
                    "start": now.replace(hour=0, minute=0, second=0, microsecond=0),
                    "end": now.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=1),
                    "index": 0}
        minutes = now.hour * 60 + now.minute
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        for i, s in enumerate(slots):
            a = _to_min(s.get("start", "00:00"))
            b = _to_min(s.get("end", "24:00"))
            if a <= minutes < b:
                start_dt = base + dt.timedelta(minutes=a)
                if b == 24 * 60:
                    end_dt = base + dt.timedelta(days=1)
                else:
                    end_dt = base + dt.timedelta(minutes=b)
                return {
                    "kind": s.get("kind", "free"),
                    "scene": s.get("scene"),
                    "start": start_dt,
                    "end": end_dt,
                    "index": i,
                }
        return {"kind": "night", "scene": "home_room", "start": now, "end": now, "index": 0}

    def slot_key(self, now: dt.datetime) -> str:
        s = self.slot(now)
        return "%s|%d|%s" % (now.strftime("%Y-%m-%d"), s["index"], s["kind"])

    # ---------------- 场景选择 ----------------
    def scene(self, now: dt.datetime | None = None, *, mood: str = "calm",
              affinity: int = 0, rng: random.Random | None = None) -> dict:
        now = now or dt.datetime.now()
        slot = self.slot(now)
        kind = slot.get("kind") or "free"

        # 1) 固定时段：直接给场景
        fixed = slot.get("scene")
        if fixed and fixed in self.scenes:
            chosen = fixed
            reason = "%s（%s~%s）" % (
                SLOT_LABELS.get(kind, kind),
                slot["start"].strftime("%H:%M"), slot["end"].strftime("%H:%M"))
        else:
            # 2) 自由时段：掷骰（同片时间内结果持久化，保证稳定）
            chosen, reason = self._roll(now, kind, mood, affinity, rng)

        scene = dict(self.scenes.get(chosen) or {})
        scene.setdefault("id", chosen)
        scene.setdefault("name_zh", chosen)
        scene.setdefault("name_ja", chosen)
        scene["id"] = chosen
        scene["reason"] = reason
        scene["kind"] = kind
        scene["slot_label"] = SLOT_LABELS.get(kind, kind)
        scene["window"] = "%s~%s" % (slot["start"].strftime("%H:%M"), slot["end"].strftime("%H:%M"))
        scene["next_change_ts"] = slot["end"].isoformat(sep=" ")
        scene["now_text"] = describe_now(now)
        return scene

    def _roll(self, now: dt.datetime, kind: str, mood: str, affinity: int,
              rng: random.Random | None) -> tuple[str, str]:
        key = self.slot_key(now)

        # 先看持久化缓存（同一个时间片内不变）
        cached = self._get_cached(key)
        if cached and cached in self.scenes:
            return cached, self._reason_for(kind, mood, cached)

        pool = self._pool(now, kind)
        if not pool:
            return "home", "没有别的安排"

        weights = {sid: self._weight(sid, mood, affinity) for sid in pool}
        total = sum(weights.values())
        if total <= 0:
            weights = {sid: 1.0 for sid in pool}
            total = float(len(pool))

        r = (rng or random.Random(_seed(key))).random() * total
        acc = 0.0
        chosen = pool[-1]
        for sid, w in weights.items():
            acc += w
            if r <= acc:
                chosen = sid
                break

        self._set_cached(key, chosen)
        log.info("场景掷骰：%s → %s（mood=%s aff=%s）", key, chosen, mood, affinity)
        return chosen, self._reason_for(kind, mood, chosen)

    def _pool(self, now: dt.datetime, kind: str) -> list[str]:
        if self.is_weekend(now):
            hour = now.hour
            if hour >= 18:
                pool = self.schedule.get("weekend_evening_pool") or []
            else:
                pool = self.schedule.get("weekend_pool") or []
            reason_key = "weekend"
        else:
            pool = self.schedule.get("weekday_free_pool") or []
            reason_key = "weekday_free"
        pool = [s for s in pool if s in self.scenes]
        return pool or [s for s in self.scenes if self.scenes[s].get("free_weight")]

    def _weight(self, scene_id: str, mood: str, affinity: int) -> float:
        base = float((self.scenes.get(scene_id) or {}).get("free_weight") or 0.0)
        if base <= 0:
            return 0.0
        mood_w = float((self.mood_weights.get(mood) or {}).get(scene_id, 1.0))
        # 好感度修正：亲近 → 更愿意出现在外面；疏远 → 更愿意躲起来
        social = scene_id in ("park", "cafe", "rooftop", "station")
        if affinity >= 60:
            base *= 1.25 if social else 1.05
        elif affinity <= -20:
            base *= 0.70 if social else 1.20
        return max(0.0, base * mood_w)

    def _reason_for(self, kind: str, mood: str, scene_id: str) -> str:
        name = (self.scenes.get(scene_id) or {}).get("name_zh") or scene_id
        if kind == "free":
            return "%s。%s" % (FREE_REASONS.get("weekday_free", "自由时间"), "她选了%s" % name)
        return "%s" % SLOT_LABELS.get(kind, kind)

    # ---------------- 持久化 ----------------
    def _get_cached(self, key: str) -> str:
        if key in self._cache:
            return self._cache[key]
        if self.store is not None:
            try:
                v = self.store.get("world:" + key) or ""
                if v:
                    self._cache[key] = v
                    return v
            except Exception:  # noqa: BLE001
                pass
        return ""

    def _set_cached(self, key: str, value: str) -> None:
        self._cache[key] = value
        if self.store is not None:
            try:
                self.store.set("world:" + key, value)
                self._prune_store()
            except Exception:  # noqa: BLE001
                pass

    def _prune_store(self) -> None:
        """只保留最近 12 个时间片，避免 meta 表无限膨胀。"""
        try:
            keys = [k for k in self._cache if k.startswith("2")]
            for stale in sorted(keys)[:-12]:
                self._cache.pop(stale, None)
        except Exception:  # noqa: BLE001
            pass

    # ---------------- 调试 ----------------
    def day_plan(self, now: dt.datetime) -> list[dict]:
        out = []
        for s in self._slots(now):
            out.append({
                "start": s.get("start"), "end": s.get("end"),
                "kind": s.get("kind"), "scene": s.get("scene") or "(随机)",
            })
        return out


def _seed(key: str) -> int:
    """把「日期+时间片」变成稳定随机种子。"""
    h = 0
    for ch in key:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return h


def _to_min(text: str) -> int:
    try:
        hh, mm = str(text).split(":")[:2]
        return int(hh) * 60 + int(mm)
    except Exception:  # noqa: BLE001
        return 0


def describe_now(now: dt.datetime) -> str:
    return "%s %02d:%02d" % (WEEKDAYS[now.weekday()], now.hour, now.minute)


# ---------------------------------------------------------------- 渲染
def render_scene_block(scene: dict, *, max_chars: int = 500) -> str:
    """把场景渲染进 system prompt。

    只给她「环境 + 此刻在做什么 + 这个场景该怎么说话」三件事，
    不要写成详细的小说描写 —— 太长反而会压住她自己的台词。
    """
    if not scene:
        return ""
    parts = []
    name = scene.get("name_full") or scene.get("name_zh") or scene.get("id", "")
    parts.append(name)
    if scene.get("desc"):
        parts.append("环境：" + scene["desc"])
    if scene.get("activity"):
        parts.append("此刻：" + scene["activity"])
    if scene.get("voice"):
        parts.append("场景对说话方式的影响：" + scene["voice"])
    parts.append("（现在是 %s，%s）" % (
        scene.get("now_text", ""), scene.get("slot_label", "")))
    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def load_world(path: str | Path, *, store=None) -> World:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        log.warning("加载场景表失败（%s）：%r", p, exc)
        data = {}
    return World(data, store=store)
