import json
import sys
import tempfile
import threading
import unittest
import urllib.error
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
from core import (Cancelled, Cue, DeepSeekClient, ResponseError, UserError, clean,
                  cues_from_segments, export_files, fingerprint, format_srt,
                  load_project, read_srt, run_job, save_project, timestamp,
                  translate_project, validate_translation)


def make_project(n=4):
    return {"version": 1, "name": "素材 日本語", "input": "fixture.mp4", "language": "en",
            "recognition_complete": True,
            "cues": [asdict(Cue(i+1, i*3.0, i*3.0+2.8, f"Original line {i+1}.")) for i in range(n)]}


def reply(rows, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": json.dumps({"translations": rows}, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "project.json"
        self.stop = threading.Event()
        self.emit = lambda *args: None

    def tearDown(self):
        self.temp.cleanup()

    def test_six_exports_preserve_single_language_and_share_timing(self):
        p = make_project()
        for row in p["cues"]:
            row["zh"] = "这是对应的中文译文。"
        files = export_files(self.path, p)
        self.assertEqual(len(files), 6)
        original, chinese, bilingual = (read_srt(files[i]) for i in (0, 2, 4))
        self.assertEqual([(c.start, c.end) for c in original], [(c.start, c.end) for c in chinese])
        self.assertEqual([(c.start, c.end) for c in original], [(c.start, c.end) for c in bilingual])
        blocks = files[4].read_text("utf-8-sig").strip().split("\n\n")
        self.assertEqual(len(blocks), len(p["cues"]))
        for block, row in zip(blocks, p["cues"]):
            self.assertEqual(block.splitlines()[2:], [row["source"], row["zh"]])
        self.assertEqual(files[5].read_text("utf-8-sig"),
                         "\n\n".join(r["source"] + "\n" + r["zh"] for r in p["cues"]) + "\n")
        for f in files:
            self.assertTrue(f.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertNotIn("Original", files[3].read_text("utf-8-sig"))
        self.assertNotIn("中文", files[1].read_text("utf-8-sig"))
        self.assertNotIn("-->", files[1].read_text("utf-8-sig"))
        self.assertNotIn("-->", files[5].read_text("utf-8-sig"))
        second = export_files(self.path, p)
        self.assertNotEqual(files[0].parent, second[0].parent)
        self.assertTrue(files[0].exists())

    def test_japanese_bilingual_golden_output_and_pairing(self):
        p = make_project(2)
        p["language"] = "ja"
        p["cues"] = [asdict(Cue(4, 1.25, 3.499, "<i>こんにちは。</i>", "你好。")),
                     asdict(Cue(9, 5.5, 7.75, "次の\n場面です。", "这是下一个场景。"))]
        files = export_files(self.path, p)
        self.assertTrue(files[4].name.endswith("_混合_ja_zh.srt"))
        self.assertEqual(files[4].read_text("utf-8-sig"),
                         "1\n00:00:01,250 --> 00:00:03,499\nこんにちは。\n你好。\n\n"
                         "2\n00:00:05,500 --> 00:00:07,750\n次の 場面です。\n这是下一个场景。\n\n")
        self.assertEqual(files[5].read_text("utf-8-sig"),
                         "こんにちは。\n你好。\n\n次の 場面です。\n这是下一个场景。\n")

    def test_long_bilingual_cues_wrap_without_losing_or_mixing_text(self):
        samples = [("en", "This longer English sentence should wrap while preserving all of its words and punctuation."),
                   ("ja", "今日は日本語の字幕を使って、長い文章が途中で消えずにすべて出力されることを確かめます。")]
        chinese = "译文在原文下面，较长的中文字幕也应完整显示，所有文字和标点都必须保留。"
        for lang, source in samples:
            with self.subTest(language=lang):
                text = format_srt([Cue(7, 0.9994, 0.99949, source, chinese)], language=lang, bilingual=True)
                lines = text.strip().splitlines()
                self.assertEqual(lines[:2], ["1", "00:00:00,999 --> 00:00:01,000"])
                split = next(i for i, line in enumerate(lines[2:], 2) if line.startswith("译文"))
                self.assertGreater(split, 3)
                self.assertGreater(len(lines) - split, 1)
                self.assertEqual("".join(lines[2:split]).replace(" ", ""), source.replace(" ", ""))
                self.assertEqual("".join(lines[split:]), chinese)

    def test_existing_complete_project_exports_six_without_asr_or_api(self):
        p = make_project(2)
        for row in p["cues"]:
            row["zh"] = "已经翻译好的内容。"
        p["translation_profile"] = {"model": "m", "glossary": "", "prompt_version": 1}
        save_project(self.path, p)
        events = []
        cfg = {"project_path": str(self.path), "mode": "translate", "model": "m", "glossary": "", "api_key": ""}
        with patch("core.transcribe", side_effect=AssertionError("Must not repeat ASR")), \
             patch("core.DeepSeekClient", side_effect=AssertionError("Must not call API")):
            run_job(cfg, self.stop, lambda k, v: events.append((k, v)))
        done = next(v for k, v in events if k == "done")
        self.assertEqual(len(done["files"]), 6)
        self.assertEqual(load_project(self.path)["cues"], p["cues"])

    def test_incomplete_translation_cannot_masquerade_as_complete(self):
        p = make_project()
        with self.assertRaises(UserError):
            export_files(self.path, p)
        self.assertFalse((self.root / "exports").exists())
        original_only = export_files(self.path, p, False)
        self.assertEqual(len(original_only), 2)
        self.assertTrue(all("原文" in f.name for f in original_only))
        with self.assertRaises(UserError):
            format_srt([Cue(**p["cues"][0])], bilingual=True)

    def test_import_srt_preserves_milliseconds_and_markup_text(self):
        p = self.root / "日语.srt"
        p.write_text("8\r\n00:00:01,250 --> 00:00:03,499\r\n<i>こんにちは。</i>\r\n\r\n12\r\n01:02:03.999 --> 01:02:05.100\r\nテストです。\r\n", encoding="utf-8-sig")
        cues = read_srt(p)
        self.assertEqual([c.id for c in cues], [1, 2])
        self.assertEqual(cues[0].start, 1.25)
        self.assertEqual(cues[1].end, 3725.1)
        self.assertEqual(cues[0].source, "こんにちは。")
        self.assertIn("01:02:03,999", format_srt(cues, language="ja"))

    def test_broken_srt_is_rejected_instead_of_dropping_cues(self):
        p = self.root / "bad.srt"
        p.write_text("1\n00:00:02,000 --> 00:00:01,000\nHello\n", "utf-8")
        with self.assertRaises(UserError):
            read_srt(p)
        p.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n\n2\nBROKEN\nWorld\n", "utf-8")
        with self.assertRaises(UserError):
            read_srt(p)

    def test_translation_ids_cannot_shift_merge_duplicate_or_disappear(self):
        cues = [Cue(7, 0, 1, "Hello"), Cue(9, 1, 2, "World")]
        for rows in ([{"id":7,"text":"你好"}], [{"id":7,"text":"你好"},{"id":7,"text":"世界"}],
                     [{"id":7,"text":"你好"},{"id":9,"text":""}], [{"id":"7","text":"你好"},{"id":9,"text":"世界"}],
                     [{"id":7,"text":"你好"},{"id":9,"text":"世界"},{"id":10,"text":"多余"}]):
            with self.assertRaises(ResponseError):
                validate_translation(json.dumps({"translations":rows}),cues)
        good = [{"id":9,"text":"世界"},{"id":7,"text":"你好"}]
        self.assertEqual(validate_translation(json.dumps({"translations":good}),cues),{9:"世界",7:"你好"})

    def test_truncation_is_split_and_every_cue_is_recovered(self):
        calls = []
        def transport(body):
            rows = json.loads(body["messages"][1]["content"])["cues"]
            calls.append([r["id"] for r in rows])
            if len(rows)>1:
                return reply([], "length")
            return reply([{"id":rows[0]["id"],"text":"译文"}])
        client = DeepSeekClient("fake-test-key", transport=transport)
        cues = [Cue(2,0,1,"one"), Cue(4,1,2,"two")]
        self.assertEqual(client.translate_safe(cues,"en",[],""), {2:"译文",4:"译文"})
        self.assertEqual(calls,[[2,4],[2],[4]])

    def test_resume_preserves_completed_batch_and_skips_paid_work(self):
        p = make_project(20)
        calls = []
        def first(body):
            rows = json.loads(body["messages"][1]["content"])["cues"]
            calls.append(len(rows))
            if len(calls)==2:
                raise UserError("模拟网络中断")
            return reply([{"id":r["id"],"text":f"中文{r['id']}"} for r in rows])
        with self.assertRaises(UserError):
            translate_project(self.path,p,"fake","model","",self.stop,self.emit,client=DeepSeekClient("fake",transport=first))
        saved = load_project(self.path)
        self.assertEqual(sum(bool(r["zh"]) for r in saved["cues"]),16)
        resumed=[]
        def second(body):
            rows=json.loads(body["messages"][1]["content"])["cues"]
            resumed.extend(r["id"] for r in rows)
            return reply([{"id":r["id"],"text":"继续翻译"} for r in rows])
        translate_project(self.path,saved,"fake","model","",self.stop,self.emit,client=DeepSeekClient("fake",transport=second))
        self.assertEqual(resumed,[17,18,19,20])
        self.assertEqual(saved["cues"][0]["zh"],"中文1")

    def test_cancellation_keeps_successful_response_before_stopping(self):
        p=make_project(20)
        def transport(body):
            rows=json.loads(body["messages"][1]["content"])["cues"]
            self.stop.set()
            return reply([{"id":r["id"],"text":"已完成"} for r in rows])
        with self.assertRaises(Cancelled):
            translate_project(self.path,p,"fake","model","",self.stop,self.emit,client=DeepSeekClient("fake",transport=transport))
        self.assertEqual(sum(bool(r["zh"]) for r in load_project(self.path)["cues"]),16)

    def test_source_edit_only_retranslates_edited_cue(self):
        p=make_project()
        p["translation_profile"]={"model":"m","glossary":"","prompt_version":1}
        for r in p["cues"]: r["zh"]="原有中文"
        p["cues"][1].update(source="Corrected source",zh="")
        translated=[]
        def transport(body):
            rows=json.loads(body["messages"][1]["content"])["cues"]
            translated.extend(r["id"] for r in rows)
            return reply([{"id":r["id"],"text":"更新中文"} for r in rows])
        translate_project(self.path,p,"fake","m","",self.stop,self.emit,client=DeepSeekClient("fake",transport=transport))
        self.assertEqual(translated,[2])
        self.assertEqual(p["cues"][0]["zh"],"原有中文")

    def test_settings_change_preserves_previous_translation(self):
        p=make_project(2)
        p["translation_profile"]={"model":"m","glossary":"old","prompt_version":1}
        for r in p["cues"]: r["zh"]="旧译文"
        called=[]
        def transport(body):
            rows=json.loads(body["messages"][1]["content"])["cues"]
            called.extend(rows)
            return reply([{"id":r["id"],"text":"新术语"} for r in rows])
        translate_project(self.path,p,"fake","m","new",self.stop,self.emit,client=DeepSeekClient("fake",transport=transport))
        self.assertEqual(called, [])
        self.assertTrue(all(r["zh"]=="旧译文" for r in p["cues"]))
        translate_project(self.path,p,"fake","m","new",self.stop,self.emit,force=True,client=DeepSeekClient("fake",transport=transport))
        self.assertEqual(len(called),2)
        self.assertTrue(all(r["zh"]=="新术语" for r in p["cues"]))

    def test_word_timing_used_for_split_without_inventing_durations(self):
        words=[SimpleNamespace(word=" Hello",start=0.,end=.7),SimpleNamespace(word=" world.",start=.7,end=1.6),
               SimpleNamespace(word=" This",start=4.,end=4.4),SimpleNamespace(word=" works.",start=4.4,end=5.5)]
        s=SimpleNamespace(start=0,end=5.5,text="Hello world. This works.",words=words)
        cues=cues_from_segments([s],"en")
        self.assertEqual([c.source for c in cues],["Hello world.","This works."])
        self.assertEqual([(c.start,c.end) for c in cues],[(0.,1.6),(4.,5.5)])
        self.assertEqual(timestamp(3599.9996),"01:00:00,000")

    def test_project_never_serializes_credential_and_fingerprint_changes(self):
        p=make_project()
        p["api_key"]="secret-do-not-persist"
        save_project(self.path,p)
        self.assertNotIn("secret-do-not-persist",self.path.read_text("utf-8"))
        f=self.root/"input.mp4"
        f.write_bytes(b"A"*20)
        old=fingerprint(f)
        f.write_bytes(b"B"*20)
        self.assertNotEqual(old,fingerprint(f))

    def test_srt_job_skips_asr_and_can_export_without_api(self):
        s=self.root/"input.srt"
        s.write_text("1\n00:00:00,000 --> 00:00:02,000\nThis is a test.\n",encoding="utf-8")
        events=[]
        cfg={"input":str(s),"project_path":None,"output":str(self.root/"out"),"asr_model":"small",
             "language":"en","device":"cpu","model_dir":str(self.root/"models"),"mode":"transcribe"}
        with patch("core.transcribe",side_effect=AssertionError("Must not run ASR")):
            run_job(cfg,self.stop,lambda k,v:events.append((k,v)))
        done=[v for k,v in events if k=="done"][0]
        self.assertEqual(len(done["files"]),2)
        self.assertTrue(all(Path(f).exists() for f in done["files"]))

    def test_authentication_error_does_not_retry_or_echo_secret(self):
        client=DeepSeekClient("private-test-secret")
        failure=urllib.error.HTTPError("https://api.deepseek.com/chat/completions",401,"Unauthorized",{},None)
        with patch("core.urllib.request.urlopen",side_effect=failure) as call:
            with self.assertRaises(UserError) as caught:
                client.translate_safe([Cue(1,0,1,"Hello")],"en",[],"")
            self.assertEqual(call.call_count,1)
            self.assertIn("401",str(caught.exception))
            self.assertNotIn("private-test-secret",str(caught.exception))

    def test_cancelled_job_never_sends_an_api_request(self):
        self.stop.set()
        with patch("core.urllib.request.urlopen") as request:
            client=DeepSeekClient("fake",stop=self.stop)
            with self.assertRaises(Cancelled):
                client.translate_safe([Cue(1,0,1,"Hello")],"en",[],"")
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
