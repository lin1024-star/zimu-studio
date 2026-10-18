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


class CrossSplitTest(unittest.TestCase):
    """跨条拆词：只是**标出来**，不自动合并（2026-10-18 实测后改的）。

    原计划是自动合并（前一条以「假名 + 单个汉字」结尾、且下一条紧贴）。
    拿本机 24 个真实项目跑了一遍：命中 50 处，逐条看约 30 处是正常断句
    （「目の出し方」｜「オルト…」、「薬局の人」｜「300万…」、「これ、車」｜「車、ちょっと」），
    只有约 20 处是真拆词（感情、請求、押し、入り、使って、最初、聞いて、周り、彼女、友達、道路）。
    自动合并＝修一个词、错一片轴，所以改成只列进「待确认」。
    """

    def test_kana_plus_kanji_then_tight_next_is_found(self):
        cues = [Cue(1, 0.0, 1.0, "もっと頑"), Cue(2, 1.05, 2.0, "張るよ")]
        found = polish.find_cross_splits(cues)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["merged"], "もっと頑張るよ")
        self.assertEqual((found[0]["id"], found[0]["next_id"]), (1, 2))

    def test_kanji_plus_kana_next_is_found_too(self):
        # 「周」+「り」= 周り
        cues = [Cue(1, 0.0, 1.0, "あの周"), Cue(2, 1.1, 2.0, "りを歩く")]
        found = polish.find_cross_splits(cues)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["merged"], "あの周りを歩く")

    def test_candidates_are_never_merged_by_polish(self):
        """关键：整条整理流程跑完，条数/文字一个字都不许变（只报告）。"""
        cues = [Cue(1, 0.0, 1.0, "もっと頑"), Cue(2, 1.05, 2.0, "張るよ"),
                Cue(3, 5.0, 6.0, "そうですね")]
        out, report = polish.polish(cues)
        self.assertEqual([c.source for c in out], [c.source for c in cues])
        self.assertEqual(len(out), len(cues))
        self.assertEqual(report["split_candidates"], 1)

    def test_split_candidate_goes_into_review(self):
        cues = [Cue(1, 0.0, 1.0, "もっと頑"), Cue(2, 1.05, 2.0, "張るよ")]
        _out, report = polish.polish(cues)
        kinds = [item["kind"] for item in report["review"]]
        self.assertIn("split", kinds)

    def test_gap_too_large_is_not_found(self):
        cues = [Cue(1, 0.0, 1.0, "もっと頑"), Cue(2, 1.5, 2.5, "張るよ")]
        self.assertEqual(polish.find_cross_splits(cues), [])

    def test_sentence_ending_in_kana_is_not_found(self):
        cues = [Cue(1, 0.0, 1.0, "そうですね"), Cue(2, 1.05, 2.0, "はい")]
        self.assertEqual(polish.find_cross_splits(cues), [])

    def test_two_kanji_ending_is_not_found(self):
        # 前一条最后两个字都是汉字（不是「假名 + 单个汉字」）
        cues = [Cue(1, 0.0, 1.0, "いい感情"), Cue(2, 1.05, 2.0, "を持つ")]
        self.assertEqual(polish.find_cross_splits(cues), [])


class RepeatBlockTest(unittest.TestCase):
    """长距离重复：同一段（连续几条）在片子里别处又演一遍 —— 只提醒，不删。"""

    def _long_cues(self):
        texts = ["おはようございます", "今日はいい天気ですね", "散歩でも行きますか", "そうしましょう"]
        cues = [Cue(i + 1, i * 1.0, i * 1.0 + 0.9, texts[i]) for i in range(4)]
        cues += [Cue(i + 5, 60.0 + i * 1.0, 60.0 + i * 1.0 + 0.9, texts[i]) for i in range(4)]
        return cues

    def test_far_apart_block_is_reported(self):
        found = polish.find_repeated_blocks(self._long_cues())
        self.assertEqual(len(found), 1)
        item = found[0]
        self.assertEqual((item["first_id"], item["first_last_id"]), (1, 4))
        self.assertEqual((item["second_id"], item["second_last_id"]), (5, 8))
        self.assertEqual(item["cues"], 4)
        self.assertGreaterEqual(item["gap"], polish.REPEAT_MIN_GAP)

    def test_short_common_phrases_are_not_reported(self):
        # 「あの」「なんだろう」这种口头语两分钟里说了两遍，不算"整段重复"
        cues = [Cue(1, 0, 1, "あの"), Cue(2, 1.1, 2, "なんだろう"),
                Cue(3, 120, 121, "あの"), Cue(4, 121.1, 122, "なんだろう")]
        self.assertEqual(polish.find_repeated_blocks(cues), [])

    def test_too_close_is_not_reported(self):
        cues = self._long_cues()
        for cue in cues[4:]:
            cue.start -= 55
            cue.end -= 55
        self.assertEqual(polish.find_repeated_blocks(cues), [])

    def test_adjoining_duplicates_are_left_to_fold(self):
        texts = ["おはようございます", "今日はいい天気ですね", "散歩でも行きますか"]
        cues = [Cue(i + 1, i * 1.0, i * 1.0 + 0.9, texts[i % 3]) for i in range(6)]
        self.assertEqual(polish.find_repeated_blocks(cues), [])

    def test_polish_reports_but_never_deletes(self):
        cues = self._long_cues()
        out, report = polish.polish(cues)
        self.assertEqual([c.source for c in out], [c.source for c in cues])
        self.assertEqual(report["repeat_candidates"], 1)
        self.assertIn("repeat", [item["kind"] for item in report["review"]])


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
