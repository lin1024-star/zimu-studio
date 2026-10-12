# -*- coding: utf-8 -*-
"""小白指南（可搜索 FAQ）的结构检查。

为什么测这个：这份文件有 151 条问答，每条一个 id 和数字编号。
手工加条目时最容易犯三种错，而三种都会让使用者搜不到东西：
  ① 编号断号（q148 后面直接跳到 q150）
  ② 开闭标签不配对（HTML 塌了，搜索框整个失效）
  ③ 加了条目，但页面顶部写着的「N 个常见问题」没跟着改
这次就真踩了③：加了 3 条，头部还写着 148。
"""
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAYLOAD = REPO / "payload" / "guide.html"
COPY = REPO / "小白指南.html"


class GuideStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAYLOAD.read_text("utf-8")

    def test_details_tags_balance(self):
        opens = len(re.findall(r'<details class="faq"', self.html))
        closes = self.html.count("</details>")
        self.assertEqual(opens, closes, f"开 {opens} 个、闭 {closes} 个 —— HTML 塌了")
        self.assertGreater(opens, 100)

    def test_question_numbers_are_continuous(self):
        numbers = sorted(int(n) for n in re.findall(r'id="q(\d+)"', self.html))
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)),
                         "编号有断号或重复 —— 搜索会漏条目")

    def test_header_count_matches_reality(self):
        """凡是写着「N 个常见问题」的地方，N 都必须等于实际条数。

        这次真出过：加了 3 条，顶部链接改了，正文大标题还写着 148 —— 是这条测试抓出来的。
        所以这里检查**所有**出现处，不是只看第一个。
        """
        real = len(re.findall(r'<details class="faq"', self.html))
        claimed = re.findall(r"(\d+) 个常见问题", self.html)
        self.assertTrue(claimed, "页面里没写条数")
        for value in claimed:
            self.assertEqual(int(value), real, f"写着 {value} 条，实际 {real} 条")

    def test_the_two_copies_are_identical(self):
        self.assertEqual(PAYLOAD.read_bytes(), COPY.read_bytes(),
                         "payload/guide.html 和根目录 小白指南.html 不一致")

    def test_new_entries_are_present(self):
        for text in ("日文纠正表", "这句识别错了", "更多操作 ▾"):
            self.assertIn(text, self.html, f"新功能「{text}」没写进 FAQ")

    def test_the_model_answer_is_updated(self):
        self.assertIn("怎么选识别模型", self.html)
        self.assertIn("准确度基本持平", self.html)
        self.assertNotIn("small 和 Turbo 怎么选", self.html, "旧的模型答案没换掉")

    def test_no_stale_count_left(self):
        self.assertNotIn("148 个常见问题", self.html)
        self.assertNotIn("148 条", self.html)


if __name__ == "__main__":
    unittest.main()
