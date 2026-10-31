"""表达欲：根据心情决定这一轮「说」还是「打字」。

为什么需要它
------------
一直发语音会很吵（尤其 CPU 合成慢），一直打字又少了「她真的在说话」的感觉。
真人其实看心情：心情好的时候愿意开口，懒得理你的时候随手打几个字。

所以这里用「心情 → 发语音的概率」来做加权随机。同一条消息可能得到不同结果，
不是纯随机，而是「今天她心情如何」在起作用。

概率表（都是实测调出来的直觉值，可以直接改）
----------
  soft    心情柔和  0.75  愿意多说几句
  shy     害羞      0.80  越是害羞越不敢打字（笑）
  calm    平静      0.45  一半一半
  tsundere 嘴硬     0.30  嘴上嫌弃，懒得动嘴
  angry   生气      0.15  不想理你
  tired   累了      0.20  能打字就不错了

另外好感度会微调：好感越高越愿意开口（最多 +0.1）。

模式
----
  voice  合成一整段语音发出去，**念完之后再发文字**
  text   只发文字，不发语音

注意：voice 模式下语音是「整段一次说完」，不再按句拆成好几条 ——
按句拆会让听感一顿一顿（这个坑之前踩过，见 docs/拟人化设计.md）。
"""

from __future__ import annotations

import random

# 心情 -> 发语音的概率
VOICE_PROBABILITY = {
    "soft": 0.75,
    "shy": 0.80,
    "calm": 0.45,
    "tsundere": 0.30,
    "angry": 0.15,
    "tired": 0.20,
}
DEFAULT_PROBABILITY = 0.45

# 心情强度对概率的影响：情绪越强，偏离 0.5 越远
INTENSITY_GAIN = 0.25


def voice_probability(mood_state: dict | None, affinity: int = 0) -> float:
    """算这一轮发语音的概率（0~1）。"""
    state = mood_state or {}
    mood = (state.get("mood") or "calm").lower()
    base = VOICE_PROBABILITY.get(mood, DEFAULT_PROBABILITY)

    # 情绪越强越「本色」：概率朝它的极端再走一点
    try:
        intensity = float(state.get("intensity") or 0.0)
    except (TypeError, ValueError):
        intensity = 0.0
    if intensity > 0:
        base += (base - 0.5) * INTENSITY_GAIN * min(1.0, intensity)

    # 好感度微调：越亲近越愿意开口（最多 +0.10）
    try:
        aff = int(affinity)
    except (TypeError, ValueError):
        aff = 0
    base += max(0.0, min(1.0, (aff + 100) / 200.0)) * 0.10

    return max(0.0, min(1.0, base))


def choose_mode(mood_state: dict | None, affinity: int = 0, *,
                rng: random.Random | None = None,
                force: str | None = None) -> dict:
    """选这一轮的输出模式。

    返回 {"mode": "voice"|"text", "probability": float, "reason": str}
    force 可以是 "voice" / "text"（用于调试或配置指定）。
    """
    mood = ((mood_state or {}).get("mood") or "calm").lower()
    prob = voice_probability(mood_state, affinity)

    if force == "voice":
        return {"mode": "voice", "probability": prob, "reason": "强制语音"}
    if force == "text":
        return {"mode": "text", "probability": prob, "reason": "强制文字"}

    r = (rng or random).random()
    mode = "voice" if r < prob else "text"
    return {
        "mode": mode,
        "probability": round(prob, 3),
        "reason": "心情=%s 语音概率=%.0f%% 掷出=%.2f" % (mood, prob * 100, r),
    }
