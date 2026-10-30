# -*- coding: utf-8 -*-
"""表情包标签 + 分条发送的单元测试。

这两件事直接决定 QQ 上的观感：
  · sticker 解析错了 → 发不出图，或者发出一个不存在的文件名把请求搞失败
  · 分条逻辑错了   → 一句话被切成碎片，或者一大段一次甩出去
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.llm.protocol import (  # noqa: E402
    KNOWN_STICKERS,
    ProtocolParser,
    Turn,
    _parse_sticker,
)


def parse(raw: str) -> Turn:
    p = ProtocolParser()
    p.feed(raw)
    p.close()
    return p.turn


class TestStickerParsing(unittest.TestCase):
    def test_basic_sticker(self):
        t = parse(
            "<ja>ふん。</ja>\n<zh>哼。</zh>\n<act>别过脸</act>\n"
            "<mood>shy</mood>\n<aff>1</aff>\n<sticker>shy</sticker>"
        )
        self.assertEqual(t.sticker, "shy")
        self.assertEqual(t.mood, "shy")
        self.assertEqual(t.aff, 1)

    def test_no_sticker_means_empty(self):
        t = parse("<ja>そう。</ja>\n<zh>是啊。</zh>\n<mood>calm</mood>\n<aff>0</aff>")
        self.assertEqual(t.sticker, "")

    def test_chinese_alias(self):
        self.assertEqual(_parse_sticker("傲娇"), "tsundere")
        self.assertEqual(_parse_sticker("害羞"), "shy")
        self.assertEqual(_parse_sticker("生气"), "angry")

    def test_unknown_sticker_is_dropped(self):
        # 认不出来的名字必须返回空，绝不能把不存在的文件名传下去
        self.assertEqual(_parse_sticker("doge"), "")
        self.assertEqual(_parse_sticker(""), "")
        self.assertEqual(_parse_sticker("随便来一张"), "")

    def test_sticker_after_other_tags_still_works(self):
        t = parse(
            "<ja>べつに。</ja>\n<zh>没什么。</zh>\n<mood>tsundere</mood>\n"
            "<aff>-1</aff>\n<sticker>tsundere</sticker>"
        )
        self.assertEqual(t.sticker, "tsundere")

    def test_all_known_stickers_parse(self):
        for name in KNOWN_STICKERS:
            self.assertEqual(_parse_sticker(name), name)

    def test_to_dict_includes_sticker(self):
        t = Turn(ja="x", zh="y", sticker="calm")
        self.assertEqual(t.to_dict().get("sticker"), "calm")

    def test_sticker_names_match_moods(self):
        # 表情包与情绪同名，图片文件名才对得上
        from app.llm.protocol import KNOWN_MOODS

        self.assertEqual(tuple(KNOWN_STICKERS), tuple(KNOWN_MOODS))


class TestSplitReply(unittest.TestCase):
    def _bot(self, parts=3):
        from app.qq.bot import QQBot

        b = QQBot.__new__(QQBot)
        b.text_parts = parts
        b.text_gap = 0.0
        return b

    def test_splits_on_japanese_punctuation(self):
        b = self._bot()
        out = b._split_reply("ふん。そういうところが本当に馬鹿みたい。別に。")
        self.assertGreaterEqual(len(out), 2)
        self.assertLessEqual(len(out), 3)
        for seg in out:
            self.assertTrue(seg.strip())

    def test_short_reply_stays_whole(self):
        b = self._bot()
        out = b._split_reply("そ。")
        self.assertEqual(len(out), 1)

    def test_long_reply_capped_by_limit(self):
        b = self._bot(parts=2)
        text = "。".join(["あ"] * 10) + "。"
        out = b._split_reply(text)
        self.assertLessEqual(len(out), 2)

    def test_empty_text(self):
        b = self._bot()
        self.assertEqual(b._split_reply(""), [])
        self.assertEqual(b._split_reply("   "), [])

    def test_chinese_text_split(self):
        b = self._bot()
        out = b._split_reply("那可不好说。既然你这么认为，那就这样吧。")
        self.assertGreaterEqual(len(out), 1)
        joined = "".join(out)
        self.assertIn("不好说", joined)
        self.assertIn("这样吧", joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
