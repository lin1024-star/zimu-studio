"""实时字幕：延迟拆解 + 提前出半句的测试（2026-10-29）。

背景：使用者反馈「延迟干到 7 秒，是网络问题吗」。为了能一眼看出延迟花在哪一段，
每句现在带 4 个墙钟时间点，拆成：识别（含安全线/节拍/推理）、等整句、翻译、显示。
同时把「半句」的门槛从 12 字收到 8 字、最长等待从 4 秒收到 2.5 秒。

这里盯三件事：
  ① 参数确实是调早了的（别以后被改回去还不知道）
  ② 组装器/翻译线程真的把时间点带上了（缺了拆解就全变成 0）
  ③ 拆解算法不会算错、旧数据缺字段也不崩
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
from live.pipeline import (DEFAULTS, SentenceAssembler, Translator,  # noqa: E402
                           latency_notice_text, latency_notice_title, latency_parts,
                           parts_summary)


class DefaultsTests(unittest.TestCase):
    def test_partial_earlier_and_maxwait_shorter(self):
        self.assertEqual(DEFAULTS["partial_chars"], 8)
        self.assertEqual(DEFAULTS["maxwait"], 2.5)
        self.assertLess(DEFAULTS["maxwait"], 4.0)


class AssemblerTimestampTests(unittest.TestCase):
    def test_flush_carries_wall_clock(self):
        got = []
        asm = SentenceAssembler(dict(DEFAULTS), got.append)
        asm.feed([(0.0, 1.0, "こんにちは")])
        asm.flush()
        self.assertEqual(len(got), 1)
        item = got[0]
        self.assertFalse(item["partial"])
        self.assertIn("commit_wall", item)
        self.assertIn("flush_wall", item)
        self.assertGreaterEqual(item["flush_wall"], item["commit_wall"])
        self.assertGreater(item["commit_wall"], 0)

    def test_partial_emitted_at_eight_chars(self):
        got = []
        asm = SentenceAssembler(dict(DEFAULTS), got.append)
        asm.feed([(0.0, 1.0, "こんにちは、みなさん")])     # 9 字 ≥ 8
        asm.flush_partial()
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0]["partial"])
        self.assertIn("flush_wall", got[0])

    def test_partial_waits_for_eight_chars(self):
        got = []
        asm = SentenceAssembler(dict(DEFAULTS), got.append)
        asm.feed([(0.0, 1.0, "こんに")])                   # 3 字 < 8
        asm.flush_partial()
        self.assertEqual(got, [])


class LatencyPartsTests(unittest.TestCase):
    def test_breakdown_from_wall_clock(self):
        payload = {"end": 10.0, "commit_wall": 113.5, "flush_wall": 115.0,
                   "tr_start_wall": 115.2, "tr_wall": 117.0}
        parts = latency_parts(payload, capture_wall=100.0, shown_wall=117.2)
        self.assertAlmostEqual(parts["recog"], 3.5, places=2)
        self.assertAlmostEqual(parts["assembly"], 1.5, places=2)
        self.assertAlmostEqual(parts["trans"], 2.0, places=2)
        self.assertAlmostEqual(parts["shown"], 0.2, places=2)
        self.assertAlmostEqual(parts["total"], 7.2, places=2)

    def test_missing_fields_do_not_crash(self):
        parts = latency_parts({"end": 0.0}, capture_wall=100.0, shown_wall=101.0)
        self.assertAlmostEqual(parts["total"], 1.0, places=2)
        self.assertGreaterEqual(parts["recog"], 0.0)

    def test_negative_times_are_clamped(self):
        # 极端情况（时钟/顺序异常）：每段都不能是负数
        parts = latency_parts({"end": 10.0, "commit_wall": 100.0, "flush_wall": 99.0,
                               "tr_wall": 98.0}, capture_wall=100.0, shown_wall=99.0)
        for key in ("recog", "assembly", "trans", "shown", "total"):
            self.assertGreaterEqual(parts[key], 0.0)


class PartsSummaryTests(unittest.TestCase):
    def test_median(self):
        parts = [{"recog": 1, "assembly": 3, "trans": 5, "shown": 0, "total": 9},
                 {"recog": 2, "assembly": 2, "trans": 4, "shown": 0, "total": 8},
                 {"recog": 3, "assembly": 1, "trans": 3, "shown": 0, "total": 7}]
        summary = parts_summary(parts)
        self.assertEqual(summary["recog"], 2)
        self.assertEqual(summary["assembly"], 2)
        self.assertEqual(summary["trans"], 4)

    def test_empty(self):
        self.assertIsNone(parts_summary([]))


class TranslatorTimestampTests(unittest.TestCase):
    def test_submit_stamps_submit_wall(self):
        tr = Translator("k", "m", lambda: "", lambda r: None)
        tr.submit({"id": 1, "start": 0, "end": 1, "ja": "こんにちは"})
        item = tr.q.get_nowait()
        self.assertIn("submit_wall", item)
        self.assertGreater(item["submit_wall"], 0)


class NoticeTextTests(unittest.TestCase):
    def test_notice_mentions_representative_causes(self):
        text = latency_notice_text()
        self.assertTrue(latency_notice_title())
        for word in ("1.5", "2.5", "翻译", "延迟"):
            self.assertIn(word, text)
        self.assertLess(len(text), 700)


if __name__ == "__main__":
    unittest.main()
