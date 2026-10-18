"""「待确认」里合并拆词候选：人点一下就把两条合成一条。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import app
import core
import polish
from core import Cue


def row(ident, start, end, source, zh=""):
    return {"id": ident, "start": start, "end": end, "source": source, "zh": zh}


class SplitMergePairTest(unittest.TestCase):
    """polish.merge_split_pair：纯函数，只管列表怎么变。"""

    def test_adjacent_pair_becomes_one_cue(self):
        cues = [Cue(1, 0.0, 1.0, "周", "周"), Cue(2, 1.0, 2.0, "りも", "里")]
        merged, info = polish.merge_split_pair(cues, 1)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].source, "周りも")
        self.assertEqual((merged[0].start, merged[0].end), (0.0, 2.0))
        self.assertEqual(info["removed_id"], 2)

    def test_end_takes_the_later_half(self):
        cues = [Cue(1, 0.0, 5.0, "周", ""), Cue(2, 4.0, 4.2, "り", "")]
        merged, _info = polish.merge_split_pair(cues, 1)
        self.assertEqual(merged[0].end, 5.0)

    def test_chinese_is_cleared_so_it_gets_retranslated(self):
        cues = [Cue(1, 0.0, 1.0, "周", "周"), Cue(2, 1.0, 2.0, "り", "里")]
        merged, _info = polish.merge_split_pair(cues, 1)
        self.assertEqual(merged[0].zh, "")

    def test_cues_after_the_pair_are_renumbered(self):
        cues = [Cue(1, 0.0, 1.0, "周", ""), Cue(2, 1.0, 2.0, "り", ""),
                Cue(3, 3.0, 4.0, "あと", "之后")]
        merged, _info = polish.merge_split_pair(cues, 1)
        self.assertEqual([cue.id for cue in merged], [1, 2])
        self.assertEqual(merged[1].source, "あと")
        self.assertEqual(merged[1].zh, "之后")

    def test_non_neighbour_ids_are_refused(self):
        cues = [Cue(1, 0.0, 1.0, "周", ""), Cue(5, 1.0, 2.0, "り", "")]
        merged, info = polish.merge_split_pair(cues, 1)
        self.assertIsNone(merged)
        self.assertIn("夹", info["error"])

    def test_missing_id_is_refused(self):
        merged, info = polish.merge_split_pair([Cue(1, 0.0, 1.0, "周", "")], 99)
        self.assertIsNone(merged)
        self.assertIn("error", info)

    def test_last_cue_has_nothing_to_merge_with(self):
        cues = [Cue(1, 0.0, 1.0, "周", ""), Cue(2, 1.0, 2.0, "り", "")]
        merged, info = polish.merge_split_pair(cues, 2)
        self.assertIsNone(merged)
        self.assertIn("error", info)


class ProjectMergeTest(unittest.TestCase):
    """core.merge_split_cues：改项目字典，顺手修好按编号记的东西。"""

    def project(self):
        return {
            "version": 1,
            "cues": [row(1, 0.0, 1.0, "周", "周"),
                     row(2, 1.0, 2.0, "りも", "里"),
                     row(3, 10.0, 11.0, "あとで", "之后")],
            "axis_review": [{"kind": "split", "id": 1, "next_id": 2,
                             "before": "周", "after": "りも", "merged": "周りも",
                             "start": 0.0, "end": 2.0, "gap": 0.0}],
            "chinese_check": {"at": "2026-10-18 21:00:00",
                              "items": [{"id": 1, "source": "周", "zh": "周", "why": "a"},
                                        {"id": 2, "source": "りも", "zh": "里", "why": "b"},
                                        {"id": 3, "source": "あとで", "zh": "之后", "why": "c"}]},
        }

    def test_merge_reduces_cues_and_joins_text(self):
        updated, info = core.merge_split_cues(self.project(), 1)
        self.assertIsNotNone(updated)
        self.assertEqual(len(updated["cues"]), 2)
        self.assertEqual(updated["cues"][0]["source"], "周りも")
        self.assertEqual(updated["cues"][0]["zh"], "")
        self.assertEqual(info["removed_id"], 2)

    def test_original_project_is_not_mutated(self):
        project = self.project()
        core.merge_split_cues(project, 1)
        self.assertEqual(len(project["cues"]), 3)
        self.assertEqual(project["cues"][1]["id"], 2)

    def test_review_is_rebuilt_for_the_new_axis(self):
        updated, _info = core.merge_split_cues(self.project(), 1)
        self.assertIsInstance(updated["axis_review"], list)
        self.assertFalse([item for item in updated["axis_review"]
                          if item.get("kind") == "split" and item.get("id") == 1])

    def test_chinese_check_ids_move_with_the_cues(self):
        updated, _info = core.merge_split_cues(self.project(), 1)
        items = updated["chinese_check"]["items"]
        self.assertEqual([item["id"] for item in items], [2])       # 1、2 作废，3 挪成 2
        self.assertEqual(items[0]["source"], "あとで")
        self.assertEqual(updated["chinese_check"]["at"], "2026-10-18 21:00:00")

    def test_project_without_check_results_still_merges(self):
        project = {"cues": [row(1, 0.0, 1.0, "周"), row(2, 1.0, 2.0, "り")]}
        updated, _info = core.merge_split_cues(project, 1)
        self.assertIsNotNone(updated)
        self.assertNotIn("chinese_check", updated)

    def test_bad_id_gives_message_instead_of_crash(self):
        updated, info = core.merge_split_cues(self.project(), 99)
        self.assertIsNone(updated)
        self.assertIn("error", info)

    def test_clear_cues_are_refused(self):
        updated, info = core.merge_split_cues({"cues": []}, 1)
        self.assertIsNone(updated)
        self.assertIn("error", info)


class AxisReviewRowTest(unittest.TestCase):
    """待确认窗口里「这一行该跳到第几条」的映射（不建界面，直接问静态方法）。"""

    def target(self, row):
        return app.Application._axis_review_target(row)

    def test_short_cue_jumps_to_itself(self):
        self.assertEqual(self.target({"kind": "short", "id": 12}), 12)

    def test_split_jumps_to_the_first_half(self):
        self.assertEqual(self.target({"kind": "split", "id": 23, "next_id": 24}), 23)

    def test_gap_jumps_to_the_cue_after_the_hole(self):
        self.assertEqual(self.target({"kind": "gap", "after_id": 10, "before_id": 11}), 11)

    def test_repeat_jumps_to_the_first_occurrence(self):
        self.assertEqual(self.target({"kind": "repeat", "first_id": 4,
                                      "second_id": 57}), 4)

    def test_unknown_row_has_no_target(self):
        self.assertIsNone(self.target(None))
        self.assertIsNone(self.target({}))


if __name__ == "__main__":
    unittest.main()
