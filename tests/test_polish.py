import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import core
import polish
from core import Cue, load_project, restore_axis_cues, save_project, tidy_axis


class ElongationTest(unittest.TestCase):
    def test_long_repeat_is_compressed_to_two(self):
        # 实测素材里的「これってああああああ」。
        self.assertEqual(polish.compress_elongation("これってああああああ"), "これってああ")

    def test_short_repeat_is_left_alone(self):
        self.assertEqual(polish.compress_elongation("ああ"), "ああ")

    def test_every_change_is_reported(self):
        cues, changes = polish.compress_elongations([Cue(1, 0, 1, "ああああ")])
        self.assertEqual(cues[0].source, "ああ")
        self.assertEqual(changes, [{"kind": "elongation", "id": 1,
                                    "before": "ああああ", "after": "ああ"}])


class FoldTest(unittest.TestCase):
    def test_exact_duplicate_is_folded_and_reported(self):
        cues, changes = polish.fold_adjacent([Cue(1, 0, 1, "そうだよ"), Cue(2, 1.1, 2, "そうだよ")])
        self.assertEqual([c.id for c in cues], [1])
        self.assertEqual(changes[0]["kind"], "fold")
        self.assertEqual(changes[0]["kept"], "そうだよ")

    def test_containment_is_not_folded(self):
        # 主播真的会重复说话；只要不是一字不差就不许动。
        cues, changes = polish.fold_adjacent([Cue(1, 0, 1, "降りてたもんね"), Cue(2, 1.1, 2, "ね")])
        self.assertEqual(len(cues), 2)
        self.assertEqual(changes, [])

    def test_punctuation_only_difference_still_folds(self):
        cues, _ = polish.fold_adjacent([Cue(1, 0, 1, "そうだよ"), Cue(2, 1.1, 2, "そうだよ。")])
        self.assertEqual(len(cues), 1)


class FragmentTest(unittest.TestCase):
    def test_adjacent_one_char_fragment_is_merged(self):
        cues, changes = polish.merge_fragments([Cue(1, 0, 2, "そうだよ"), Cue(2, 2.1, 2.4, "ね")])
        self.assertEqual([c.id for c in cues], [1])
        self.assertEqual(changes[0]["dropped"], "ね")

    def test_isolated_fragment_is_left_alone(self):
        # 前后隔了好几秒的孤立碎片，并到哪边都不对，宁可不动。
        cues, changes = polish.merge_fragments([Cue(1, 0, 2, "そうだよ"), Cue(2, 8, 8.3, "ね")])
        self.assertEqual(len(cues), 2)
        self.assertEqual(changes, [])

    def test_real_short_line_is_not_merged(self):
        # 「ほんと?」只有 0.34 秒，但它是一句完整的话；合并就是吞内容。
        cues, changes = polish.merge_fragments([Cue(1, 0, 2, "そうだよ"), Cue(2, 2.05, 2.39, "ほんと?")])
        self.assertEqual(len(cues), 2)
        self.assertEqual(changes, [])


class ReportTest(unittest.TestCase):
    def cues(self):
        return [Cue(1, 0, 2, "そうだよ"), Cue(2, 2.1, 2.4, "ね"), Cue(3, 3, 4, "ああああ"),
                Cue(4, 4.1, 5, "そうだよ"), Cue(5, 5.1, 6, "そうだよ"),
                Cue(6, 20, 21, "ほんと?"), Cue(7, 30, 30.26, "ち")]

    def test_report_lists_every_change_in_order(self):
        out, report = polish.polish(self.cues(), gap_threshold=3.0)
        self.assertEqual(report["cues_before"], 7)
        self.assertEqual(report["cues_after"], 5)
        self.assertEqual([c["kind"] for c in report["changes"]],
                         ["elongation", "fold", "merge"])
        self.assertEqual([c.id for c in out], [1, 2, 3, 4, 5])

    def test_review_lists_gaps_and_remaining_short_cues(self):
        _, report = polish.polish(self.cues(), gap_threshold=3.0)
        gaps = [r for r in report["review"] if r["kind"] == "gap"]
        shorts = [r for r in report["review"] if r["kind"] == "short"]
        self.assertEqual(len(gaps), 2)
        self.assertEqual([r["text"] for r in shorts], ["ち"])

    def test_overlap_count_is_reported(self):
        _, report = polish.polish([Cue(1, 0, 2, "a"), Cue(2, 1.5, 3, "b")])
        self.assertEqual(report["overlaps_after"], 1)


class TidyAxisTest(unittest.TestCase):
    def collect(self):
        lines = []
        return lines, (lambda kind, value: lines.append(value))

    def test_each_change_is_logged(self):
        lines, emit = self.collect()
        cues = [Cue(1, 0, 2, "そうだよ"), Cue(2, 2.1, 2.4, "ね")]
        polished, report = tidy_axis(cues, emit)
        self.assertEqual(len(polished), 1)
        self.assertTrue(any("原文 2 条 → 1 条" in line for line in lines))
        self.assertTrue(any("并入碎片" in line for line in lines))
        self.assertTrue(any("可还原" in line for line in lines))

    def test_quiet_when_nothing_to_do(self):
        lines, emit = self.collect()
        tidy_axis([Cue(1, 0, 2, "そうだよ")], emit)
        self.assertTrue(any("未发现重复或碎片" in line for line in lines))

    def test_long_gap_is_only_reported(self):
        lines, emit = self.collect()
        cues = [Cue(1, 0, 2, "a"), Cue(2, 40, 41, "b")]
        polished, report = tidy_axis(cues, emit)
        self.assertEqual([c.id for c in polished], [1, 2])
        self.assertEqual((polished[0].start, polished[1].end), (0, 41))
        self.assertTrue(any("没有字幕" in line for line in lines))

    def test_change_line_reads_like_a_sentence(self):
        self.assertEqual(core.axis_change_text({"kind": "elongation", "id": 3,
                                                "before": "ああああ", "after": "ああ"}),
                         "第 3 条：拖音「ああああ」→「ああ」")


class RestoreAxisTest(unittest.TestCase):
    def test_exact_times_keep_their_translation(self):
        raw = [{"id": 1, "start": 0.0, "end": 2.0, "source": "a", "zh": ""},
               {"id": 2, "start": 3.0, "end": 4.0, "source": "b", "zh": ""}]
        current = [{"id": 1, "start": 0.0, "end": 2.0, "source": "a", "zh": "甲"},
                   {"id": 2, "start": 3.0, "end": 4.0, "source": "b", "zh": "乙"}]
        self.assertEqual([r["zh"] for r in restore_axis_cues(raw, current)], ["甲", "乙"])

    def test_shifted_times_are_matched_by_overlap(self):
        # 折叠会把 end 顺延，还原时按重叠取回中文。
        raw = [{"id": 1, "start": 0.0, "end": 2.0, "source": "a", "zh": ""}]
        current = [{"id": 1, "start": 0.0, "end": 2.4, "source": "a", "zh": "甲"}]
        self.assertEqual(restore_axis_cues(raw, current)[0]["zh"], "甲")

    def test_one_translation_is_never_reused_twice(self):
        raw = [{"id": 1, "start": 0.0, "end": 2.0, "source": "a", "zh": ""},
               {"id": 2, "start": 2.1, "end": 2.4, "source": "b", "zh": ""}]
        current = [{"id": 1, "start": 0.0, "end": 2.4, "source": "a", "zh": "甲"}]
        self.assertEqual([r["zh"] for r in restore_axis_cues(raw, current)], ["甲", ""])

    def test_ids_are_renumbered_from_one(self):
        raw = [{"id": 7, "start": 0.0, "end": 1.0, "source": "a", "zh": ""},
               {"id": 9, "start": 2.0, "end": 3.0, "source": "b", "zh": ""}]
        self.assertEqual([r["id"] for r in restore_axis_cues(raw, [])], [1, 2])


class ProjectStorageTest(unittest.TestCase):
    def test_axis_backup_survives_a_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            save_project(path, {"version": 1, "cues": [], "axis_raw": [{"id": 1, "zh": ""}],
                                "axis_review": [{"kind": "gap", "seconds": 5.0}]})
            loaded = load_project(path)
            self.assertEqual(loaded["axis_raw"], [{"id": 1, "zh": ""}])
            self.assertEqual(loaded["axis_review"], [{"kind": "gap", "seconds": 5.0}])

    def test_credentials_are_still_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            save_project(path, {"version": 1, "cues": [], "api_key": "sk-secret"})
            self.assertNotIn("api_key", load_project(path))


class RunJobWiringTest(unittest.TestCase):
    def run_job(self, source, cues, tidy=True, extra=None):
        config = {"input": str(source), "output": str(source.parent), "asr_model": "turbo",
                  "language": "ja", "device": "cpu", "model_dir": str(source.parent),
                  "mode": "transcribe", "tidy_axis": tidy}
        config.update(extra or {})
        events = []
        with patch.object(core, "transcribe", return_value=(cues, "ja")):
            core.run_job(config, threading.Event(), lambda kind, value: events.append((kind, value)))
        project_path = Path([v for k, v in events if k == "done"][0]["project"])
        return load_project(project_path), [v for k, v in events if k == "log"]

    def test_asr_result_is_tidied_and_the_original_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "clip.mp4"
            source.write_bytes(b"fake")
            cues = [Cue(1, 0, 2, "そうだよ"), Cue(2, 2.1, 2.4, "ね"), Cue(3, 40, 41, "ほんと?")]
            project, logs = self.run_job(source, cues)
            self.assertEqual(len(project["cues"]), 2)
            self.assertEqual(len(project["axis_raw"]), 3)
            self.assertTrue(any("轴整理" in line for line in logs))
            self.assertTrue(any("待确认" in line for line in logs))

    def test_imported_srt_is_left_untouched(self):
        srt = "1\n00:00:00,000 --> 00:00:02,000\nそうだよ\n\n2\n00:00:02,100 --> 00:00:02,400\nね\n\n"
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "hand.srt"
            source.write_text(srt, encoding="utf-8")
            project, _ = self.run_job(source, [])
            self.assertEqual(len(project["cues"]), 2)
            self.assertNotIn("axis_raw", project)

    def test_switching_the_option_off_skips_tidying(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "clip.mp4"
            source.write_bytes(b"fake")
            cues = [Cue(1, 0, 2, "そうだよ"), Cue(2, 2.1, 2.4, "ね")]
            project, logs = self.run_job(source, cues, tidy=False)
            self.assertEqual(len(project["cues"]), 2)
            self.assertFalse(any("轴整理" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
