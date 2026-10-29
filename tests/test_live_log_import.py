"""「从运行记录导出」的测试（2026-10-29 加）。

背景：实时字幕可以单独打开，那时它的结果只在那个进程内存里；主界面靠这份运行记录
把这一场读出来导出。这里盯住三件事：

  ① 只认最后一场（最后一条「启动：」之后），旧场次不能混进来
  ② 只导「整句」、同一句不要重复；坏行跳过、不崩
  ③ 时间轴是估算的 —— 起点按「写下时刻 − 延迟」，相邻两条不重叠、至少 0.8 秒
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
from live import logimport


def record(time_text, kind, latency, zh, ja):
    return f"[{time_text}] 字幕[{kind}] 延迟 {latency:4.1f}s | {zh}  〔{ja}〕"


class ParseSessionTests(unittest.TestCase):
    def test_only_last_session(self):
        text = "\n".join([
            "[10-29 19:00:00] 启动：抓系统声音",
            record("10-29 19:00:05", "整句", 1.0, "上一场的话", "まえのばん"),
            "[10-29 20:29:14] 启动：抓系统声音",
            record("10-29 20:29:20", "整句", 2.0, "这一场的话", "このばん"),
        ])
        records = logimport.parse_session(text)
        self.assertEqual([r["zh"] for r in records], ["这一场的话"])

    def test_no_start_marker_reads_whole_file(self):
        text = record("10-29 20:00:01", "整句", 1.0, "你好", "こんにちは")
        self.assertEqual(len(logimport.parse_session(text)), 1)

    def test_ignores_non_subtitle_lines(self):
        text = "\n".join([
            "[10-29 20:29:14] 启动：抓系统声音",
            "[10-29 20:29:15] 音频来源：扬声器（系统声音）",
            "[10-29 20:29:16] 状况：已出 0 条｜延迟中位 0.0s",
            record("10-29 20:29:20", "整句", 1.0, "你好", "こんにちは"),
            "随手写的一行",
        ])
        records = logimport.parse_session(text)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["ja"], "こんにちは")

    def test_midnight_rollover(self):
        text = "\n".join([
            "[10-29 23:59:00] 启动：抓系统声音",
            record("10-29 23:59:58", "整句", 1.0, "第一句", "だいいち"),
            record("10-30 00:00:03", "整句", 1.0, "第二句", "だいに"),
        ])
        records = logimport.parse_session(text)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["t"] - records[0]["t"], 5)

    def test_malformed_latency_line_skipped(self):
        text = "\n".join([
            "[10-29 20:00:00] 启动：抓系统声音",
            "[10-29 20:00:01] 字幕[整句] 延迟 坏s | 没有延迟  〔なし〕",
            record("10-29 20:00:02", "整句", 1.0, "好的", "はい"),
        ])
        self.assertEqual([r["zh"] for r in logimport.parse_session(text)], ["好的"])


class CuesTests(unittest.TestCase):
    def test_timing_uses_latency(self):
        text = "\n".join([
            "[10-29 20:00:00] 启动：抓系统声音",
            record("10-29 20:00:10", "整句", 4.0, "第一句", "だいいち"),
            record("10-29 20:00:13", "整句", 4.0, "第二句", "だいに"),
        ])
        cues = logimport.cues_from_records(logimport.parse_session(text))
        self.assertEqual(len(cues), 2)
        # 第一条起点 10-4=6 秒、第二条 13-4=9 秒 → 相对第一条分别是 0 和 3
        self.assertEqual(cues[0]["start"], 0.0)
        self.assertEqual(cues[1]["start"], 3.0)
        self.assertEqual(cues[0]["end"], 3.0)      # 终点 = 下一条起点

    def test_never_overlap_and_at_least_min(self):
        # 两条几乎同时：第二条只比第一条晚 0.1 秒，也不能压到一起
        text = "\n".join([
            "[10-29 20:00:00] 启动：抓系统声音",
            record("10-29 20:00:10", "整句", 5.0, "第一句", "だいいち"),
            record("10-29 20:00:10", "整句", 5.0, "第二句", "だいに"),
        ])
        cues = logimport.cues_from_records(logimport.parse_session(text))
        self.assertEqual(len(cues), 2)
        self.assertGreaterEqual(cues[0]["end"], cues[0]["start"] + 0.79)
        self.assertGreaterEqual(cues[1]["start"], cues[0]["end"] - 0.01)

    def test_last_cue_gets_estimated_duration(self):
        text = "\n".join([
            "[10-29 20:00:00] 启动：抓系统声音",
            record("10-29 20:00:10", "整句", 2.0, "只有一句", "ひとつだけ"),
        ])
        cues = logimport.cues_from_records(logimport.parse_session(text))
        self.assertEqual(len(cues), 1)
        self.assertGreaterEqual(cues[0]["end"] - cues[0]["start"], 1.19)
        self.assertLessEqual(cues[0]["end"] - cues[0]["start"], 8.0)

    def test_partial_lines_ignored(self):
        text = "\n".join([
            "[10-29 20:00:00] 启动：抓系统声音",
            record("10-29 20:00:10", "半句", 3.0, "还没说完", "まだ"),
            record("10-29 20:00:12", "整句", 3.0, "说完了", "おわり"),
        ])
        cues = logimport.cues_from_records(logimport.parse_session(text))
        self.assertEqual([c["zh"] for c in cues], ["说完了"])

    def test_duplicate_whole_line_collapsed(self):
        text = "\n".join([
            "[10-29 20:00:00] 启动：抓系统声音",
            record("10-29 20:00:10", "整句", 3.0, "同一句", "おなじ"),
            record("10-29 20:00:10", "整句", 3.0, "同一句", "おなじ"),
            record("10-29 20:00:14", "整句", 3.0, "下一句", "つぎ"),
        ])
        cues = logimport.cues_from_records(logimport.parse_session(text))
        self.assertEqual([c["zh"] for c in cues], ["同一句", "下一句"])

    def test_no_whole_lines_returns_empty(self):
        text = "\n".join([
            "[10-29 20:00:00] 启动：抓系统声音",
            record("10-29 20:00:10", "半句", 3.0, "只有半句", "まだ"),
        ])
        self.assertEqual(logimport.cues_from_records(logimport.parse_session(text)), [])


class ReadSessionTests(unittest.TestCase):
    def test_reads_file_and_reports_bounds(self):
        text = "\n".join([
            "[10-29 20:29:14] 启动：抓系统声音",
            record("10-29 20:29:20", "整句", 2.0, "第一句", "だいいち"),
            record("10-29 20:30:01", "整句", 2.0, "第二句", "だいに"),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "实时字幕_运行记录.txt"
            path.write_text(text + "\n", encoding="utf-8")
            result = logimport.read_session(path)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["started"], "10-29 20:29:14")
        self.assertEqual(result["last"], "10-29 20:30:01")

    def test_missing_file_returns_empty(self):
        result = logimport.read_session(Path(tempfile.gettempdir()) / "不存在的记录_abc.txt")
        self.assertEqual(result["cues"], [])
        self.assertEqual(result["count"], 0)

    def test_garbage_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.txt"
            path.write_bytes(b"\xff\xfe\x00garbage")
            result = logimport.read_session(path)
        self.assertEqual(result["count"], 0)


if __name__ == "__main__":
    unittest.main()
