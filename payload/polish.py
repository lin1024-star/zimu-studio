"""轴后处理：只合并条目与调整时间，不重跑识别、不碰模型、不发请求。

对应改进计划阶段 1 的 1.1 / 1.2 / 1.3 / 1.6。
全部是纯函数，便于用同一段真实素材做前后计数对比。
"""
import re

from core import Cue, clean

# 同一字符连续出现几次算拖音；压到留几个。
ELONGATION_MIN = 4
ELONGATION_KEEP = 2
# 短于这个时长算碎片。
FRAGMENT_SECONDS = 0.5
# 碎片最多这么长（去掉标点后）才认为"没有实义"。
FRAGMENT_MAX_CHARS = 2
# 合并碎片时允许的最大间隔；两条之间有朗读停顿就不硬并。
FRAGMENT_MAX_GAP = 0.3
# 超过这个时长的无字幕段算长间隙。
LONG_GAP_SECONDS = 3.0

_PUNCT = re.compile(r"[\s、。，．,.!！?？…‥・「」『』（）()\[\]【】\-—~〜]+")


def normalize_text(text):
    """去掉标点与空白，用于比较两条是不是同一句。"""
    return _PUNCT.sub("", clean(text))


def compress_elongation(text):
    """把 ああああああ 这类拖音压到两个：ああ。"""
    return re.sub(r"(.)\1{%d,}" % (ELONGATION_MIN - 1), r"\1" * ELONGATION_KEEP, text)


def compress_elongations(cues):
    """对每一句做拖音压缩，返回 (新列表, 改动列表)。"""
    out, changes = [], []
    for cue in cues:
        fixed = compress_elongation(cue.source)
        if fixed != cue.source:
            changes.append({"kind": "elongation", "id": cue.id,
                            "before": cue.source, "after": fixed})
            out.append(Cue(cue.id, cue.start, cue.end, fixed, cue.zh))
        else:
            out.append(cue)
    return out, changes


def fold_adjacent(cues):
    """相邻两条文本完全相同 → 合并为一条，时长顺延（start 取前、end 取后）。

    只折叠“一字不差”的情况，这基本可以确定是识别循环。
    “互相包含”故意不做：主播真的会重复说同一句话，而一句里包含另一句
    （如「ね」与「降りてたもんね」）属于碎片关系，交给 merge_fragments 处理，
    那里有时长与字数限制，安全得多。
    只处理紧挨着的两条，不做连锁传递，避免把整段合成一条。
    返回 (新列表, 改动列表)。
    """
    out, changes = [], []
    for cue in cues:
        if out:
            prev = out[-1]
            a, b = normalize_text(prev.source), normalize_text(cue.source)
            if a and b and a == b:
                keep = prev.source if len(a) >= len(b) else cue.source
                end = max(prev.end, cue.end)
                changes.append({"kind": "fold", "id": prev.id, "kept": keep,
                                "dropped": cue.source, "start": prev.start, "end": end})
                out[-1] = Cue(prev.id, prev.start, end, keep, prev.zh or cue.zh)
                continue
        out.append(cue)
    return out, changes


def merge_fragments(cues):
    """把过短且没有实义的碎片并入相邻条。

    条件偏保守：时长 < 0.5 秒、去标点后不超过 2 个字、且与相邻条几乎相接。
    宁可不并，也不要把一句真实台词吞掉。
    返回 (新列表, 改动列表)。
    """
    out = []
    changes = []
    for cue in cues:
        text = normalize_text(cue.source)
        duration = cue.end - cue.start
        short = duration < FRAGMENT_SECONDS and len(text) <= FRAGMENT_MAX_CHARS
        prev = out[-1] if out else None
        gap = cue.start - prev.end if prev else None
        if short and prev is not None and 0 <= gap <= FRAGMENT_MAX_GAP:
            changes.append({"kind": "merge", "id": prev.id, "kept": prev.source,
                            "dropped": cue.source})
            out[-1] = Cue(prev.id, prev.start, max(prev.end, cue.end), prev.source, prev.zh)
            continue
        out.append(cue)
    return out, changes


def find_long_gaps(cues, threshold=LONG_GAP_SECONDS):
    """找出超过阈值的无字幕段，供人工抽听（只报告，不改时间轴）。"""
    gaps = []
    for before, after in zip(cues, cues[1:]):
        length = after.start - before.end
        if length > threshold:
            gaps.append({"after_id": before.id, "before_id": after.id,
                         "start": before.end, "end": after.start, "seconds": length})
    return gaps


def count_short(cues, limit=FRAGMENT_SECONDS):
    """时长小于 limit 的条目数。"""
    return sum(1 for c in cues if (c.end - c.start) < limit)


def count_overlaps(cues):
    """与前一条重叠的条目数（正常应为 0）。"""
    return sum(1 for a, b in zip(cues, cues[1:]) if b.start < a.end)


def review_list(cues, gap_threshold=LONG_GAP_SECONDS, short_limit=FRAGMENT_SECONDS):
    """列出需要人工确认的位置：长静音段 + 仍然过短的条目。

    只报告，不改动。整理之后剩下的这些，要么是主播真的停顿了好几秒，
    要么是「ほんと?」这类说得又快又短的完整台词——机器分不清，
    交给人看一眼最稳妥。
    """
    items = []
    for gap in find_long_gaps(cues, gap_threshold):
        items.append({"kind": "gap", "seconds": gap["seconds"], "start": gap["start"],
                      "end": gap["end"], "after_id": gap["after_id"],
                      "before_id": gap["before_id"]})
    for cue in cues:
        duration = cue.end - cue.start
        if duration < short_limit:
            items.append({"kind": "short", "id": cue.id, "seconds": duration,
                          "start": cue.start, "end": cue.end, "text": cue.source})
    return items


def polish(cues, fold=True, merge=True, compress=True, gap_threshold=LONG_GAP_SECONDS):
    """按顺序做拖音压缩 → 重复折叠 → 碎片合并，返回 (新列表, 报告)。

    只动时间与重复文本，不改变模型输出里独一无二的内容。
    report["changes"] 逐条记录改了什么，供日志直接展示。
    """
    before = list(cues)
    report = {"cues_before": len(before), "short_before": count_short(before),
              "gaps_before": len(find_long_gaps(before, gap_threshold)),
              "changes": []}
    current = before
    if compress:
        current, changed = compress_elongations(current)
        report["changes"] += changed
    report["elongation_fixed"] = sum(1 for c in report["changes"] if c["kind"] == "elongation")
    if fold:
        current, changed = fold_adjacent(current)
        report["changes"] += changed
    report["folded"] = sum(1 for c in report["changes"] if c["kind"] == "fold")
    if merge:
        current, changed = merge_fragments(current)
        report["changes"] += changed
    report["fragments_merged"] = sum(1 for c in report["changes"] if c["kind"] == "merge")
    # 重新编号，保持连续
    current = [Cue(i + 1, c.start, c.end, c.source, c.zh) for i, c in enumerate(current)]
    report.update({"cues_after": len(current), "short_after": count_short(current),
                   "overlaps_after": count_overlaps(current),
                   "gaps_after": len(find_long_gaps(current, gap_threshold)),
                   "review": review_list(current, gap_threshold)})
    return current, report
