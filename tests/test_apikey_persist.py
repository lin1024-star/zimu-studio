# -*- coding: utf-8 -*-
"""API 密钥"存/删"规则的测试。

**为什么专门测这个**：真出过两次事 ——
原来是 `if 勾着 and 框里有内容: 存 else: 删`，
于是任何"把密钥框清空"的路径（界面异常、测试脚本）在退出时都会把
使用者存好的密钥删掉，他得重新粘一遍。2026-10-13 被坑了两次。

现在的规矩：**只有明确取消勾选才删**；勾着但框是空的，什么都不做。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))

import app as app_module


class FakeVar:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeApp:
    """只带 persist_api_key 需要的那两个变量。"""

    def __init__(self, remember, key):
        self.remember_var = FakeVar(remember)
        self.api_key_var = FakeVar(key)

    persist_api_key = app_module.Application.persist_api_key


class PersistApiKeyTest(unittest.TestCase):

    def _run(self, remember, key):
        calls = {"save": [], "forget": 0}
        with patch("winsecret.save_key", lambda value: calls["save"].append(value)), \
             patch("winsecret.forget_key", lambda: calls.__setitem__("forget", calls["forget"] + 1)):
            ok = FakeApp(remember, key).persist_api_key()
        return ok, calls

    def test_checked_with_key_saves(self):
        ok, calls = self._run(True, "sk-abc")
        self.assertTrue(ok)
        self.assertEqual(calls["save"], ["sk-abc"])
        self.assertEqual(calls["forget"], 0)

    def test_checked_but_empty_does_nothing(self):
        """★ 就是这条：框空了不代表"要删"，绝不能动已存的密钥。"""
        ok, calls = self._run(True, "")
        self.assertTrue(ok)
        self.assertEqual(calls["save"], [])
        self.assertEqual(calls["forget"], 0, "勾着但框是空的 → 不该删密钥")

    def test_checked_but_only_spaces_does_nothing(self):
        _, calls = self._run(True, "   ")
        self.assertEqual(calls["save"], [])
        self.assertEqual(calls["forget"], 0)

    def test_unchecked_forgets(self):
        """明确取消勾选 → 按说明清除。"""
        ok, calls = self._run(False, "sk-abc")
        self.assertTrue(ok)
        self.assertEqual(calls["forget"], 1)
        self.assertEqual(calls["save"], [])

    def test_unchecked_and_empty_forgets_once(self):
        _, calls = self._run(False, "")
        self.assertEqual(calls["forget"], 1)

    def test_never_raises(self):
        with patch("winsecret.save_key", side_effect=RuntimeError("钥匙串坏了")):
            self.assertFalse(FakeApp(True, "sk-abc").persist_api_key())


if __name__ == "__main__":
    unittest.main()
