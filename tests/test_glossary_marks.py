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
さくら = 樱
やまもとさくら = 柊樱
はな = 花
ちゃん = 酱

【问候语】
こんさくらし = 「樱亲你好」（复读时译作 你好 或 大家好）
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
        self.assertEqual(table["さくら"], ["樱"])
        self.assertEqual(table["はな"], ["花"])
        self.assertNotIn("【人名与称呼】以下是固定译法，必须照用，不要自行发挥", table)

    def test_multiple_choices_split_by_slash_and_notes_dropped(self):
        table = {t[0]: t[1] for t in core.parse_glossary(GLOSSARY)}
        self.assertEqual(table["クライアント"], ["甲方", "单主"])   # 括号里的「也写作 Client」丢掉
        self.assertEqual(table["グロい"], ["残酷", "太惨了"])
        self.assertEqual(table["こんさくらし"], ["樱亲你好"])        # 括号里的说明丢掉

    def test_full_width_equal_sign_is_accepted(self):
        terms = core.parse_glossary("さくら ＝ 樱")
        self.assertEqual(terms[0][0], "さくら")

    def test_empty_glossary_is_harmless(self):
        self.assertEqual(core.parse_glossary(""), [])
        self.assertEqual(core.parse_glossary(None), [])


class HitTests(unittest.TestCase):
    def setUp(self):
        self.terms = core.parse_glossary(GLOSSARY)

    def test_hit_and_translation_present(self):
        matched, missing = core.glossary_hits(self.terms, "さくらが来た", "樱来了")
        self.assertEqual([t for t, _ in matched], ["さくら"])
        self.assertEqual(missing, [])

    def test_hit_but_translation_missing_is_flagged(self):
        """命中而译文里没有译名 —— 这就是「可能翻漏了」，最该核对的一类。"""
        matched, missing = core.glossary_hits(self.terms, "さくらのこと、ずっと", "她的事，一直")
        self.assertEqual(matched, [])
        self.assertEqual([t for t, _ in missing], ["さくら"])

    def test_any_of_several_choices_counts_as_present(self):
        for text in ("甲方说要改", "单主说要改"):
            with self.subTest(text=text):
                matched, missing = core.glossary_hits(self.terms, "クライアントが", text)
                self.assertEqual(missing, [])
                self.assertEqual([t for t, _ in matched], ["クライアント"])

    def test_longer_term_wins_over_its_own_substring(self):
        """やまもとさくら 命中时，不再单列 さくら，否则一片噪音。"""
        matched, missing = core.glossary_hits(self.terms, "やまもとさくらさん", "柊樱小姐")
        self.assertEqual([t for t, _ in matched], ["やまもとさくら"])

    def test_several_terms_in_one_cue(self):
        matched, missing = core.glossary_hits(self.terms, "はなちゃん、おはよう", "花酱，早上好")
        self.assertEqual(sorted(t for t, _ in matched), ["ちゃん", "はな"])
        self.assertEqual(missing, [])

    def test_no_term_no_marks(self):
        self.assertEqual(core.glossary_hits(self.terms, "今日はいい天気", "今天天气不错"), ([], []))

    def test_empty_translation_flags_everything(self):
        matched, missing = core.glossary_hits(self.terms, "さくらと はな", "")
        self.assertEqual(matched, [])
        self.assertEqual(sorted(t for t, _ in missing), ["さくら", "はな"])
        self.assertEqual(dict(missing)["さくら"], "樱")

    def test_value_is_returned_for_display(self):
        matched, _ = core.glossary_hits(self.terms, "さくら", "樱")
        self.assertEqual(matched[0], ("さくら", "樱"))

    def test_chinese_project_matches_from_the_translation_side(self):
        """「中文 ASS」导入时原文栏装的是中文，日文术语匹配不上，改从译文侧认术语。"""
        zh = "樱说过今天会早回家的吧"
        matched, missing = core.glossary_hits(self.terms, zh, zh)
        self.assertEqual([t for t, _ in matched], ["さくら"])
        self.assertEqual(missing, [])

    def test_chinese_project_longest_term_wins(self):
        zh = "柊樱来了"
        matched, _ = core.glossary_hits(self.terms, zh, zh)
        self.assertEqual([t for t, _ in matched], ["やまもとさくら"])

    def test_same_translation_listed_once(self):
        """片假名和平假名写法各写一行时，只显示一条。"""
        terms = core.parse_glossary("カンジャ = 审会\nまのさば = 审会")
        matched, _ = core.glossary_hits(terms, "审会", "审会")
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0][1], "审会")

    def test_original_side_still_wins_when_both_could_match(self):
        """原文栏是日文时，仍以原文侧为准，「可能翻漏了」才有意义。"""
        matched, missing = core.glossary_hits(self.terms, "さくら", "她来了")
        self.assertEqual(matched, [])
        self.assertEqual([t for t, _ in missing], ["さくら"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
