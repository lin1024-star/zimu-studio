"""报错格式（errfmt）的单元测试。

这套格式的价值全在细节上：状态要分三种、步骤要能照着点、数字要带上。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import errfmt  # noqa: E402


class DescribePathTests(unittest.TestCase):
    """「不存在 / 文件 / 空目录 / 里面有 X」必须分得清 —— 三种情况解决办法不一样。"""

    def test_missing_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = errfmt.describe_path(Path(tmp) / "没有这个")
            self.assertIn("不存在", text)
            self.assertNotIn("空", text)

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = errfmt.describe_path(tmp)
            self.assertIn("空的", text)
            self.assertNotIn("不存在", text)

    def test_dir_with_content_lists_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("turbo", "small"):
                (Path(tmp) / name).mkdir()
            text = errfmt.describe_path(tmp)
            self.assertIn("里面有", text)
            self.assertIn("turbo", text)
            self.assertIn("small", text)

    def test_many_items_are_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(20):
                (Path(tmp) / f"m{i:02d}").mkdir()
            text = errfmt.describe_path(tmp)
            self.assertIn("等 20 项", text)
            self.assertLess(len(text), 250, "不能把几十个名字全列出来")

    def test_file_reports_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "model.bin"
            f.write_bytes(b"x" * 1234)
            text = errfmt.describe_path(f)
            self.assertIn("文件在", text)
            self.assertIn("1234", text)

    def test_missing_file_is_not_confused_with_missing_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = errfmt.describe_path(Path(tmp) / "model.bin")
            self.assertIn("不存在", text)


class DiagnoseTests(unittest.TestCase):
    def test_has_all_four_sections(self):
        text = errfmt.diagnose("出事了。", checks=[("A", "不存在")], steps=["点一下"])
        self.assertIn("出事了。", text)
        self.assertIn("我实际看到的是：", text)
        self.assertIn("你可以这样办：", text)
        self.assertIn("发给开发者", text)

    def test_checks_use_arrow_format(self):
        text = errfmt.diagnose("x", checks=[("目录", "空的")])
        self.assertIn("　· 目录 —— 空的", text)

    def test_plain_string_checks_allowed(self):
        text = errfmt.diagnose("x", checks=["目录不存在"])
        self.assertIn("　· 目录不存在", text)

    def test_steps_are_numbered(self):
        text = errfmt.diagnose("x", steps=["第一步", "第二步", "第三步"])
        self.assertIn("　1) 第一步", text)
        self.assertIn("　3) 第三步", text)

    def test_already_numbered_steps_are_not_numbered_twice(self):
        text = errfmt.diagnose("x", steps=["1) 自己编好号了"])
        self.assertIn("　1) 自己编好号了", text)
        self.assertNotIn("1) 1)", text)

    def test_extra_is_kept(self):
        text = errfmt.diagnose("x", extra="已完成的翻译不受影响。")
        self.assertIn("已完成的翻译不受影响。", text)

    def test_tail_can_be_turned_off(self):
        text = errfmt.diagnose("x", tail=False)
        self.assertNotIn("发给开发者", text)

    def test_no_checks_still_works(self):
        text = errfmt.diagnose("就一句话。")
        self.assertIn("就一句话。", text)
        self.assertNotIn("我实际看到的是：", text)

    def test_trailing_newlines_are_trimmed(self):
        text = errfmt.diagnose("出事了。\n\n")
        self.assertFalse(text.endswith("\n"))
        self.assertIn("出事了。", text)


class RealMessageTests(unittest.TestCase):
    """真跑一遍 core 里那条新报错，确认拼出来的东西是对的。"""

    def test_unrecognized_speech_message(self):
        import core
        try:
            list(core.cues_from_segments(iter([]), "ja", None, None,
                                         facts={"duration": 223.3, "detected": "日语",
                                                "device": "显卡（CUDA）", "model": "turbo"}))
            self.fail("应该报错")
        except core.UserError as exc:
            text = str(exc)
        self.assertIn("我实际看到的是：", text)
        self.assertIn("识别出几段 —— 一段都没有", text)
        self.assertIn("音频总长 —— 223.3 秒", text)
        self.assertIn("用的模型 —— turbo", text)
        self.assertIn("你可以这样办：", text)

    def test_unrecognized_speech_has_numbers(self):
        """带数字才算合格 —— 光有形容词定位不了问题。"""
        import core
        try:
            list(core.cues_from_segments(iter([]), None, None, None))
            self.fail("应该报错")
        except core.UserError as exc:
            text = str(exc)
        self.assertIn("0 秒", text)


class ChooseDownloadRouteTests(unittest.TestCase):
    """下载线路探测要留下记录，失败时才能说清「我试了哪几条」。"""

    def test_records_every_probe(self):
        import core
        seen = []

        def fake_probe(url, use_proxy, timeout=6):
            seen.append((url, use_proxy))
            return False

        record = []
        core.choose_download_route("Systran/faster-whisper-tiny", probe=fake_probe, record=record)
        self.assertEqual(len(record), 4, "四条线路都该试")
        self.assertTrue(all(ok is False for _, ok in record))
        self.assertEqual(len(seen), 4, "每条都真的探过")

    def test_records_stop_at_first_success(self):
        import core
        calls = {"n": 0}

        def probe(url, use_proxy, timeout=6):
            calls["n"] += 1
            return calls["n"] == 2          # 第二条通

        record = []
        core.choose_download_route("x/y", probe=probe, record=record)
        self.assertEqual(len(record), 2, "通了就不该继续探")
        self.assertEqual([ok for _, ok in record], [False, True])

    def test_record_is_optional(self):
        import core
        core.choose_download_route("x/y", probe=lambda *a, **k: True)   # 不传 record 也不能炸


if __name__ == "__main__":
    unittest.main(verbosity=2)
