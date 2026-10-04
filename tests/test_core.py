import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
from core import (Cancelled, Cue, DEFAULT_EXPORT_KEYS, DeepSeekClient, ResponseError, UserError,
                  clean, cues_from_segments, default_export_keys, default_export_selection,
                  export_blank, export_choices, export_files, fingerprint, format_blank_ass,
                  format_blank_srt, format_srt, load_project, read_srt, run_job, save_project,
                  should_alert, timestamp, translate_project, validate_translation, worker)


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
        one = estimate_cost(cues, "deepseek-flash", force=False)
        self.assertEqual(one["count"], 1)
        self.assertGreater(one["input_tokens"], 0)
        self.assertGreater(one["output_tokens"], 0)
        self.assertGreater(one["off_cost"], 0)
        both = estimate_cost(cues, "deepseek-flash", force=True)
        self.assertEqual(both["count"], 2)
        self.assertGreater(both["input_tokens"], one["input_tokens"])
        self.assertGreater(both["off_cost"], one["off_cost"])

    def test_price_table_matches_the_official_current_prices(self):
        # 官方 2026 年价格（元/百万 tokens，缓存未命中）。改了这里就等于改了给使用者看的钱数。
        from core import PRICE_CNY
        self.assertEqual(PRICE_CNY["deepseek-flash"]["peak"], (2.0, 8.0))
        self.assertEqual(PRICE_CNY["deepseek-flash"]["off"], (1.0, 4.0))
        self.assertEqual(PRICE_CNY["deepseek-v4-pro"]["peak"], (9.0, 27.0))
        self.assertEqual(PRICE_CNY["deepseek-v4-pro"]["off"], (4.5, 13.5))

    def test_off_peak_is_exactly_half_of_peak(self):
        from core import PRICE_CNY, estimate_cost
        cues = [asdict(Cue(1, 0.0, 1.0, "こんにちは世界"))]
        estimate = estimate_cost(cues, "deepseek-flash")
        self.assertAlmostEqual(estimate["peak_cost"], estimate["off_cost"] * 2, places=9)
        for name, table in PRICE_CNY.items():
            self.assertAlmostEqual(table["peak"][0], table["off"][0] * 2, msg=name)
            self.assertAlmostEqual(table["peak"][1], table["off"][1] * 2, msg=name)

    def test_pricing_period_follows_the_official_peak_windows(self):
        from core import pricing_period
        monday = datetime(2026, 3, 2, 10, 0)          # 2026-03-02 周一，且不在任何假期里
        self.assertEqual(monday.weekday(), 0)
        self.assertTrue(pricing_period(monday)[0])                        # 周一 10:00 高峰
        self.assertTrue(pricing_period(datetime(2026, 3, 2, 14, 30))[0])   # 14:00-18:00 高峰
        self.assertFalse(pricing_period(datetime(2026, 3, 2, 12, 0))[0])   # 12:00 起空闲
        self.assertFalse(pricing_period(datetime(2026, 3, 2, 18, 0))[0])   # 18:00 起空闲
        self.assertFalse(pricing_period(datetime(2026, 3, 2, 8, 59))[0])
        saturday = datetime(2026, 3, 7, 10, 0)
        self.assertEqual(saturday.weekday(), 5)
        self.assertFalse(pricing_period(saturday)[0])                     # 周末全天空闲

    def test_public_holidays_on_weekdays_are_off_peak(self):
        # 法定节假日落在工作日时，DeepSeek 全天按空闲计费；只看星期几会把它算成高峰。
        from core import pricing_period
        for stamp in ("2026-01-01", "2026-01-02", "2026-02-16", "2026-02-17", "2026-02-23",
                      "2026-04-06", "2026-05-01", "2026-05-04", "2026-05-05",
                      "2026-06-19", "2026-09-25", "2026-10-01", "2026-10-02",
                      "2026-10-05", "2026-10-06", "2026-10-07"):
            moment = datetime.strptime(stamp + " 10:00", "%Y-%m-%d %H:%M")
            self.assertLess(moment.weekday(), 5, stamp + " 应当是工作日")
            self.assertFalse(pricing_period(moment)[0], stamp + " 是法定节假日，应为空闲")
        after = datetime(2026, 10, 8, 10, 0)          # 国庆假期结束后的周四
        self.assertEqual(after.weekday(), 3)
        self.assertTrue(pricing_period(after)[0])

    def test_makeup_workdays_on_weekends_stay_off_peak(self):
        # 调休上班的周末仍然是周末，DeepSeek 明确按空闲计费。
        from core import pricing_period
        for stamp in ("2026-01-04", "2026-02-14", "2026-02-28",
                      "2026-05-09", "2026-09-20", "2026-10-10"):
            moment = datetime.strptime(stamp + " 10:00", "%Y-%m-%d %H:%M")
            self.assertGreaterEqual(moment.weekday(), 5, stamp + " 应当是周末")
            self.assertFalse(pricing_period(moment)[0], stamp + " 是调休上班的周末，仍按空闲计费")

    def test_holiday_coverage_admits_which_years_are_known(self):
        from core import holiday_coverage
        self.assertTrue(holiday_coverage(datetime(2026, 10, 1))[0])
        covered, years = holiday_coverage(datetime(2029, 10, 1))
        self.assertFalse(covered)                     # 没收录就必须承认，不能假装知道
        self.assertIn("2026", years)

    def test_displayed_rule_keeps_the_official_wording(self):
        # 官方原文里的“（不含中国法定节假日）”必须保留：漏掉它就等于改了政策。
        from core import PRICING_RULE
        self.assertIn("周一至周五（不含中国法定节假日）", PRICING_RULE)
        self.assertIn("9:00-12:00", PRICING_RULE)
        self.assertIn("14:00-18:00", PRICING_RULE)
        self.assertIn("包括周末及中国法定节假日全天", PRICING_RULE)
        self.assertIn("一半", PRICING_RULE)

    def test_cost_message_shows_both_peak_and_off_peak_prices(self):
        from core import cost_message, estimate_cost
        cues = [asdict(Cue(1, 0.0, 1.0, "こんにちは世界"))]
        text = cost_message(estimate_cost(cues, "deepseek-v4-pro"), "deepseek-v4-pro")
        self.assertIn("高峰时段", text)
        self.assertIn("空闲时段", text)
        self.assertIn("现在开始", text)
        self.assertNotIn("不在内置价格表", text)
        unknown = cost_message(estimate_cost(cues, "some-unlisted-model"), "some-unlisted-model")
        self.assertIn("不在内置价格表", unknown)      # 认不出的模型必须说清楚按哪档估的

    def test_cost_message_admits_when_the_holiday_calendar_does_not_cover_the_year(self):
        from core import cost_message, estimate_cost
        cues = [asdict(Cue(1, 0.0, 1.0, "こんにちは世界"))]
        future = datetime(2029, 6, 4, 10, 0)
        text = cost_message(estimate_cost(cues, "deepseek-flash", now=future), "deepseek-flash")
        self.assertIn("只覆盖 2026 年", text)
        known = cost_message(estimate_cost(cues, "deepseek-flash", now=datetime(2026, 6, 4, 10, 0)),
                             "deepseek-flash")
        self.assertNotIn("只覆盖", known)

    def test_unlisted_model_falls_back_to_the_most_expensive_tier(self):
        from core import PRICE_CNY, estimate_cost, FALLBACK_PRICE_KEY
        cues = [asdict(Cue(1, 0.0, 1.0, "こんにちは世界"))]
        unknown = estimate_cost(cues, "some-unlisted-model")
        fallback = estimate_cost(cues, FALLBACK_PRICE_KEY)
        self.assertFalse(unknown["model_known"])
        self.assertTrue(fallback["model_known"])
        self.assertEqual(unknown["peak_cost"], fallback["peak_cost"])
        for table in PRICE_CNY.values():
            self.assertLessEqual(table["peak"][1], PRICE_CNY[FALLBACK_PRICE_KEY]["peak"][1])

    def test_legacy_flash_model_names_are_priced_as_flash(self):
        # 官方脚注(1)：旧模型名仍可调用，由 V4.1-Flash 提供服务并按 Flash 价格计费。
        from core import estimate_cost
        cues = [asdict(Cue(1, 0.0, 1.0, "こんにちは世界"))]
        current = estimate_cost(cues, "deepseek-flash")
        for legacy in ("deepseek-v4-flash", "deepseek-v4-flash-vision-exp"):
            alias = estimate_cost(cues, legacy)
            self.assertTrue(alias["model_known"], legacy)
            self.assertEqual(alias["peak_cost"], current["peak_cost"], legacy)
            self.assertEqual(alias["off_cost"], current["off_cost"], legacy)

    def test_cost_message_says_input_is_estimated_at_cache_miss_price(self):
        from core import cost_message, estimate_cost
        cues = [asdict(Cue(1, 0.0, 1.0, "こんにちは世界"))]
        text = cost_message(estimate_cost(cues, "deepseek-flash"), "deepseek-flash")
        self.assertIn("缓存未命中", text)
        self.assertIn("命中缓存会更便宜", text)

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

    def test_blank_srt_keeps_exact_timeline_and_carries_no_text(self):
        p=make_project(3)                       # zh 全空，空轴本来就在翻译之前用
        save_project(self.path,p)
        files=export_blank(self.path,p)
        self.assertEqual(len(files),1)          # 只产出 1 个文件
        self.assertTrue(str(files[0]).endswith("_空轴.srt"))
        blank=Path(files[0]).read_text(encoding="utf-8-sig")
        plain=format_srt([Cue(**r) for r in p["cues"]])
        times=lambda t:[line for line in t.splitlines() if "-->" in line]
        self.assertEqual(times(blank),times(plain))     # 时间轴逐条完全一致
        self.assertEqual(len(times(blank)),3)
        self.assertNotIn("Original line",blank)          # 正文不写进空轴
        self.assertIn("1\n",blank)
        self.assertIn("3\n",blank)

    def test_blank_ass_keeps_every_empty_cue_and_the_template_style(self):
        cues=[Cue(1,0,2,"A"),Cue(2,2,4,"B")]
        template=("[Script Info]\nTitle: 模板\n\n[V4+ Styles]\nFormat: Name, Fontname\n"
                  "Style: 主字幕,Arial\nStyle: 副字幕,Arial\n\n[Events]\n"
                  "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")
        text=format_blank_ass(cues,"主字幕",template)
        self.assertIn("Style: 主字幕,Arial",text)
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:02.00,主字幕,,0,0,0,,",text)
        self.assertIn("Dialogue: 0,0:00:02.00,0:00:04.00,主字幕,,0,0,0,,",text)
        self.assertEqual(text.count("Dialogue:"),2)      # 文本为空也不跳过条目

    def test_blank_export_needs_no_translation_but_chinese_export_still_does(self):
        p=make_project(2)                       # zh 全空
        save_project(self.path,p)
        files=export_blank(self.path,p,"ass")
        self.assertEqual(len(files),1)
        self.assertTrue(str(files[0]).endswith("_空轴.ass"))
        with self.assertRaises(UserError):
            export_files(self.path,p,include={"zh_srt"})

    def test_export_subset_writes_only_the_selected_files(self):
        p=make_project(2)
        for row in p["cues"]:
            row["zh"]="这是译文。"
        save_project(self.path,p)
        files=export_files(self.path,p,include={"zh_srt","blank"},blank_format="srt")
        names=sorted(Path(f).name for f in files)
        self.assertEqual(len(files),2)
        self.assertTrue(all(n.endswith("_zh.srt") or n.endswith("_空轴.srt") for n in names),names)

    def test_selecting_nothing_is_rejected_instead_of_exporting_everything(self):
        p=make_project(2)
        for row in p["cues"]:
            row["zh"]="这是译文。"
        save_project(self.path,p)
        with self.assertRaises(UserError) as ctx:
            export_files(self.path,p,include=set())
        self.assertIn("至少选择一项",str(ctx.exception))

    def test_default_export_is_byte_identical_to_full_explicit_selection(self):
        # 不传 include 的老调用必须一个字都不变：这是向后兼容的回归保护。
        p=make_project(2)
        for row in p["cues"]:
            row["zh"]="这是对应译文。"
        save_project(self.path,p)
        default=export_files(self.path,p)
        explicit=export_files(self.path,p,include=default_export_keys(True,False))
        self.assertEqual(len(default),7)
        self.assertEqual([Path(f).name for f in default],[Path(f).name for f in explicit])
        for lhs,rhs in zip(default,explicit):
            self.assertEqual(Path(lhs).read_bytes(),Path(rhs).read_bytes(),Path(lhs).name)

    def test_export_panel_choices_and_defaults_lock_the_agreed_decisions(self):
        # 使用者确认过的约定：默认全选 = 现有 7 项全勾；空轴不默认勾。
        normal=[key for key,_ in export_choices(False)]
        self.assertEqual(normal,["source_srt","source_txt","zh_srt","zh_txt",
                                 "bilingual_srt","bilingual_txt","zh_ass","blank"])
        self.assertEqual([key for key,_ in export_choices(True)],["zh_srt","zh_txt","zh_ass","blank"])
        full=default_export_selection(True,False,True)
        self.assertEqual(full,set(DEFAULT_EXPORT_KEYS))
        self.assertEqual(len(full),7)
        self.assertNotIn("blank",full)
        # 中文还没译完：中文相关项不默认勾，其余照常
        self.assertEqual(default_export_selection(True,False,False),{"source_srt","source_txt"})
        self.assertEqual(default_export_selection(False,False,True),{"source_srt","source_txt"})
        self.assertEqual(default_export_selection(True,True,True),{"zh_srt","zh_txt","zh_ass"})

    def test_finish_alert_fires_only_for_a_successful_translation(self):
        self.assertTrue(should_alert("translate",True,False,False))
        self.assertFalse(should_alert("transcribe",True,False,False))    # 仅识别很快，不打断
        self.assertFalse(should_alert("translate",False,False,False))    # 失败已有错误弹窗
        self.assertFalse(should_alert("translate",True,True,False))      # 队列模式已统一弹过
        self.assertFalse(should_alert("translate",True,False,True))      # 正在退出，不弹
        self.assertFalse(should_alert("translate",True,False,False,enabled=False))  # 使用者关了开关
        self.assertFalse(should_alert(None,True,False,False))            # 没有 mode 不猜


if __name__ == "__main__":
    unittest.main(verbosity=2)
