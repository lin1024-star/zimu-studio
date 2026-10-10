"""实时字幕用到的术语表：读写「临时补充」文件 + 从日文里推荐候选词。

设计考虑：看直播的人不懂日语，看到中文翻得怪，也**说不出对应的日文是哪个词**。
所以对话框要从这一句日文里自动挑出「像是专有名词的候选」（片假名、汉字名词），
让人点一下就能填，而不是让人自己去找。
"""
import re
from pathlib import Path

DEFAULT_EXTRA = Path(__file__).resolve().parents[1] / "live_glossary_extra.txt"

KATAKANA = re.compile(r"[ァ-ヴー]{2,}")
KANJI = re.compile(r"[一-龥]{2,}")
LATIN = re.compile(r"[A-Za-z]{2,}")


def load_extra(path=DEFAULT_EXTRA):
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        return p.read_text("utf-8")
    except OSError:
        return ""


def save_term(term, value, path=DEFAULT_EXTRA):
    """追加一条「原文 = 译名」。返回 (是否成功, 说明)。"""
    term = (term or "").strip()
    value = (value or "").strip()
    if not term or not value:
        return False, "日文和中文都要填。"
    if "=" in term or "\n" in term or "\n" in value:
        return False, "不能包含换行或等号。"
    p = Path(path)
    try:
        old = p.read_text("utf-8") if p.is_file() else ""
        if not old:
            old = "【实时字幕·临时补充】这里加的译法，实时翻译立刻生效\n"
            p.write_text(old, encoding="utf-8")
        if re.search(r"^\s*%s\s*=" % re.escape(term), old, re.M):
            return False, f"「{term}」已经记过了。"
        with p.open("a", encoding="utf-8", newline="\n") as f:
            f.write(f"{term} = {value}\n")
        return True, f"已记住：{term} = {value}（后面立刻按这个翻）"
    except OSError as exc:
        return False, f"写入失败：{exc}"


def suggest_terms(ja_text, extra_terms=()):
    """从一句日文里挑出「值得加进术语表」的候选词。"""
    text = ja_text or ""
    found, seen = [], set()

    def add(word, weight):
        w = word.strip("ー・、。！？!?「」『』（）()[]【】 \t")
        if len(w) < 2 or w in seen:
            return
        seen.add(w)
        found.append((weight, w))

    for m in KATAKANA.finditer(text):
        add(m.group(), 100 + len(m.group()))     # 片假名最可能是外来语/人名
    for m in LATIN.finditer(text):
        add(m.group(), 90 + len(m.group()))
    for m in KANJI.finditer(text):
        add(m.group(), 50 + len(m.group()))      # 汉字名词次之
    for t in extra_terms:
        if t and t in text:
            add(t, 200)                          # 已经在术语表里的排最前
    found.sort(key=lambda x: -x[0])
    return [w for _, w in found][:8]


def merged_glossary(base_text, extra_path=DEFAULT_EXTRA):
    """软件里那份术语表 + 实时补充，拼成最终发给翻译模型的文本。"""
    extra = load_extra(extra_path)
    if not extra.strip():
        return base_text or ""
    return (base_text or "") + "\n\n" + extra
