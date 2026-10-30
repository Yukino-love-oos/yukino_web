"""人格卡加载与 system prompt 渲染。

人格卡用 JSON 而不是 YAML：核心链路只依赖标准库，而标准库没有 YAML 解析器。
多行文本在 JSON 里用 `\\n` 转义，编辑体验略差，但换来「装不上任何包也能跑」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_LANG_NAMES = {"ja": "日语", "zh": "中文", "en": "英语"}


@dataclass
class Persona:
    name: str
    name_ja: str
    identity: str
    speech: dict = field(default_factory=dict)
    taboos: list[str] = field(default_factory=list)
    moods: list[str] = field(default_factory=list)
    affinity: dict = field(default_factory=dict)
    greetings: list[str] = field(default_factory=list)
    few_shot: list[dict] = field(default_factory=list)
    system_template: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    # ---------- 好感度分段 ----------
    def band(self, affinity: int) -> dict:
        for b in self.affinity.get("bands", []):
            if b["min"] <= affinity <= b["max"]:
                return b
        return {"label": "平常", "tone": "标准的清冷语气", "min": -100, "max": 100}

    @property
    def per_turn_max(self) -> int:
        return int(self.affinity.get("per_turn_max", 3))

    @property
    def daily_gain_cap(self) -> int:
        return int(self.affinity.get("daily_gain_cap", 10))

    @property
    def anti_farming(self) -> dict:
        return self.affinity.get("anti_farming", {})

    @property
    def languages(self) -> list[str]:
        return self.affinity.get("languages", [])  # 兼容占位


def load_persona(path: Path) -> Persona:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return Persona(
        name=data.get("name", "雪之下雪乃"),
        name_ja=data.get("name_ja", "雪ノ下雪乃"),
        identity=data.get("identity", ""),
        speech=data.get("speech", {}),
        taboos=list(data.get("taboos", [])),
        moods=list(data.get("moods", [])),
        affinity=data.get("affinity", {}),
        greetings=list(data.get("greetings", [])),
        few_shot=list(data.get("few_shot", [])),
        system_template=data.get("system_template", ""),
        raw=data,
    )


def _bullets(items: list[str], indent: str = "  ") -> str:
    return "".join(f"{indent}- {x}\n" for x in items) if items else f"{indent}-（无）\n"


def render_system_prompt(
    persona: Persona,
    *,
    affinity: int,
    memory_block: str = "",
    mood_hint: str = "",
    extra_rules: str = "",
) -> str:
    """渲染 system prompt。所有可变部分都由外部注入，人格卡里不写死数值。

    extra_rules 是三层人格里的 L2/L3（主人补充 + 她自己的相处偏好），
    由 app/llm/rules.py 读出来；没有就是空字符串，不占位置。
    """
    band = persona.band(affinity)
    speech = persona.speech or {}

    mem = ""
    if memory_block.strip():
        mem = "\n【你记得的事】\n" + memory_block.strip() + "\n"

    rules = ""
    if (extra_rules or "").strip():
        rules = "\n" + extra_rules.strip() + "\n"

    return persona.system_template.format(
        identity=persona.identity,
        extra_rules=rules,
        affinity=affinity,
        band_label=band.get("label", ""),
        band_tone=band.get("tone", ""),
        mood_hint=mood_hint or "平静",
        memory_block=mem,
        language_name=_LANG_NAMES.get(speech.get("language", "ja"), "日语"),
        style=speech.get("style", ""),
        notes=_bullets(speech.get("notes", [])),
        taboos=_bullets(persona.taboos),
        per_turn_max=persona.per_turn_max,
        gains=_bullets(persona.affinity.get("gains", [])),
        losses=_bullets(persona.affinity.get("losses", [])),
        moods="/".join(persona.moods) or "calm",
    )
