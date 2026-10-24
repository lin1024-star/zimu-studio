"""ASS 编辑保真的三个回归测试（2026-10-24 修的 Bug 3/4/5）。

Bug 3：正文里转义的 \\{ \\} 被当成标签剥掉，编辑后导出会丢字、错位。
Bug 4：正文的 \\N 手动换行在导出时被压成空格。
Bug 5：带 BOM 的 UTF-16BE ASS 读不出来（去掉 BOM 后仍按小端解码）。

这三个 bug 的判据都是"字节级"：没改的条目必须一个字节都不动，改了的条目必须只改文字。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import ass
import core

HEAD = [
    "[Script Info]",
    "Title: edge test",
    "ScriptType: v4.00+",
    "PlayResX: 1920",
    "PlayResY: 1080",
    "",
    "[V4+ Styles]",
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
    "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
    "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1",
    "",
    "[Events]",
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
]


def ass_text(dialogue_text):
    lines = list(HEAD) + ["Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,%s" % dialogue_text]
    return "\r\n".join(lines)


def dialogue_lines(raw):
    text = raw.decode("utf-16") if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else raw.decode("utf-8-sig")
    return [line for line in text.splitlines() if line.startswith("Dialogue:")]


class AssTextEdgeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def make_ass(self, name, dialogue_text, encoding="utf-8", bom=b""):
        path = self.dir / name
        path.write_bytes(bom + ass_text(dialogue_text).encode(encoding))
        return path

    def export(self, project, cues, name="out.ass"):
        out = self.dir / name
        changed = core.export_ass_from_source(project["ass"], cues, out)
        return changed, out

    def test_escaped_braces_survive_import_and_edit(self):
        path = self.make_ass("brace.ass", r"这是 \{测试\} 文本")
        for mode in ("source", "chinese"):
            with self.subTest(mode=mode):
                project = core.read_ass_project(path, mode)
                cues = [core.Cue(**c) for c in project["cues"]]
                self.assertEqual(cues[0].source, "这是 {测试} 文本")
                original = path.read_bytes()

                changed, out = self.export(project, cues)
                self.assertEqual(changed, 0)
                self.assertEqual(out.read_bytes(), original)     # 没改就不动一个字节

                cues[0].source += "！"
                changed, out = self.export(project, cues)
                self.assertEqual(changed, 1)
                dialogs = dialogue_lines(out.read_bytes())
                self.assertEqual(len(dialogs), 1)
                self.assertTrue(dialogs[0].endswith(r"这是 \{测试\} 文本！"), dialogs[0])

    def test_manual_line_break_survives_import_and_edit(self):
        path = self.make_ass("newline.ass", r"第一行\N第二行")
        for mode in ("source", "chinese"):
            with self.subTest(mode=mode):
                project = core.read_ass_project(path, mode)
                cues = [core.Cue(**c) for c in project["cues"]]
                self.assertEqual(cues[0].source, "第一行\n第二行")
                original = path.read_bytes()

                changed, out = self.export(project, cues)
                self.assertEqual(changed, 0)
                self.assertEqual(out.read_bytes(), original)

                cues[0].source += "！"
                changed, out = self.export(project, cues)
                self.assertEqual(changed, 1)
                dialogs = dialogue_lines(out.read_bytes())
                self.assertTrue(dialogs[0].endswith(r"第一行\N第二行！"), dialogs[0])

    def test_utf16be_ass_with_bom_is_readable(self):
        path = self.make_ass("be.ass", "你好世界", encoding="utf-16-be", bom=b"\xfe\xff")
        document = ass.Document.load(path)
        self.assertEqual(document.encoding, "utf-16-be")
        self.assertEqual([item["text"] for item in document.dialogues()], ["你好世界"])
        self.assertEqual(document.render(), path.read_bytes())

        project = core.read_ass_project(path, "source")
        self.assertEqual(project["ass"]["encoding"], "utf-16-be")
        cues = [core.Cue(**c) for c in project["cues"]]
        self.assertEqual(cues[0].source, "你好世界")
        changed, out = self.export(project, cues)
        self.assertEqual(changed, 0)
        self.assertEqual(out.read_bytes(), path.read_bytes())

        cues[0].source += "！"
        changed, out = self.export(project, cues)
        self.assertEqual(changed, 1)
        self.assertEqual(dialogue_lines(out.read_bytes()),
                         ["Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,你好世界！"])

    def test_utf16le_ass_roundtrips_without_double_bom(self):
        path = self.make_ass("le.ass", "你好世界", encoding="utf-16-le", bom=b"\xff\xfe")
        document = ass.Document.load(path)
        self.assertEqual(document.encoding, "utf-16-le")
        self.assertEqual(document.render(), path.read_bytes())
        self.assertFalse(path.read_bytes().startswith(b"\xff\xfe\xff\xfe"))

    def test_legacy_utf16_project_snapshot_rebuilds_without_extra_bom(self):
        # 旧版本项目里存的编码是统称 "utf-16"；按 BOM 定字节序后只能有一个 BOM（Bug 9）。
        text = ass_text("你好世界")
        document = ass.Document.from_parts(text, "utf-16", b"\xff\xfe".hex(), "crlf")
        self.assertEqual(document.encoding, "utf-16-le")
        rendered = document.render()
        self.assertTrue(rendered.startswith(b"\xff\xfe"))
        self.assertFalse(rendered.startswith(b"\xff\xfe\xff\xfe"))
        self.assertEqual(rendered, b"\xff\xfe" + text.encode("utf-16-le"))

    def test_cp932_source_translated_to_chinese_exports_as_utf8(self):
        # Bug 6：日文 CP932 ASS 翻成中文后，cp932 存不下汉字。导出不能抛异常，
        # 应自动改用 UTF-8（带 BOM），并且导出的文件还能正常读回来。
        path = self.make_ass("jp.ass", "こんにちは", encoding="cp932")
        project = core.read_ass_project(path, "source")
        cues = [core.Cue(**c) for c in project["cues"]]
        cues[0].zh = "你好世界"
        out = self.dir / "zh.ass"
        core.export_ass_from_source(project["ass"], cues, out)
        data = out.read_bytes()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertIn("你好世界", data.decode("utf-8-sig"))
        self.assertNotIn("こんにちは", data.decode("utf-8-sig"))
        self.assertEqual([item["text"] for item in ass.Document.load(out).dialogues()], ["你好世界"])

    def test_export_files_completes_instead_of_leaving_half_output(self):
        # 走完整默认导出：cp932 日文 ASS + 中文译文 → 7 份文件都要生成，不能只剩半套。
        path = self.make_ass("jpfull.ass", "こんにちは", encoding="cp932")
        project = core.read_ass_project(path, "source")
        for cue in project["cues"]:
            cue["zh"] = "你好世界"
        project["name"] = "jpfull"
        work = self.dir / "work"
        work.mkdir()
        project_path = work / "project.json"
        core.save_project(project_path, project)
        files = core.export_files(project_path, project, include_zh=True)
        self.assertTrue(all(item.is_file() for item in files), files)
        ass_files = [item for item in files if item.suffix == ".ass"]
        self.assertEqual(len(ass_files), 1)
        self.assertTrue(ass_files[0].read_bytes().startswith(b"\xef\xbb\xbf"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
