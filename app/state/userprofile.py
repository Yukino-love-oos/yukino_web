"""用户档案：她「知道」对面这个人是谁。

为什么单独做一个文件
--------------------
人格卡描述的是「她是谁」，用户档案描述的是「她在跟谁说话」。
两者混在一起会让 prompt 越来越乱，而且换一个用户就得改人格卡。

结构
----
  name/age/school/grade   基本信息（手写）
  likes                   明确说过的喜好（手写）
  notes                   关系设定（手写，优先级高）
  facts                   自动追加的事实（由记忆写回写入，最多 40 条）

自动追加的节奏
--------------
记忆写回（app/memsum.py）提取到关于用户的稳定事实时，会顺手调用
`append_facts()` 把新事实写进这里。所以聊得越久，她的「人物档案」越完整。
先到先得：同样的内容不会写第二次；超过上限就丢最早的。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

MAX_FACTS = 40


def load(path: str | Path) -> dict:
    """读用户档案；文件不存在或损坏都返回空 dict（不能让档案把对话搞崩）。"""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save(path: str | Path, profile: dict) -> bool:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError as exc:
        log.warning("写用户档案失败：%r", exc)
        return False


def render_block(profile: dict | None, *, max_chars: int = 700) -> str:
    """渲染成 system prompt 里的一段。"""
    if not profile:
        return ""
    parts: list[str] = []

    head = []
    for key, label in (("name", "名字"), ("age", "年龄"), ("school", "学校"), ("grade", "年级")):
        val = profile.get(key)
        if val:
            head.append("%s：%s" % (label, val))
    if head:
        parts.append("；".join(head))

    likes = [x for x in (profile.get("likes") or []) if x]
    if likes:
        parts.append("明确喜欢：%s" % "、".join(likes))

    notes = [x for x in (profile.get("notes") or []) if x]
    if notes:
        parts.append("【关系】\n" + "\n".join("  · " + x for x in notes))

    facts = [x for x in (profile.get("facts") or []) if x]
    if facts:
        parts.append("【你记得关于他的事】\n" + "\n".join("  · " + x for x in facts))

    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n  …（其余略）"
    return text


def append_facts(path: str | Path, facts: list[str], *, max_facts: int = MAX_FACTS) -> list[str]:
    """把新事实追加进档案。返回真正新增的条目。"""
    profile = load(path)
    if not profile:
        profile = {"facts": []}
    existing = list(profile.get("facts") or [])
    keyed = {x.strip() for x in existing}
    added: list[str] = []
    for raw in facts or []:
        text = (raw or "").strip()
        if not text or text in keyed:
            continue
        existing.append(text)
        keyed.add(text)
        added.append(text)
    if not added:
        return []
    # 超出上限丢最早的
    if len(existing) > max_facts:
        existing = existing[-max_facts:]
    profile["facts"] = existing
    save(path, profile)
    return added
