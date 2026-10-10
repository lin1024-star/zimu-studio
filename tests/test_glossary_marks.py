"""术语表高亮的测试：认得准、不误伤、标出「可能翻漏了」。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import core

# 与用户真实术语表同一种写法
GLOSSARY = """字幕工坊 · 人名 / 术语说明
（整段复制，粘贴到软件「翻译与识别设置」页的「人名 / 术语说明」框里）

【人名与称呼】以下是固定译法，必须照用，不要自行发挥
ゆか = 优花
ひいらぎゆか = 柊优花
はる = 春
ちゃん = 酱

【问候语】
こんゆかし = 「优花亲你好」（复读时译作 你好 或 大家好）
ないすぱぱ = 非常好SC

【其他用词】
クライアント = 甲方 / 单主（也写作 Client）
グロい = 残酷 / 太惨了（不要译成"恶心"）

【识别容易听错的地方】
「政治したら」是「成人したら」的识别误听，译作"成年了"。
"""


class ParseTests(unittest.TestCase):
    def test_parses_pairs_and_skips_headers_and_notes(self):
        terms = core.parse_glossary(GLOSSARY)
        table = {t[0]: t[1] for t in terms}
        self.assertEqual(table["ゆか"], ["优花"])
        self.assertEqual(table["はる"], ["春"])
        self.assertNotIn("【人名与称呼】以下是固定译法，必须照用，不要自行发挥", table)

    def test_multiple_choices_split_by_slash_and_notes_dropped(self):
        table = {t[0]: t[1] for t in core.parse_glossary(GLOSSARY)}
        self.assertEqual(table["クライアント"], ["甲方", "单主"])   # 括号里的「也写作 Client」丢掉
        self.assertEqual(table["グロい"], ["残酷", "太惨了"])
        self.assertEqual(table["こんゆかし"], ["优花亲你好"])        # 括号里的说明丢掉

    def test_full_width_equal_sign_is_accepted(self):
        terms = core.parse_glossary("ゆか ＝ 优花")
        self.assertEqual(terms[0][0], "ゆか")

    def test_empty_glossary_is_harmless(self):
        self.assertEqual(core.parse_glossary(""), [])
        self.assertEqual(core.parse_glossary(None), [])


class HitTests(unittest.TestCase):
    def setUp(self):
        self.terms = core.parse_glossary(GLOSSARY)

    def test_hit_and_translation_present(self):
        matched, missing = core.glossary_hits(self.terms, "ゆかが来た", "优花来了")
        self.assertEqual([t for t, _ in matched], ["ゆか"])
        self.assertEqual(missing, [])

    def test_hit_but_translation_missing_is_flagged(self):
        """命中而译文里没有译名 —— 这就是「可能翻漏了」，最该核对的一类。"""
        matched, missing = core.glossary_hits(self.terms, "ゆかのこと、ずっと", "她的事，一直")
        self.assertEqual(matched, [])
        self.assertEqual([t for t, _ in missing], ["ゆか"])

    def test_any_of_several_choices_counts_as_present(self):
        for text in ("甲方说要改", "单主说要改"):
            with self.subTest(text=text):
                matched, missing = core.glossary_hits(self.terms, "クライアントが", text)
                self.assertEqual(missing, [])
                self.assertEqual([t for t, _ in matched], ["クライアント"])

    def test_longer_term_wins_over_its_own_substring(self):
        """ひいらぎゆか 命中时，不再单列 ゆか，否则一片噪音。"""
        matched, missing = core.glossary_hits(self.terms, "ひいらぎゆかさん", "柊优花小姐")
        self.assertEqual([t for t, _ in matched], ["ひいらぎゆか"])

    def test_several_terms_in_one_cue(self):
        matched, missing = core.glossary_hits(self.terms, "はるちゃん、おはよう", "春酱，早上好")
        self.assertEqual(sorted(t for t, _ in matched), ["ちゃん", "はる"])
        self.assertEqual(missing, [])

    def test_no_term_no_marks(self):
        self.assertEqual(core.glossary_hits(self.terms, "今日はいい天気", "今天天气不错"), ([], []))

    def test_empty_translation_flags_everything(self):
        matched, missing = core.glossary_hits(self.terms, "ゆかと はる", "")
        self.assertEqual(matched, [])
        self.assertEqual(sorted(t for t, _ in missing), ["はる", "ゆか"])
        self.assertEqual(dict(missing)["ゆか"], "优花")

    def test_value_is_returned_for_display(self):
        matched, _ = core.glossary_hits(self.terms, "ゆか", "优花")
        self.assertEqual(matched[0], ("ゆか", "优花"))

    def test_chinese_project_matches_from_the_translation_side(self):
        """「中文 ASS」导入时原文栏装的是中文，日文术语匹配不上，改从译文侧认术语。"""
        zh = "优花说过今天会早回家的吧"
        matched, missing = core.glossary_hits(self.terms, zh, zh)
        self.assertEqual([t for t, _ in matched], ["ゆか"])
        self.assertEqual(missing, [])

    def test_chinese_project_longest_term_wins(self):
        zh = "柊优花来了"
        matched, _ = core.glossary_hits(self.terms, zh, zh)
        self.assertEqual([t for t, _ in matched], ["ひいらぎゆか"])

    def test_same_translation_listed_once(self):
        """片假名和平假名写法各写一行时，只显示一条。"""
        terms = core.parse_glossary("マノサバ = 魔审\nまのさば = 魔审")
        matched, _ = core.glossary_hits(terms, "魔审", "魔审")
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0][1], "魔审")

    def test_original_side_still_wins_when_both_could_match(self):
        """原文栏是日文时，仍以原文侧为准，「可能翻漏了」才有意义。"""
        matched, missing = core.glossary_hits(self.terms, "ゆか", "她来了")
        self.assertEqual(matched, [])
        self.assertEqual([t for t, _ in missing], ["ゆか"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
