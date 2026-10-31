"""艾宾浩斯遗忘曲线 + 多层记忆。

为什么照这个模型做
------------------
直接把所有对话塞进 prompt 会撑爆；按关键词捞又会「三年前的闲话和昨天的偏好同权」。
艾宾浩斯曲线解决的是「**该忘的忘掉、常用的记牢**」：

    R(t) = exp(-t / S)

  R = 当前保持率（0~1）
  t = 距上次被想起的天数
  S = 记忆稳定度（天）：越常被想起，S 越大，忘得越慢

每被召回一次就做一次「复习」（spaced repetition）：

    S' = min(S_MAX, S * 1.6)
    review_count += 1

复习到第 3 次，这条记忆升格为**长期记忆**（layer=long），稳定度直接抬到 60 天以上。
R 掉到 0.15 以下就不再注入 prompt —— 相当于「想不起来了」，但**不删库**，
以后被提到还能复活。

三层结构
--------
  working  工作记忆：最近几轮对话原文（turns 表，不进 memory 表）
  short    短期记忆：刚总结出来的事件/摘要，稳定度低，容易淡忘
  long     长期记忆：反复被想起的事实/偏好，稳定度高

这样她既不会忘掉「你讨厌下雨」，也不会把三个月前的一句闲话一直挂在嘴边。
"""

from __future__ import annotations

import datetime as _dt
import math

# 记忆层的权重：长期记忆在召回时更优先
LAYER_WEIGHT = {
    "long": 1.25,
    "short": 1.0,
}

# 各类型记忆的初始稳定度（天）——越"硬"的事实忘得越慢
INITIAL_STABILITY = {
    "fact": 90.0,
    "preference": 60.0,
    "event": 20.0,
    "summary": 10.0,
    "mood": 7.0,
}
DEFAULT_STABILITY = 14.0

# 每次成功召回，稳定度乘以这个系数
REVIEW_BOOST = 1.6
# 稳定度上限（天）
MAX_STABILITY = 365.0
# 复习多少次之后升格为长期记忆
PROMOTE_AFTER_REVIEWS = 3
# 低于这个保持率就不再注入（已淡忘，但不删）
DORMANT_THRESHOLD = 0.15


def initial_stability(kind: str) -> float:
    return INITIAL_STABILITY.get((kind or "").lower(), DEFAULT_STABILITY)


def retention(age_days: float, stability: float) -> float:
    """当前保持率 R = exp(-t/S)。"""
    try:
        t = max(0.0, float(age_days))
        s = float(stability)
    except (TypeError, ValueError):
        return 0.0
    if s <= 0:
        return 0.0
    try:
        return max(0.0, min(1.0, math.exp(-t / s)))
    except OverflowError:
        return 0.0


def days_since(ts: str, *, now: _dt.datetime | None = None) -> float:
    now = now or _dt.datetime.now()
    try:
        t = _dt.datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return 365.0
    if t.tzinfo is not None:
        t = t.replace(tzinfo=None)
    return max(0.0, (now - t).total_seconds() / 86400.0)


def after_review(stability: float, review_count: int) -> dict:
    """复习一次之后的新状态。"""
    try:
        s = float(stability)
    except (TypeError, ValueError):
        s = DEFAULT_STABILITY
    try:
        n = int(review_count)
    except (TypeError, ValueError):
        n = 0
    new_s = min(MAX_STABILITY, max(s, 1.0) * REVIEW_BOOST)
    new_n = n + 1
    layer = "long" if new_n >= PROMOTE_AFTER_REVIEWS else "short"
    if layer == "long":
        new_s = max(new_s, 60.0)
    return {"stability": round(new_s, 2), "review_count": new_n, "layer": layer}


def is_dormant(age_days: float, stability: float) -> bool:
    return retention(age_days, stability) < DORMANT_THRESHOLD


def describe(age_days: float, stability: float, layer: str = "short") -> str:
    """调试用：打印一条记忆的当前状态。"""
    r = retention(age_days, stability)
    return "layer=%s R=%.2f age=%.1fd S=%.1fd %s" % (
        layer, r, age_days, stability,
        "（已淡忘）" if r < DORMANT_THRESHOLD else "",
    )
