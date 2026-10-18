# -*- coding: utf-8 -*-
"""术语表作用范围的测试：**打开某一期再关软件，不许改掉全局默认**。

## 为什么加（2026-10-18 修，埋了很久的雷）

打开项目时，软件会把这一期存的术语表塞进「人名 / 术语说明」输入框；
关软件时 save_settings 又把输入框内容写回 settings.json ——
于是"打开某一期看一眼、关掉软件"，全局术语表就被那一期替掉了。
实测有一期存的是 477 字的窄版，全局是 707 字。

现在的规矩：
- 有项目时，输入框里的是「本期术语表」；save_settings **只保留原来的全局默认**
- 想改以后每一期的默认值 → 必须点「把术语表存为全局默认」（明确动作）
- 没有项目时，输入框就是全局默认，保存照旧

用控制器替身（不建窗口），跟 test_editor_actions 同一套办法。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import app  # noqa: E402


class Value:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Text:
    """ScrolledText 的替身。"""

    def __init__(self, text=""):
        self.value = text

    def get(self, *args):
        return self.value

    def delete(self, *args):
        self.value = ""

    def insert(self, position, value):
        self.value = value

    def edit_modified(self, *args):
        pass


class Controller:
    """只带 save_settings / 术语表那几个方法用得到的属性。"""

    def __init__(self, settings, project=None):
        self.settings = dict(settings)
        self.project = project
        for name in ("out_var", "lang_var", "asr_var", "device_var", "local_model_var", "model_var",
                     "ass_style_var", "live_lang_var"):
            setattr(self, name, Value(""))
        self.ass_style_name_var = Value("Default")
        self.alert_var = Value(True)
        self.tidy_var = Value(True)
        self.ccheck_var = Value(False)
        self.separate_var = Value(False)
        self.glossary = Text()
        self.glossary_hint_var = Value()
        self.status_var = Value()
        self.log = lambda *args, **kwargs: None
        self.refresh_glossary_hint = lambda *args, **kwargs: None
        self.apply_glossary_marks = lambda *args, **kwargs: None

    persist_api_key = lambda self: True

    def __getattr__(self, name):
        """没定义的属性 → 用 app.Application 里的真方法（绑到这个替身上）。"""
        return getattr(app.Application, name).__get__(self, Controller)


class GlossaryScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "settings.json"
        self.patcher = patch.object(app, "CONFIG_PATH", self.config)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def saved_glossary(self):
        return json.loads(self.config.read_text("utf-8"))["glossary"]

    def test_without_project_the_widget_is_saved_as_global(self):
        ctrl = Controller({"glossary": "旧默认"}, project=None)
        ctrl.glossary.value = "新默认 = 值"
        self.assertTrue(ctrl.save_settings())
        self.assertEqual(self.saved_glossary(), "新默认 = 值")

    def test_opening_a_project_does_not_overwrite_global(self):
        """核心回归：有项目时，本期术语表不许写回 settings.json。"""
        global_text = "全局 = " + "长" * 700          # 707 字那期的样子
        project_text = "本期 = " + "短" * 470         # 477 字窄版
        ctrl = Controller({"glossary": global_text}, project={"cues": []})
        ctrl.glossary.value = project_text            # 打开项目后输入框被换成这一期的
        self.assertTrue(ctrl.save_settings())
        self.assertEqual(self.saved_glossary(), global_text)

    def test_save_as_global_is_the_explicit_way(self):
        ctrl = Controller({"glossary": "旧默认"}, project={"cues": []})
        ctrl.glossary.value = "本期 = 专用"
        with patch.object(app.messagebox, "showinfo") as info:
            ctrl.save_glossary_as_global()
        self.assertTrue(info.called)
        self.assertEqual(self.saved_glossary(), "本期 = 专用")
        self.assertEqual(ctrl.settings["glossary"], "本期 = 专用")

    def test_glossary_for_settings_switches_by_project(self):
        no_project = Controller({"glossary": "全局"}, project=None)
        no_project.glossary.value = "输入的"
        self.assertEqual(no_project.glossary_for_settings(), "输入的")

        with_project = Controller({"glossary": "全局"}, project={"cues": []})
        with_project.glossary.value = "本期"
        self.assertEqual(with_project.glossary_for_settings(), "全局")

    def test_failed_write_restores_previous_global(self):
        ctrl = Controller({"glossary": "旧默认"}, project={"cues": []})
        ctrl.glossary.value = "本期 = 专用"
        with patch.object(app, "atomic_write", side_effect=OSError("disk full")), \
                patch.object(app.messagebox, "showwarning") as warn, \
                patch.object(app.messagebox, "showinfo") as info:
            ctrl.save_glossary_as_global()
        self.assertTrue(warn.called)
        self.assertFalse(info.called)
        self.assertEqual(ctrl.settings["glossary"], "旧默认")


if __name__ == "__main__":
    unittest.main()
