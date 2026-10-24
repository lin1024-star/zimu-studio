"""字幕文件编码自动判断 + 原文语言自动判断的回归测试（2026-10-24 修的两个 bug）。

Bug 1：GB18030/GBK 的简体中文字幕被 cp932 "解开"成乱码（cp932 几乎不会抛错）。
Bug 2：中文原文被自动判断成英语（原来只有"有假名=日文，否则英文"两档）。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import ass
import core

CH_TEXTS = ["你好，世界！这是简体中文。", "字幕工坊使用说明：先选择视频，再开始识别。"]
JP_TEXTS = ["こんにちは、世界！これは日本語のテストです。", "字幕工房の使い方：まず動画を選んでください。"]
EN_TEXTS = ["Hello, world!", "Choose a video before recognising."]
ASS_TEXTS = ["第一句字幕", "第二句字幕"]

ASS_HEAD = [
    "[Script Info]",
    "Title: encoding test",
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


def srt_bytes(texts, encoding):
    blocks = []
    for number, text in enumerate(texts, 1):
        start = (number - 1) * 2
        blocks.append("%d\n00:00:%02d,000 --> 00:00:%02d,000\n%s" % (number, start, start + 1, text))
    return ("\n\n".join(blocks) + "\n").encode(encoding)


def ass_bytes(texts, encoding):
    lines = list(ASS_HEAD)
    lines += ["Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,%s" % text for text in texts]
    return "\r\n".join(lines).encode(encoding)


class SrtEncodingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def write(self, name, data):
        path = self.dir / name
        path.write_bytes(data)
        return path

    def test_gb18030_chinese_srt_is_not_mistaken_for_cp932(self):
        path = self.write("gb18030.srt", srt_bytes(CH_TEXTS, "gb18030"))
        self.assertEqual([c.source for c in core.read_srt(path)], CH_TEXTS)

    def test_gbk_chinese_srt_also_decodes(self):
        path = self.write("gbk.srt", srt_bytes(CH_TEXTS, "gbk"))
        self.assertEqual([c.source for c in core.read_srt(path)], CH_TEXTS)

    def test_cp932_japanese_srt_still_decodes(self):
        path = self.write("cp932.srt", srt_bytes(JP_TEXTS, "cp932"))
        self.assertEqual([c.source for c in core.read_srt(path)], JP_TEXTS)

    def test_utf8_and_utf16_are_unchanged(self):
        for encoding in ("utf-8", "utf-8-sig", "utf-16"):
            with self.subTest(encoding=encoding):
                path = self.write("utf.srt", srt_bytes(CH_TEXTS, encoding))
                self.assertEqual([c.source for c in core.read_srt(path)], CH_TEXTS)

    def test_english_srt_unchanged(self):
        path = self.write("en.srt", srt_bytes(EN_TEXTS, "utf-8"))
        self.assertEqual([c.source for c in core.read_srt(path)], EN_TEXTS)


class LanguageDetectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def import_with_auto_language(self, texts, encoding="utf-8", name="素材.srt"):
        path = self.dir / name
        path.write_bytes(srt_bytes(texts, encoding))
        return core.import_srt_project(path, mode="source", language="")

    def test_chinese_source_imports_as_chinese(self):
        project = self.import_with_auto_language(CH_TEXTS)
        self.assertEqual(project["language"], "zh")
        self.assertEqual([c["source"] for c in project["cues"]], CH_TEXTS)

    def test_chinese_gb18030_source_imports_as_chinese(self):
        project = self.import_with_auto_language(CH_TEXTS, "gb18030")
        self.assertEqual(project["language"], "zh")
        self.assertEqual([c["source"] for c in project["cues"]], CH_TEXTS)

    def test_japanese_still_detected(self):
        self.assertEqual(self.import_with_auto_language(JP_TEXTS)["language"], "ja")

    def test_english_still_detected(self):
        self.assertEqual(self.import_with_auto_language(EN_TEXTS)["language"], "en")

    def test_filename_suffix_still_wins(self):
        # 软件自己导出的文件名里已经带语言，重新导入时以文件名为准（原有约定不变）。
        project = self.import_with_auto_language(CH_TEXTS, name="素材_原文_en.srt")
        self.assertEqual(project["language"], "en")


class AssEncodingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def test_gb18030_chinese_ass_decodes_and_roundtrips(self):
        raw = ass_bytes(ASS_TEXTS, "gb18030")
        path = self.dir / "gb18030.ass"
        path.write_bytes(raw)
        document = ass.Document.load(path)
        self.assertEqual([item["text"] for item in document.dialogues()], ASS_TEXTS)
        self.assertEqual(document.encoding, "gb18030")
        self.assertEqual(document.render(), raw)

    def test_cp932_japanese_ass_still_detected(self):
        jp_texts = ["こんにちは、世界！", "これは日本語です。"]
        raw = ass_bytes(jp_texts, "cp932")
        path = self.dir / "cp932.ass"
        path.write_bytes(raw)
        document = ass.Document.load(path)
        self.assertEqual(document.encoding, "cp932")
        self.assertEqual([item["text"] for item in document.dialogues()], jp_texts)
        self.assertEqual(document.render(), raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
