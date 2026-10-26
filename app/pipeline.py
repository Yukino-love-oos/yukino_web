"""对话流水线：用户输入 → LLM 流式 → 标签协议 → 分句 → TTS → 音频块。

这是「低延迟」的实现核心，三段并发流水：
    LLM 还在生成第 3 句时，第 1 句可能已经在合成、第 0 句已经在播了。

对比改造前的链路（全部串行、且 TTS 参数错误根本不通）：
    LLM 全文(2~4s) → TTS 整段(1~3s) → 才播放 = 首响 3~8s
现在：
    LLM 首句(~0.5s) + TTS 首句(mode3 ~0.4s) ≈ 首响 1s 量级
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator

from .config import Settings
from .llm.persona import Persona, render_system_prompt
from .llm.protocol import ProtocolParser, Turn, looks_japanese
from .state import memory
from .state.session import Session
from .tts.cache import AudioCache, fix_wav_header
from .tts.gsv import GsvClient, GsvError

log = logging.getLogger(__name__)


@dataclass
class Pipeline:
    settings: Settings
    persona: Persona
    session: Session
    llm: object
    tts: GsvClient
    cache: AudioCache

    # ---------------- TTS ----------------
    def _tts_params(self) -> dict:
        """影响音色的全部参数 —— 缓存键必须包含它们，否则切档会串音。"""
        return {
            "ref": str(self.tts.ref_audio),
            "prompt_text": self.tts.prompt_text,
            "prompt_lang": self.tts.prompt_lang,
            "text_lang": self.tts.text_lang,
            "top_k": self.tts.top_k,
            "top_p": self.tts.top_p,
            "temperature": self.tts.temperature,
            "speed_factor": self.tts.speed_factor,
            "repetition_penalty": self.tts.repetition_penalty,
        }

    def speak(self, sentence: str, *, first: bool = False) -> bytes | None:
        """把一句日语合成成 WAV 字节。失败返回 None（降级为纯文字，不中断对话）。"""
        if not sentence.strip():
            return None

        params = self._tts_params()
        if self.settings.use_tts_cache:
            hit = self.cache.get(sentence, params)
            if hit:
                log.debug("TTS 缓存命中：%s", sentence)
                return hit

        data: bytes | None = None
        try:
            if self.settings.tts_stream:
                level = (
                    self.settings.tts_first_stream_mode
                    if first
                    else self.settings.tts_stream_mode
                )
                chunks = list(
                    self.tts.synth_stream(sentence, media_type="wav", streaming_mode=str(level))
                )
                raw = b"".join(chunks)
                data = fix_wav_header(raw) if raw else None
            else:
                data = self.tts.synth(sentence)
        except GsvError as e:
            log.warning("TTS 合成失败，降级为纯文字：%s", e)
            return None
        except Exception as e:  # 兜底：语音挂了也不能让对话挂掉
            log.warning("TTS 未预期错误，降级为纯文字：%r", e)
            return None

        if data and self.settings.use_tts_cache:
            self.cache.put(sentence, params, data)
        return data

    # ---------------- 组 prompt ----------------
    def build_messages(self, user_text: str) -> list[dict]:
        hits = memory.recall(self.session.conn, user_text, k=3)
        sys_prompt = render_system_prompt(
            self.persona,
            affinity=self.session.affinity,
            memory_block=memory.build_memory_block(hits),
        )
        msgs = [{"role": "system", "content": sys_prompt}]

        # few-shot：小模型（如本地 qwen2:7b）对标签协议的遵循度很差，
        # 实测 3 次里只有 1 次能把 5 个标签写全。给 2~3 个完整示例后明显改善。
        for ex in self.persona.few_shot:
            u = (ex or {}).get("user")
            a = (ex or {}).get("assistant")
            if u and a:
                msgs.append({"role": "user", "content": u})
                msgs.append({"role": "assistant", "content": a})

        msgs.extend(self.session.history_messages(self.settings.history_turns))
        msgs.append({"role": "user", "content": user_text})
        return msgs

    # ---------------- 主流程 ----------------
    def respond(self, user_text: str) -> Iterator[dict]:
        """产出事件流。

        事件类型：
          {"type": "sentence", "text": str, "audio": bytes|None, "index": int}
          {"type": "done", "turn": Turn, "delta": int, "reason": str,
                          "affinity": int, "stats": dict}
          {"type": "error", "message": str}
        """
        parser = ProtocolParser()
        index = 0

        try:
            messages = self.build_messages(user_text)
        except Exception as e:
            yield {"type": "error", "message": f"构造请求失败：{e}"}
            return

        try:
            stream = (
                self.llm.stream(messages)
                if self.settings.llm_stream
                else iter([self.llm.complete(messages)])
            )
            for chunk in stream:
                for sentence in parser.feed(chunk):
                    audio = self.speak(sentence, first=(index == 0))
                    yield {"type": "sentence", "text": sentence, "audio": audio, "index": index}
                    index += 1
            for sentence in parser.close():
                audio = self.speak(sentence, first=(index == 0))
                yield {"type": "sentence", "text": sentence, "audio": audio, "index": index}
                index += 1
        except Exception as e:
            log.exception("LLM 流式生成失败")
            yield {"type": "error", "message": f"生成失败：{e}"}

        turn: Turn = parser.turn

        # 本地小模型偶尔会把 <ja> 写成中文。中文没有假名，用这点做廉价告警。
        if turn.ja and not looks_japanese(turn.ja):
            log.warning("台词语言异常（疑似中文混入 <ja>）：%s", turn.ja)
            yield {"type": "warn", "message": "台词疑似不是日语，可能影响发音"}

        delta, reason = self.session.apply_affinity(turn.aff, user_text)
        try:
            self.session.record_turn(user_text, turn, delta)
        except Exception as e:
            log.warning("写入对话记录失败：%r", e)

        yield {
            "type": "done",
            "turn": turn,
            "delta": delta,
            "reason": reason,
            "affinity": self.session.affinity,
            "stats": self.session.stats(),
        }

    # ---------------- 语气档位 ----------------
    def voice_for_affinity(self, affinity: int) -> str:
        """好感度高用 strong，低用 weak（切换有加载开销，调用方自行判断是否真需要切）。"""
        return "strong" if affinity >= -19 else "weak"


def build_pipeline(settings: Settings) -> Pipeline:
    """按配置装配整条流水线。"""
    from .llm.client import build_client
    from .llm.persona import load_persona
    from .state import db
    from .tts.gsv import from_settings

    persona = load_persona(settings.persona_path)
    conn = db.connect(settings.db_path)
    db.init_schema(conn)
    session = Session(conn, persona)
    llm = build_client(settings)
    tts = from_settings(settings)
    cache = AudioCache(settings.tts_cache_dir, enabled=settings.use_tts_cache)
    return Pipeline(settings, persona, session, llm, tts, cache)
