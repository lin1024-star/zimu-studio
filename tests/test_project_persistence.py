"""Project metadata must survive real disk writes and reopen without an API call."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PAYLOAD = Path(__file__).resolve().parents[1] / "payload"
sys.path.insert(0, str(PAYLOAD))
import app
import core
import ja_fix
from test_editor_actions import Controller, Widget


def project():
    return {"version": 1, "name": "保存测试", "language": "ja", "recognition_complete": True,
            "cues": [{"id": 1, "start": 0.0, "end": 2.0, "source": "テストです。", "zh": "这是测试。"}]}


def saved_check(items=True):
    return {"at": "2026-01-01 12:00:00", "total": 1, "batches": 2, "failed": 1,
            "items": [{"id": 1, "start": 0.0, "source": "テストです。", "zh": "这是测试。",
                       "why": "需要复核", "from": "模型"}] if items else []}


class ProjectPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "project.json"

    def test_local_rule_is_available_after_reopening(self):
        p = project()
        ja_fix.add_project_rule(p, "お食", "汚職", "本片纠正")
        core.save_project(self.path, p)
        loaded = core.load_project(self.path)
        self.assertEqual(loaded.get("ja_fix_local"), p["ja_fix_local"])
        fixed, _ = ja_fix.apply_text("お食", ja_fix.project_rules(loaded))
        self.assertEqual(fixed, "汚職")

    def test_check_result_survives_reopening_and_another_subtitle_edit(self):
        p = project()
        p["chinese_check"] = saved_check()
        core.save_project(self.path, p)
        loaded = core.load_project(self.path)
        loaded["cues"][0]["zh"] = "人工校对过的译文。"
        core.save_project(self.path, loaded)
        reopened = core.load_project(self.path)
        self.assertEqual(reopened.get("chinese_check"), p["chinese_check"])
        self.assertEqual(reopened["cues"][0]["zh"], "人工校对过的译文。")

    def test_fresh_python_process_reads_both_metadata_fields(self):
        p = project()
        ja_fix.add_project_rule(p, "お食", "汚職")
        p["chinese_check"] = saved_check()
        core.save_project(self.path, p)
        code = ("import json,sys; sys.path.insert(0,sys.argv[1]); import core; "
                "p=core.load_project(sys.argv[2]); "
                "print(json.dumps({k:p.get(k) for k in ('ja_fix_local','chinese_check')},ensure_ascii=True))")
        result = subprocess.run([sys.executable, "-B", "-c", code, str(PAYLOAD), str(self.path)],
                                capture_output=True, text=True, check=True, timeout=20)
        loaded = json.loads(result.stdout)
        self.assertEqual(loaded, {k: p[k] for k in ("ja_fix_local", "chinese_check")})

    def test_removing_local_rule_persists_without_affecting_other_projects(self):
        p = project()
        ja_fix.add_project_rule(p, "お食", "汚職")
        core.save_project(self.path, p)
        other_path = self.root / "other.json"
        core.save_project(other_path, project())
        loaded = core.load_project(self.path)
        self.assertTrue(ja_fix.remove_project_rule(loaded, "お食"))
        core.save_project(self.path, loaded)
        self.assertEqual(ja_fix.project_rules(core.load_project(self.path)), [])
        self.assertEqual(ja_fix.project_rules(core.load_project(other_path)), [])

    def test_legacy_project_and_credential_whitelist_still_work(self):
        p = project()
        p.update(api_key="PRIVATE_TEST_SENTINEL", unknown_field="PRIVATE_TEST_SENTINEL")
        core.save_project(self.path, p)
        loaded = core.load_project(self.path)
        self.assertEqual(loaded["cues"], p["cues"])
        self.assertNotIn("chinese_check", loaded)
        self.assertNotIn("ja_fix_local", loaded)
        self.assertNotIn("PRIVATE_TEST_SENTINEL", self.path.read_text("utf-8"))

    def test_empty_check_result_is_preserved(self):
        p = project()
        p["chinese_check"] = saved_check(items=False)
        core.save_project(self.path, p)
        self.assertEqual(core.load_project(self.path).get("chinese_check"), p["chinese_check"])


class ReopenedCheckControllerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "project.json"
        self.ui = Controller()
        self.ui.ccheck_button = Widget()
        self.ui.checking = False

    def reopen(self, check=True):
        p = project()
        if check:
            p["chinese_check"] = saved_check()
        core.save_project(self.path, p)
        self.ui.reload_project(self.path)

    def test_reopen_restores_count_and_opens_result_without_api_key(self):
        self.reopen()
        self.assertIn("1 处", self.ui.ccheck_button.props.get("text", ""))
        with patch.object(self.ui, "run_chinese_check") as rerun, \
                patch("app.DeepSeekClient", side_effect=AssertionError("No paid request")), \
                patch("app.ChineseCheckDialog") as dialog:
            self.ui.show_chinese_check()
        rerun.assert_not_called()
        self.assertEqual(dialog.call_args.args[1], saved_check()["items"])
        self.assertEqual(dialog.call_args.kwargs["failed"], 1)
        self.assertEqual(dialog.call_args.kwargs["checked_at"], saved_check()["at"])

    def test_empty_saved_result_is_viewable_without_running_check_again(self):
        self.ui.project = project()
        self.ui.project["chinese_check"] = saved_check(items=False)
        with patch.object(self.ui, "run_chinese_check") as rerun, patch("app.ChineseCheckDialog") as dialog:
            self.ui.show_chinese_check()
        rerun.assert_not_called()
        self.assertEqual(dialog.call_args.args[1], [])

    def test_project_without_saved_result_uses_existing_check_action(self):
        self.reopen(check=False)
        with patch.object(self.ui, "run_chinese_check") as rerun, patch("app.ChineseCheckDialog") as dialog:
            self.ui.show_chinese_check()
        rerun.assert_called_once_with()
        dialog.assert_not_called()

    def test_switching_project_or_source_resets_previous_result_count(self):
        self.reopen()
        other = self.path.with_name("other.json")
        core.save_project(other, project())
        self.ui.reload_project(other)
        self.assertNotIn("1 处", self.ui.ccheck_button.props.get("text", ""))
        self.ui.reload_project(self.path)
        self.ui._load_source("synthetic.mp4")
        self.assertNotIn("1 处", self.ui.ccheck_button.props.get("text", ""))

    def test_finished_check_saves_partial_result_status(self):
        self.reopen(check=False)
        result = {"items": saved_check()["items"], "batches": 2, "failed": 1}
        self.ui._wait_chinese_check({"done": True, "error": None, "result": result}, auto=True)
        loaded = core.load_project(self.path)
        self.assertEqual(loaded["chinese_check"]["failed"], 1)
        self.assertEqual(loaded["chinese_check"]["batches"], 2)
        self.assertEqual(loaded["chinese_check"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
