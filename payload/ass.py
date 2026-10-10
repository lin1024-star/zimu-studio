"""ASS 字幕的读取与重建：保留格式，只改文字。

做法是「原样保存 + 定点替换」：把整份 .ass 原文留住，导出时只替换 Dialogue 行的正文
（以及样式行里被改过的字段），其余每一行都不动。

因此下列东西全部字节级保真，不靠"重新生成"去猜：
· BOM 有无、编码（含日文常见的 Shift-JIS/cp932）、换行符（CRLF/LF）、结尾有无空行
· [Script Info]、[Aegisub Project Garbage]、[V4+ Styles]、注释行、Comment 行
· 每条字幕的图层、样式名、角色名、边距、特效字段
· 没有文字的绘图行（{\\p1}）会整条原样保留，且**不会**被当作字幕送去翻译

本文档对象不依赖 core，只吃文本、吐字节，便于单独测试。
"""
from __future__ import annotations

import re
from pathlib import Path

EVENT_FIELDS = ["Layer", "Start", "End", "Style", "Name", "MarginL", "MarginR", "MarginV", "Effect"]
TAG_RE = re.compile(r"\{[^}]*\}")


class AssError(ValueError):
    """文件不是可用的 ASS 时抛出，消息直接给使用者看。"""


def _detect(raw):
    """返回 (bom, 编码名)。BOM 原样保留，编码按日文优先的顺序试。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return b"\xef\xbb\xbf", "utf-8"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw[:2], "utf-16"
    for name in ("utf-8", "cp932", "gb18030"):
        try:
            raw.decode(name)
            return b"", name
        except UnicodeDecodeError:
            continue
    raise AssError("无法识别这个 ASS 文件的编码，请先用 Aegisub 另存为 UTF-8。")


def parse_time(value):
    """ASS 时间 0:00:02.73 —— 时:分:秒.厘秒。"""
    parts = value.strip().split(":")
    if len(parts) != 3:
        raise AssError("ASS 里的时间格式不对：" + value.strip())
    try:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        raise AssError("ASS 里的时间格式不对：" + value.strip()) from None


def plain_text(ass_text):
    """去掉内联标签，得到可读、可编辑、可翻译的纯文字。"""
    text = TAG_RE.sub("", ass_text)
    text = text.replace("\\N", "\n").replace("\\n", " ")
    return text.replace("\\{", "{").replace("\\}", "}").strip()


def tag_prefix_suffix(ass_text):
    """取出正文开头的标签块和结尾的标签块；中间夹在文字里的标签单独返回。

    中文长度和原文不同，中间标签的位置本来就对不上，所以先分开记，导出时分开处理。
    """
    head = []
    pos = 0
    while True:
        m = re.match(r"\s*(\{[^}]*\})", ass_text[pos:])
        if not m:
            break
        head.append(m.group(1))
        pos += m.end()
    rest = ass_text[pos:]
    tail = []
    while True:
        m = re.search(r"(\{[^}]*\})\s*$", rest)
        if not m:
            break
        tail.insert(0, m.group(1))
        rest = rest[:m.start()]
    middle = TAG_RE.findall(rest)
    return "".join(head), "".join(tail), middle, plain_text(ass_text)


def to_ass_text(text, head="", tail="", middle=()):
    """把编辑后的纯文字写回 ASS 正文：转义花括号、换行转 \\N，再套回标签。"""
    body = text.replace("{", "\\{").replace("}", "\\}")
    body = body.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\N")
    if middle:
        # 中间标签按原顺序贴回正文开头之后，位置不保证与原图完全一致
        body = "".join(middle) + body
    return head + body + tail


def format_size(value):
    """字号写成 ASS 认的数字：整数不带小数点，小数保留。"""
    number = float(value)
    return str(int(number)) if number == int(number) else ("%g" % number)


def colour_to_rgb(value):
    """&HAABBGGRR → '#RRGGBB'。

    注意 ASS 存的是 BGR 顺序，不是 RGB：&H002E0C9B 是 RGB(155,12,46) 深红。
    按 RGB 读会得到完全不同的颜色，所以这里单独写一个函数并测试。
    """
    digits = value.strip().upper().replace("&H", "").replace("&", "")
    if not digits or any(c not in "0123456789ABCDEF" for c in digits):
        return None
    digits = digits.rjust(8, "0")[-8:]
    return "#" + digits[6:8] + digits[4:6] + digits[2:4]


def rgb_to_colour(rgb, original=None):
    """'#RRGGBB' → &HAABBGGRR，并沿用原值的 alpha（透明度）通道。"""
    text = rgb.strip().lstrip("#")
    if len(text) != 6:
        return None
    alpha = "00"
    if original:
        digits = original.strip().upper().replace("&H", "").replace("&", "").rjust(8, "0")[-8:]
        alpha = digits[0:2]
    return "&H%s%s%s%s" % (alpha, text[4:6].upper(), text[2:4].upper(), text[0:2].upper())


class Document:
    """一份 .ass 的全部原始内容，外加按需重建的能力。"""

    def __init__(self, lines, encoding, bom, eol):
        self.lines = lines
        self.encoding = encoding
        self.bom = bom
        self.eol = eol
        self._events = None
        self._styles = None

    # ---------- 载入 ----------
    @classmethod
    def load(cls, path, require_events=True):
        raw = Path(path).read_bytes()
        bom, encoding = _detect(raw)
        body = raw[len(bom):].decode(encoding)
        crlf = body.count("\r\n")
        lf = body.count("\n") - crlf
        eol = "\r\n" if crlf else "\n"
        text = body.replace("\r\n", "\n").replace("\r", "\n")
        if require_events and not re.search(r"^\s*\[Events\]", text, re.M):
            raise AssError("这个文件里没有 [Events] 段，看起来不是 ASS 字幕。")
        return cls(text.split("\n"), encoding, bom, eol)

    @classmethod
    def from_parts(cls, text, encoding, bom_hex, eol_name):
        """从项目里存的记录复原。"""
        body = text.replace("\r\n", "\n").replace("\r", "\n")
        return cls(body.split("\n"), encoding,
                   bytes.fromhex(bom_hex) if bom_hex else b"",
                   "\r\n" if eol_name == "crlf" else "\n")

    def snapshot(self):
        """存进项目的一份可复原记录（含编码/BOM/换行，导出时照原样写回）。"""
        return {
            "text": "\n".join(self.lines),
            "encoding": self.encoding,
            "bom": self.bom.hex(),
            "eol": "crlf" if self.eol == "\r\n" else "lf",
        }

    # ---------- 结构与样式 ----------
    @property
    def events(self):
        """按行号顺序列出所有 Dialogue / Comment 行。"""
        if self._events is None:
            found = []
            section = None
            for i, line in enumerate(self.lines):
                s = line.strip()
                if s.startswith("[") and s.endswith("]"):
                    section = s.lower()
                    continue
                if section != "[events]":
                    continue
                kind = "Dialogue" if s.startswith("Dialogue:") else ("Comment" if s.startswith("Comment:") else None)
                if not kind:
                    continue
                rest = s.split(":", 1)[1]
                # 9 个字段 + 正文，所以最多切 10 段；正文里可以含逗号，只切前 9 个
                parts = rest.split(",", len(EVENT_FIELDS))
                if len(parts) < len(EVENT_FIELDS) + 1:
                    continue          # 字段不全的行不当作字幕，但会原样写回
                fields = parts[:len(EVENT_FIELDS)]
                text = parts[len(EVENT_FIELDS)]
                found.append({"line": i, "kind": kind, "fields": fields, "text": text})
            self._events = found
        return self._events

    @property
    def styles(self):
        """样式名 -> {行号, 字段列表, 字段名顺序}。解析不了的样式直接跳过（原样保留）。"""
        if self._styles is None:
            names, out, section = None, {}, None
            for i, line in enumerate(self.lines):
                s = line.strip()
                if s.startswith("[") and s.endswith("]"):
                    section = s.lower()
                    continue
                if section not in ("[v4+ styles]", "[v4 styles]"):
                    continue
                if s.startswith("Format:"):
                    names = [x.strip() for x in s.split(":", 1)[1].split(",")]
                elif s.startswith("Style:") and names:
                    fields = s.split(":", 1)[1].split(",")
                    if len(fields) != len(names):
                        continue
                    out[fields[0].strip()] = {"line": i, "fields": fields, "format": names}
            self._styles = out
        return self._styles

    def script_info(self):
        """[Script Info] 段里的键值。字号是相对 PlayRes 算的，界面上要显示出来。"""
        out, section = {}, None
        for line in self.lines:
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                section = s.lower()
                continue
            if section == "[script info]" and ":" in s and not s.startswith(";"):
                key, value = s.split(":", 1)
                out[key.strip()] = value.strip()
        return out

    def dialogues(self):
        """真正能编辑的字幕：Dialogue 行、有可读文字（绘图行与空行自动排除）。"""
        out = []
        for ev in self.events:
            if ev["kind"] != "Dialogue":
                continue
            if "\\p1" in ev["text"] or "\\p2" in ev["text"]:
                continue
            text = plain_text(ev["text"])
            if not text:
                continue
            out.append({
                "line": ev["line"],
                "start": parse_time(ev["fields"][1]),
                "end": parse_time(ev["fields"][2]),
                "style": ev["fields"][3].strip(),
                "text": text,
                "raw_text": ev["text"],
            })
        return out

    # ---------- 重建 ----------
    def render(self, texts=None, font_sizes=None, style_edits=None):
        """重建整份文件，返回字节。

        texts       {行号: 新正文}（纯文字，标签会按原文的头尾标签补回）
        font_sizes  {样式名: 新字号}（等价于 style_edits 里的 Fontsize）
        style_edits {样式名: {字段名: 新值}}，可改字号、字体、颜色、描边等任意样式字段

        只有真正变了的样式行才会被重写，其余行一个字节都不碰。
        """
        out = list(self.lines)
        by_line = {ev["line"]: ev for ev in self.events}

        edits = {}
        for name, size in (font_sizes or {}).items():
            edits.setdefault(name, {})["Fontsize"] = format_size(size)
        for name, changes in (style_edits or {}).items():
            edits.setdefault(name, {}).update({k: v for k, v in (changes or {}).items() if v is not None})

        for line_no, new_text in (texts or {}).items():
            ev = by_line.get(line_no)
            if ev is None:
                continue
            head, tail, middle, _ = tag_prefix_suffix(ev["text"])
            out[line_no] = "%s:%s,%s" % (ev["kind"], ",".join(ev["fields"]), to_ass_text(new_text, head, tail, middle))

        for name, changes in edits.items():
            info = self.styles.get(name)
            if not info:
                continue
            fields = list(info["fields"])
            touched = False
            for key, value in changes.items():
                if key not in info["format"]:
                    continue
                text = value if isinstance(value, str) else format_size(value)
                pos = info["format"].index(key)
                if fields[pos] != text:
                    fields[pos] = text
                    touched = True
            if touched:
                out[info["line"]] = "Style:" + ",".join(fields)

        body = self.eol.join(out)
        return self.bom + body.encode(self.encoding)
