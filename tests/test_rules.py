# -*- coding: utf-8 -*-
"""三层人格规则（L2 owner / L3 self）的单元测试。

L3 是「她自己能改的一层」，最容易失控：放任写下去会把人格挤没。
所以要测三件事：行数上限、去重、以及没授权时绝对写不进去。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.llm import rules  # noqa: E402


class TestRulesLayers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="yukino-rules-"))
        self.persona = self.tmp / "yukino.json"
        self.persona.write_text("{}", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_files_return_empty(self):
        layers = rules.load_layers(self.persona)
        self.assertEqual(layers, {"owner": "", "self": ""})
        self.assertEqual(rules.render(layers), "")

    def test_owner_layer_is_loaded(self):
        d = rules.rules_dir(self.persona)
        d.mkdir(parents=True, exist_ok=True)
        (d / "owner.md").write_text("- 回复短一点", encoding="utf-8")
        layers = rules.load_layers(self.persona)
        self.assertIn("回复短一点", layers["owner"])
        rendered = rules.render(layers)
        self.assertIn("主人补充的规矩", rendered)

    def test_self_layer_is_loaded(self):
        d = rules.rules_dir(self.persona)
        d.mkdir(parents=True, exist_ok=True)
        (d / "self.md").write_text("- [2026-10-30] 喜欢短句", encoding="utf-8")
        rendered = rules.render(rules.load_layers(self.persona))
        self.assertIn("相处偏好", rendered)
        self.assertIn("喜欢短句", rendered)

    def test_owner_long_text_is_truncated(self):
        d = rules.rules_dir(self.persona)
        d.mkdir(parents=True, exist_ok=True)
        (d / "owner.md").write_text("字" * (rules.MAX_OWNER_CHARS + 500), encoding="utf-8")
        layers = rules.load_layers(self.persona)
        self.assertLessEqual(len(layers["owner"]), rules.MAX_OWNER_CHARS + 40)


class TestAppendSelfRule(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="yukino-self-"))
        self.persona = self.tmp / "yukino.json"
        self.persona.write_text("{}", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_requires_permission(self):
        ok, msg = rules.append_self_rule(self.persona, "喜欢短句", allowed=False)
        self.assertFalse(ok)
        self.assertIn("同意", msg)
        self.assertFalse((rules.rules_dir(self.persona) / "self.md").exists())

    def test_writes_with_permission(self):
        ok, msg = rules.append_self_rule(self.persona, "喜欢短句", allowed=True)
        self.assertTrue(ok)
        text = (rules.rules_dir(self.persona) / "self.md").read_text(encoding="utf-8")
        self.assertIn("喜欢短句", text)
        # 自动带日期
        self.assertRegex(text, r"\[\d{4}-\d{2}-\d{2}\]")

    def test_duplicate_is_rejected(self):
        self.assertTrue(rules.append_self_rule(self.persona, "喜欢短句", allowed=True)[0])
        ok, msg = rules.append_self_rule(self.persona, "喜欢短句", allowed=True)
        self.assertFalse(ok)
        self.assertIn("已经有了", msg)

    def test_too_long_rule_rejected(self):
        ok, msg = rules.append_self_rule(self.persona, "字" * 200, allowed=True)
        self.assertFalse(ok)
        self.assertIn("太长", msg)

    def test_empty_rule_rejected(self):
        ok, _ = rules.append_self_rule(self.persona, "   ", allowed=True)
        self.assertFalse(ok)

    def test_line_cap_enforced(self):
        # 先塞满上限
        for i in range(rules.MAX_SELF_LINES):
            ok, _ = rules.append_self_rule(self.persona, "规则%d" % i, allowed=True)
            self.assertTrue(ok, "第 %d 条应能写入" % i)
        ok, msg = rules.append_self_rule(self.persona, "再多一条", allowed=True)
        self.assertFalse(ok)
        self.assertIn("上限", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
