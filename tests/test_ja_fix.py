# -*- coding: utf-8 -*-
"""日文纠正表的测试。

重点盯三件事：
  1. 长的词先替换（「お食系官」不能被「お食」抢先，否则会造出「汚職系官」）
  2. 只改日文原文，中文 / 时间轴 / 编号 / 条数一个字都不动
  3. 表丢了、写坏了都不能拦着识别
"""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import core
import ja_fix
from core import Cue

# 真实素材里出现过的 5 种错法（SURGECITY 日语直播，原音都是 おしょく）
REAL_TABLE = [
    ("お食系官", "汚職警官"),
    ("お食系感", "汚職警官"),
    ("お食器官", "汚職警官"),
    ("お食警官", "汚職警官"),
    ("お食", "汚職"),
]


class ParseTableTest(unittest.TestCase):
    def test_comments_and_blank_lines_are_skipped(self):
        text = "# 说明\n\n   \nお食 = 汚職\n# 又一行说明\n"
        self.assertEqual(ja_fix.parse_table(text), [("お食", "汚職")])

    def test_longer_wrong_word_comes_first(self):
        table = ja_fix.parse_table("お食 = 汚職\nお食系官 = 汚職警官\n")
        self.assertEqual(table, [("お食系官", "汚職警官"), ("お食", "汚職")])

    def test_broken_lines_are_skipped_one_by_one(self):
        table = ja_fix.parse_table("没有等号的一行\n= 汚職\nお食 =\nお食\nお食 = お食\nお食 = 汚職\n")
        self.assertEqual(table, [("お食", "汚職")])

    def test_duplicate_keeps_the_first(self):
        table = ja_fix.parse_table("お食 = 汚職\nお食 = 别的\n")
        self.assertEqual(table, [("お食", "汚職")])

    def test_value_may_contain_spaces(self):
        self.assertEqual(ja_fix.parse_table("お食 = 汚 職\n"), [("お食", "汚 職")])


class ApplyTextTest(unittest.TestCase):
    def test_the_real_forms_are_all_fixed(self):
        cases = {
            "お食大好き警察官": "汚職大好き警察官",
            "お食警官だから": "汚職警官だから",
            "お食系官代表やからね": "汚職警官代表やからね",
            "お食系感になったよ": "汚職警官になったよ",
            "ん お食器官だからね": "ん 汚職警官だからね",
            "自分でお食してる人はお食って言わないの": "自分で汚職してる人は汚職って言わないの",
        }
        for before, after in cases.items():
            self.assertEqual(ja_fix.apply_text(before, REAL_TABLE)[0], after, before)

    def test_long_word_wins_so_no_new_word_is_invented(self):
        # 只写「お食=汚職」的话，这里会变成「汚職系官」——一个日语里不存在的词。
        fixed, _ = ja_fix.apply_text("お食系官", REAL_TABLE)
        self.assertEqual(fixed, "汚職警官")
        self.assertNotIn("汚職系官", fixed)

    def test_two_occurrences_in_one_line_are_both_counted(self):
        fixed, hits = ja_fix.apply_text("自分でお食してる人はお食って言わないの", REAL_TABLE)
        self.assertEqual(fixed.count("汚職"), 2)
        self.assertEqual(hits, [("お食", "汚職", 2)])

    def test_counts_are_reported_per_rule(self):
        _, hits = ja_fix.apply_text("お食系官とお食器官とお食", REAL_TABLE)
        self.assertEqual(dict((w, n) for w, _, n in hits),
                         {"お食系官": 1, "お食器官": 1, "お食": 1})

    def test_one_pass_only_so_replacements_are_not_rewritten(self):
        # A→B、B→C 同时存在时，A 只能变成 B，不能被接着改成 C。
        table = ja_fix.parse_table("A = B\nB = C\n")
        self.assertEqual(ja_fix.apply_text("A", table)[0], "B")

    def test_empty_table_or_empty_text_changes_nothing(self):
        self.assertEqual(ja_fix.apply_text("お食", [])[0], "お食")
        self.assertEqual(ja_fix.apply_text("", REAL_TABLE)[0], "")
        self.assertEqual(ja_fix.apply_text(None, REAL_TABLE)[0], "")

    def test_applying_twice_is_the_same_as_once(self):
        once = ja_fix.apply_text("お食大好き警察官", REAL_TABLE)[0]
        twice = ja_fix.apply_text(once, REAL_TABLE)[0]
        self.assertEqual(once, twice)

    def test_text_without_hits_is_returned_unchanged(self):
        text = "こんにちは、いい天気ですね"
        self.assertEqual(ja_fix.apply_text(text, REAL_TABLE), (text, []))


class FixCuesTest(unittest.TestCase):
    def cues(self):
        return [Cue(1, 0.0, 2.0, "お食大好き警察官", "最喜欢贪污的警察"),
                Cue(2, 2.5, 4.0, "こんにちは", "你好"),
                Cue(3, 4.5, 6.0, "お食系官代表やからね", "")]

    def test_only_the_japanese_source_is_touched(self):
        cues = self.cues()
        fixed, _ = ja_fix.fix_cues(cues, REAL_TABLE)
        self.assertEqual([c.id for c in fixed], [1, 2, 3])
        self.assertEqual([(c.start, c.end) for c in fixed], [(c.start, c.end) for c in cues])
        self.assertEqual([c.zh for c in fixed], [c.zh for c in cues])
        self.assertEqual(len(fixed), len(cues))

    def test_report_lists_every_change(self):
        fixed, report = ja_fix.fix_cues(self.cues(), REAL_TABLE)
        self.assertEqual(report["cues_changed"], 2)
        self.assertEqual(report["replacements"], 2)
        self.assertEqual(report["by_rule"], {("お食", "汚職"): 1, ("お食系官", "汚職警官"): 1})
        self.assertEqual([c["id"] for c in report["changes"]], [1, 3])
        self.assertEqual(report["changes"][0]["before"], "お食大好き警察官")
        self.assertEqual(report["changes"][0]["after"], "汚職大好き警察官")

    def test_untouched_cues_are_kept_as_they_were(self):
        cues = self.cues()
        fixed, _ = ja_fix.fix_cues(cues, REAL_TABLE)
        self.assertIs(fixed[1], cues[1])

    def test_every_change_is_explainable_by_the_table_alone(self):
        """硬验收：改后的文字，只能由「表里的那几对」换出来，其它字一个都不许动。

        不能用字符级 diff 去比对：「お食系官」→「汚職警官」两条尾巴都带「官」，
        diff 会把「お食系」对「汚職警」当成一整块，看起来像"不属于表里的规则"。
        """
        pairs = set(REAL_TABLE)

        def explainable(before, after):
            from functools import lru_cache

            @lru_cache(maxsize=None)
            def go(i, j):
                if i == len(before) and j == len(after):
                    return True
                if i < len(before) and j < len(after) and before[i] == after[j] and go(i + 1, j + 1):
                    return True
                return any(before.startswith(w, i) and after.startswith(r, j) and go(i + len(w), j + len(r))
                           for w, r in pairs)

            return go(0, 0)

        cues = [Cue(1, 0, 1, "お食大好き警察官"), Cue(2, 1, 2, "お食系官になったから早速人殺しといたそこで"),
                Cue(3, 2, 3, "ブーブーいいよ一人お食器官ながらねー"), Cue(4, 3, 4, "そのまま")]
        fixed, _ = ja_fix.fix_cues(cues, REAL_TABLE)
        for before, after in zip(cues, fixed):
            self.assertTrue(explainable(before.source, after.source),
                            f"{before.source} → {after.source} 里有表解释不了的改动")

    def test_missing_table_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixed, report = ja_fix.fix_cues(self.cues(), root=tmp)
            self.assertEqual(report["cues_changed"], 0)
            self.assertEqual([c.source for c in fixed], [c.source for c in self.cues()])

    def test_count_word_matches_manual_count(self):
        self.assertEqual(ja_fix.count_word(self.cues(), "お食"), 2)
        self.assertEqual(ja_fix.count_word(self.cues(), "無い言葉"), 0)


class TableFileTest(unittest.TestCase):
    def test_load_reads_a_file_from_the_given_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ja_fix.TABLE_NAME).write_text("お食 = 汚職\n", encoding="utf-8")
            self.assertEqual(ja_fix.load_table(tmp), [("お食", "汚職")])

    def test_missing_file_or_folder_is_an_empty_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ja_fix.load_table(tmp), [])
            self.assertEqual(ja_fix.load_table(str(Path(tmp) / "根本不存在")), [])

    def test_ensure_writes_the_seed_only_into_an_existing_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = ja_fix.ensure_table(tmp)
            self.assertTrue(path.is_file())
            self.assertIn("お食系官 = 汚職警官", path.read_text("utf-8"))
            self.assertEqual(len(ja_fix.load_table(tmp)), 5)

    def test_ensure_does_not_create_a_missing_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "还没有这个目录"
            ja_fix.ensure_table(str(missing))
            self.assertFalse(missing.exists())

    def test_ensure_never_overwrites_an_edited_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ja_fix.TABLE_NAME
            path.write_text("お食 = 我的写法\n", encoding="utf-8")
            ja_fix.ensure_table(tmp)
            self.assertEqual(ja_fix.load_table(tmp), [("お食", "我的写法")])

    def test_seed_is_written_with_lf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = ja_fix.ensure_table(tmp)
            self.assertNotIn(b"\r\n", path.read_bytes())


class TableCacheTest(unittest.TestCase):
    def test_table_is_cached_and_reloaded_after_an_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = ja_fix.TableCache(tmp)
            self.assertEqual(cache.get(), [])
            path = Path(tmp) / ja_fix.TABLE_NAME
            path.write_text("お食 = 汚職\n", encoding="utf-8")
            self.assertEqual(cache.get(), [("お食", "汚職")])
            path.write_text("お食 = 改过的\n", encoding="utf-8")
            self.assertEqual(cache.get(), [("お食", "改过的")])

    def test_deleting_the_table_falls_back_to_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ja_fix.TABLE_NAME
            path.write_text("お食 = 汚職\n", encoding="utf-8")
            cache = ja_fix.TableCache(tmp)
            self.assertEqual(len(cache.get()), 1)
            path.unlink()
            self.assertEqual(cache.get(), [])


class ReportLinesTest(unittest.TestCase):
    def test_nothing_changed_means_no_log_noise(self):
        _, report = ja_fix.fix_cues([Cue(1, 0, 1, "こんにちは")], REAL_TABLE)
        self.assertEqual(ja_fix.report_lines(report), [])

    def test_summary_and_each_cue_are_listed(self):
        _, report = ja_fix.fix_cues([Cue(1, 0, 1, "お食大好き")], REAL_TABLE)
        lines = ja_fix.report_lines(report)
        self.assertIn("改了 1 条、共 1 处", lines[0])
        self.assertIn("お食→汚職", lines[0])
        self.assertIn("第 1 条：お食大好き → 汚職大好き", lines[1])

    def test_per_cue_can_be_turned_off(self):
        _, report = ja_fix.fix_cues([Cue(1, 0, 1, "お食大好き")], REAL_TABLE)
        self.assertEqual(len(ja_fix.report_lines(report, per_cue=False)), 1)


class RunJobWiringTest(unittest.TestCase):
    """真实管线：识别出来的原文，在整理轴和翻译之前就该被改对。"""

    def run_job(self, source, cues, root, tidy=True):
        config = {"input": str(source), "output": str(source.parent), "asr_model": "turbo",
                  "language": "ja", "device": "cpu", "model_dir": str(source.parent),
                  "mode": "transcribe", "tidy_axis": tidy}
        events = []
        with patch.dict(os.environ, {"SUBTITLE_STUDIO_ROOT": str(root)}):
            with patch.object(core, "transcribe", return_value=(cues, "ja")):
                core.run_job(config, threading.Event(), lambda kind, value: events.append((kind, value)))
        project_path = Path([v for k, v in events if k == "done"][0]["project"])
        return core.load_project(project_path), [v for k, v in events if k == "log"]

    def test_recognized_text_is_corrected_before_being_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            (root / ja_fix.TABLE_NAME).write_text("お食 = 汚職\nお食系官 = 汚職警官\n", encoding="utf-8")
            source = Path(tmp) / "clip.mp4"
            source.write_bytes(b"fake")
            cues = [Cue(1, 0, 2, "お食大好き警察官"), Cue(2, 3, 4, "お食系官代表やからね")]
            project, logs = self.run_job(source, cues, root)
            self.assertEqual([c["source"] for c in project["cues"]],
                             ["汚職大好き警察官", "汚職警官代表やからね"])
            self.assertTrue(any("日文纠正表" in line for line in logs))
            self.assertTrue(any("第 1 条" in line for line in logs))

    def test_without_a_table_nothing_is_touched_and_nothing_is_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            source = Path(tmp) / "clip.mp4"
            source.write_bytes(b"fake")
            project, logs = self.run_job(source, [Cue(1, 0, 2, "お食大好き警察官")], root)
            self.assertEqual(project["cues"][0]["source"], "お食大好き警察官")
            self.assertFalse(any("日文纠正表" in line for line in logs))
            self.assertEqual(sorted(p.name for p in root.iterdir()), [])


class LiveTranslatorTest(unittest.TestCase):
    """实时字幕那条路：送去翻译之前也要先按表改对（表是同一条数据根目录下的）。"""

    def translator(self, root, **kwargs):
        from live.pipeline import Translator
        with patch.dict(os.environ, {"SUBTITLE_STUDIO_ROOT": str(root)}):
            return Translator("fake-key", "fake-model", lambda: "", lambda item: None, **kwargs)

    def test_sentence_is_corrected_on_the_way_into_the_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ja_fix.TABLE_NAME).write_text("お食系官 = 汚職警官\nお食 = 汚職\n", encoding="utf-8")
            with patch.dict(os.environ, {"SUBTITLE_STUDIO_ROOT": str(root)}):
                translator = self.translator(root)
                translator.submit({"id": 1, "ja": "お食大好き警察官", "start": 0, "end": 1})
                queued = translator.q.get_nowait()
        self.assertEqual(queued["ja"], "汚職大好き警察官")
        self.assertEqual(translator.stats["ja_fixed"], 1)

    def test_every_later_sentence_is_corrected_too_not_just_the_first(self):
        """这条是补丁：真出过一次「只有第一句生效」的 bug。

        `import ja_fix` 原来写在 `if self._ja_tables is None:` 里面，
        第二次调用跳过 import → 局部变量未赋值 → 抛 UnboundLocalError →
        被兜底吞掉 → 之后每一句都原样放行。
        只发一句的测试正好命中"第一次能用"，所以测不出来。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ja_fix.TABLE_NAME).write_text("お食 = 汚職\n", encoding="utf-8")
            with patch.dict(os.environ, {"SUBTITLE_STUDIO_ROOT": str(root)}):
                translator = self.translator(root, max_pending=10)   # 默认只留 3 条，会丢最旧
                for i in range(5):
                    translator.submit({"id": i + 1, "ja": f"お食その{i + 1}", "start": i, "end": i + 1})
                got = [translator.q.get_nowait()["ja"] for _ in range(5)]
        self.assertEqual(got, ["汚職その1", "汚職その2", "汚職その3", "汚職その4", "汚職その5"])
        self.assertEqual(translator.stats["ja_fixed"], 5)

    def test_works_even_if_the_table_cache_was_already_built(self):
        """表缓存已经被建好时（不是第一次调用），也必须照样生效。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ja_fix.TABLE_NAME).write_text("お食 = 汚職\n", encoding="utf-8")
            with patch.dict(os.environ, {"SUBTITLE_STUDIO_ROOT": str(root)}):
                translator = self.translator(root)
                translator._ja_tables = ja_fix.TableCache()      # 先建好，模拟"不是第一次"
                fixed = translator._fix_ja({"id": 1, "ja": "お食大好き", "start": 0, "end": 1})
        self.assertEqual(fixed["ja"], "汚職大好き")

    def test_sentence_without_hits_is_passed_through_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ja_fix.TABLE_NAME).write_text("お食 = 汚職\n", encoding="utf-8")
            with patch.dict(os.environ, {"SUBTITLE_STUDIO_ROOT": str(root)}):
                translator = self.translator(root)
                sentence = {"id": 1, "ja": "こんにちは", "start": 0, "end": 1}
                self.assertIs(translator._fix_ja(sentence), sentence)
        self.assertEqual(translator.stats["ja_fixed"], 0)

    def test_a_broken_table_never_blocks_the_subtitle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"SUBTITLE_STUDIO_ROOT": str(root)}):
                translator = self.translator(root)

                class Boom:
                    def get(self):
                        raise RuntimeError("表读坏了")

                translator._ja_tables = Boom()
                sentence = {"id": 1, "ja": "お食大好き", "start": 0, "end": 1}
                self.assertIs(translator._fix_ja(sentence), sentence)


if __name__ == "__main__":
    unittest.main()
