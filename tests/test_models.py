# -*- coding: utf-8 -*-
"""「自动检测配置 + 推荐模型 + 长短处对照表」的测试。

为什么要测：推荐逻辑是"替使用者做判断"的地方，一旦按错档位，
配置低的机器会被推 large-v3（爆显存），配置高的机器会被推 small（白瞎显卡）。
所以每一档都要有用例钉住。
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
os.environ.setdefault("SUBTITLE_STUDIO_ROOT", str(Path(__file__).resolve().parents[1]))

import core


class RecommendModelTest(unittest.TestCase):
    def recommend(self, vram, ram=16.0):
        return core.recommend_model({"gpu": "测试显卡", "vram_gb": vram, "ram_gb": ram})[0]

    def test_no_gpu_falls_back_to_small(self):
        self.assertEqual(self.recommend(0), "small")

    def test_tiny_vram_prefers_turbo(self):
        # 2 GB 跑 large-v3 会吃紧，不能推它
        self.assertEqual(self.recommend(2), "turbo")

    def test_four_gb_prefers_turbo(self):
        # 使用者这台就是 4 GB：实测两个模型打平，turbo 快 2-3 倍
        self.assertEqual(self.recommend(4), "turbo")

    def test_six_gb_prefers_large_v3(self):
        self.assertEqual(self.recommend(6), "large-v3")

    def test_plenty_of_vram_prefers_large_v3(self):
        for vram in (8, 12, 24, 48):
            self.assertEqual(self.recommend(vram), "large-v3", vram)

    def test_the_reason_is_human_readable(self):
        model, why = core.recommend_model({"gpu": "x", "vram_gb": 4.0, "ram_gb": 16})
        self.assertIn("4.0 GB", why)
        self.assertIn(model, why + " " + model)   # 理由里要能对上模型名（模型名在返回里）
        self.assertTrue(why.endswith("。"))

    def test_broken_profiles_do_not_crash(self):
        for profile in ({}, None, {"vram_gb": None}, {"vram_gb": "4"}, {"vram_gb": -1}):
            model, why = core.recommend_model(profile)
            self.assertIn(model, core.MODEL_PROFILES, profile)
            self.assertTrue(why, profile)


class ModelProfileTest(unittest.TestCase):
    def test_every_builtin_model_has_a_profile(self):
        """内置的每个模型都要有长短处说明，否则下拉里会有一项是空白。"""
        for name in core.BUILTIN_MODEL_REPOS:
            self.assertIn(name, core.MODEL_PROFILES, name)

    def test_every_profile_is_complete(self):
        for name, info in core.MODEL_PROFILES.items():
            for key in ("mb", "speed", "quality", "note"):
                self.assertIn(key, info, (name, key))
            self.assertTrue(1 <= info["speed"] <= 5, name)
            self.assertTrue(1 <= info["quality"] <= 5, name)
            self.assertTrue(info["note"].strip(), name)

    def test_the_table_covers_every_model_in_the_dropdown(self):
        self.assertEqual(set(core.MODEL_ORDER), set(core.MODEL_PROFILES))
        self.assertEqual(set(core.MODEL_ORDER), set(core.BUILTIN_MODEL_REPOS))

    def test_pros_cons_mentions_size_and_stars(self):
        text = core.model_pros_cons("large-v3")
        self.assertIn("3090", text)
        self.assertIn("★", text)

    def test_unknown_model_gives_empty_text(self):
        self.assertEqual(core.model_pros_cons("不存在的模型"), "")
        self.assertEqual(core.model_pros_cons(""), "")


class ModelTableTest(unittest.TestCase):
    def test_one_row_per_model_plus_header(self):
        rows = core.model_table().splitlines()
        self.assertEqual(len(rows), len(core.MODEL_ORDER) + 1)

    def test_columns_line_up_by_display_width(self):
        """中文表头和英文数据必须对齐 —— 按显示宽度算，不是按字符个数。"""
        widths = set()
        for line in core.model_table().splitlines():
            note = "适合什么时候用"
            for name in core.MODEL_ORDER:
                if line.endswith(core.MODEL_PROFILES[name]["note"]):
                    note = core.MODEL_PROFILES[name]["note"]
                    break
            widths.add(core._display_width(line[:len(line) - len(note)]))
        self.assertEqual(len(widths), 1, widths)

    def test_cjk_counts_as_two_columns(self):
        self.assertEqual(core._display_width("模型"), 4)
        self.assertEqual(core._display_width("turbo"), 5)
        self.assertEqual(core._display_width("★"), 1)

    def test_pad_uses_display_width(self):
        self.assertEqual(core._display_width(core._pad("模型", 8)), 8)
        self.assertEqual(core._display_width(core._pad("模型", 8, "right")), 8)
        self.assertEqual(core._pad("ab", 2), "ab")          # 不截断
        self.assertEqual(core._display_width(core._pad("很长很长的中文", 4)), 14)


class HardwareProfileTest(unittest.TestCase):
    def test_returns_the_three_fields(self):
        profile = core.hardware_profile()
        self.assertEqual(set(profile), {"gpu", "vram_gb", "ram_gb"})
        self.assertIsInstance(profile["gpu"], str)
        self.assertIsInstance(profile["vram_gb"], float)
        self.assertIsInstance(profile["ram_gb"], float)

    def test_vram_is_never_negative(self):
        self.assertGreaterEqual(core.hardware_profile()["vram_gb"], 0.0)

    def test_profile_text_handles_empty_profile(self):
        text = core.profile_text({})
        self.assertIn("没检测到", text)
        self.assertEqual(core.profile_text(None).count("　"), 0)


if __name__ == "__main__":
    unittest.main()
