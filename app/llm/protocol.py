"""输出协议解析器 —— 替代原来「`|` 分隔 + 正则切第一个括号」的脆弱做法。

协议（模型按此格式输出，可流式解析）::

    <ja>それはどうかしら。あなたがそう思うなら、そうなんでしょうね。</ja>
    <zh>那可不好说吧。既然你这么认为，那就这样吧。</zh>
    <act>合上书，微微挑眉</act>
    <mood>tsundere</mood>
    <aff>-2</aff>

为什么不用 JSON mode：
    JSON 必须等整个对象闭合才能用，与「边生成边播」冲突。
    标签协议在 `</ja>` 一出现就能取到台词，这是把首包延迟从 3 秒压到 1 秒的关键。

为什么比原来的正则稳：
    1. 逐标签增量扫描，不做全文正则替换，天然支持流式
    2. 多个 `<ja>` 块会按序拼接，不会互相覆盖
    3. 括号（中文/日文/英文）统一切除，不会把动作描写漏进 TTS
    4. 完全没标签时兜底为整段台词，不会静默丢内容
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---- 标签扫描 ----
_OPEN = re.compile(r"<([a-zA-Z_]+)>")
_CLOSE = re.compile(r"</([a-zA-Z_]+)>")
_TAGS = frozenset({"ja", "zh", "act", "mood", "aff", "sticker"})

# ---- 句子切分：只在真正的句末标点后切 ----
# 故意不把 `…` / `。` 里的例外情况算进去：省略号在日语里多半是句中停顿
# （`……そう。`），当成分隔符会把语气切碎，还会产生只有 `…` 的假句子
# —— 服务端会以「请输入有效文本」拒绝并断连。
_SENT_END = re.compile(r"(?<=[。．！？!?])")

# ---- 需要从日语台词里剔除的内容 ----
_BRACKETS = re.compile(
    r"[（(【\[〔][^）)】\]〕]*[）)】\]〕]"  # 中/日/英括号内的动作描写
)
_MARKUP = re.compile(r"[*#`>]+")
_SPACES = re.compile(r"[ \t\u3000]+")
# 句首残留的标点（省略号保留，它代表停顿）
_LEAD_PUNCT = re.compile(r"^[\s、。，．,.]\s*")
# 句末标点前的多余顿号/逗号：`なら、。` → `なら。`
_TAIL_DUP = re.compile(r"[、，,]\s*(?=[。．.！？!?])")
# 是否含有可发音的文字（假名或汉字）
_JA = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
# 假名（判断「是不是真的日语」—— 中文只有汉字，没有假名）
_KANA = re.compile(r"[\u3040-\u30ff]")

KNOWN_MOODS = ("calm", "tsundere", "soft", "angry", "shy", "tired")

# 表情包名字与情绪同名：一张情绪一张图，免得模型还要记两套词表。
# 图片在 app/ui/assets/stickers/<name>.png（由 expressions 立绘裁切而来）。
KNOWN_STICKERS = KNOWN_MOODS

# 模型可能写中文/日文名，归一化到英文键
_STICKER_ALIASES = {
    "冷静": "calm", "平静": "calm", "冷淡": "calm", "calm": "calm",
    "傲娇": "tsundere", "ツンデレ": "tsundere", "tsundere": "tsundere",
    "温柔": "soft", "柔和": "soft", "soft": "soft",
    "生气": "angry", "愤怒": "angry", "怒": "angry", "angry": "angry",
    "害羞": "shy", "腼腆": "shy", "shy": "shy",
    "疲惫": "tired", "累": "tired", "tired": "tired",
}


def looks_japanese(text: str) -> bool:
    """粗判是否真的是日语台词。

    本地小模型（qwen2:7b）偶尔会把 <ja> 写成中文。中文没有假名，
    所以「有假名」是最便宜的判据。纯汉字短语（如「猫」）会误判，
    因此只在句子足够长时才下结论。
    """
    if not text:
        return True
    if _KANA.search(text):
        return True
    return len(text) <= 6  # 太短的纯汉字句（「猫。」「そう」）不追究


def clean_for_tts(text: str) -> str:
    """把一句台词清理成适合送进 TTS 的纯文本。

    只保留要「念出来」的部分：去掉动作描写、markdown 痕迹、多余空白和句首垃圾标点。
    不做语言过滤 —— 交给 GPT-SoVITS 的 text_lang="ja" 处理。
    """
    if not text:
        return ""
    t = _BRACKETS.sub("", text)
    t = _MARKUP.sub("", t)
    t = _SPACES.sub(" ", t).strip()
    t = _LEAD_PUNCT.sub("", t)
    t = _TAIL_DUP.sub("", t)
    return t.strip()


def is_speakable(text: str) -> bool:
    """是否值得送去合成。

    过滤掉纯标点 / 纯空白 / 只剩符号的碎片 —— 否则 GPT-SoVITS 会返回
    「请输入有效文本」并直接断开连接（表现为客户端 IncompleteRead）。
    """
    return bool(text) and bool(_JA.search(text))


@dataclass
class Turn:
    """一轮回复的解析结果。"""

    ja: str = ""
    zh: str = ""
    act: str = ""
    mood: str = "calm"
    aff: int = 0
    # 可选表情包名（见 KNOWN_STICKERS）；空字符串表示这轮不发图
    sticker: str = ""
    raw: str = ""
    sentences: list[str] = field(default_factory=list)

    # ---- 展示用 ----
    def display(self, show_translation: bool = True) -> str:
        parts = [self.ja or self.raw.strip()]
        if self.act:
            parts.append(f"（{self.act}）")
        if show_translation and self.zh:
            parts.append(f"\n{self.zh}")
        return "".join(parts)

    @property
    def expression(self) -> str:
        return self.mood if self.mood in KNOWN_MOODS else "calm"

    def to_dict(self) -> dict:
        return {
            "ja": self.ja,
            "zh": self.zh,
            "act": self.act,
            "mood": self.mood,
            "aff": self.aff,
            "sticker": self.sticker,
        }


def _parse_affinity(raw: str) -> int:
    """`-2` / `+3` / `3` / `-2点` → int，解析不了就是 0。"""
    m = re.search(r"[-+]?\d+", raw or "")
    return int(m.group()) if m else 0


def _parse_sticker(raw: str) -> str:
    """把 `<sticker>` 内容归一化成 KNOWN_STICKERS 里的键；认不出就当没写。

    模型可能写英文键，也可能写「傲娇」「生气」这类中文；还可能带上多余标点。
    宁可静默丢弃，也不要把一个不存在的文件名传给发送端。
    """
    v = (raw or "").strip().lower().strip("。.、,，!！?？\"'`")
    if not v:
        return ""
    if v in KNOWN_STICKERS:
        return v
    if v in _STICKER_ALIASES:
        return _STICKER_ALIASES[v]
    # 内容里夹了别的字（`<sticker>用傲娇那张</sticker>`）时做一次包含匹配
    for alias, key in _STICKER_ALIASES.items():
        if alias and alias in v:
            return key
    for key in KNOWN_STICKERS:
        if key in v:
            return key
    return ""


def _parse_mood(raw: str) -> str:
    v = (raw or "").strip().lower()
    for mood in KNOWN_MOODS:
        if mood in v:
            return mood
    return "calm"


class ProtocolParser:
    """增量解析模型输出，流式喂入，流式产出可合成的句子。

    典型用法::

        p = ProtocolParser()
        for chunk in llm_stream():
            for sentence in p.feed(chunk):
                play(tts(sentence))
        for sentence in p.close():
            play(tts(sentence))
        turn = p.turn
    """

    def __init__(self) -> None:
        self.turn = Turn()
        self._buf = ""          # 尚未消费的原始缓冲
        self._ja_tail = ""      # 日语台词里还没成句的残段
        self._ja_parts: list[str] = []
        self._seen_any_tag = False

    # ---------- 主接口 ----------
    def feed(self, chunk: str) -> list[str]:
        """喂入流式片段，返回本次可以立刻送去 TTS 的新句子。"""
        if not chunk:
            return []
        self._buf += chunk
        self.turn.raw += chunk
        out: list[str] = []

        while True:
            m = _OPEN.search(self._buf)
            m_close = _CLOSE.search(self._buf)

            # 孤立闭合标签：模型偶尔只写 `……そう。</ja>` 而漏掉开标签
            # （本地小模型尤其常见，因为它会模仿历史里未加标签的格式）。
            # 这种情况把闭合标签之前的文本当作该标签内容，否则台词会被整段丢弃。
            if m_close and (not m or m_close.start() < m.start()):
                content = self._buf[: m_close.start()]
                name = m_close.group(1).lower()
                self._buf = self._buf[m_close.end():]
                if name in _TAGS:
                    out.extend(self._apply(name, content))
                continue

            if not m:
                # 没有标签了：只保留可能是半个标签的尾巴（`<ja`）
                cut = self._buf.rfind("<")
                if cut > 0:
                    self._buf = self._buf[cut:]
                elif cut < 0:
                    self._buf = ""
                break

            name = m.group(1).lower()
            if name not in _TAGS:
                # 未知标签：直接丢弃开标签，继续扫描
                self._buf = self._buf[m.end():]
                continue

            close_tok = f"</{name}>"
            end = self._buf.find(close_tok, m.end())
            if end < 0:
                # 闭合标签还没到，等下一次 feed
                self._buf = self._buf[m.start():]
                break

            content = self._buf[m.end():end]
            self._buf = self._buf[end + len(close_tok):]
            out.extend(self._apply(name, content))

        return out

    def close(self) -> list[str]:
        """输入结束，冲出残留句子。"""
        out: list[str] = []

        # 冲刷尚未成句的日语残段（同样要过滤纯标点碎片）
        tail = clean_for_tts(self._ja_tail)
        if is_speakable(tail):
            out.append(tail)
            self.turn.sentences.append(tail)
        self._ja_tail = ""
        self.turn.ja = clean_for_tts("".join(self._ja_parts))

        # 兜底：模型完全没用标签 —— 整段当台词，别静默丢内容
        if not self._seen_any_tag and not self.turn.ja:
            salvaged = clean_for_tts(self.turn.raw)
            if is_speakable(salvaged):
                self.turn.ja = salvaged
                self.turn.sentences.append(salvaged)
                out.append(salvaged)
            return out

        self._buf = ""
        return out

    # ---------- 内部 ----------
    def _apply(self, name: str, content: str) -> list[str]:
        self._seen_any_tag = True
        out: list[str] = []

        if name == "ja":
            text = content
            self._ja_parts.append(text)
            self._ja_tail += text
            out.extend(self._drain_sentences())
            self.turn.ja = clean_for_tts("".join(self._ja_parts))
        elif name == "zh":
            self.turn.zh = (self.turn.zh + content).strip() if self.turn.zh else content.strip()
        elif name == "act":
            self.turn.act = content.strip()
        elif name == "mood":
            self.turn.mood = _parse_mood(content)
        elif name == "aff":
            self.turn.aff = _parse_affinity(content)
        elif name == "sticker":
            parsed = _parse_sticker(content)
            if parsed:
                self.turn.sticker = parsed

        return out

    def _drain_sentences(self) -> list[str]:
        """从日语残段里切出完整句子。"""
        out: list[str] = []
        while True:
            parts = _SENT_END.split(self._ja_tail, maxsplit=1)
            if len(parts) < 2:
                break
            head, self._ja_tail = parts[0], parts[1]
            sentence = clean_for_tts(head)
            if is_speakable(sentence):
                self.turn.sentences.append(sentence)
                out.append(sentence)
        return out
