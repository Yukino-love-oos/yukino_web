"""标签协议解析器单元测试 —— 只用标准库 unittest。

运行：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.llm.protocol import ProtocolParser, clean_for_tts, is_speakable  # noqa: E402


FULL = (
    "<ja>それはどうかしら。あなたがそう思うなら、そうなんでしょうね。</ja>\n"
    "<zh>那可不好说吧。既然你这么认为，那就这样吧。</zh>\n"
    "<act>合上书，微微挑眉</act>\n"
    "<mood>tsundere</mood>\n"
    "<aff>-2</aff>"
)


class TestSentenceSplitting(unittest.TestCase):
    def test_sentences_emitted_progressively(self):
        p = ProtocolParser()
        got: list[str] = []
        for ch in FULL:
            got.extend(p.feed(ch))
        got.extend(p.close())
        self.assertEqual(
            got,
            ["それはどうかしら。", "あなたがそう思うなら、そうなんでしょうね。"],
        )

    def test_tag_split_across_chunks(self):
        """标签被切成两半也必须能拼回来。"""
        p = ProtocolParser()
        out: list[str] = []
        pieces = ["<j", "a>そう", "。", "</j", "a>", "<aff>-", "1</aff>"]
        for piece in pieces:
            out.extend(p.feed(piece))
        out.extend(p.close())
        self.assertEqual(out, ["そう。"])
        self.assertEqual(p.turn.ja, "そう。")
        self.assertEqual(p.turn.aff, -1)

    def test_multiple_ja_blocks_concatenate(self):
        p = ProtocolParser()
        out: list[str] = []
        out.extend(p.feed("<ja>まず一つ目。</ja>"))
        out.extend(p.feed("<ja>次に二つ目。</ja>"))
        out.extend(p.close())
        self.assertEqual(out, ["まず一つ目。", "次に二つ目。"])
        self.assertEqual(p.turn.ja, "まず一つ目。次に二つ目。")

    def test_tail_flushed_on_close(self):
        """最后一句没有句末标点也要在 close() 时吐出，不能丢。"""
        p = ProtocolParser()
        out = p.feed("<ja>句点のない台詞</ja>")
        self.assertEqual(out, [])
        out.extend(p.close())
        self.assertEqual(out, ["句点のない台詞"])

    def test_exclamation_and_question(self):
        p = ProtocolParser()
        out = p.feed("<ja>本当に？まさか！そうなの。</ja>")
        out.extend(p.close())
        self.assertEqual(out, ["本当に？", "まさか！", "そうなの。"])


class TestFields(unittest.TestCase):
    def test_all_fields_parsed(self):
        p = ProtocolParser()
        p.feed(FULL)
        p.close()
        self.assertEqual(p.turn.mood, "tsundere")
        self.assertEqual(p.turn.aff, -2)
        self.assertEqual(p.turn.act, "合上书，微微挑眉")
        self.assertIn("那可不好说吧", p.turn.zh)

    def test_positive_affinity(self):
        p = ProtocolParser()
        p.feed("<aff>+3</aff>")
        p.close()
        self.assertEqual(p.turn.aff, 3)

    def test_affinity_garbage_is_zero(self):
        p = ProtocolParser()
        p.feed("<aff>なし</aff>")
        p.close()
        self.assertEqual(p.turn.aff, 0)

    def test_unknown_mood_falls_back_to_calm(self):
        p = ProtocolParser()
        p.feed("<mood>超絶不機嫌</mood>")
        p.close()
        self.assertEqual(p.turn.mood, "calm")

    def test_mood_substring_match(self):
        p = ProtocolParser()
        p.feed("<mood>有点shy</mood>")
        p.close()
        self.assertEqual(p.turn.mood, "shy")


class TestCleanForTts(unittest.TestCase):
    def test_removes_bracketed_action(self):
        self.assertEqual(clean_for_tts("それは（小声で）どうかしら。"), "それはどうかしら。")
        self.assertEqual(clean_for_tts("それは(whisper)どうかしら。"), "それはどうかしら。")
        self.assertEqual(clean_for_tts("それは【注】どうかしら。"), "それはどうかしら。")

    def test_action_never_leaks_into_tts(self):
        """原实现的 bug：只切第一个括号，后续内容会漏进 TTS。"""
        p = ProtocolParser()
        out = p.feed("<ja>……そう。(間)(もっと)(余計なこと言わないで)</ja>")
        out.extend(p.close())
        self.assertEqual("".join(out), "……そう。")

    def test_keeps_quotes_and_normal_punctuation(self):
        self.assertEqual(clean_for_tts("「そう」って言ったのよ。"), "「そう」って言ったのよ。")

    def test_strips_markdown_noise(self):
        self.assertEqual(clean_for_tts("**強調**したわ"), "強調したわ")


class TestFallback(unittest.TestCase):
    def test_untagged_output_salvaged_as_ja(self):
        """模型完全没按格式输出时，不能静默丢内容。"""
        p = ProtocolParser()
        out = p.feed("それはどうかしら。")
        out.extend(p.close())
        self.assertEqual(out, ["それはどうかしら。"])
        self.assertEqual(p.turn.ja, "それはどうかしら。")

    def test_empty_input(self):
        p = ProtocolParser()
        self.assertEqual(p.feed(""), [])
        self.assertEqual(p.close(), [])
        self.assertEqual(p.turn.ja, "")

    def test_display_helper(self):
        p = ProtocolParser()
        p.feed(FULL)
        p.close()
        text = p.turn.display()
        self.assertIn("それはどうかしら。", text)
        self.assertIn("合上书", text)
        self.assertIn("那可不好说吧", text)


class TestJunkFiltering(unittest.TestCase):
    """回归：把纯标点碎片送去 TTS 会被服务端以「请输入有效文本」拒绝并断连。"""

    def test_ellipsis_stays_with_sentence(self):
        """省略号是语气停顿，不能被当成分隔符切掉。"""
        p = ProtocolParser()
        out = p.feed("<ja>……そう。別に。</ja>")
        out.extend(p.close())
        self.assertEqual(out, ["……そう。", "別に。"])
        self.assertTrue(p.turn.ja.startswith("……"))

    def test_punctuation_only_not_emitted(self):
        p = ProtocolParser()
        out = p.feed("<ja>…</ja>")
        out.extend(p.close())
        self.assertEqual(out, [])

    def test_symbols_only_not_emitted(self):
        p = ProtocolParser()
        out = p.feed("<ja>……！？</ja>")
        out.extend(p.close())
        self.assertEqual(out, [])

    def test_trailing_comma_before_period_cleaned(self):
        """服务端二次切句会产出 `なら、。` 这种垃圾，客户端必须清掉。"""
        self.assertEqual(clean_for_tts("あなたがそう言うなら、。"), "あなたがそう言うなら。")
        p = ProtocolParser()
        out = p.feed("<ja>そう言うなら、。</ja>")
        out.extend(p.close())
        self.assertEqual(out, ["そう言うなら。"])

    def test_leading_period_stripped(self):
        self.assertEqual(clean_for_tts("。べつに。"), "べつに。")

    def test_is_speakable(self):
        self.assertFalse(is_speakable(""))
        self.assertFalse(is_speakable("…"))
        self.assertFalse(is_speakable("！？"))
        self.assertTrue(is_speakable("そう。"))
        self.assertTrue(is_speakable("猫"))
        self.assertTrue(is_speakable("カタカナ"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
