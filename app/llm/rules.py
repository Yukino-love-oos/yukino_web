"""人格的三层规则（参考同类项目的做法）。

为什么要分层
------------
单一人格卡有个问题：想加一条「主人不喜欢太长的语音」，要么改人格卡（容易越改越乱），
要么让模型自己记（记不住）。分成三层后各归各位：

  L1 核心人格   persona/yukino.json      谁都不该随手改，决定她是谁
  L2 主人附加   persona/rules/owner.md   主人维护的补充规矩，优先级低于核心
  L3 相处偏好   persona/rules/self.md    她自己可以追加，但受限：≤40 行、带日期、要先问过主人

注入顺序就是上面的顺序，冲突时以核心为准（模板里也写明了）。

为什么 L3 要限制
----------------
「让模型改自己的提示词」很容易失控：几轮之后规则膨胀成几百行，
把人格挤没了。所以强制三条：行数上限、每行带日期、必须显式授权才能写。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

MAX_SELF_LINES = 40
MAX_OWNER_CHARS = 2000
MAX_SELF_CHARS = 1200


def rules_dir(persona_path: Path) -> Path:
    """规则文件放在人格卡同级的 rules/ 目录下。"""
    return Path(persona_path).resolve().parent / "rules"


def _read(path: Path, *, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n  …（超出长度上限，已截断）"
    return text


def load_layers(persona_path: Path) -> dict[str, str]:
    """读取 L2/L3 两层文本。缺失就是空字符串，不报错。"""
    d = rules_dir(persona_path)
    return {
        "owner": _read(d / "owner.md", max_chars=MAX_OWNER_CHARS),
        "self": _read(d / "self.md", max_chars=MAX_SELF_CHARS),
    }


def render(layers: dict[str, str]) -> str:
    """渲染成 system prompt 里的一段。两层都空就返回空串（不留标题噪音）。"""
    parts: list[str] = []
    owner = (layers or {}).get("owner", "").strip()
    self_rules = (layers or {}).get("self", "").strip()
    if owner:
        parts.append("【主人补充的规矩】\n" + owner)
    if self_rules:
        parts.append("【她自己整理的相处偏好】\n" + self_rules)
    return "\n\n".join(parts)


def append_self_rule(persona_path: Path, rule: str, *, allowed: bool) -> tuple[bool, str]:
    """往 L3 追加一条相处偏好。

    返回 (是否写入, 说明)。调用方必须显式传 allowed=True ——
    对应「必须先征得主人同意」这条约定，不要绕过。
    """
    rule = (rule or "").strip()
    if not rule:
        return False, "内容为空"
    if not allowed:
        return False, "未获得主人同意，不能修改自己的规矩"
    if len(rule) > 120:
        return False, "单条太长（上限 120 字）"

    d = rules_dir(persona_path)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "self.md"
    existing = _read(path, max_chars=MAX_SELF_CHARS)
    lines = [ln for ln in existing.splitlines() if ln.strip()]
    if len(lines) >= MAX_SELF_LINES:
        return False, f"已经到 {MAX_SELF_LINES} 行上限，先删掉过时的再写"
    if any(rule in ln for ln in lines):
        return False, "这条已经有了"

    stamp = _dt.date.today().isoformat()
    new_line = f"- [{stamp}] {rule}"
    body = "\n".join(lines + [new_line]) + "\n"
    path.write_text(body, encoding="utf-8")
    return True, f"已记下：{rule}"
