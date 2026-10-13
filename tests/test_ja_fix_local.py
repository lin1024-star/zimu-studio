# -*- coding: utf-8 -*-
r"""「只对本片生效」的纠正规则 —— 测试。

为什么要有（2026-10-14 使用者发现的问题）：
纠正表原来是全局一张，所有视频都套用。可"识别听错"多半是这一期的事，
而**错的写法在别的视频里可能是正常内容** ✗
例：这一期把「多すぎ」听成「大杉」→ 写进全局表；
    下一期真有个说话对象叫「大杉」（真实姓氏）→ 会被静默改成「多すぎ」，
    中文跟着错，而他看不出来。

所以「这句识别错了」默认写成**本片规则**（存在项目里），下面这条是核心保障：
**本片规则不许影响别的项目。**
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import ja_fix
from core import Cue


def cue(ident, source, zh=""):
    return Cue(ident, 0.0, 1.0, source, zh)


class ProjectRuleTests(unittest.TestCase):

    def setUp(self):
        self.project = {"name": "这一期", "cues": []}

    # ---- 写入 ----

    def test_adds_local_rule(self):
        self.assertEqual(ja_fix.add_project_rule(self.project, "大杉", "多すぎ"), "new")
        self.assertEqual(ja_fix.project_rules(self.project), [("大杉", "多すぎ")])

    def test_same_rule_twice_is_exists(self):
        ja_fix.add_project_rule(self.project, "大杉", "多すぎ")
        self.assertEqual(ja_fix.add_project_rule(self.project, "大杉", "多すぎ"), "exists")
        self.assertEqual(len(self.project["ja_fix_local"]), 1)

    def test_conflicting_rule_is_not_overwritten(self):
        ja_fix.add_project_rule(self.project, "大杉", "多すぎ")
        self.assertEqual(ja_fix.add_project_rule(self.project, "大杉", "大杉さん"), "conflict")
        self.assertEqual(ja_fix.project_rules(self.project), [("大杉", "多すぎ")])

    def test_rejects_bad_input(self):
        for wrong, right in (("", "多すぎ"), ("大杉", ""), ("大杉", "大杉"),
                             ("大=杉", "多すぎ"), ("大\n杉", "多すぎ")):
            self.assertEqual(ja_fix.add_project_rule(self.project, wrong, right), "error",
                             f"{wrong!r} → {right!r} 应该被拒绝")
        self.assertEqual(ja_fix.project_rules(self.project), [])

    def test_no_project_is_error_not_crash(self):
        self.assertEqual(ja_fix.add_project_rule(None, "大杉", "多すぎ"), "error")

    def test_records_note_and_time(self):
        ja_fix.add_project_rule(self.project, "大杉", "多すぎ", note="应该是太多了")
        row = self.project["ja_fix_local"][0]
        self.assertEqual(row["note"], "应该是太多了")
        self.assertTrue(row["at"])

    def test_remove(self):
        ja_fix.add_project_rule(self.project, "大杉", "多すぎ")
        self.assertTrue(ja_fix.remove_project_rule(self.project, "大杉"))
        self.assertEqual(ja_fix.project_rules(self.project), [])
        self.assertFalse(ja_fix.remove_project_rule(self.project, "大杉"))   # 再删就没有了

    # ---- 读取：不信任项目文件内容 ----

    def test_tolerates_hand_edited_project(self):
        self.project["ja_fix_local"] = [
            {"wrong": "大杉", "right": "多すぎ"},
            ["お食", "汚職"],
            {"wrong": "坏的", "right": ""},          # 空的不认
            {"wrong": "自言自语"},                   # 缺一项不认
            "根本不是字典",                            # 类型不对不认
            None,
        ]
        self.assertEqual(ja_fix.project_rules(self.project), [("大杉", "多すぎ"), ("お食", "汚職")])

    def test_tolerates_wrong_type(self):
        self.project["ja_fix_local"] = {"wrong": "大杉"}      # 存成了字典而不是列表
        self.assertEqual(ja_fix.project_rules(self.project), [])

    def test_no_rules_key(self):
        self.assertEqual(ja_fix.project_rules({}), [])
        self.assertEqual(ja_fix.project_rules(None), [])

    # ---- 合并：本片优先 + 长的优先 ----

    def test_merged_puts_local_first_and_sorts_longest_first(self):
        ja_fix.add_project_rule(self.project, "お食", "汚職")
        ja_fix.add_project_rule(self.project, "お食系官", "汚職系官")
        table = ja_fix.merged_table(self.project)
        self.assertEqual([w for w, _ in table][:2], ["お食系官", "お食"])

    def test_local_wins_over_global_for_same_wrong_form(self):
        """同一个错词：全局表写「多すぎ」，这一期特地改成别的 → 以这一期为准。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ja_fix.TABLE_NAME).write_text("大杉 = 多すぎ\n", encoding="utf-8")
            ja_fix.add_project_rule(self.project, "大杉", "这一期指的是大杉先生")
            table = dict(ja_fix.merged_table(self.project, root=root))
        self.assertEqual(table["大杉"], "这一期指的是大杉先生")

    def test_rules_argument_merges_with_project_rules(self):
        """rules（识别流程从配置带进来的）和项目里存的会合并，重复的以 rules 为先。"""
        ja_fix.add_project_rule(self.project, "お食", "汚職")
        table = dict(ja_fix.merged_table(self.project, rules=[("お食", "汚職（改名）"),
                                                             ("大杉", "多すぎ")]))
        self.assertEqual(table["お食"], "汚職（改名）")
        self.assertEqual(table["大杉"], "多すぎ")

    def test_merged_reads_global_table_from_disk(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ja_fix.TABLE_NAME).write_text("お食 = 汚職\n", encoding="utf-8")
            ja_fix.add_project_rule(self.project, "大杉", "多すぎ")
            table = ja_fix.merged_table(self.project, root=root)
        self.assertEqual(sorted(table), sorted([("お食", "汚職"), ("大杉", "多すぎ")]))

    # ---- ★ 核心保障：本片规则不许影响别的项目 ----

    def test_local_rule_does_not_leak_into_another_project(self):
        other = {"name": "下一期", "cues": []}
        ja_fix.add_project_rule(self.project, "大杉", "多すぎ")
        # 下一期里真的说了「大杉」（正常内容）
        cues = [cue(1, "大杉さんが来た")]
        fixed, report = ja_fix.fix_cues(cues, table=ja_fix.merged_table(other))
        self.assertEqual(fixed[0].source, "大杉さんが来た", "别的项目不该被这一期的规则改到")
        self.assertEqual(report["cues_changed"], 0)

    def test_local_rule_still_applies_inside_its_own_project(self):
        ja_fix.add_project_rule(self.project, "大杉", "多すぎ")
        cues = [cue(1, "大杉さんが来た")]
        fixed, report = ja_fix.fix_cues(cues, table=ja_fix.merged_table(self.project))
        self.assertEqual(fixed[0].source, "多すぎさんが来た")
        self.assertEqual(report["cues_changed"], 1)

    def test_global_rule_would_leak_and_that_is_why_default_is_local(self):
        """反证：全局规则确实会波及别的项目 —— 所以默认必须是本片。

        这条测试是拿来提醒自己的：全局这条路是**故意保留**的（同一个说话人、
        同一个错反复出现时有用），但它确实有这个代价。
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ja_fix.TABLE_NAME).write_text("大杉 = 多すぎ\n", encoding="utf-8")
            other = {"name": "下一期", "cues": []}
            table = ja_fix.merged_table(other, root=root)
        fixed, _ = ja_fix.fix_cues([cue(1, "大杉さんが来た")], table=table)
        self.assertEqual(fixed[0].source, "多すぎさんが来た", "全局规则会改到别的项目（预期行为）")


if __name__ == "__main__":
    unittest.main()
