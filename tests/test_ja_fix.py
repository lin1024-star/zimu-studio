# -*- coding: utf-8 -*-
"""日文纠正表的测试。

重点盯三件事：
  1. 长的词先替换（「お食系官」不能被「お食」抢先，否则会造出「汚職系官」）
  2. 只改日文原文，中文 / 时间轴 / 编号 / 条数一个字都不动
  3. 表丢了、写坏了都不能拦着识别
"""
import os
import re
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

# 真实素材里出现过的 5 种错法（测试素材 日语直播，原音都是 おしょく）
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
            # 2026-10-16：5 → 7。新加「お職警官 / お職」—— 真实素材里那期
            # 还残留 4 处纠正表没覆盖的「お職」（同一个项目里「汚職大好き」翻对了、
            # 「お職大好き」翻成"超喜欢主职"）。计数跟着改，是为了让下次再加规则时
            # 这条断言会提醒你「表变了，去看一眼」。
            self.assertEqual(len(ja_fix.load_table(tmp)), 7)

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


class IssueRecordTest(unittest.TestCase):
    """「这句识别错了」：把看不顺眼的一条记下来，攒着交给懂日语的人。

    使用者不懂日语，填不出"正确的日文"，所以这个功能的关键是
    **程序替他把日文原文抄进去**，他只要写中文备注。
    """

    def cue(self, **over):
        values = {"id": 1070, "start": 2973.93, "end": 2975.39,
                  "source": "お食警官だから", "zh": "因为是食警官嘛"}
        values.update(over)
        return values

    def test_record_writes_a_readable_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, block, status = ja_fix.record_issue("测试素材", self.cue(), "应该是贪污", root=tmp)
            self.assertEqual(status, "new")
            text = Path(path).read_text(encoding="utf-8")
        self.assertIn("日文原文：お食警官だから", text)
        self.assertIn("现在的译文：因为是食警官嘛", text)
        self.assertIn("他觉得不对的地方：应该是贪污", text)
        self.assertIn("第 1070 条", text)
        self.assertIn("49:33.93", text)              # 2973.93 秒 → 49 分 33.93 秒
        self.assertIn("测试素材", text)

    def test_over_an_hour_gets_the_hour_field(self):
        block = ja_fix.format_issue("x", self.cue(start=3900.0, end=3901.5), when="2026-10-12 23:00")
        self.assertIn("1:05:00.00", block)

    def test_the_header_is_written_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            ja_fix.record_issue("p", self.cue(), root=tmp)
            ja_fix.record_issue("p", self.cue(id=2, source="別の行"), root=tmp)
            text = Path(ja_fix.issue_path(tmp)).read_text(encoding="utf-8")
            self.assertEqual(text.count("这里记的是"), 1)
            self.assertEqual(ja_fix.count_issues(tmp), 2)   # 开头的说明不算一条

    def test_same_line_is_not_recorded_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            ja_fix.record_issue("p", self.cue(), "备注", root=tmp)
            _path, _block, status = ja_fix.record_issue("p", self.cue(), "备注", root=tmp)
            self.assertEqual(status, "duplicate")
            self.assertEqual(ja_fix.count_issues(tmp), 1)

    def test_a_different_note_is_a_new_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            ja_fix.record_issue("p", self.cue(), "第一个想法", root=tmp)
            _path, _block, status = ja_fix.record_issue("p", self.cue(), "又想到一点", root=tmp)
            self.assertEqual(status, "new")
            self.assertEqual(ja_fix.count_issues(tmp), 2)

    def test_same_line_in_another_project_is_a_new_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            ja_fix.record_issue("第一期", self.cue(), root=tmp)
            _path, _block, status = ja_fix.record_issue("第二期", self.cue(), root=tmp)
            self.assertEqual(status, "new")

    def test_a_missing_folder_is_an_error_not_a_new_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "还没有这个目录"
            _path, _block, status = ja_fix.record_issue("p", self.cue(), root=str(missing))
            self.assertEqual(status, "error")
            self.assertFalse(missing.exists())

    def test_counting_an_absent_file_is_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ja_fix.count_issues(tmp), 0)

    def test_the_source_is_copied_so_the_user_never_types_japanese(self):
        """这条是这个功能的灵魂：日文由程序抄，使用者只写中文。"""
        block = ja_fix.format_issue("p", self.cue(), note="这里应该是贪污", when="2026-10-12 23:00")
        japanese = block.split("日文原文：")[1].splitlines()[0]
        self.assertEqual(japanese, "お食警官だから")
        note = block.split("他觉得不对的地方：")[1].splitlines()[0]
        self.assertFalse(re.search(r"[\u3040-\u30ff]", note), "备注里不该有假名（使用者不懂日语）")


class FakeClient:
    """假模型：按剧本返回 JSON，不联网。"""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def json_chat(self, system, user, max_tokens=800):
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        return self.payload


class GuessFixTest(unittest.TestCase):
    """拿使用者的中文反馈反推正确日文 —— 这是「自动写纠正表」的核心那一步。

    实测依据：只给这一句 + 上下文，模型自己猜正确写法是 0/5；
    给上「他期望的中文」之后是 5/5。所以这里重点测：他的反馈有没有真的传进去、
    以及模型返回不合逻辑时敢不敢拒收。
    """

    TEXT = "お食大好き警察官"

    def payload(self, **over):
        data = {"wrong": "お食", "right": "汚職", "fixed": "汚職大好き警察官", "why": "同音错字"}
        data.update(over)
        return data

    def test_a_clean_answer_is_accepted(self):
        client = FakeClient(self.payload())
        fix = ja_fix.guess_fix(self.TEXT, "我是最喜欢吃饭的警察", "应该是贪污",
                              ["わーい", "こんにちは"], client)
        self.assertEqual(fix["wrong"], "お食")
        self.assertEqual(fix["right"], "汚職")
        self.assertEqual(fix["fixed"], "汚職大好き警察官")

    def test_his_chinese_feedback_is_sent_to_the_model(self):
        client = FakeClient(self.payload())
        ja_fix.guess_fix(self.TEXT, "我是最喜欢吃饭的警察", "应该是贪污", ["わーい"], client)
        sent = client.calls[0]["user"]
        self.assertIn("应该是贪污", sent)
        self.assertIn("我是最喜欢吃饭的警察", sent)
        self.assertIn("わーい", sent)

    def test_an_answer_that_touches_other_words_is_rejected(self):
        # 模型说改好了，但除了那一处还动了别的字 —— 不敢信
        client = FakeClient(self.payload(fixed="汚職が大好きな警察官"))
        self.assertIsNone(ja_fix.guess_fix(self.TEXT, "", "应该是贪污", [], client))

    def test_a_word_that_is_not_in_the_sentence_is_rejected(self):
        client = FakeClient(self.payload(wrong="お食事"))
        self.assertIsNone(ja_fix.guess_fix(self.TEXT, "", "应该是贪污", [], client))

    def test_empty_or_identical_answers_are_rejected(self):
        for over in ({"right": ""}, {"wrong": ""}, {"fixed": ""}, {"right": "お食"}):
            self.assertIsNone(ja_fix.guess_fix(self.TEXT, "", "x", [], FakeClient(self.payload(**over))), over)

    def test_the_client_is_asked_for_json(self):
        client = FakeClient(self.payload())
        ja_fix.guess_fix(self.TEXT, "", "应该是贪污", [], client)
        self.assertIn("JSON", client.calls[0]["system"])

    def test_the_prompt_says_use_kanji_not_kana(self):
        """真踩过：模型给了「おしょく」（假名），试翻成"想被请客 / 超喜欢职场"。

        所以提示词必须明确要求写汉字 —— 这条是那次事故的回归保护。
        """
        self.assertIn("不要只给假名", ja_fix.FIX_SYSTEM)
        self.assertIn("汉字", ja_fix.FIX_SYSTEM)


class JudgeFixTest(unittest.TestCase):
    """让模型当判官：改后的译文对不对得上他说的意思。

    为什么不用关键词匹配当主判据（实测踩过）：
      · 旧译文里本来就有那个词时（这条早就翻对了），关键词法什么都判不出来
      · 模型换个同义词（贪污 / 腐败）就匹配不上
    """

    def test_the_judge_says_yes(self):
        client = FakeClient({"ok": True, "why": "意思一致"})
        self.assertTrue(ja_fix.judge_fix("应该是贪污", "吃饭的警察", "腐败的警察", client))

    def test_the_judge_says_no(self):
        client = FakeClient({"ok": False, "why": "还是吃饭"})
        self.assertFalse(ja_fix.judge_fix("应该是贪污", "吃饭的警察", "吃饭的警察", client))

    def test_his_note_and_both_versions_are_sent(self):
        client = FakeClient({"ok": True})
        ja_fix.judge_fix("应该是贪污", "改前", "改后", client)
        sent = client.calls[0]["user"]
        self.assertIn("应该是贪污", sent)
        self.assertIn("改前", sent)
        self.assertIn("改后", sent)

    def test_a_silent_judge_is_treated_as_no(self):
        self.assertFalse(ja_fix.judge_fix("x", "a", "b", FakeClient({})))


class KanaUpgradeTest(unittest.TestCase):
    """模型只给假名读音时，自动再问一次汉字写法。

    真事故（2026-10-12 第 24 条）：模型给了「お食 → おしょく」，试翻成
    "哎呀，还是想被请客啊～"，判官判"对不上"，于是把使用者的反馈驳回了。
    他说的没错（就是贪污），是这条链路漏了一步。
    """

    class TwoStep:
        """第一次给假名，第二次（问汉字时）给汉字。"""

        def __init__(self, kanji="汚職"):
            self.kanji = kanji
            self.calls = []

        def json_chat(self, system, user, max_tokens=800):
            self.calls.append(system)
            # 注意：不能用"汉字"两个字来判断是第二次调用 ——
            # 主提示词里也写了"要写汉字"，用它会把第一次也当成问汉字（测试踩过）
            if "读音（假名）" in user or "该写的汉字" in system:
                return {"kanji": self.kanji}
            return {"wrong": "お食", "right": "おしょく",
                    "fixed": "おしょく大好き警察官", "why": "同音"}

    def test_a_kana_answer_is_upgraded_to_kanji(self):
        client = self.TwoStep()
        fix = ja_fix.guess_fix("お食大好き警察官", "吃饭的警察", "应该是贪污", [], client)
        self.assertEqual(fix["right"], "汚職")
        self.assertEqual(fix["fixed"], "汚職大好き警察官")
        self.assertEqual(len(client.calls), 2, "应该多问了一次汉字写法")

    def test_it_does_not_ask_twice_when_the_answer_is_already_kanji(self):
        client = FakeClient({"wrong": "お食", "right": "汚職",
                             "fixed": "汚職大好き警察官", "why": ""})
        ja_fix.guess_fix("お食大好き警察官", "", "应该是贪污", [], client)
        self.assertEqual(len(client.calls), 1)

    def test_a_word_that_is_really_kana_is_left_alone(self):
        # 像 おしゃべり 这种本来就是假名 —— 拿不到汉字就保持原样，不硬改
        client = self.TwoStep(kanji="")
        fix = ja_fix.guess_fix("お食大好き警察官", "", "应该是话多", [], client)
        self.assertEqual(fix["right"], "おしょく")

    def test_kanji_form_refuses_kana_or_rubbish(self):
        for payload in ({"kanji": "おしょく"}, {"kanji": ""}, {"kanji": "汚職 警官"},
                        {"kanji": "很长很长很长的词"}):
            self.assertEqual(ja_fix.kanji_form("おしょく", "句", "note", FakeClient(payload)), "", payload)

    def test_kanji_form_swallows_errors(self):
        class Boom:
            def json_chat(self, *a, **k):
                raise RuntimeError("网络断了")
        self.assertEqual(ja_fix.kanji_form("おしょく", "句", "note", Boom()), "")


class NoteKeywordsTest(unittest.TestCase):
    def test_chinese_keywords_are_picked_up(self):
        self.assertIn("贪污", ja_fix.note_keywords("应该是贪污"))

    def test_stop_words_are_dropped(self):
        words = ja_fix.note_keywords("应该不是这句的问题")
        self.assertNotIn("应该", words)
        self.assertNotIn("不是", words)

    def test_an_empty_note_gives_nothing(self):
        self.assertEqual(ja_fix.note_keywords(""), [])
        self.assertEqual(ja_fix.note_keywords("abc 123"), [])

    def test_latin_and_punctuation_are_ignored(self):
        self.assertEqual(ja_fix.note_keywords("「」、。！？"), [])


class MatchesNoteTest(unittest.TestCase):
    """核对：改完的中文里，有没有出现「他说过、而原来没有」的词。"""

    def test_the_change_he_asked_for_counts_as_a_match(self):
        self.assertTrue(ja_fix.matches_note("我是最喜欢吃饭的警察", "最喜欢贪污的警察",
                                            ja_fix.note_keywords("应该是贪污")))

    def test_the_wrong_candidate_does_not_match(self):
        # 这就是实测里"お食事"那条错路：中文还是吃的
        self.assertFalse(ja_fix.matches_note("我是最喜欢吃饭的警察", "最喜欢美食的警察",
                                             ja_fix.note_keywords("应该是贪污")))

    def test_common_words_shared_by_both_do_not_count(self):
        # 「警察」两边都有，不能拿它当命中的理由
        self.assertFalse(ja_fix.matches_note("吃饭的警察", "美食的警察",
                                             ja_fix.note_keywords("警察应该对")))

    def test_no_keywords_means_no_match(self):
        self.assertFalse(ja_fix.matches_note("啊", "诶", []))


class AppendRuleTest(unittest.TestCase):
    def test_a_new_rule_is_written_with_a_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ja_fix.TABLE_NAME).write_text("お食 = 汚職\n", encoding="utf-8")
            state = ja_fix.append_rule("お食系官", "汚職警官", "他确认过", root=tmp, when="2026-10-12 23:50")
            text = (Path(tmp) / ja_fix.TABLE_NAME).read_text(encoding="utf-8")
        self.assertEqual(state, "new")
        self.assertIn("お食系官 = 汚職警官", text)
        self.assertIn("2026-10-12 23:50", text)
        self.assertIn("他确认过", text)

    def test_the_new_rule_is_usable_right_away(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ja_fix.TABLE_NAME).write_text("", encoding="utf-8")
            ja_fix.append_rule("お食", "汚職", root=tmp)
            self.assertEqual(ja_fix.load_table(tmp), [("お食", "汚職")])

    def test_the_same_rule_is_reported_as_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ja_fix.TABLE_NAME).write_text("お食 = 汚職\n", encoding="utf-8")
            self.assertEqual(ja_fix.append_rule("お食", "汚職", root=tmp), "exists")
            self.assertEqual(ja_fix.count_word([Cue(1, 0, 1, "お食")], "お食"), 1)

    def test_a_different_target_is_a_conflict_and_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ja_fix.TABLE_NAME
            path.write_text("お食 = 别的写法\n", encoding="utf-8")
            self.assertEqual(ja_fix.append_rule("お食", "汚職", root=tmp), "conflict")
            self.assertIn("别的写法", path.read_text(encoding="utf-8"))

    def test_a_missing_folder_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "没有这个目录"
            self.assertEqual(ja_fix.append_rule("お食", "汚職", root=str(missing)), "error")
            self.assertFalse(missing.exists())

    def test_rubbish_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ja_fix.TABLE_NAME).write_text("", encoding="utf-8")
            for wrong, right in (("", "汚職"), ("お食", ""), ("お食", "お食"), ("a = b", "c"), ("お食", "a=b")):
                self.assertEqual(ja_fix.append_rule(wrong, right, root=tmp), "error", (wrong, right))


if __name__ == "__main__":
    unittest.main()
