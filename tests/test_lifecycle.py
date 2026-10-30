# -*- coding: utf-8 -*-
"""时间轮（年龄随生日增长）+ 人生经历渲染的测试。

为什么要测时间轮：
年龄一旦写死，一年后她就会自我介绍「我 18 岁」而实际世界已经过去一年 ——
这种破绽比任何语气问题都出戏。这里把「生日前后」的边界全部钉死。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.llm import lifecycle  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PERSONA = ROOT / "persona" / "yukino.json"


class TestParseBirthday(unittest.TestCase):
    def test_various_formats(self):
        for raw in ("01-03", "1-3", "01/03", "1.3"):
            self.assertEqual(lifecycle._parse_birthday(raw), (1, 3), raw)

    def test_invalid(self):
        for raw in ("", "abc", "13-01", "01-32", "2020-01-03"):
            self.assertIsNone(lifecycle._parse_birthday(raw), raw)

    def test_from_dict(self):
        life = lifecycle.Life.from_dict({"birthday": "01-03", "birth_year": 2008})
        self.assertIsNotNone(life)
        self.assertEqual(life.birthday, (1, 3))
        self.assertEqual(life.birth_year, 2008)

    def test_from_dict_missing(self):
        self.assertIsNone(lifecycle.Life.from_dict(None))
        self.assertIsNone(lifecycle.Life.from_dict({}))
        self.assertIsNone(lifecycle.Life.from_dict({"birthday": "01-03"}))
        self.assertIsNone(lifecycle.Life.from_dict({"birth_year": 2008}))


class TestAgeWheel(unittest.TestCase):
    def setUp(self):
        self.life = lifecycle.Life(birthday=(1, 3), birth_year=2008)

    def test_day_before_birthday(self):
        # 2027-01-02：还没过生日，仍是 18
        self.assertEqual(self.life.age_on(dt.date(2027, 1, 2)), 18)

    def test_on_birthday_increments(self):
        # 生日当天就该长一岁（不能等到第二天）
        self.assertEqual(self.life.age_on(dt.date(2027, 1, 3)), 19)

    def test_day_after_birthday(self):
        self.assertEqual(self.life.age_on(dt.date(2027, 1, 4)), 19)

    def test_late_in_year(self):
        self.assertEqual(self.life.age_on(dt.date(2026, 10, 30)), 18)

    def test_next_birthday_wraps_year(self):
        self.assertEqual(self.life.next_birthday(dt.date(2026, 10, 30)), dt.date(2027, 1, 3))

    def test_next_birthday_same_year(self):
        self.assertEqual(self.life.next_birthday(dt.date(2026, 1, 10)), dt.date(2027, 1, 3))

    def test_next_birthday_on_the_day_is_today(self):
        self.assertEqual(self.life.next_birthday(dt.date(2027, 1, 3)), dt.date(2027, 1, 3))

    def test_snapshot_on_birthday(self):
        snap = self.life.snapshot(dt.date(2027, 1, 3))
        self.assertTrue(snap["is_birthday"])
        self.assertEqual(snap["age"], 19)
        self.assertEqual(snap["days_to_birthday"], 0)

    def test_snapshot_counts_down(self):
        snap = self.life.snapshot(dt.date(2026, 12, 31))
        self.assertFalse(snap["is_birthday"])
        self.assertEqual(snap["days_to_birthday"], 3)
        self.assertEqual(snap["age"], 18)
        self.assertEqual(snap["next_age"], 19)

    def test_feb29_birthday_does_not_crash(self):
        life = lifecycle.Life(birthday=(2, 29), birth_year=2008)
        snap = life.snapshot(dt.date(2027, 3, 1))
        self.assertIn("age", snap)


class TestRendering(unittest.TestCase):
    def test_age_hint_plain(self):
        life = lifecycle.Life((1, 3), 2008)
        hint = lifecycle.render_age_hint(life.snapshot(dt.date(2026, 6, 1)))
        self.assertIn("18", hint)
        self.assertNotIn("生日", hint)

    def test_age_hint_near_birthday(self):
        life = lifecycle.Life((1, 3), 2008)
        hint = lifecycle.render_age_hint(life.snapshot(dt.date(2026, 12, 31)))
        self.assertIn("生日", hint)

    def test_age_hint_on_birthday(self):
        life = lifecycle.Life((1, 3), 2008)
        hint = lifecycle.render_age_hint(life.snapshot(dt.date(2027, 1, 3)))
        self.assertIn("生日", hint)
        self.assertIn("19", hint)

    def test_life_block_contains_sections(self):
        data = json.loads(PERSONA.read_text(encoding="utf-8-sig"))["life"]
        block = lifecycle.render_life_block(data)
        for token in ("年表", "家庭", "喜欢", "讨厌"):
            self.assertIn(token, block)
        self.assertGreater(len(block), 300)

    def test_life_block_budget(self):
        data = {"summary": "x" * 500, "timeline": [{"age": i, "event": "y" * 80} for i in range(40)]}
        block = lifecycle.render_life_block(data, max_chars=300)
        self.assertLessEqual(len(block), 340)

    def test_life_block_empty(self):
        self.assertEqual(lifecycle.render_life_block(None), "")
        self.assertEqual(lifecycle.render_life_block({}), "")


class TestPersonaConsistency(unittest.TestCase):
    """人格卡里的人生经历要和已有的身份/年龄对得上，且不能有八幡。"""

    def setUp(self):
        self.data = json.loads(PERSONA.read_text(encoding="utf-8-sig"))

    def test_has_life(self):
        life = self.data.get("life") or {}
        self.assertIn("birthday", life)
        self.assertIn("birth_year", life)
        self.assertGreaterEqual(len(life.get("timeline") or []), 10)

    def test_no_hachiman(self):
        raw = json.dumps(self.data, ensure_ascii=False)
        for banned in ("八幡", "比企谷", "Hachiman"):
            self.assertNotIn(banned, raw, "人设里不该出现 %s（这个世界线没有他）" % banned)

    def test_age_matches_school_year(self):
        # 18 岁应处于高中三年级，不能出现「二年级」这种冲突
        identity = self.data.get("identity", "")
        timeline = self.data["life"]["timeline"]
        last = timeline[-1]["event"]
        self.assertIn("三年级", identity)
        self.assertIn("三年级", last)

    def test_template_has_age_and_life_placeholders(self):
        tpl = self.data["system_template"]
        self.assertIn("{age_hint}", tpl)
        self.assertIn("{life_block}", tpl)


if __name__ == "__main__":
    unittest.main(verbosity=2)
