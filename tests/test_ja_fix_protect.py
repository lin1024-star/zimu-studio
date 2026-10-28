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
import tempfile
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

    def test_other_short_rules_still_need_explicit_protection(self):
        """内置保护只覆盖已确认的种子规则，任意手工短规则仍是子串替换。"""
        table = ja_fix.parse_table("ABC = X\n")
        self.assertEqual(ja_fix.apply_text("ABCDE", table)[0], "XDE")
        protected = ja_fix.parse_table("ABC = X\nABCDE = ABCDE\n")
        self.assertEqual(ja_fix.apply_text("ABCDE", protected), ("ABCDE", []))

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


class SeedWordSafetyTest(unittest.TestCase):
    def test_normal_meal_and_workplace_words_are_preserved(self):
        table = ja_fix.parse_table(ja_fix.SEED)
        for text in ("お食事を用意しました。", "お食事券をいただきました。",
                     "お食い初めのお祝いです。", "お職場に行きます。"):
            with self.subTest(text=text):
                self.assertEqual(ja_fix.apply_text(text, table), (text, []))

    def test_protection_and_real_correction_in_same_cue(self):
        table = ja_fix.parse_table(ja_fix.SEED)
        text, hits = ja_fix.apply_text("お食事の後でお食系官とお職警官に会う", table)
        self.assertEqual(text, "お食事の後で汚職警官と汚職警官に会う")
        self.assertCountEqual(hits, [("お食系官", "汚職警官", 1), ("お職警官", "汚職警官", 1)])

    def test_explicit_complete_word_rule_still_wins(self):
        table = ja_fix.parse_table("お食 = 汚職\nお食事 = 汚職\n")
        self.assertEqual(ja_fix.apply_text("お食事", table), ("汚職", [("お食事", "汚職", 1)]))

    def test_other_manual_mapping_is_not_changed(self):
        table = ja_fix.parse_table("お食 = 料理\n")
        self.assertEqual(ja_fix.apply_text("お食事", table)[0], "料理事")

    def test_existing_table_is_protected_without_rewriting_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ja_fix.TABLE_NAME
            path.write_text(ja_fix.SEED, encoding="utf-8")
            before = path.read_bytes()
            cues = [Cue(1, 1.25, 3.5, "お食事を用意しました。", "饭准备好了。"),
                    Cue(2, 4.0, 5.5, "お食大好き警察官", "贪污警察")]
            fixed, report = ja_fix.fix_cues(cues, root=tmp)
            self.assertIs(fixed[0], cues[0])
            self.assertEqual(fixed[1], Cue(2, 4.0, 5.5, "汚職大好き警察官", "贪污警察"))
            self.assertEqual(report["cues_changed"], 1)
            self.assertEqual(report["replacements"], 1)
            self.assertEqual(report["table_size"], 7)
            self.assertEqual(report["protections"], 3)
            self.assertEqual(path.read_bytes(), before)

    def test_project_complete_word_rule_overrides_global_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ja_fix.TABLE_NAME).write_text(ja_fix.SEED, encoding="utf-8")
            table = ja_fix.merged_table(root=tmp, rules=[("お食事", "汚職")])
            self.assertEqual(ja_fix.apply_text("お食事", table)[0], "汚職")

    def test_project_short_rule_override_keeps_its_literal_meaning(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ja_fix.TABLE_NAME).write_text(ja_fix.SEED, encoding="utf-8")
            table = ja_fix.merged_table(root=tmp, rules=[("お食", "料理")])
            self.assertEqual(ja_fix.apply_text("お食事", table)[0], "料理事")

    def test_table_cache_uses_the_same_protection_during_text_correction(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ja_fix.TABLE_NAME).write_text(ja_fix.SEED, encoding="utf-8")
            table = ja_fix.TableCache(tmp).get()
            self.assertEqual(ja_fix.apply_text("お食事券とお職警官", table)[0], "お食事券と汚職警官")


if __name__ == "__main__":
    unittest.main()
