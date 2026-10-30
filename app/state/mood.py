"""心情的衰减模型。

为什么需要
----------
如果只把 `<mood>` 存成一个字符串，那「上次聊天时她有点生气」会一直挂着，
三天后再说话她还在生气 —— 不像人。人的情绪会自己回落。

模型（参考同类项目的做法，参数按我们的使用频率调整）
------
· 每个情绪有一个强度，刚产生时 = 1.0
· 强度按指数衰减：intensity = 0.5 ** (age_h / half_life_h)
· 强度掉到 0.35 以下就回落到平静（calm）
· 半衰期分两档：
    - 持久心情（calm / soft / tired）：3 小时
    - 短暂情绪（anger / shy / tsundere）：90 分钟，来得快去得也快

这样「刚吵完架」和「昨天吵过架」是两种状态，不需要额外状态机。
"""

from __future__ import annotations

import math
import time

# 键 = 情绪名，值 = 半衰期（小时）
HALF_LIFE_H = {
    "calm": 3.0,
    "soft": 3.0,
    "tired": 3.0,
    "angry": 1.5,
    "shy": 1.5,
    "tsundere": 1.5,
}

DEFAULT_HALF_LIFE_H = 3.0
CALM_THRESHOLD = 0.35
KNOWN = tuple(HALF_LIFE_H.keys())


def half_life(mood: str) -> float:
    return HALF_LIFE_H.get(mood, DEFAULT_HALF_LIFE_H)


def decay(mood: str, set_at: float, *, now: float | None = None) -> dict:
    """算出某个情绪在 now 时刻的残余状态。

    返回 {"mood": 情绪名, "intensity": 强度0~1, "age_h": 小时, "expired": 是否已回落}
    """
    now = time.time() if now is None else now
    if mood not in KNOWN or mood == "calm":
        return {"mood": "calm", "intensity": 1.0 if mood == "calm" else 0.0,
                "age_h": 0.0, "expired": mood != "calm"}

    age_h = max(0.0, (now - float(set_at)) / 3600.0) if set_at else 999.0
    hl = half_life(mood)
    try:
        intensity = math.pow(0.5, age_h / hl)
    except (OverflowError, ValueError):
        intensity = 0.0

    expired = intensity < CALM_THRESHOLD
    return {
        "mood": "calm" if expired else mood,
        "intensity": round(0.0 if expired else intensity, 3),
        "age_h": round(age_h, 2),
        "expired": expired,
    }


def describe(state: dict) -> str:
    """给 system prompt 用的中文描述（让模型知道现在情绪有多强）。"""
    mood = state.get("mood") or "calm"
    if mood == "calm":
        return "平静"
    labels = {
        "angry": "有点恼火",
        "shy": "有点害羞",
        "tsundere": "嘴硬（不必当真）",
        "soft": "心情柔和",
        "tired": "有点累",
    }
    base = labels.get(mood, mood)
    i = float(state.get("intensity") or 0)
    if i >= 0.75:
        return base + "（很明显）"
    if i <= 0.45:
        return base + "（残留，快消退了）"
    return base


def blend(prev: dict, new_mood: str, *, now: float | None = None, set_at: float | None = None) -> dict:
    """新情绪覆盖旧情绪，但保留一点旧强度做平滑（避免一句话情绪跳变）。

    · 同一种情绪再次出现 → 强度回满
    · 不同情绪 → 新情绪强度 = max(0.6, 旧强度 * 0.5)，旧的按自身半衰期淡出
    """
    now = time.time() if now is None else now
    set_at = now if set_at is None else set_at
    new_mood = new_mood if new_mood in KNOWN else "calm"
    old = decay(prev.get("mood") or "calm",
                float(prev.get("mood_at") or 0.0), now=now)
    if new_mood == "calm":
        return {"mood": "calm", "intensity": 1.0, "mood_at": set_at}
    if new_mood == old.get("mood"):
        return {"mood": new_mood, "intensity": 1.0, "mood_at": set_at}
    intensity = max(0.6, float(old.get("intensity") or 0.0) * 0.5)
    return {"mood": new_mood, "intensity": round(min(1.0, intensity), 3), "mood_at": set_at}
