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
from .llm import lifecycle, rules
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
    def _lifeview(self):
        """时间轮：按今天的日期现算年龄，并把人生经历压成一段文本。

        年龄不写死，是因为写死「18 岁」一年后就露馅了；
        这里每次请求都按生日重新算，过了生日自动加一岁。
        """
        life = lifecycle.Life.from_dict(getattr(self.persona, "life", None))
        if life is None:
            return {}, "", ""
        snap = life.snapshot()
        return snap, lifecycle.render_age_hint(snap), lifecycle.render_life_block(self.persona.life)

    def build_messages(self, user_text: str) -> list[dict]:
        # 记忆召回：关键词相关度 × 新鲜度（半衰期 30 天）× 类型重要度
        hits = memory.recall(self.session.conn, user_text, k=self.settings.memory_k)
        # 三层人格：L1 = 人格卡；L2/L3 = persona/rules/{owner,self}.md
        layers = rules.load_layers(self.settings.persona_path)
        snap, age_hint, life_block = self._lifeview()
        sys_prompt = render_system_prompt(
            self.persona,
            affinity=self.session.affinity,
            age_hint=age_hint,
            life_block=life_block,
            memory_block=memory.build_memory_block(hits, max_chars=self.settings.memory_max_chars),
            # 心情带衰减：3 小时没聊，愤怒/害羞会自己回落，不会一直挂着脸
            mood_hint=self.session.mood_hint(),
            extra_rules=rules.render(layers),
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

    # ---------------- 语言守卫 ----------------
    def repair_language(self, user_text: str, bad: Turn) -> Turn | None:
        """台词不是日语时，带纠正指令重跑一次。

        为什么必须有：本地 qwen2:7b 经常把 <ja> 写成中文。
        GPT-SoVITS 用 text_lang="ja" 去念中文会输出乱码音，比没声音更糟。
        """
        try:
            msgs = self.build_messages(user_text)
            msgs.append(
                {
                    "role": "user",
                    "content": (
                        "前回の返答は中国語になっていた。やり直して。\n"
                        "厳守：<ja> の中は**日本語だけ**。中国語の文字を一文字も入れない。"
                        "翻訳は <zh>、動作は <act> に書く。"
                    ),
                }
            )
            raw = self.llm.complete(msgs)
            parser = ProtocolParser()
            for ch in raw:
                parser.feed(ch)
            parser.close()
            fixed = parser.turn
            if fixed.ja and looks_japanese(fixed.ja):
                log.info("语言守卫：重试后得到日语台词")
                return fixed
            log.warning("语言守卫：重试后仍非日语（%s）", fixed.ja[:40])
        except Exception as e:
            log.warning("语言守卫重试失败：%r", e)
        return None

    # ---------------- 主流程 ----------------
    def respond(self, user_text: str, *, speak: bool = True) -> Iterator[dict]:
        """产出事件流。

        事件类型：
          {"type": "sentence", "text": str, "audio": bytes|None, "index": int}
          {"type": "done", "turn": Turn, "delta": int, "reason": str,
                          "affinity": int, "stats": dict}
          {"type": "error", "message": str}

        speak=False 时只走文本、完全跳过 TTS（audio 恒为 None）。
        QQ/微信这类「先秒回文字、语音后补」的入口需要这个开关：
        CPU 服务器上 TTS 是 RT 6~7 倍，若边生成边合成，文字要等一分钟才发得出去。
        """
        parser = ProtocolParser()
        index = 0
        lang_bad = False

        try:
            messages = self.build_messages(user_text)
        except Exception as e:
            yield {"type": "error", "message": f"构造请求失败：{e}"}
            return

        def emit(sentence: str) -> dict | None:
            """对一句话：是日语就合成，不是就别送去 TTS（否则会出乱码音）。"""
            nonlocal index, lang_bad
            if not looks_japanese(sentence):
                lang_bad = True
                log.warning("跳过非日语台词，不做语音合成：%s", sentence[:40])
                return None
            audio = self.speak(sentence, first=(index == 0)) if speak else None
            ev = {"type": "sentence", "text": sentence, "audio": audio, "index": index}
            index += 1
            return ev

        try:
            stream = (
                self.llm.stream(messages)
                if self.settings.llm_stream
                else iter([self.llm.complete(messages)])
            )
            for chunk in stream:
                for sentence in parser.feed(chunk):
                    ev = emit(sentence)
                    if ev:
                        yield ev
            for sentence in parser.close():
                ev = emit(sentence)
                if ev:
                    yield ev
        except Exception as e:
            log.exception("LLM 流式生成失败")
            yield {"type": "error", "message": f"生成失败：{e}"}

        turn: Turn = parser.turn

        # ---- 语言守卫：本地小模型把 <ja> 写成中文时，带纠正指令重试一次 ----
        if lang_bad and self.settings.repair_ja:
            yield {"type": "info", "message": "台词不是日语，正在让模型重写…"}
            fixed = self.repair_language(user_text, turn)
            if fixed is not None:
                turn = fixed
                for sentence in turn.sentences:
                    audio = self.speak(sentence, first=(index == 0)) if speak else None
                    yield {
                        "type": "sentence",
                        "text": sentence,
                        "audio": audio,
                        "index": index,
                    }
                    index += 1
            else:
                yield {"type": "warn", "message": "重写后仍不是日语，本次可能没有声音"}

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
