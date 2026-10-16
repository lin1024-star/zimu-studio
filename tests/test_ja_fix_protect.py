# -*- coding: utf-8 -*-
"""纠正表的「保护词」：让正常的词不被更短的规则改掉。

为什么加（2026-10-16 调研出来的）：
纠正表是**子串**匹配 + 长的优先。所以规则写得越短，越容易咬进正常词里面：

    规则：  お職 = 汚職
    正文：  お職場に行く（敬语版的"工作单位"）→ 被改成「汚職場に行く」✗

原来没有办法防这个：`A = A` 这种行会被 `parse_table` **静默丢掉** ——
使用者写了保护词、以为生效了，其实根本没有。这一组测试守住两件事：
  ① `A = A` 真的当保护词用（占位、原样保留、不进改动清单）
  ② 同一个左边写了两次时，**真规则优先于保护词**（手滑写了保护词不会把规则弄没）
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "payload"))

import ja_fix                                              # noqa: E402
from core import Cue                                       # noqa: E402


class ProtectionWordTest(unittest.TestCase):
    def test_identity_line_is_kept_now(self):
        """`A = A` 不再被丢掉 —— 它就是保护词。"""
        table = ja_fix.parse_table("お職場 = お職場\n")
        self.assertEqual(table, [("お職場", "お職場")])

    def test_protection_blocks_the_shorter_rule(self):
        table = ja_fix.parse_table("お職場 = お職場\nお職 = 汚職\n")
        fixed, hits = ja_fix.apply_text("お職場に行く", table)
        self.assertEqual(fixed, "お職場に行く", "保护词没挡住短规则")
        self.assertEqual(hits, [], "保护词不该进改动清单")

    def test_without_protection_the_shorter_rule_does_damage(self):
        """反证：没有保护词时，短规则确实会咬进正常词里。

        这条不是"希望这样"，是把风险钉在这里 —— 以后谁改长了规则，能立刻看见。
        """
        table = ja_fix.parse_table("お職 = 汚職\n")
        fixed, _ = ja_fix.apply_text("お職場に行く", table)
        self.assertEqual(fixed, "汚職場に行く", "预期短规则会误伤，结果没有 —— 那说明匹配方式变了，要重看")

    def test_real_rule_still_works_alongside_protection(self):
        table = ja_fix.parse_table("お職場 = お職場\nお職警官 = 汚職警官\nお職 = 汚職\n")
        fixed, hits = ja_fix.apply_text("お職大好き", table)
        self.assertEqual(fixed, "汚職大好き")
        self.assertEqual([(w, r) for w, r, _ in hits], [("お職", "汚職")])
        fixed2, _ = ja_fix.apply_text("お職場とお職警官", table)
        self.assertEqual(fixed2, "お職場と汚職警官")

    def test_real_rule_beats_protection_for_the_same_word(self):
        """同一个左边写了两次：真规则赢 —— 手滑写保护词不会把规则静默取消。"""
        a = ja_fix.parse_table("お食 = お食\nお食 = 汚職\n")
        b = ja_fix.parse_table("お食 = 汚職\nお食 = お食\n")
        self.assertEqual(a, [("お食", "汚職")])
        self.assertEqual(b, [("お食", "汚職")])

    def test_longest_wins_regardless_of_file_order(self):
        """文件里谁写在前面不影响结果，长的先匹配。"""
        table = ja_fix.parse_table("お食 = 汚職\nお食系官 = 汚職警官\n")
        fixed, _ = ja_fix.apply_text("お食系官です", table)
        self.assertEqual(fixed, "汚職警官です", "短规则先命中 → 造出「汚職系官」这种新词")

    def test_protection_is_not_counted_as_a_rule(self):
        cues = [Cue(1, 0.0, 1.0, "お職大好き", "")]
        _, report = ja_fix.fix_cues(cues, ja_fix.parse_table("お職場 = お職場\nお職 = 汚職\n"))
        self.assertEqual(report["table_size"], 1, "保护词被算成规则了")
        self.assertEqual(report["protections"], 1)
        self.assertEqual(report["replacements"], 1)

    def test_protection_only_cue_is_not_reported_as_a_change(self):
        """整条只命中保护词 → 不算改动，日志里不该冒出「第 N 条：… → …」。"""
        cues = [Cue(1, 0.0, 1.0, "お職場に行く", "")]
        out, report = ja_fix.fix_cues(cues, ja_fix.parse_table("お職場 = お職場\nお職 = 汚職\n"))
        self.assertEqual(report["cues_changed"], 0)
        self.assertEqual(ja_fix.report_lines(report), [])
        self.assertEqual(out[0].source, "お職場に行く")


class RealTableTest(unittest.TestCase):
    """拿**现在这份真表**（含新加的「お職」）跑一遍那期真实素材里的句子。"""

    SEED_LINES = ja_fix.SEED

    def test_the_seed_fixes_oshoku_variants(self):
        table = ja_fix.parse_table(self.SEED_LINES)
        cases = {
            "お食大好き": "汚職大好き",
            "お食警官です": "汚職警官です",
            "お職大好き": "汚職大好き",
            "お職を認めるから": "汚職を認めるから",
            "お職大好き警察官と": "汚職大好き警察官と",
            "お職警官です": "汚職警官です",
            # 正常词必须一个字都不动
            "お尻拭き経験者": "お尻拭き経験者",
            "お飾り許すマジ": "お飾り許すマジ",
        }
        for before, after in cases.items():
            fixed, _ = ja_fix.apply_text(before, table)
            self.assertEqual(fixed, after, f"「{before}」应该变成「{after}」")

    def test_the_seed_covers_both_wrong_forms(self):
        """表里两种错写法都得有：お食…（原来的 5 条）+ お職…（这次补的 2 条）。"""
        table = ja_fix.parse_table(self.SEED_LINES)
        lefts = [wrong for wrong, _ in table]
        self.assertIn("お食", lefts)
        self.assertIn("お職", lefts)
        self.assertIn(("お職", "汚職"), table)
        self.assertIn(("お職警官", "汚職警官"), table)
        self.assertEqual(len(table), 7)


if __name__ == "__main__":
    unittest.main()
