"""ASS 保留格式编辑的测试。

核心承诺只有一条：**没改的行一个字节都不动**。所以这里全部用"逐字节比对"来验，
而不是"看起来差不多"。样本是合成的，覆盖 BOM、CRLF、Shift-JIS、注释行、绘图行、
内联标签、硬换行这些边界。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import ass
import core

STYLE_FORMAT = ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
                "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
                "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
EVENT_FORMAT = "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"

# 各行正文：索引 0/1/2 是可编辑字幕，3 是注释行，4 是绘图行，5 是空行
CN_TEXTS = ["第一句", "{\\i1}带标签的第二句{\\i0}", "第一行\\N第二行",
            "这行是注释，不该被编辑", "{\\p1}m 0 0 l 100 0 100 100{\\p0}", ""]
# Shift-JIS/cp932 存不下中文，另备一份全英文与日文的样本
JP_TEXTS = ["最初の一言", "{\\i1}タグ付きの二言目{\\i0}", "上の行\\N下の行",
            "これはコメント行", "{\\p1}m 0 0 l 100 0 100 100{\\p0}", ""]


def sample_lines(texts=CN_TEXTS):
    return [
        "[Script Info]",
        "Title: test",
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "",
        "[Aegisub Project Garbage]",
        "Last Style Storage: Default",
        "",
        "[V4+ Styles]",
        STYLE_FORMAT,
        "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1",
        "Style: haru,Arial,90,&H002E0C9B,&H000000FF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,5,0,2,10,10,30,1",
        "",
        "[Events]",
        EVENT_FORMAT,
        "Dialogue: 0,0:00:01.00,0:00:02.50,haru,,0,0,0,,%s" % texts[0],
        "Dialogue: 0,0:00:03.00,0:00:04.50,haru,,0,0,0,,%s" % texts[1],
        "Dialogue: 0,0:00:05.00,0:00:06.50,Default,,0,0,0,,%s" % texts[2],
        "Comment: 0,0:00:07.00,0:00:08.00,Default,,0,0,0,,%s" % texts[3],
        "Dialogue: 0,0:00:09.00,0:00:10.00,Default,,0,0,0,,%s" % texts[4],
        "Dialogue: 0,0:00:11.00,0:00:12.00,Default,,0,0,0,,%s" % texts[5],
    ]


def sample_bytes(encoding="utf-8", bom=True, eol="\r\n", texts=CN_TEXTS):
    text = eol.join(sample_lines(texts))
    return (b"\xef\xbb\xbf" if (bom and encoding == "utf-8") else b"") + text.encode(encoding)


class AssDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.path = self.dir / "sample.ass"
        self.path.write_bytes(sample_bytes())

    # ---------- 保真 ----------
    def test_unchanged_rebuild_is_byte_identical(self):
        raw = self.path.read_bytes()
        self.assertEqual(ass.Document.load(self.path).render(), raw)

    def test_bom_encoding_and_line_endings_survive(self):
        for encoding, bom, eol, texts in (("utf-8", True, "\r\n", CN_TEXTS),
                                          ("utf-8", False, "\n", CN_TEXTS),
                                          ("cp932", False, "\r\n", JP_TEXTS)):
            with self.subTest(encoding=encoding, bom=bom, eol=eol):
                p = self.dir / ("s_%s_%s_%s.ass" % (encoding, bom, eol == "\r\n"))
                raw = sample_bytes(encoding, bom, eol, texts)
                p.write_bytes(raw)
                doc = ass.Document.load(p)
                self.assertEqual(doc.render(), raw)
                self.assertEqual(doc.encoding, encoding)
                self.assertEqual(doc.eol, eol)
                self.assertEqual(bool(doc.bom), bom)

    def test_lf_file_stays_lf(self):
        p = self.dir / "lf.ass"
        p.write_bytes(sample_bytes(eol="\n"))
        out = ass.Document.load(p).render()
        self.assertNotIn(b"\r\n", out)
        self.assertEqual(out, sample_bytes(eol="\n"))

    # ---------- 该编辑的与不该编辑的 ----------
    def test_only_real_subtitles_become_editable(self):
        items = ass.Document.load(self.path).dialogues()
        self.assertEqual([i["text"] for i in items],
                         ["第一句", "带标签的第二句", "第一行\n第二行"])

    def test_comment_drawing_and_empty_lines_are_kept_but_not_editable(self):
        doc = ass.Document.load(self.path)
        lines = doc.render().decode("utf-8-sig").split("\r\n")
        # 三种行都还在，且内容原样
        self.assertTrue(any(x.startswith("Comment:") and "注释" in x for x in lines))
        self.assertTrue(any("\\p1" in x for x in lines))
        self.assertEqual(len(doc.dialogues()), 3)
        self.assertEqual(len([x for x in lines if x.startswith("Dialogue:")]), 5)

    # ---------- 定点替换 ----------
    def test_editing_one_line_touches_exactly_one_line(self):
        doc = ass.Document.load(self.path)
        item = doc.dialogues()[0]
        out = doc.render(texts={item["line"]: "改过的文字"}).decode("utf-8-sig").split("\r\n")
        before = self.path.read_bytes().decode("utf-8-sig").split("\r\n")
        changed = [i for i, (a, b) in enumerate(zip(before, out)) if a != b]
        self.assertEqual(changed, [item["line"]])
        self.assertEqual(out[item["line"]],
                         "Dialogue: 0,0:00:01.00,0:00:02.50,haru,,0,0,0,,改过的文字")

    def test_inline_tags_are_restored_around_new_text(self):
        doc = ass.Document.load(self.path)
        item = doc.dialogues()[1]
        out = doc.render(texts={item["line"]: "换掉的译文"}).decode("utf-8-sig")
        self.assertIn("{\\i1}换掉的译文{\\i0}", out)

    def test_hard_line_break_round_trips(self):
        doc = ass.Document.load(self.path)
        item = doc.dialogues()[2]
        out = doc.render(texts={item["line"]: "上\n下"}).decode("utf-8-sig")
        self.assertIn("上\\N下", out)

    def test_font_size_change_touches_only_that_style_line(self):
        doc = ass.Document.load(self.path)
        out = doc.render(font_sizes={"haru": 120}).decode("utf-8-sig").split("\r\n")
        before = self.path.read_bytes().decode("utf-8-sig").split("\r\n")
        changed = [i for i, (a, b) in enumerate(zip(before, out)) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertIn(",120,", out[changed[0]])
        self.assertTrue(out[changed[0]].startswith("Style: haru,"))

    def test_unknown_style_is_ignored(self):
        doc = ass.Document.load(self.path)
        self.assertEqual(doc.render(font_sizes={"不存在": 120}), self.path.read_bytes())

    # ---------- 坏的输入 ----------
    def test_file_without_events_is_rejected(self):
        p = self.dir / "not.ass"
        p.write_text("[Script Info]\nTitle: x\n", encoding="utf-8")
        with self.assertRaises(ass.AssError):
            ass.Document.load(p)


class AssProjectTests(unittest.TestCase):
    """走 core 的完整回路：导入 → 改 → 导出。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.path = self.dir / "sample.ass"
        self.raw = sample_bytes()
        self.path.write_bytes(self.raw)

    def test_import_puts_text_in_the_chosen_column(self):
        for mode, expect_zh in (("source", False), ("chinese", True)):
            with self.subTest(mode=mode):
                project = core.read_ass_project(self.path, mode)
                self.assertEqual(len(project["cues"]), 3)
                self.assertEqual([c["source"] for c in project["cues"]],
                                 ["第一句", "带标签的第二句", "第一行\n第二行"])
                self.assertEqual(bool(core.clean(project["cues"][0]["zh"])), expect_zh)
                self.assertEqual(project["subtitle_mode"], "chinese" if expect_zh else "source")
                # 这份样本是中文、没有假名，所以按现有启发式判成 en；选中文时直接定为 zh
                self.assertEqual(project["language"], "zh" if expect_zh else "en")

    def test_japanese_source_is_detected_as_ja(self):
        p = self.dir / "jp.ass"
        p.write_bytes(sample_bytes(texts=JP_TEXTS))
        project = core.read_ass_project(p, "source")
        self.assertEqual(project["language"], "ja")
        self.assertEqual(project["name"], "jp")

    def test_import_rejects_bad_mode(self):
        with self.assertRaises(core.UserError):
            core.read_ass_project(self.path, "bilingual")

    def _load(self, mode="chinese"):
        project = core.read_ass_project(self.path, mode)
        return [core.Cue(**c) for c in project["cues"]], project["ass"]

    def test_export_without_changes_is_byte_identical(self):
        cues, meta = self._load()
        out = self.dir / "out.ass"
        self.assertEqual(core.export_ass_from_source(meta, cues, out), 0)
        self.assertEqual(out.read_bytes(), self.raw)

    def test_export_only_rewrites_changed_cues(self):
        cues, meta = self._load()
        cues[1].source = "改了第二条"      # 中文模式下界面改的是原文栏
        out = self.dir / "out.ass"
        self.assertEqual(core.export_ass_from_source(meta, cues, out), 1)
        before = self.raw.decode("utf-8-sig").split("\r\n")
        after = out.read_bytes().decode("utf-8-sig").split("\r\n")
        changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertIn("改了第二条", after[changed[0]])

    def test_export_applies_font_size(self):
        cues, meta = self._load()
        out = self.dir / "out.ass"
        core.export_ass_from_source(meta, cues, out, font_sizes={"haru": 130})
        text = out.read_bytes().decode("utf-8-sig")
        self.assertIn("Style: haru,Arial,130,", text)

    def test_source_mode_exports_edited_source(self):
        cues, meta = self._load("source")
        cues[0].source = "原文改过了"
        out = self.dir / "out.ass"
        self.assertEqual(core.export_ass_from_source(meta, cues, out), 1)
        self.assertIn("原文改过了", out.read_bytes().decode("utf-8-sig"))

    def test_chinese_mode_takes_the_edited_source_column(self):
        """中文模式下软件把原文栏当中文字幕编辑区，导出必须以它为准，不能用 zh 里那份旧副本。"""
        project = core.read_ass_project(self.path, "chinese")
        self.assertEqual(project["cues"][0]["source"], project["cues"][0]["zh"])
        project["cues"][0]["source"] = "改过的新文字"      # 界面改的是这一栏
        project_path = self.dir / "p4.json"
        core.save_project(project_path, project)
        files = core.export_files(project_path, project, True, None, None, include={"zh_ass"})
        target = [f for f in files if f.suffix == ".ass"][0]
        text = target.read_bytes().decode("utf-8-sig")
        self.assertIn("改过的新文字", text)
        self.assertNotIn("第一句", text)

    def test_source_mode_prefers_the_translation(self):
        """原文模式（翻译流程）下，中文栏有内容时导出中文。"""
        cues, meta = self._load("source")
        cues[0].zh = "译好的中文"
        out = self.dir / "out2.ass"
        core.export_ass_from_source(meta, cues, out)
        self.assertIn("译好的中文", out.read_bytes().decode("utf-8-sig"))

    def test_source_metadata_survives_save_and_load(self):
        """project.json 是白名单写入；ass 记录必须留在名单里，否则重开项目就不认识它了。"""
        project = core.read_ass_project(self.path, "chinese")
        project["ass"]["font_sizes"] = {"haru": 132}
        project["ass_style_mode"] = "keep"
        project_path = self.dir / "p5.json"
        core.save_project(project_path, project)
        back = core.load_project(project_path)
        self.assertIn("ass", back)
        self.assertEqual(back["ass"]["font_sizes"], {"haru": 132})
        self.assertEqual(back["ass_style_mode"], "keep")
        self.assertEqual(back["ass"]["lines"], project["ass"]["lines"])

    def test_export_files_rebuilds_from_source_by_default(self):
        """导入过 .ass 的项目：导出走原格式重建——样式不变，只改文字。"""
        project = core.read_ass_project(self.path, "chinese")
        project["cues"][0]["source"] = "只改这一条"
        project_path = self.dir / "p.json"
        core.save_project(project_path, project)
        files = core.export_files(project_path, project, True, None, None, include={"zh_ass"})
        target = [f for f in files if f.suffix == ".ass"][0]
        text = target.read_bytes().decode("utf-8-sig")
        self.assertIn("只改这一条", text)
        self.assertIn("Style: haru,Arial,90,", text)        # 样式原样
        self.assertIn("[Aegisub Project Garbage]", text)    # 原文件结构也原样

    def test_export_files_only_uses_template_when_explicitly_chosen(self):
        """只有明确选了「换成模板样式」才套模板，此时原文件结构不再保留。"""
        project = core.read_ass_project(self.path, "chinese")
        project["ass_style_mode"] = "template"
        project_path = self.dir / "p2.json"
        core.save_project(project_path, project)
        files = core.export_files(project_path, project, True, None, None, include={"zh_ass"})
        target = [f for f in files if f.suffix == ".ass"][0]
        self.assertNotIn("[Aegisub Project Garbage]", target.read_bytes().decode("utf-8-sig"))

    def test_font_sizes_saved_in_project_reach_the_export(self):
        project = core.read_ass_project(self.path, "chinese")
        project["ass"]["font_sizes"] = {"haru": 144}
        project_path = self.dir / "p3.json"
        core.save_project(project_path, project)
        files = core.export_files(project_path, project, True, None, None, include={"zh_ass"})
        target = [f for f in files if f.suffix == ".ass"][0]
        self.assertIn("Style: haru,Arial,144,", target.read_bytes().decode("utf-8-sig"))


class ColourTests(unittest.TestCase):
    """ASS 的颜色是 BGR 顺序，按 RGB 读会得到完全不同的颜色，所以单独测。"""

    def test_ass_colour_is_bgr_not_rgb(self):
        # &H002E0C9B：AA=00 BB=2E GG=0C RR=9B -> RGB(155,12,46) 深红
        self.assertEqual(ass.colour_to_rgb("&H002E0C9B"), "#9B0C2E")
        self.assertEqual(ass.colour_to_rgb("&H00FFFFFF"), "#FFFFFF")
        self.assertEqual(ass.colour_to_rgb("&H000000FF"), "#FF0000")   # 蓝底 → 红？

    def test_rgb_back_to_ass_keeps_alpha(self):
        self.assertEqual(ass.rgb_to_colour("#9B0C2E", "&H002E0C9B"), "&H002E0C9B")
        self.assertEqual(ass.rgb_to_colour("#9B0C2E", "&H802E0C9B"), "&H802E0C9B")

    def test_round_trip(self):
        for value in ("&H00FFFFFF", "&H002E0C9B", "&HFF123456"):
            with self.subTest(value=value):
                self.assertEqual(ass.rgb_to_colour(ass.colour_to_rgb(value), value), value)

    def test_garbage_is_rejected(self):
        self.assertIsNone(ass.colour_to_rgb("不是颜色"))
        self.assertIsNone(ass.rgb_to_colour("红色", "&H00FFFFFF"))


class StyleEditTests(unittest.TestCase):
    """改样式字段：两种来源都要能改，且只动那一个字段。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.path = self.dir / "s.ass"
        self.raw = sample_bytes()
        self.path.write_bytes(self.raw)

    def test_editing_colour_only_touches_that_style_line(self):
        doc = ass.Document.load(self.path)
        out = doc.render(style_edits={"haru": {"PrimaryColour": "&H0000FF00"}}).decode("utf-8-sig").split("\r\n")
        before = self.raw.decode("utf-8-sig").split("\r\n")
        changed = [i for i, (a, b) in enumerate(zip(before, out)) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertIn("&H0000FF00", out[changed[0]])
        self.assertTrue(out[changed[0]].startswith("Style: haru,"))

    def test_multiple_fields_at_once(self):
        doc = ass.Document.load(self.path)
        out = doc.render(style_edits={"haru": {"Fontsize": "140", "Fontname": "黑体", "Outline": "8"}}).decode("utf-8-sig")
        self.assertIn("Style: haru,黑体,140,", out)
        self.assertIn(",8,0,2,10,10,30,1", out)      # Outline 改了，Shadow 等没动

    def test_unknown_field_ignored(self):
        doc = ass.Document.load(self.path)
        self.assertEqual(doc.render(style_edits={"haru": {"没有这个字段": "1"}}), self.raw)

    def test_project_style_edits_reach_the_export(self):
        project = core.read_ass_project(self.path, "chinese")
        project["ass"]["style_edits"] = {"haru": {"Fontsize": "150", "PrimaryColour": "&H00112233"}}
        project["ass"]["font_sizes"] = {"haru": 150}
        project_path = self.dir / "p6.json"
        core.save_project(project_path, project)
        files = core.export_files(project_path, project, True, None, None, include={"zh_ass"})
        target = [f for f in files if f.suffix == ".ass"][0]
        text = target.read_bytes().decode("utf-8-sig")
        self.assertIn("Style: haru,Arial,150,", text)
        self.assertIn("&H00112233", text)

    def test_template_export_also_applies_edits(self):
        """换成模板照样能改字号和颜色——这是之前拧巴的地方。"""
        project = core.read_ass_project(self.path, "chinese")
        project["ass_style_mode"] = "template"
        project["ass"]["font_sizes"] = {"Default": 99}
        project["ass"]["style_edits"] = {"Default": {"Fontsize": "99"}}
        project_path = self.dir / "p7.json"
        core.save_project(project_path, project)
        files = core.export_files(project_path, project, True, None, None, include={"zh_ass"})
        target = [f for f in files if f.suffix == ".ass"][0]
        text = target.read_bytes().decode("utf-8-sig")
        self.assertIn(",99,", text)                       # 内置默认样式被改到了
        self.assertIn("Style: Default,", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
