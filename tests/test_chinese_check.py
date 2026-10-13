# -*- coding: utf-8 -*-
"""中文自检模块的测试。

重点是那条**本地规则**：它必须只抓"识别乱码造成的字符堆叠"，
绝不能误伤台词里正常的复读（朗读剧那期台词全是「エマエマエマ…」）。
这条规则是拿真实素材调出来的，所以测试里把那 12 个用例钉死。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))

import chinese_check as cc


def cue(cid, source, zh, start=0.0):
    return {"id": cid, "source": source, "zh": zh, "start": start}


class StackedRunTest(unittest.TestCase):
    """本地规则：同一字符连续 ≥4 次、且不是常见语气字 → 报。"""

    def test_should_not_fire_on_normal_repetition(self):
        """正常复读不报 —— 朗读剧台词里全是这种，误伤了就没法用。"""
        for text in ("艾玛艾玛艾玛艾玛艾玛", "艾玛艾玛艾玛艾玛艾玛艾玛艾玛",
                     "喜欢喜欢喜欢喜欢", "真的真的真的真的",
                     "哈哈哈哈哈哈", "啊啊啊啊啊", "嗯嗯嗯嗯嗯", "呜呜呜呜呜呜",
                     "对 对 对 对", "啊啊啊啊啊的呢"):
            self.assertIsNone(cc.stacked_run(text), f"不该报：{text}")

    def test_should_fire_on_garbled_stacking(self):
        """识别乱码的形态要报（真事：マエマエマエ → 前前前前前前）。"""
        self.assertEqual(cc.stacked_run("前前前前前前前前"), "前前前前前前前前")

    def test_fires_on_the_first_offending_run(self):
        self.assertEqual(cc.stacked_run("啊啊 前前前前 后后"), "前前前前")

    def test_needs_four_in_a_row(self):
        self.assertIsNone(cc.stacked_run("前前前"))       # 3 个不算
        self.assertEqual(cc.stacked_run("前前前前"), "前前前前")

    def test_empty_and_none_are_safe(self):
        self.assertIsNone(cc.stacked_run(""))
        self.assertIsNone(cc.stacked_run(None))


class LocalFlagsTest(unittest.TestCase):

    def test_only_flags_the_garbled_one(self):
        cues = [cue(1, "エマエマエマ", "艾玛艾玛艾玛艾玛", 0.0),
                cue(2, "マエマエマエ", "前前前前前前", 2.0),
                cue(3, "ありがとう", "谢谢", 4.0)]
        found = cc.local_flags(cues)
        self.assertEqual([f["id"] for f in found], [2])
        self.assertIn("连续重复", found[0]["why"])
        self.assertEqual(found[0]["from"], "本地规则")

    def test_handles_missing_zh(self):
        self.assertEqual(cc.local_flags([cue(1, "ありがとう", "")]), [])
        self.assertEqual(cc.local_flags([{"id": 1, "source": "x", "start": 0.0}]), [])


class ParseReplyTest(unittest.TestCase):

    def setUp(self):
        self.cues = [cue(1, "a", "甲"), cue(2, "b", "乙"), cue(3, "c", "丙")]

    def test_keeps_valid_items(self):
        data = {"items": [{"id": 2, "why": "意思反了"}]}
        got = cc.parse_reply(data, self.cues)
        self.assertEqual([(x["id"], x["why"], x["from"]) for x in got],
                         [(2, "意思反了", "模型")])

    def test_drops_items_the_model_itself_says_not_to_report(self):
        """实测踩过：模型一边写「不报」一边还是把它报出来了。"""
        data = {"items": [{"id": 1, "why": "基本对应，不报"},
                          {"id": 2, "why": "暂不报"},
                          {"id": 3, "why": "真的错了"}]}
        got = cc.parse_reply(data, self.cues)
        self.assertEqual([x["id"] for x in got], [3])

    def test_drops_unknown_or_bad_ids(self):
        data = {"items": [{"id": 99, "why": "x"}, {"id": "abc", "why": "y"},
                          {"id": None, "why": "z"}, {"id": 1, "why": "ok"}]}
        self.assertEqual([x["id"] for x in cc.parse_reply(data, self.cues)], [1])

    def test_survives_empty_or_broken_reply(self):
        self.assertEqual(cc.parse_reply(None, self.cues), [])
        self.assertEqual(cc.parse_reply({}, self.cues), [])
        self.assertEqual(cc.parse_reply({"items": None}, self.cues), [])
        self.assertEqual(cc.parse_reply({"items": ["不是字典"]}, self.cues), [])


class MergeTest(unittest.TestCase):

    def test_dedupes_by_id_and_sorts_by_time(self):
        local = [{"id": 3, "start": 5.0, "zh": "丙", "why": "本地"}]
        model = [{"id": 1, "start": 1.0, "zh": "甲", "why": "模型"},
                 {"id": 3, "start": 5.0, "zh": "丙", "why": "模型也报了"}]
        got = cc.merge(local, model)
        self.assertEqual([x["id"] for x in got], [1, 3])
        self.assertEqual(got[1]["why"], "本地")      # 同一条以本地为准


class PromptTest(unittest.TestCase):

    def test_prompt_carries_the_judging_rules(self):
        text = cc.system_prompt("ゆか = 优花")
        self.assertIn("ゆか = 优花", text)                  # 术语表进去了
        self.assertIn("术语表为准", text)                  # 不许说术语表里的人名错
        self.assertIn("每批最多报", text)
        self.assertIn("不要报", text)

    def test_prompt_survives_missing_glossary(self):
        self.assertIn("没有术语表", cc.system_prompt(""))

    def test_prompt_truncates_a_huge_glossary(self):
        text = cc.system_prompt("あ" * 5000)
        self.assertLess(len(text), 5000)


class CheckTest(unittest.TestCase):
    """整体流程：用假 client，不联网。"""

    class FakeClient:
        def __init__(self, replies):
            self.replies = list(replies)
            self.calls = []

        def json_chat(self, system, user, max_tokens=800):
            self.calls.append(user)
            return self.replies.pop(0) if self.replies else {"items": []}

    def test_merges_local_and_model(self):
        cues = [cue(1, "a", "甲", 0.0), cue(2, "b", "前前前前", 1.0)]
        client = self.FakeClient([{"items": [{"id": 1, "why": "意思反了"}]}])
        result = cc.check(cues, "glossary", client)
        self.assertEqual([x["id"] for x in result["items"]], [1, 2])
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["batches"], 1)

    def test_a_failing_batch_does_not_raise(self):
        """自检失败绝不能让主流程失败。"""
        class Boom:
            def json_chat(self, *a, **k):
                raise RuntimeError("网络炸了")

        cues = [cue(1, "a", "前前前前", 0.0)]
        result = cc.check(cues, "", Boom())
        self.assertEqual(result["failed"], 1)
        self.assertEqual([x["id"] for x in result["items"]], [1])   # 本地规则仍在

    def test_cancellation_stops_early(self):
        class Stop:
            def is_set(self):
                return True

        client = self.FakeClient([])
        result = cc.check([cue(i, "a", "甲", i) for i in range(1, 60)], "", client, stop=Stop())
        self.assertEqual(result["batches"], 0)
        self.assertEqual(client.calls, [])

    def test_summarize(self):
        self.assertIn("没有发现", cc.summarize([]))
        self.assertIn("2 处", cc.summarize([{"id": 1}, {"id": 2}], total=100))


if __name__ == "__main__":
    unittest.main()
