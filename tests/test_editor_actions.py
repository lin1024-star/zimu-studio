"""Persistence/controller checks with widget doubles; no graphical display needed."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import app
from core import Cue, import_srt_project, load_project, save_project, store_imported_project


class Value:
    def __init__(self, value=""):
        self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class Widget:
    def __init__(self, value=""):
        self.value, self.props = value, {"state": "normal"}
    def get(self, *args): return self.value
    def delete(self, *args): self.value = ""
    def insert(self, pos, value): self.value = value
    def configure(self, **kw): self.props.update(kw)
    def cget(self, name): return self.props[name]
    def focus_set(self): pass


class Tree:
    def __init__(self): self.rows, self.order, self.selected = {}, [], ()
    def get_children(self): return tuple(self.order)
    def exists(self, ident): return ident in self.rows
    def delete(self, *ids):
        for ident in ids:
            self.rows.pop(ident); self.order.remove(ident)
    def item(self, ident, **kw): self.rows[ident] = kw
    def insert(self, parent, pos, iid, **kw): self.rows[iid] = kw; self.order.append(iid)
    def move(self, ident, parent, position): self.order.remove(ident); self.order.insert(position, ident)
    def selection(self): return self.selected
    def selection_set(self, ident): self.selected = (ident,)
    def heading(self, *args, **kw): pass
    def see(self, *args): pass


class Controller:
    def __init__(self):
        self.project, self.project_path, self.selected_id, self.proc = None, None, None, None
        for name in ("source_var", "out_var", "lang_var", "status_var", "summary_var", "time_start", "time_end", "api_key_var", "model_var", "local_model_var"):
            setattr(self, name, Value())
        self.force_var = Value(False)
        for name in ("source_text", "zh_text", "source_label", "zh_label", "full_export_button", "source_export_button", "glossary", "key_entry"):
            setattr(self, name, Widget())
        self.tree = Tree()
        self.progress = {}
        self.notebook = SimpleNamespace(select=lambda *args: None)
        self.log = lambda *args: None
        self.wait_window = lambda *args: None
        self.diagnostics = SimpleNamespace(start_task=lambda *args: None, record=lambda *args, **kw: None)
    def __getattr__(self, name):
        return getattr(app.Application, name).__get__(self, Controller)


class EditorActions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.a = Controller()
        p = {"version": 1, "name": "编辑测试", "input": "old-video.mp4", "language": "en", "recognition_complete": True,
             "cues": [{"id": 1, "start": 1., "end": 3., "source": "One.", "zh": "第一句。"},
                      {"id": 2, "start": 7., "end": 9., "source": "Two.", "zh": "第二句。"}]}
        self.path = self.root / "project.json"
        save_project(self.path, p)
        self.a.reload_project(self.path)
        self.a.out_var.set(str(self.root / "imports"))

    def tearDown(self): self.temp.cleanup()

    def test_correction_selection_autosave_and_failed_write_preserves_translation(self):
        a = self.a
        a.source_text.value = "Corrected one."
        a.zh_text.value = "第一句已校对。"
        a.time_start.set("00:00:00,950")
        a.tree.selection_set("2")
        a.select_cue()
        saved = load_project(self.path)
        self.assertEqual(saved["cues"][0]["source"], "Corrected one.")
        self.assertEqual(saved["cues"][0]["zh"], "第一句已校对。")
        self.assertEqual(saved["cues"][0]["start"], .95)
        a.zh_text.value = "第二句尝试修改。"
        with patch("app.save_project", side_effect=OSError("disk full")), patch("app.messagebox.showerror"):
            self.assertFalse(a.save_current())
        self.assertEqual(a.project["cues"][1]["zh"], "第二句。")
        self.assertEqual(load_project(self.path)["cues"][1]["zh"], "第二句。")

    def test_add_action_reorders_tree_and_preserves_paid_rows_and_failed_write(self):
        a = self.a
        dialog = SimpleNamespace(result=Cue(1, 4, 5.5, "New cue.", "手动中文。"))
        with patch("app.AddCueDialog", return_value=dialog):
            a.add_cue()
        self.assertEqual(a.tree.get_children(), ("1", "2", "3"))
        self.assertEqual(a.selected_id, 2)
        self.assertEqual(a.source_text.value, "New cue.")
        saved = load_project(self.path)
        self.assertEqual([r["zh"] for r in saved["cues"]], ["第一句。", "手动中文。", "第二句。"])
        with patch("app.AddCueDialog", return_value=dialog), patch("app.save_project", side_effect=OSError("disk full")), patch("app.messagebox.showerror"):
            a.add_cue()
        self.assertEqual(load_project(self.path)["cues"], saved["cues"])
        self.assertEqual(a.project["cues"], saved["cues"])

    def test_complete_editor_continue_exports_without_key_model_or_worker(self):
        a = self.a
        a.local_model_var.set("missing-model")
        a.api_key_var.set("")
        with patch.object(a, "launch_job", side_effect=AssertionError("No worker for completed work")), patch("core.DeepSeekClient", side_effect=AssertionError("No paid request")):
            a.start_job("translate")
        self.assertEqual(len(list(Path(a.project["last_export"]).iterdir())), 6)
        self.assertIsNone(a.proc)

    def test_srt_file_entry_imports_and_saves_without_worker_then_chinese_edit(self):
        a = self.a
        f = self.root / "字幕_中文_zh.srt"
        f.write_text("1\n00:00:01,000 --> 00:00:03,000\n以前翻译的中文。\n", "utf-8-sig")
        project = import_srt_project(f, "chinese")
        with patch("app.filedialog.askopenfilename", return_value=str(f)), patch("app.SrtImportDialog", return_value=SimpleNamespace(result=project)):
            a.open_project()
        self.assertEqual(a.project["subtitle_mode"], "chinese")
        self.assertEqual(a.zh_text.cget("state"), "disabled")
        a.source_text.value = "校对后的中文。"
        a.save_current()
        a.force_var.set(True)
        with patch("core.DeepSeekClient", side_effect=AssertionError("No paid request")):
            a.start_job("translate")
        self.assertEqual(len(list(Path(a.project["last_export"]).iterdir())), 2)
        self.assertEqual(load_project(a.project_path)["cues"][0]["zh"], "校对后的中文。")
        a.reload_project(a.project_path)
        self.assertEqual(a.source_text.value, "校对后的中文。")

    def test_cancelled_import_keeps_current_project(self):
        original = self.a.project
        with patch("app.SrtImportDialog", return_value=SimpleNamespace(result=None)):
            self.a.import_subtitles("cancel.srt")
        self.assertIs(self.a.project, original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
