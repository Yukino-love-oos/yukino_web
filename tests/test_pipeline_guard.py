"""语言守卫测试：模型把 <ja> 写成中文时，应自动重试一次并改用日语结果。

用假 LLM / 假 TTS，不依赖网络、不占显存、秒级完成。
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.llm.persona import load_persona  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402
from app.state import db  # noqa: E402
from app.state.session import Session  # noqa: E402
from app.tts.cache import AudioCache  # noqa: E402

CHINESE_TURN = (
    "<ja>最近在读的书是「存在的哲学」。我喜欢探索深邃的思想。</ja>\n"
    "<zh>最近在读的书是「存在的哲学」。</zh>\n"
    "<act>微微一笑</act>\n"
    "<mood>calm</mood>\n"
    "<aff>1</aff>"
)

JAPANESE_TURN = (
    "<ja>最近読んでいる本は「存在の哲学」よ。深い思想を探るのが好きなの。</ja>\n"
    "<zh>最近在读的书是「存在的哲学」。我喜欢探索深邃的思想。</zh>\n"
    "<act>微微一笑</act>\n"
    "<mood>calm</mood>\n"
    "<aff>1</aff>"
)


class FakeLLM:
    provider = "fake"
    model = "fake"

    def __init__(self, first: str, second: str | None = None) -> None:
        self.first = first
        self.second = second
        self.calls = 0
        self.repair_prompts: list[str] = []

    def stream(self, messages):
        self.calls += 1
        for i in range(0, len(self.first), 7):
            yield self.first[i: i + 7]

    def complete(self, messages):
        self.calls += 1
        self.repair_prompts.append(messages[-1]["content"])
        return self.second or ""


class FakeTTS:
    """只要方法签名对、返回非空字节即可。"""

    ref_audio = pathlib.Path("fake.wav")
    prompt_text = "p"
    prompt_lang = "ja"
    text_lang = "ja"
    top_k = 5
    top_p = 1.0
    temperature = 1.0
    speed_factor = 1.0
    repetition_penalty = 1.35

    def synth(self, text, **kwargs):
        return b"RIFF" + b"\x00" * 100

    def synth_stream(self, text, **kwargs):
        yield b"RIFF" + b"\x00" * 100


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(pathlib.Path(self.tmp.name) / "t.db")
        db.init_schema(self.conn)
        settings = dataclasses.replace(
            load_settings(),
            use_tts_cache=False,
            tts_stream=False,
            repair_ja=True,
        )
        self.settings = settings
        persona = load_persona(ROOT / "persona" / "yukino.json")
        self.session = Session(self.conn, persona)
        self.persona = persona

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _run(self, llm):
        pipeline = Pipeline(
            self.settings,
            self.persona,
            self.session,
            llm,
            FakeTTS(),
            AudioCache(pathlib.Path(self.tmp.name) / "cache", enabled=False),
        )
        return list(pipeline.respond("最近在读什么书"))

    def test_chinese_ja_triggers_repair(self):
        llm = FakeLLM(CHINESE_TURN, JAPANESE_TURN)
        events = self._run(llm)

        types = [e["type"] for e in events]
        self.assertIn("info", types, "应提示正在重写")

        done = [e for e in events if e["type"] == "done"][0]
        self.assertIn("存在", done["turn"].ja)
        self.assertTrue(
            any("\u3040" <= ch <= "\u30ff" for ch in done["turn"].ja),
            f"重写后应是日语（含假名），实际: {done['turn'].ja}",
        )
        self.assertEqual(llm.calls, 2, "应恰好重试一次")

    def test_repair_prompt_mentions_japanese_only(self):
        llm = FakeLLM(CHINESE_TURN, JAPANESE_TURN)
        self._run(llm)
        self.assertIn("日本語", llm.repair_prompts[0])

    def test_no_chinese_sent_to_tts(self):
        """中文台词不能送去合成，否则出声是乱码。"""
        llm = FakeLLM(CHINESE_TURN, JAPANESE_TURN)
        events = self._run(llm)
        sentence_events = [e for e in events if e["type"] == "sentence"]
        self.assertTrue(sentence_events)
        for ev in sentence_events:
            self.assertTrue(
                any("\u3040" <= ch <= "\u30ff" for ch in ev["text"]),
                f"送去 TTS 的句子必须含假名，实际: {ev['text']}",
            )

    def test_japanese_first_time_no_extra_call(self):
        llm = FakeLLM(JAPANESE_TURN, None)
        events = self._run(llm)
        types = [e["type"] for e in events]
        self.assertNotIn("info", types)
        self.assertEqual(llm.calls, 1, "本来就是日语就不该重试")

    def test_repair_failure_falls_back_with_warning(self):
        llm = FakeLLM(CHINESE_TURN, CHINESE_TURN)
        events = self._run(llm)
        types = [e["type"] for e in events]
        self.assertIn("warn", types)
        self.assertTrue([e for e in events if e["type"] == "done"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
