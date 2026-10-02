import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import app
import core
from core import UserError, duration_milliseconds, export_files, load_project, read_srt, save_duration_change, save_project, set_project_duration
from test_editor_actions import Controller, Value, Widget


def project():
    # Intentionally unsorted rows: the next start must be found by time, not list order.
    return {"version": 1, "name": "字幕调时", "language": "en", "recognition_complete": True,
            "translation_profile": {"model": "saved-model", "glossary": "keep", "prompt_version": 1},
            "cues": [{"id": 7, "start": 8., "end": 9., "source": "Last.", "zh": "最后一句。"},
                     {"id": 2, "start": 1.25, "end": 2.5, "source": "First.", "zh": "第一句。"},
                     {"id": 4, "start": 3., "end": 4.5, "source": "Second.", "zh": "第二句。"}]}


class DurationTests(unittest.TestCase):
    def test_millisecond_values_and_invalid_durations(self):
        for value, expected in [("2.5", 2500), ("0.001", 1), (".750", 750), (3, 3000), ("2.", 2000)]:
            self.assertEqual(duration_milliseconds(value), expected)
        for value in ("", "0", "-1", "nan", "inf", "2.1234", "1e4", "86401", True, None):
            with self.subTest(value=value), self.assertRaises(UserError):
                duration_milliseconds(value)

    def test_single_precise_duration_preserves_other_rows_text_and_settings(self):
        original = project()
        snapshot = deepcopy(original)
        updated, report = set_project_duration(original, "2.500", [4], False)
        self.assertEqual(updated["cues"][2]["end"], 5.5)
        self.assertEqual(updated["cues"][:2], snapshot["cues"][:2])
        self.assertEqual(updated["translation_profile"], snapshot["translation_profile"])
        self.assertEqual(updated["cues"][2]["zh"], "第二句。")
        self.assertEqual(report["changed_count"], 1)
        self.assertEqual(original, snapshot)

    def test_uniform_full_duration_keeps_starts_and_allows_overlap_when_disabled(self):
        original = project()
        updated, report = set_project_duration(original, "4", stop_at_next=False)
        self.assertEqual([r["end"] for r in updated["cues"]], [12., 5.25, 7.])
        self.assertEqual([r["start"] for r in updated["cues"]], [r["start"] for r in original["cues"]])
        self.assertEqual(report["capped_count"], 0)

    def test_cap_uses_next_chronological_start_and_last_keeps_requested_length(self):
        updated, report = set_project_duration(project(), "4", stop_at_next=True)
        self.assertEqual([r["end"] for r in updated["cues"]], [12., 3., 7.])
        self.assertEqual(report["capped_count"], 1)
        updated, report = set_project_duration(project(), "20", [2], True)
        self.assertEqual(updated["cues"][1]["end"], 3.)
        self.assertEqual(updated["cues"][2]["end"], 4.5)

    def test_simultaneous_starts_remain_positive_and_are_reported(self):
        p = project()
        p["cues"][0]["start"] = 3.
        updated, report = set_project_duration(p, "0.001")
        self.assertEqual([r["end"] for r in updated["cues"]], [3.001, 1.251, 3.001])
        self.assertEqual(report["same_start_count"], 2)
        self.assertTrue(all(r["end"] > r["start"] for r in updated["cues"]))
        for ids in ([], [999], [True]):
            with self.assertRaises(UserError):
                set_project_duration(p, 2, ids)

    def test_saved_backup_reopens_and_exports_keep_matching_bilingual_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            original = project()
            save_project(path, original)
            with patch("core.DeepSeekClient", side_effect=AssertionError("API forbidden")), patch("core.transcribe", side_effect=AssertionError("ASR forbidden")):
                updated, report, backup = save_duration_change(path, original, "2.5", [4], False)
                files = export_files(path, updated)
            self.assertEqual(load_project(backup)["cues"], original["cues"])
            self.assertEqual(load_project(path)["cues"][2]["end"], 5.5)
            self.assertEqual(len(files), 6)
            times = [[(c.start, c.end) for c in read_srt(files[i])] for i in (0, 2, 4)]
            self.assertEqual(times[0], times[1])
            self.assertEqual(times[1], times[2])

    def test_backup_or_project_write_failure_cannot_overwrite_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            original = project()
            save_project(path, original)
            before = path.read_bytes()
            real_save = core.save_project
            for fail_backup in (True, False):
                def write(target, data):
                    if (Path(target) != path) == fail_backup:
                        raise OSError("simulated disk full")
                    real_save(target, data)
                with patch("core.save_project", side_effect=write), self.assertRaises(OSError):
                    save_duration_change(path, original, 4)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(original, project())

    def test_unchanged_duration_creates_no_extra_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            p = project()
            save_project(path, p)
            updated, report, backup = save_duration_change(path, p, 1, [7])
            self.assertEqual(report["changed_count"], 0)
            self.assertIsNone(backup)
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)

    def test_editor_action_keeps_selected_row_and_existing_translations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            save_project(path, project())
            a = Controller()
            a.reload_project(path)
            a.selected_id = 4
            a.tree.selection_set("4")
            a.select_cue()
            opts = {"seconds": "2.75", "cue_ids": [4], "stop_at_next": False}
            with patch("app.DurationDialog", return_value=SimpleNamespace(result=opts)):
                a.set_duration()
            self.assertEqual(a.selected_id, 4)
            self.assertEqual(a.time_start.get(), "00:00:03,000")
            self.assertEqual(a.time_end.get(), "00:00:05,750")
            self.assertEqual(a.zh_text.value, "第二句。")
            before = deepcopy(a.project)
            with patch("app.DurationDialog", return_value=SimpleNamespace(result={**opts, "seconds": "4"})), patch("app.save_duration_change", side_effect=OSError("disk full")), patch("app.messagebox.showerror"):
                a.set_duration()
            self.assertEqual(a.project, before)
            self.assertEqual(load_project(path)["cues"], before["cues"])
            with patch("app.DurationDialog", return_value=SimpleNamespace(result=None)):
                a.set_duration()
            self.assertEqual(a.project, before)

    def test_dialog_preview_blocks_invalid_input_and_reports_truncation(self):
        class Preview:
            def __getattr__(self, name): return getattr(app.DurationDialog, name).__get__(self, Preview)
        d = Preview()
        d.project, d.selected_id = project(), 2
        d.seconds, d.scope, d.stop_at_next = Value("4"), Value("current"), Value(True)
        d.hint, d.apply_button = Value(), Widget()
        self.assertTrue(d.preview())
        self.assertIn("00:00:03,000", d.hint.get())
        self.assertIn("提前结束", d.hint.get())
        d.scope.set("all")
        self.assertTrue(d.preview())
        self.assertIn("3 条字幕", d.hint.get())
        d.seconds.set("0")
        self.assertFalse(d.preview())
        self.assertEqual(d.apply_button.cget("state"), "disabled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
