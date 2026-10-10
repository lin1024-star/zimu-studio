"""时间轴自查的测试。

对应实测结论：与音频对齐校验在 BGM 铺满的素材上做不了
（见 timeline_qc 模块开头的记录），所以改用"不碰音频、只查字幕硬伤"这一套。
这里把每一项检查都钉死，包括"该修的不该修"的边界。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
from core import Cue  # noqa: E402
import timeline_qc as qc  # noqa: E402


def make(*rows):
    """rows: (id, start, end, source, zh)"""
    return [Cue(*r) for r in rows]


def kinds(result):
    return sorted(result["counts"].keys())


class CheckTests(unittest.TestCase):
    def test_clean_timeline_reports_nothing(self):
        cues = make((1, 0.0, 2.0, "こんにちは", "你好"),
                    (2, 2.5, 4.5, "元気ですか", "你好吗"),
                    (3, 5.0, 7.5, "はい", "是"))
        result = qc.check_timeline(cues)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["counts"], {})
        self.assertIn("通过", qc.summarize(result))

    def test_reversed_time_is_error(self):
        result = qc.check_timeline(make((1, 5.0, 4.0, "あ", "啊")))
        self.assertIn("reversed", result["counts"])
        self.assertEqual(result["issues"][0]["severity"], "error")

    def test_zero_length_is_error(self):
        result = qc.check_timeline(make((1, 3.0, 3.0, "あ", "啊")))
        self.assertIn("reversed", result["counts"])

    def test_overlap_is_error(self):
        result = qc.check_timeline(make((1, 0.0, 3.0, "あ", "啊"),
                                        (2, 2.0, 4.0, "い", "一")))
        self.assertIn("overlap", result["counts"])
        self.assertEqual(result["issues"][0]["severity"], "error")

    def test_empty_text_is_error(self):
        result = qc.check_timeline(make((1, 0.0, 2.0, "   ", "")))
        self.assertIn("empty", result["counts"])

    def test_punctuation_only_counts_as_empty(self):
        result = qc.check_timeline(make((1, 0.0, 2.0, "。。。", "")))
        self.assertIn("empty", result["counts"])

    def test_tiny_gap_is_warning(self):
        result = qc.check_timeline(make((1, 0.0, 2.0, "あ", "啊"),
                                        (2, 2.02, 4.0, "い", "一")))
        self.assertIn("tiny_gap", result["counts"])
        self.assertEqual([i for i in result["issues"] if i["kind"] == "tiny_gap"][0]["severity"],
                         "warn")

    def test_normal_gap_is_fine(self):
        result = qc.check_timeline(make((1, 0.0, 2.0, "あ", "啊"),
                                        (2, 2.5, 4.0, "い", "一")))
        self.assertEqual(result["issues"], [])

    def test_too_short(self):
        result = qc.check_timeline(make((1, 0.0, 0.3, "あ", "啊")))
        self.assertIn("too_short", result["counts"])

    def test_too_long(self):
        result = qc.check_timeline(make((1, 0.0, 9.5, "あ" * 4, "啊")))
        self.assertIn("too_long", result["counts"])

    def test_too_fast_reading_speed(self):
        # 还没翻译时按原文算：2 秒里 30 个字 → 15 字/秒
        result = qc.check_timeline(make((1, 0.0, 2.0, "あ" * 30, "")))
        self.assertIn("too_fast", result["counts"])

    def test_reading_speed_uses_chinese_when_present(self):
        """有中文就按中文算 —— 观众真正要看的是中文那一行。

        实测教训：AutoKiri 那种原文 SRT 的字/秒 中位数是 6.6，
        拿中文标准去卡它，768 条里会报 94 条假警报。
        """
        # 原文很长、中文很短 → 不该报（观众看的是短的那行）
        result = qc.check_timeline(make((1, 0.0, 2.0, "あ" * 40, "短")))
        self.assertNotIn("too_fast", result["counts"])
        # 中文很长 → 该报
        result = qc.check_timeline(make((1, 0.0, 2.0, "短", "中" * 30)))
        self.assertIn("too_fast", result["counts"])

    def test_punctuation_does_not_count_as_reading_load(self):
        """标点不算阅读量 —— 否则「次の動画でお会いしましょう。」会虚高。"""
        # 24 字 / 2 秒 = 12 字/秒，超过 9；加上一堆标点不该有区别
        plain = qc.check_timeline(make((1, 0.0, 2.0, "あ" * 24, "")))
        dotted = qc.check_timeline(make((1, 0.0, 2.0, "あ" * 24 + "。" * 12, "")))
        self.assertEqual(plain["counts"].get("too_fast"), 1)
        self.assertEqual(plain["counts"].get("too_fast"), dotted["counts"].get("too_fast"))
        # 报出来的字数也应该是 24 而不是 36
        msg = [i for i in dotted["issues"] if i["kind"] == "too_fast"][0]["message"]
        self.assertIn("24 字", msg)
        self.assertNotIn("36 字", msg)

    def test_too_fast_message_says_which_language(self):
        """要说清是按中文还是按原文算的，不然看不懂为什么报。"""
        result = qc.check_timeline(make((1, 0.0, 2.0, "あ" * 30, "")))
        msg = [i for i in result["issues"] if i["kind"] == "too_fast"][0]["message"]
        self.assertIn("原文", msg)
        result = qc.check_timeline(make((1, 0.0, 2.0, "短", "中" * 30)))
        msg = [i for i in result["issues"] if i["kind"] == "too_fast"][0]["message"]
        self.assertIn("中文", msg)

    def test_short_but_plausible_line_is_not_too_fast(self):
        # 「はい」2 个字 1 秒，不算快
        result = qc.check_timeline(make((1, 0.0, 1.0, "はい", "是")))
        self.assertNotIn("too_fast", result["counts"])

    def test_long_gap_is_info(self):
        result = qc.check_timeline(make((1, 0.0, 2.0, "あ", "啊"),
                                        (2, 12.0, 14.0, "い", "一")))
        self.assertIn("long_gap", result["counts"])
        self.assertEqual([i for i in result["issues"] if i["kind"] == "long_gap"][0]["severity"],
                         "info")

    def test_head_and_tail_silence(self):
        result = qc.check_timeline(make((1, 20.0, 22.0, "あ", "啊")), duration=100.0)
        self.assertIn("head_silence", result["counts"])
        self.assertIn("tail_silence", result["counts"])

    def test_head_silence_not_reported_when_duration_unknown(self):
        result = qc.check_timeline(make((1, 20.0, 22.0, "あ", "啊")))
        self.assertNotIn("head_silence", result["counts"])

    def test_issues_sorted_by_severity_then_time(self):
        cues = make((1, 0.0, 0.3, "あ", "啊"),                    # too_short (warn)
                    (2, 10.0, 30.0, "い", "一"),                  # too_long (warn)
                    (3, 29.0, 31.0, "う", "乌"))                  # overlap (error)
        result = qc.check_timeline(cues)
        sevs = [i["severity"] for i in result["issues"]]
        self.assertEqual(sevs, sorted(sevs, key=lambda s: qc.SEVERITY_ORDER[s]))
        self.assertEqual(result["issues"][0]["severity"], "error")

    def test_empty_list_is_fine(self):
        result = qc.check_timeline([])
        self.assertEqual(result["issues"], [])

    def test_limits_can_be_overridden(self):
        cues = make((1, 0.0, 2.0, "あ", "啊"), (2, 2.5, 4.0, "い", "一"))
        self.assertEqual(qc.check_timeline(cues)["issues"], [])
        strict = qc.check_timeline(cues, limits={"min_duration": 3.0})
        self.assertIn("too_short", strict["counts"])

    def test_every_kind_has_explanation(self):
        """每种问题都要有中文说明，界面直接显示 —— 不能漏。"""
        for kind in ("reversed", "empty", "overlap", "tiny_gap", "too_short",
                     "too_long", "too_fast", "long_gap", "head_silence", "tail_silence"):
            self.assertIn(kind, qc.KINDS)
            title, advice = qc.KINDS[kind]
            self.assertTrue(title and advice, kind)

    def test_summarize_counts_by_severity(self):
        cues = make((1, 0.0, 0.3, "あ", "啊"), (2, 0.5, 0.9, "い", "一"))
        text = qc.summarize(qc.check_timeline(cues))
        self.assertIn("警告", text)


class FixSafeTests(unittest.TestCase):
    def test_overlap_is_fixed_by_shortening_earlier(self):
        cues = make((1, 0.0, 3.0, "あ", "啊"), (2, 2.0, 4.0, "い", "一"))
        fixed, changes = qc.fix_safe(cues)
        self.assertEqual(len(changes), 1)
        self.assertLessEqual(fixed[0].end, fixed[1].start)
        self.assertEqual(qc.check_timeline(fixed)["counts"].get("overlap"), None)

    def test_tiny_gap_is_widened(self):
        cues = make((1, 0.0, 2.0, "あ", "啊"), (2, 2.02, 4.0, "い", "一"))
        fixed, changes = qc.fix_safe(cues)
        self.assertEqual(len(changes), 1)
        self.assertNotIn("tiny_gap", qc.check_timeline(fixed)["counts"])

    def test_fix_does_not_eat_the_previous_line(self):
        """前一条只剩 0.12 秒时收不动了，不能把它收成负的。"""
        cues = make((1, 0.0, 0.12, "あ", "啊"), (2, 0.10, 2.0, "い", "一"))
        fixed, _ = qc.fix_safe(cues)
        for cue in fixed:
            self.assertGreater(cue.end, cue.start)
            self.assertGreaterEqual(cue.end - cue.start, 0.099)

    def test_reversed_gets_visible_duration(self):
        cues = make((1, 5.0, 5.0, "あ", "啊"))
        fixed, changes = qc.fix_safe(cues)
        self.assertEqual(len(changes), 1)
        self.assertGreater(fixed[0].end, fixed[0].start)

    def test_text_is_never_touched(self):
        """修时间轴绝不能改文字 —— 这是红线。"""
        cues = make((1, 0.0, 3.0, "原文です", "译文"), (2, 2.0, 4.0, "次", "二"))
        fixed, _ = qc.fix_safe(cues)
        self.assertEqual([c.source for c in fixed], ["原文です", "次"])
        self.assertEqual([c.zh for c in fixed], ["译文", "二"])

    def test_ids_are_kept(self):
        cues = make((7, 0.0, 3.0, "あ", "啊"), (9, 2.0, 4.0, "い", "一"))
        fixed, _ = qc.fix_safe(cues)
        self.assertEqual([c.id for c in fixed], [7, 9])

    def test_too_short_and_too_long_are_not_touched(self):
        """这两类要重排句子，机器不许自动改。"""
        cues = make((1, 0.0, 0.2, "あ", "啊"), (2, 5.0, 20.0, "い", "一"))
        fixed, changes = qc.fix_safe(cues)
        self.assertEqual(changes, [])
        self.assertEqual([(c.start, c.end) for c in fixed], [(0.0, 0.2), (5.0, 20.0)])

    def test_clean_timeline_is_untouched(self):
        cues = make((1, 0.0, 2.0, "あ", "啊"), (2, 2.5, 4.0, "い", "一"))
        fixed, changes = qc.fix_safe(cues)
        self.assertEqual(changes, [])
        self.assertEqual([(c.start, c.end) for c in fixed], [(0.0, 2.0), (2.5, 4.0)])

    def test_fix_is_idempotent(self):
        """修完再修一次，不该再改动 —— 否则一键修会一直"有变化"。"""
        cues = make((1, 0.0, 3.0, "あ", "啊"), (2, 2.0, 4.0, "い", "一"),
                    (3, 4.01, 6.0, "う", "乌"))
        once, _ = qc.fix_safe(cues)
        twice, changes2 = qc.fix_safe(once)
        self.assertEqual(changes2, [])
        self.assertEqual([(c.start, c.end) for c in once], [(c.start, c.end) for c in twice])

    def test_unsorted_input_is_handled(self):
        cues = make((2, 2.0, 4.0, "い", "一"), (1, 0.0, 3.0, "あ", "啊"))
        fixed, changes = qc.fix_safe(cues)
        self.assertEqual(len(changes), 1)
        self.assertLessEqual(fixed[0].end, fixed[1].start)


class SaveFixTests(unittest.TestCase):
    """一键修要写盘、要备份 —— 备份读不回来的话「还原」就是废的。

    踩过的坑：load_project 要求 project 里 version == 1
    （那是**项目格式版本**，不是软件版本号），我一开始写成 "2.1"，
    备份就再也读不回来了。所以这里专门验证备份能 load_project。
    """

    def _project(self):
        return {"version": 1, "name": "测试", "cues": [
            {"id": 1, "start": 0.0, "end": 3.0, "source": "あ", "zh": "啊"},
            {"id": 2, "start": 2.0, "end": 4.0, "source": "い", "zh": "一"},
            {"id": 3, "start": 4.02, "end": 6.0, "source": "う", "zh": "乌"},
            {"id": 4, "start": 8.0, "end": 20.0, "source": "え", "zh": "诶"},
        ]}

    def test_backup_is_loadable_and_holds_old_values(self):
        import tempfile
        from pathlib import Path
        from core import load_project, save_project
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            save_project(path, self._project())
            _updated, changes, backup = qc.save_fix(path, self._project())
            self.assertEqual(len(changes), 2)
            self.assertIsNotNone(backup)
            self.assertTrue(backup.exists())
            old = load_project(backup)          # ← 必须读得回来
            self.assertEqual(old["cues"][0]["end"], 3.0)
            self.assertEqual(old["cues"][2]["start"], 4.02)

    def test_saved_file_has_no_overlap_left(self):
        import tempfile
        from pathlib import Path
        from core import load_project, save_project
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            save_project(path, self._project())
            qc.save_fix(path, self._project())
            saved = load_project(path)
            result = qc.check_timeline(qc.cues_from_rows(saved["cues"]))
            self.assertNotIn("overlap", result["counts"])
            self.assertNotIn("tiny_gap", result["counts"])
            self.assertIn("too_long", result["counts"])     # 超长的不许动

    def test_second_run_changes_nothing(self):
        import tempfile
        from pathlib import Path
        from core import save_project
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            save_project(path, self._project())
            updated, _changes, _backup = qc.save_fix(path, self._project())
            _again, changes2, backup2 = qc.save_fix(path, updated)
            self.assertEqual(changes2, [])
            self.assertIsNone(backup2, "没改动就不该产生备份文件")

    def test_untouched_project_makes_no_backup(self):
        import tempfile
        from pathlib import Path
        from core import save_project
        clean = {"version": 1, "cues": [
            {"id": 1, "start": 0.0, "end": 2.0, "source": "あ", "zh": "啊"},
            {"id": 2, "start": 2.5, "end": 4.0, "source": "い", "zh": "一"}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            save_project(path, clean)
            _p, changes, backup = qc.save_fix(path, clean)
            self.assertEqual(changes, [])
            self.assertIsNone(backup)

    def test_text_never_changed_by_save_fix(self):
        import tempfile
        from pathlib import Path
        from core import load_project, save_project
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            save_project(path, self._project())
            qc.save_fix(path, self._project())
            saved = load_project(path)
            self.assertEqual([r["source"] for r in saved["cues"]], ["あ", "い", "う", "え"])
            self.assertEqual([r["zh"] for r in saved["cues"]], ["啊", "一", "乌", "诶"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
