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
                  translate_project, validate_translation, worker)


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

    def test_seven_exports_preserve_single_language_and_share_timing(self):
        p = make_project()
        for row in p["cues"]:
            row["zh"] = "这是对应的中文译文。"
        files = export_files(self.path, p)
        self.assertEqual(len(files), 7)
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

    def test_existing_complete_project_exports_seven_without_asr_or_api(self):
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
        self.assertEqual(len(done["files"]), 7)
        self.assertTrue(any(str(f).endswith("_中文_zh.ass") for f in done["files"]))
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
        p.write_text("8\r\n00:00:01,250 --> 00:00:03,499\r\n<i>こんにちは。</i>\r\n\r\n12\r\n01:02:03.999 --> 01:02:05.100\r\nテストです。\r\n", encoding="utf-8-sig", newline="")
        cues = read_srt(p)
        self.assertEqual([c.id for c in cues], [1, 2])
        self.assertEqual(cues[0].start, 1.25)
        self.assertEqual(cues[1].end, 3725.1)
        self.assertEqual(cues[0].source, "こんにちは。")
        self.assertIn("01:02:03,999", format_srt(cues, language="ja"))

    def test_asr_options_keep_quiet_and_low_confidence_speech(self):
        from core import ASR_OPTIONS
        # 针对带背景音乐的素材放宽丢弃阈值，减少漏识别。
        self.assertEqual(ASR_OPTIONS["beam_size"], 5)
        self.assertTrue(ASR_OPTIONS["word_timestamps"])
        self.assertTrue(ASR_OPTIONS["vad_filter"])
        self.assertLessEqual(ASR_OPTIONS["vad_parameters"]["threshold"], 0.35)
        self.assertGreaterEqual(ASR_OPTIONS["vad_parameters"]["speech_pad_ms"], 500)
        self.assertLessEqual(ASR_OPTIONS["no_speech_threshold"], 0.35)
        self.assertLessEqual(ASR_OPTIONS["log_prob_threshold"], -1.2)
        self.assertGreaterEqual(ASR_OPTIONS["compression_ratio_threshold"], 3.5)
        self.assertFalse(ASR_OPTIONS["condition_on_previous_text"])

    def test_estimate_cost_counts_only_pending_cues(self):
        from core import estimate_cost
        cues = [asdict(Cue(1, 0.0, 1.0, "こんにちは世界")), asdict(Cue(2, 1.0, 2.0, "hello world"))]
        cues[0]["zh"] = "已经翻译"
        n, tin, tout, cost = estimate_cost(cues, "deepseek-flash", force=False)
        self.assertEqual(n, 1)
        self.assertGreater(tin, 0)
        self.assertGreater(tout, 0)
        self.assertGreater(cost, 0)
        n2, tin2, tout2, cost2 = estimate_cost(cues, "deepseek-flash", force=True)
        self.assertEqual(n2, 2)
        self.assertGreater(tin2, tin)
        self.assertGreater(cost2, cost)

    def test_ass_format_uses_template_styles_and_times(self):
        from core import format_ass, parse_ass_template
        template = """[Script Info]
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Demo,SimSun,50,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        parts = parse_ass_template(template)
        self.assertIsNotNone(parts)
        self.assertIn("Demo", parts[2])
        cues = [asdict(Cue(1, 1.25, 2.0, "Original text", "中文译文"))]
        text = format_ass(cues, "Demo", template)
        self.assertIn("Style: Demo", text)
        self.assertIn("PlayResX: 1920", text)
        self.assertIn("Dialogue: 0,0:00:01.25,0:00:02.00,Demo", text)
        self.assertIn("中文译文", text)
        self.assertNotIn("Original text", text)

    def test_ass_format_falls_back_to_default_style(self):
        from core import format_ass
        cues = [asdict(Cue(1, 0.0, 1.0, "src", "中文"))]
        text = format_ass(cues, "Missing", None)
        self.assertIn("Style: Default", text)
        self.assertIn("[Events]", text)

    def test_ass_format_escapes_braces(self):
        from core import format_ass
        cues = [asdict(Cue(1, 0.0, 1.0, "src", "带{花括号}的中文"))]
        text = format_ass(cues, "Default", None)
        self.assertIn("带\\{花括号\\}的中文", text)

    def test_export_files_writes_chinese_ass(self):
        p = make_project(2)
        for row in p["cues"]:
            row["zh"] = "已经翻译好的内容。"
        save_project(self.path, p)
        files = export_files(self.path, p)
        self.assertEqual(len(files), 7)
        ass = next(f for f in files if str(f).endswith(".ass"))
        content = Path(ass).read_text(encoding="utf-8-sig")
        self.assertIn("[Events]", content)
        self.assertIn("已经翻译好的内容。", content)

    def test_export_reports_blocked_write_with_actionable_message(self):
        p = make_project(2)
        for row in p["cues"]:
            row["zh"] = "已经翻译好的内容。"
        save_project(self.path, p)
        with patch("core.atomic_write", side_effect=PermissionError(13, "拒绝访问")):
            with self.assertRaises(UserError) as ctx:
                export_files(self.path, p)
        self.assertIn("信任区", str(ctx.exception))

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

    def test_dead_system_proxy_falls_back_to_direct_connection(self):
        # 系统里配着代理软件但代理没开时，请求会秒失败；应当自动改走直连而不是直接报错。
        body=json.dumps(reply([{"id":1,"text":"你好"}])).encode("utf-8")
        class Response:
            def read(self,n): return body
            def __enter__(self): return self
            def __exit__(self,*exc): return False
        class Opener:
            def __init__(self): self.calls=0
            def open(self,request,timeout=None):
                self.calls+=1
                return Response()
        opener=Opener()
        client=DeepSeekClient("fake-key")
        with patch("core.urllib.request.getproxies",return_value={"https":"http://127.0.0.1:7890"}), \
             patch("core.urllib.request.urlopen",side_effect=urllib.error.URLError("connection refused")) as proxied, \
             patch("core.urllib.request.build_opener",return_value=opener):
            result=client.translate_safe([Cue(1,0,1,"Hello")],"en",[],"")
        self.assertEqual(result,{1:"你好"})
        self.assertEqual(proxied.call_count,1)  # 先按系统设置试一次代理
        self.assertEqual(opener.calls,1)        # 失败后立刻改直连，不等待

    def test_proxy_fallback_sends_a_fresh_unproxied_request(self):
        # urllib 走代理时会改写 request.host；如果复用同一个 Request 对象，
        # “改直连”的那一次仍会连到代理地址。这里模拟这个改写，确保直连请求是全新的。
        body=json.dumps(reply([{"id":1,"text":"你好"}])).encode("utf-8")
        seen=[]
        def proxied(request,timeout=None):
            request.host="127.0.0.1:7890"   # 与 urllib.request.Request.set_proxy 的行为一致
            raise urllib.error.URLError("connection refused")
        class Response:
            def read(self,n): return body
            def __enter__(self): return self
            def __exit__(self,*exc): return False
        class Opener:
            def open(self,request,timeout=None):
                seen.append(request.host)
                return Response()
        client=DeepSeekClient("fake-key")
        with patch("core.urllib.request.getproxies",return_value={"https":"http://127.0.0.1:7890"}), \
             patch("core.urllib.request.urlopen",side_effect=proxied), \
             patch("core.urllib.request.build_opener",return_value=Opener()):
            result=client.translate_safe([Cue(1,0,1,"Hello")],"en",[],"")
        self.assertEqual(result,{1:"你好"})
        self.assertEqual(seen,["api.deepseek.com"])

    def test_proxy_and_direct_both_dead_reports_network_error(self):
        class NoSleep:
            def is_set(self): return False
            def wait(self,timeout): return False
        def dead(request,timeout=None): raise urllib.error.URLError("connection refused")
        class DeadOpener:
            def open(self,request,timeout=None): raise urllib.error.URLError("connection refused")
        client=DeepSeekClient("fake-key",stop=NoSleep())
        with patch("core.urllib.request.getproxies",return_value={"https":"http://127.0.0.1:7890"}), \
             patch("core.urllib.request.urlopen",side_effect=dead) as proxied, \
             patch("core.urllib.request.build_opener",return_value=DeadOpener()):
            with self.assertRaises(UserError) as caught:
                client.translate_safe([Cue(1,0,1,"Hello")],"en",[],"")
        self.assertEqual(proxied.call_count,1)  # 系统代理只试一次，不再反复重试它
        self.assertIn("检查网络",str(caught.exception))

    def test_http_error_does_not_bypass_configured_proxy(self):
        # 代理本身是通的（能返回 401），就不能绕过它——只对连不上才改直连。
        failure=urllib.error.HTTPError("https://api.deepseek.com/chat/completions",401,"Unauthorized",{},None)
        client=DeepSeekClient("fake-key")
        with patch("core.urllib.request.getproxies",return_value={"https":"http://127.0.0.1:7890"}), \
             patch("core.urllib.request.urlopen",side_effect=failure) as call, \
             patch("core.urllib.request.build_opener") as build:
            with self.assertRaises(UserError):
                client.translate_safe([Cue(1,0,1,"Hello")],"en",[],"")
        self.assertEqual(call.call_count,1)
        build.assert_not_called()

    def test_job_write_failure_reports_security_software_guidance(self):
        captured=[]
        with patch("core.run_job",side_effect=PermissionError(13,"拒绝访问")):
            worker({},self.stop,SimpleNamespace(put=lambda item: captured.append(item)))
        message=[v for k,v in captured if k=="error"][0]
        self.assertIn("信任区",message)
        self.assertNotIn("PermissionError",message)

    def test_job_other_failure_keeps_generic_message(self):
        captured=[]
        with patch("core.run_job",side_effect=RuntimeError("boom")):
            worker({},self.stop,SimpleNamespace(put=lambda item: captured.append(item)))
        message=[v for k,v in captured if k=="error"][0]
        self.assertIn("处理失败（RuntimeError）",message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
