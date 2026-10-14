# -*- coding: utf-8 -*-
"""通用性：语言相关的测试（日语 / 英语两条路都要对）。

为什么要有这一组（2026-10-14）：
产品的母语场景是日语，但**引擎对任何语言都成立**（Whisper 支持 99 种、翻译只看语言代码）。
实测发现几处把日语写死的地方 ✗ 改完之后用这组测试把两边都锁住：
  · 日语路径的行为**一个字都不许变**（那是主场景 ✓）
  · 非日语路径要真的走对（不是"能跑就算"）
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import chinese_check  # noqa: E402
import core  # noqa: E402
import ja_fix  # noqa: E402


class LanguageHelperTests(unittest.TestCase):
    """界面选择 ↔ 语言代码（原来这段映射在 app.py 里内联写了 4 遍 ✗）。"""

    def test_choice_to_code(self):
        self.assertEqual(core.language_code("英语"), "en")
        self.assertEqual(core.language_code("日语"), "ja")
        self.assertEqual(core.language_code("  英语  "), "en")

    def test_auto_or_empty_is_none(self):
        self.assertIsNone(core.language_code("自动识别"))
        self.assertIsNone(core.language_code(""))
        self.assertIsNone(core.language_code(None))

    def test_unknown_choice_falls_back_to_auto(self):
        """不认识的选择退回"自动识别"，不许乱猜一个语言出来。"""
        self.assertIsNone(core.language_code("法语"))

    def test_code_to_name(self):
        self.assertEqual(core.language_name("en"), "英语")
        self.assertEqual(core.language_name("JA"), "日语")          # 大小写不敏感
        self.assertEqual(core.language_name(None), "自动识别")
        self.assertEqual(core.language_name("xx"), "自动识别")

    def test_ui_choices_are_mapped(self):
        """下拉框里的每个选项（除自动识别）都要有对应代码 —— 否则选了等于没选。"""
        for choice in core.UI_LANGUAGE_CHOICES:
            if choice == core.AUTO_LANGUAGE:
                self.assertIsNone(core.language_code(choice))
            else:
                self.assertTrue(core.language_code(choice), choice)


class ChineseCheckPromptTests(unittest.TestCase):

    def test_japanese_prompt_is_unchanged(self):
        """日语提示词是六轮迭代调出来的，**不许动** ✓（这条是回归锁）。

        术语表拿「タロウ = 太郎」这种通用占位样例就够，**不要写真实素材里的人名** ——
        测试文件也会跟着进公开仓库，这里漏过一次（2026-10-16 清掉）。
        """
        prompt = chinese_check.system_prompt("タロウ = 太郎", "ja")
        self.assertIn("他不懂日语", prompt)
        self.assertIn("【日文字幕】", prompt)
        self.assertIn("カンジャ", prompt)                      # 日语例子还在
        self.assertIn("タロウ = 太郎", prompt)                   # 术语表进去了
        self.assertIn("不要报", prompt)                        # 判据里的"不许报"清单还在

    def test_english_prompt_has_no_japanese(self):
        prompt = chinese_check.system_prompt("Alice = 爱丽丝", "en")
        self.assertNotIn("日语", prompt)
        self.assertNotIn("日文", prompt)
        self.assertIn("原文", prompt)
        self.assertIn("Alice = 爱丽丝", prompt)

    def test_both_prompts_keep_the_same_judgement(self):
        """两套话术的**判据必须一样**（只是措辞和举例不同）——否则等于换了个功能 ✗。"""
        ja = chinese_check.system_prompt("", "ja")
        en = chinese_check.system_prompt("", "en")
        for keyword in ("不成话", "矛盾", "不要报", "每批最多报"):
            self.assertIn(keyword, ja, keyword)
            self.assertIn(keyword, en, keyword)
        self.assertIn("JSON", ja)
        self.assertIn("JSON", en)

    def test_default_language_is_japanese(self):
        self.assertEqual(chinese_check.system_prompt("x"), chinese_check.system_prompt("x", "ja"))

    def test_unknown_language_gets_the_generic_prompt(self):
        prompt = chinese_check.system_prompt("", "fr")
        self.assertIn("原文", prompt)
        self.assertNotIn("日文", prompt)


class FixPromptTests(unittest.TestCase):

    def test_japanese_fix_prompt_unchanged(self):
        self.assertIn("日语字幕校对", ja_fix.fix_system_prompt("ja"))
        self.assertIn("假名", ja_fix.fix_system_prompt("ja"))

    def test_english_fix_prompt(self):
        prompt = ja_fix.fix_system_prompt("en")
        self.assertIn("source", prompt)
        self.assertNotIn("日语", prompt)
        self.assertNotIn("假名", prompt)

    def test_guess_fix_uses_english_prompt_and_skips_kana_step(self):
        """英语素材走这条路时：用英文提示词，而且**不做"换汉字写法"那一步** ✗。"""
        seen = {}

        class FakeClient:
            def json_chat(self, system, user, max_tokens=600):
                seen["system"] = system
                if "kana" in seen or "kanji" in seen:          # 第二次调用 = 不该发生
                    seen["kanji"] = True
                seen["kana"] = True
                # fixed 必须正好是"把 wrong 换成 right"，否则会被安全检查正确地拒掉 ✗
                return {"wrong": "thier", "right": "their", "fixed": "their house is big.",
                        "why": "拼写错了"}

        result = ja_fix.guess_fix("thier house is big.", "他们的房子很大", "应该是 their",
                                  [], FakeClient(), language="en")
        self.assertEqual(result["right"], "their")
        self.assertIn("proofreading", seen["system"])          # 英文提示词
        self.assertNotIn("kanji", seen)                        # 没走假名那一步 ✓

    def test_guess_fix_japanese_still_asks_for_kanji_form(self):
        """日语素材仍要问汉字写法（实测这一步是必要的 ✓）——回归锁。"""
        calls = []

        class FakeClient:
            def json_chat(self, system, user, max_tokens=600):
                calls.append(system)
                if len(calls) == 1:
                    # fixed = 把 wrong 换成 right（安全检查要求），且 right 是纯假名
                    return {"wrong": "お食", "right": "おしょく", "fixed": "おしょくだいすき",
                            "why": "听错"}
                # 第二次是"把假名读成汉字写法"那一步 —— 它读的键是 kanji（见 kanji_form）
                return {"kanji": "汚職"}

        result = ja_fix.guess_fix("お食だいすき", "超喜欢吃饭", "应该是贪污", [], FakeClient())
        self.assertEqual(len(calls), 2, "日语这条路要问第二次（汉字写法）")
        self.assertEqual(result["right"], "汚職")

    def test_judge_prompt_is_language_neutral(self):
        self.assertNotIn("日语", ja_fix.JUDGE_SYSTEM)


class LiveLanguageTests(unittest.TestCase):

    def test_live_default_is_still_japanese(self):
        """实时字幕的默认语言必须还是日语 —— 老用法的行为不能变 ✓。"""
        from live.pipeline import DEFAULTS
        self.assertEqual(DEFAULTS["language"], "ja")

    def test_transcriber_uses_the_configured_language(self):
        """识别调用里带的语言要跟着 cfg 走（以前写死 "ja" ✗）。"""
        import threading
        from live.capture import AudioRing
        from live.pipeline import DEFAULTS, LiveTranscriber

        seen = {}

        class FakeModel:
            def transcribe(self, path, **kwargs):
                seen.update(kwargs)
                return [], None

        cfg = dict(DEFAULTS)
        cfg["language"] = "en"
        transcriber = LiveTranscriber(AudioRing(16000, 5), cfg, on_sentence=lambda s: None,
                                      tmp_path=str(Path(__file__).with_name("_tmp_live.wav")))
        transcriber.model = FakeModel()
        transcriber.options = {}          # 真实路径里由 load() 设置
        import numpy as np
        transcriber._transcribe_window(np.zeros(16000, dtype="float32"))
        self.assertEqual(seen.get("language"), "en")

        # 不设语言时退回日语（老行为）
        cfg2 = dict(DEFAULTS)
        cfg2.pop("language", None)
        transcriber.cfg = cfg2
        transcriber._transcribe_window(np.zeros(16000, dtype="float32"))
        self.assertEqual(seen.get("language"), "ja")
        Path(__file__).with_name("_tmp_live.wav").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
