import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
from core import (Cue, DeepSeekClient, UserError, clean, export_files, format_srt,
                  import_srt_project, insert_project_cue, load_project, read_srt,
                  run_job, store_imported_project, translate_project)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "素材_原文_ja.srt"
        self.zh = self.root / "素材_中文_zh.srt"
        self.source.write_text("8\n00:00:01,125 --> 00:00:02,750\nこんにちは。\n\n9\n00:00:05,000 --> 00:00:07,500\n次の場面。\n", "utf-8-sig")
        # Deliberately reversed order and unrelated SRT cue numbers.
        self.zh.write_text("800\n00:00:05,000 --> 00:00:07,500\n下一个场景。\n\n1\n00:00:01,125 --> 00:00:02,750\n你好。\n", "utf-8-sig")
        self.stop = threading.Event()
        self.emit = lambda *args: None

    def tearDown(self):
        self.temp.cleanup()

    def paired(self):
        return import_srt_project(self.source, "paired", chinese_path=self.zh)

    def test_pair_by_millisecond_times_not_srt_indices_or_position(self):
        project = self.paired()
        self.assertEqual(project["language"], "ja")
        self.assertEqual([c["zh"] for c in project["cues"]], ["你好。", "下一个场景。"])
        self.assertEqual(project["cues"][0]["start"], 1.125)
        path = store_imported_project(project, self.root / "output")
        self.assertEqual(load_project(path)["cues"], project["cues"])
        files = export_files(path, project)
        self.assertEqual(len(files), 6)
        self.assertEqual(read_srt(files[2])[0].source, "你好。")

    def test_mismatched_times_or_extra_cues_rejected_before_creating_project(self):
        original = self.zh.read_text("utf-8-sig")
        for data in [original.replace("01,125", "01,126"), original + "\n2\n00:00:08,000 --> 00:00:09,000\n多余。\n"]:
            self.zh.write_text(data, "utf-8-sig")
            with self.assertRaises(UserError):
                self.paired()
        self.assertFalse(list(self.root.rglob("project.json")))

    def test_import_save_is_separate_and_does_not_overwrite_subtitles(self):
        before = self.source.read_bytes()
        project = import_srt_project(self.source)
        with patch("core.DeepSeekClient", side_effect=AssertionError("API forbidden")), patch("core.transcribe", side_effect=AssertionError("ASR forbidden")):
            first = store_imported_project(project, self.root)
            second = store_imported_project(project, self.root)
            export_files(first, project, False)
        self.assertNotEqual(first, second)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertTrue(load_project(first)["recognition_complete"])

    def test_chinese_only_reopens_and_exports_two_chinese_files_without_api(self):
        p = import_srt_project(self.zh, "chinese")
        path = store_imported_project(p, self.root)
        events = []
        cfg = {"project_path": str(path), "mode": "translate", "model": "new-model", "glossary": "new", "api_key": "", "force": True}
        with patch("core.DeepSeekClient", side_effect=AssertionError("API forbidden")), patch("core.transcribe", side_effect=AssertionError("ASR forbidden")):
            run_job(cfg, self.stop, lambda k, v: events.append((k, v)))
        done = next(v for k, v in events if k == "done")
        self.assertEqual(len(done["files"]), 2)
        self.assertTrue(all("_中文_zh." in f for f in done["files"]))
        self.assertEqual(load_project(path)["subtitle_mode"], "chinese")

    def test_complete_import_needs_no_key_despite_changed_model_or_glossary(self):
        p = self.paired()
        p["translation_profile"] = {"model": "old", "glossary": "old", "prompt_version": 1}
        path = store_imported_project(p, self.root)
        cfg = {"project_path": str(path), "mode": "translate", "model": "new", "glossary": "new", "api_key": ""}
        with patch("core.DeepSeekClient", side_effect=AssertionError("Must reuse translated work")), patch("core.transcribe", side_effect=AssertionError("ASR forbidden")):
            run_job(cfg, self.stop, self.emit)
        self.assertEqual(load_project(path)["cues"], p["cues"])

    def test_insertion_keeps_translations_and_only_new_blank_cue_is_sent(self):
        p = self.paired()
        p["translation_profile"] = {"model": "old", "glossary": "old", "prompt_version": 1}
        updated, selected = insert_project_cue(p, 3.1, 4.5, "追加された行。")
        self.assertEqual(selected, 2)
        self.assertEqual([c["id"] for c in updated["cues"]], [1, 2, 3])
        self.assertEqual([c["zh"] for c in p["cues"]], ["你好。", "下一个场景。"])
        sent = []
        def transport(body):
            rows = json.loads(body["messages"][1]["content"])["cues"]
            sent.extend(rows)
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"translations": [{"id": r["id"], "text": "补充的一行。"} for r in rows]})}}]}
        path = store_imported_project(updated, self.root)
        translate_project(path, updated, "fake", "new", "new", self.stop, self.emit,
                          client=DeepSeekClient("fake", transport=transport))
        self.assertEqual(sent, [{"id": 2, "text": "追加された行。"}])
        self.assertEqual([r["zh"] for r in load_project(path)["cues"]], ["你好。", "补充的一行。", "下一个场景。"])

    def test_manual_translation_in_inserted_cue_skips_api_and_preserves_old_rows(self):
        p = self.paired()
        new, selected = insert_project_cue(p, 0, .8, "Opening.", "开场。")
        self.assertEqual(selected, 1)
        path = store_imported_project(new, self.root)
        with patch("core.DeepSeekClient", side_effect=AssertionError("API forbidden")):
            translate_project(path, new, "", "m", "", self.stop, self.emit)
        self.assertEqual([r["source"] for r in new["cues"]][1:], [r["source"] for r in p["cues"]])
        for args in [(2, 1, "invalid"), (-1, 2, "invalid"), (1, 2, " ")]:
            with self.assertRaises(UserError):
                insert_project_cue(p, *args)
        self.assertEqual(len(p["cues"]), 2)

    def test_bilingual_split_requires_review_for_wrapped_lines(self):
        f = self.root / "mixed.srt"
        f.write_text("1\n00:00:01,000 --> 00:00:04,000\nAn English sentence that wraps\nonto a second line.\n这是一句英文。\n", "utf-8-sig")
        with self.assertRaises(UserError):
            import_srt_project(f, "bilingual")
        p = import_srt_project(f, "bilingual", splits={1: 2})
        self.assertEqual(p["cues"][0]["source"], "An English sentence that wraps onto a second line.")
        self.assertEqual(p["cues"][0]["zh"], "这是一句英文。")
        for split in (0, 3, "2"):
            with self.assertRaises(UserError):
                import_srt_project(f, "bilingual", splits={1: split})

    def test_two_line_japanese_bilingual_with_encoding_choices(self):
        f = self.root / "mixed.srt"
        content = "1\r\n00:00:00,100 --> 00:00:02,999\r\n<i>今日はいい天気。</i>\r\n今天天气真好。\r\n"
        for encoding in ("utf-8-sig", "utf-16", "gb18030"):
            f.write_bytes(content.encode(encoding))
            p = import_srt_project(f, "bilingual", encoding="auto" if encoding != "gb18030" else encoding)
            self.assertEqual(p["language"], "ja")
            self.assertEqual(p["cues"][0]["zh"], "今天天气真好。")
        f.write_bytes("1\n00:00:00,000 --> 00:00:01,000\n字幕です。\n".encode("cp932"))
        self.assertEqual(read_srt(f, "cp932")[0].source, "字幕です。")

    def test_duplicate_time_intervals_pair_in_occurrence_order(self):
        self.source.write_text("1\n00:00:01,000 --> 00:00:03,000\nA\n\n2\n00:00:01,000 --> 00:00:03,000\nB\n", "utf-8")
        self.zh.write_text("7\n00:00:01,000 --> 00:00:03,000\n甲\n\n7\n00:00:01,000 --> 00:00:03,000\n乙\n", "utf-8")
        self.assertEqual([r["zh"] for r in self.paired()["cues"]], ["甲", "乙"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
