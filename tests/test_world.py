# -*- coding: utf-8 -*-
"""场景系统测试：时间线（硬约束）+ 随机指数（软约束）。

容易出错的地方：
  · 时间窗算错（比如把 09:00-16:00 算成 09:00-15:01）
  · 同一时间段内场景乱跳（心情一变就换地方，非常出戏）
  · 心情对分布没有实际影响（随机指数形同虚设）
  · 固定时段被随机覆盖（上课时间跑到家里去了）
"""

from __future__ import annotations

import datetime as dt
import os
import random
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import world as world_mod  # noqa: E402

SCENES = str(Path(__file__).resolve().parent.parent / "persona" / "scenes.json")

# 2026-11-05 周四 / 2026-11-07 周六
THU = dt.date(2026, 11, 5)
SAT = dt.date(2026, 11, 7)


def at(day: dt.date, hh: int, mm: int = 0) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hh, mm)


class FakeStore:
    def __init__(self):
        self.d = {}

    def get(self, k, default=""):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


def fresh():
    return world_mod.load_world(SCENES)


# ---------------------------------------------------------------- 时间片
class TestSlots(unittest.TestCase):
    def setUp(self):
        self.w = fresh()

    def test_weekday_morning_commute(self):
        sc = self.w.scene(at(THU, 8, 0), mood="calm", affinity=0)
        self.assertEqual(sc["id"], "station")
        self.assertEqual(sc["kind"], "commute")

    def test_weekday_school_hours(self):
        for hh, mm in ((9, 0), (12, 30), (15, 59)):
            sc = self.w.scene(at(THU, hh, mm), mood="tired", affinity=-100)
            self.assertEqual(sc["id"], "classroom", "%02d:%02d" % (hh, mm))

    def test_weekday_club_hour(self):
        for hh, mm in ((16, 0), (16, 30), (16, 59)):
            sc = self.w.scene(at(THU, hh, mm), mood="angry", affinity=-100)
            self.assertEqual(sc["id"], "clubroom")

    def test_weekday_home_evening(self):
        sc = self.w.scene(at(THU, 20, 0), mood="soft", affinity=100)
        self.assertEqual(sc["id"], "home")

    def test_night_both_days(self):
        for day in (THU, SAT):
            sc = self.w.scene(at(day, 23, 30), mood="calm", affinity=0)
            self.assertEqual(sc["id"], "home_room")

    def test_windows_are_correct(self):
        cases = {
            (8, 0): ("07:00", "09:00"),
            (10, 0): ("09:00", "16:00"),
            (16, 30): ("16:00", "17:00"),
            (18, 0): ("17:00", "19:00"),
            (20, 0): ("19:00", "23:00"),
        }
        for (hh, mm), (want_a, want_b) in cases.items():
            sc = self.w.scene(at(THU, hh, mm), mood="calm", affinity=0)
            self.assertEqual(sc["window"], "%s~%s" % (want_a, want_b), "%02d:%02d" % (hh, mm))

    def test_weekend_is_free(self):
        sc = self.w.scene(at(SAT, 14, 0), mood="calm", affinity=0)
        self.assertEqual(sc["kind"], "free")

    def test_fixed_slots_ignore_randomness(self):
        # 同一时刻用 50 个不同种子，固定时段必须始终同一个场景
        for hh, mm in ((8, 0), (10, 0), (16, 30), (20, 0), (23, 30)):
            seen = set()
            for i in range(50):
                sc = fresh().scene(at(THU, hh, mm), mood="soft", affinity=100,
                                   rng=random.Random(i))
                seen.add(sc["id"])
            self.assertEqual(len(seen), 1, "%02d:%02d -> %s" % (hh, mm, seen))


# ---------------------------------------------------------------- 稳定性
class TestStability(unittest.TestCase):
    def test_same_slot_stable_with_store(self):
        store = FakeStore()
        w = world_mod.load_world(SCENES, store=store)
        first = w.scene(at(SAT, 14, 0), mood="soft", affinity=100)
        # 心情变化（这是真实会发生的：聊完一轮 mood 会变）
        second = w.scene(at(SAT, 14, 0), mood="tired", affinity=100)
        self.assertEqual(first["id"], second["id"])

    def test_slot_key_changes_at_boundary(self):
        w = fresh()
        k1 = w.slot_key(at(THU, 15, 59))
        k2 = w.slot_key(at(THU, 16, 0))
        self.assertNotEqual(k1, k2)

    def test_new_day_new_roll(self):
        # 不带 store 时，不同日期种子不同（不一定不同场景，但种子必须不同）
        w = fresh()
        self.assertNotEqual(w.slot_key(at(THU, 14, 0)), w.slot_key(at(SAT, 14, 0)))


# ---------------------------------------------------------------- 随机指数
class TestRandomIndex(unittest.TestCase):
    def _dist(self, mood: str, *, affinity: int = 100, limit: int = 300):
        c = Counter()
        for i in range(limit):
            sc = world_mod.World(fresh().data).scene(
                at(SAT, 14, 0), mood=mood, affinity=affinity, rng=random.Random(i))
            c[sc["id"]] += 1
        return c

    def test_tired_prefers_home_room(self):
        soft = self._dist("soft")
        tired = self._dist("tired")
        self.assertGreater(tired["home_room"], soft["home_room"])

    def test_soft_prefers_outdoor(self):
        soft = self._dist("soft")
        tired = self._dist("tired")
        outdoor = ("park", "cafe", "rooftop")
        self.assertGreater(sum(soft[k] for k in outdoor), sum(tired[k] for k in outdoor))

    def test_angry_avoids_social(self):
        angry = self._dist("angry")
        soft = self._dist("soft")
        self.assertGreater(angry["home"], angry["park"])
        self.assertLess(angry["park"], soft["park"])

    def test_high_affinity_more_social(self):
        high = self._dist("calm", affinity=100)
        low = self._dist("calm", affinity=-100)
        social = ("park", "cafe", "rooftop", "station")
        self.assertGreater(sum(high[k] for k in social), sum(low[k] for k in social))

    def test_weekend_pool_only(self):
        allowed = set(world_mod.load_world(SCENES).schedule.get("weekend_pool") or [])
        for i in range(80):
            sc = world_mod.World(fresh().data).scene(
                at(SAT, 14, 0), mood="calm", affinity=0, rng=random.Random(i))
            self.assertIn(sc["id"], allowed)

    def test_weekend_evening_pool(self):
        allowed = set(world_mod.load_world(SCENES).schedule.get("weekend_evening_pool") or [])
        for i in range(40):
            sc = world_mod.World(fresh().data).scene(
                at(SAT, 20, 0), mood="calm", affinity=0, rng=random.Random(i))
            self.assertIn(sc["id"], allowed)


# ---------------------------------------------------------------- 渲染
class TestRender(unittest.TestCase):
    def test_block_has_key_parts(self):
        w = fresh()
        sc = w.scene(at(THU, 16, 30), mood="calm", affinity=100)
        block = world_mod.render_scene_block(sc)
        self.assertIn("奉仕部", block)
        self.assertIn("环境：", block)
        self.assertIn("此刻：", block)
        self.assertIn("16:30", block)

    def test_block_respects_budget(self):
        w = fresh()
        sc = w.scene(at(THU, 16, 30))
        block = world_mod.render_scene_block(sc, max_chars=60)
        self.assertLessEqual(len(block), 62)

    def test_empty_scene_safe(self):
        self.assertEqual(world_mod.render_scene_block({}), "")

    def test_describe_now(self):
        text = world_mod.describe_now(at(THU, 16, 30))
        self.assertIn("周四", text)
        self.assertIn("16:30", text)


class TestRobustness(unittest.TestCase):
    def test_empty_data_does_not_crash(self):
        w = world_mod.World({})
        sc = w.scene(at(THU, 10, 0), mood="calm", affinity=0)
        self.assertIn("id", sc)

    def test_unknown_store_does_not_crash(self):
        w = world_mod.World(fresh().data, store=object())  # 没有 get/set
        sc = w.scene(at(SAT, 14, 0), mood="calm", affinity=0, rng=random.Random(1))
        self.assertTrue(sc.get("id"))

    def test_all_scene_ids_exist_in_config(self):
        w = fresh()
        for sid in w.scenes:
            self.assertTrue((w.scenes[sid].get("name_zh")), sid)
            self.assertTrue((w.scenes[sid].get("bg")), sid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
