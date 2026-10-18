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
# 跨条拆词：某条以「假名 + 单个汉字」结尾，且下一条与它紧贴（≤ 这个间隔）→ 是一个词被切开了。
# 依据：2026-10-13 朗读剧对比实测（large-v3 把「頑張る」拆成「頑」+「張る」），
# 一期素材里这种地方是 5 处量级。判据故意保守：只认"紧贴 + 假名后跟单个汉字"。
SPLIT_WORD_MAX_GAP = 0.35
# 超过这个时长的无字幕段算长间隙。
LONG_GAP_SECONDS = 3.0
# 长距离重复：同一段（连续多条）在片子里别处又出现一遍 —— 只提醒，不改。
# 判据（2026-10-18 拿 24 个真实项目定的）：
#   · 至少连续 3 条完全相同；或连续 2 条但两条都不短、合起来 ≥12 字
#     （只按 2 条算会报一堆「あの／なんだろう」这种常见口头语）
#   · 两段开始时间至少隔 30 秒（同一段放两遍，通常隔着几十秒到几分钟）
REPEAT_MIN_CUES = 3
REPEAT_MIN_CHARS = 12
REPEAT_MIN_GAP = 30.0

_PUNCT = re.compile(r"[\s、。，．,.!！?？…‥・「」『』（）()\[\]【】\-—~〜]+")
_KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u30fc]")
_KANJI = re.compile(r"[\u4e00-\u9fff\u3005]")


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


def is_cross_split(prev, cue, max_gap=SPLIT_WORD_MAX_GAP):
    """这两条是不是"一个词被切成了两条"。

    判据（2026-10-13 朗读剧逐条对比时定的，故意保守）：
    1. 前一条去掉标点后**以「假名 + 单个汉字」结尾**（如「…て頑」）
    2. 后一条与它**紧贴**：开始时间不晚于前一条结束 + 0.35 秒
    3. 后一条得有内容（去标点后非空）

    典型例子：「頑」+「張る」= 頑張る；「周」+「り」= 周り。
    只认这种形状，是因为真被切开的词一定长这样；
    而正常断句的句尾基本都是假名/标点（「…ですね」），不会命中。
    """
    if cue is None or prev is None:
        return False
    if cue.start - prev.end > max_gap:
        return False
    left = normalize_text(prev.source)
    right = normalize_text(cue.source)
    if len(left) < 2 or not right:
        return False
    if not _KANJI.fullmatch(left[-1]):
        return False
    return bool(_KANA.fullmatch(left[-2]))


def find_cross_splits(cues, max_gap=SPLIT_WORD_MAX_GAP):
    """找出「可能是一个词被切成两条」的位置 —— **只报告，不合并**。

    ## 为什么只报告（2026-10-18 实测把原计划推翻了）

    原计划是"自动合并"。拿本机 24 个真实项目跑了一遍，命中 **50 处**，逐条看：
    真正被切开的词大约 20 处（感情、請求、押し、入り、使って、最初、聞いて、
    周り、彼女、友達、道路…），其余 ~30 处是**正常断句**被误命中：
    「目の出し方」｜「オルトオルトオルト」、「薬局の人」｜「300万の時に混ぜる」、
    「これ、車」｜「車、ちょっと」、「警察の方」｜「犯罪者しかいない」…
    自动合并会把它们焊成一条，等于"修一个词、错一片轴"。

    所以这里只返回候选，由 core 放进「待确认」，人看过再决定。
    返回 [{"kind": "split", "id", "next_id", "before", "after", "merged", "start", "end"}, ...]
    """
    out = []
    for i, cue in enumerate(cues):
        if i == 0:
            continue
        prev = cues[i - 1]
        if is_cross_split(prev, cue, max_gap):
            out.append({"kind": "split", "id": prev.id, "next_id": cue.id,
                        "before": prev.source, "after": cue.source,
                        "merged": prev.source.rstrip() + cue.source.lstrip(),
                        "start": prev.start, "end": max(prev.end, cue.end),
                        "gap": round(cue.start - prev.end, 3)})
    return out


def merge_split_pair(cues, first_id):
    """把「可能被切开的词」那相邻两条合成一条 —— **人确认之后才调**。

    2026-10-18 加：原来待确认窗口只把候选列出来，还得自己回到列表里改两遍、删一条。
    现在窗口里选中一行点「合并这两条」即可；判断仍然由人做 —— 实测一半候选是
    正常断句（见 find_cross_splits 的说明），所以绝不自动合并。

    合并规则：
      · 原文直接接起来（日语一个字一个字接，不加空格 —— 被切开的本来就是一个词的两半）
      · 开始取前一条，结束取两条里晚的那个
      · **中文清空**：半截译文接起来一定是错意思。合并后点「识别并翻译 / 继续」
        只会补缺中文的条目，也就是只补这一条，不会重复花钱
      · 合并后整个列表重新编号（跟其它整理步骤一致）

    返回 (新列表, 说明)；这两条不能合并时新列表为 None、说明里带 error。
    """
    try:
        wanted = int(first_id)
    except (TypeError, ValueError):
        return None, {"error": "编号不是数字，不能合并。"}
    index = {cue.id: position for position, cue in enumerate(cues)}
    position = index.get(wanted)
    if position is None or position + 1 >= len(cues):
        return None, {"error": "这两条已经不在列表里了（可能刚合并过），关掉窗口重新打开再看。"}
    first, second = cues[position], cues[position + 1]
    if second.id != first.id + 1:
        return None, {"error": "这两条中间夹了别的条目，不能直接合并。"}
    source = first.source.rstrip() + second.source.lstrip()
    end = max(first.end, second.end)
    merged = Cue(first.id, first.start, end, source, "")
    rows = cues[:position] + [merged] + cues[position + 2:]
    rows = [Cue(number, cue.start, cue.end, cue.source, cue.zh)
            for number, cue in enumerate(rows, 1)]
    return rows, {"id": first.id, "removed_id": second.id,
                  "before": first.source, "after": second.source,
                  "merged": source, "start": first.start, "end": end}


def find_repeated_blocks(cues, min_gap=REPEAT_MIN_GAP):
    """找出「同一段连续字幕在片子里别处又演了一遍」——**只提醒，不改**。

    用途：他把同一段素材在片子里放两遍（真事），成片会出现两段一模一样的中文字幕。
    轴整理原来只查**相邻**重复（fold_adjacent），隔了几分钟的两段完全看不到。

    做法：用相邻两条（2-gram）当索引，找到两处相同的起点后向两边扩到最长，
    再把被包含的短块去掉；最后按"至少 3 条，或 2 条但都不短"过滤掉口头语噪音。
    返回 [{"kind": "repeat", ...}]，字段与待确认窗口一致。
    """
    texts = [normalize_text(cue.source) for cue in cues]
    n = len(texts)
    if n < 2:
        return []
    index = {}
    for i in range(n - 1):
        if not (texts[i] and texts[i + 1]):
            continue
        index.setdefault((texts[i], texts[i + 1]), []).append(i)

    blocks = set()
    for positions in index.values():
        if len(positions) < 2:
            continue
        for x in range(len(positions) - 1):
            for y in range(x + 1, len(positions)):
                i, j = positions[x], positions[y]
                if j - i < 2:            # 重叠或紧挨着的不算"长距离"
                    continue
                a, b = i, j
                while a > 0 and b > 0 and texts[a - 1] == texts[b - 1] and a - 1 + 2 < b:
                    a -= 1
                    b -= 1
                length = 2
                while a + length < n and b + length < n and texts[a + length] == texts[b + length]:
                    length += 1
                blocks.add((a, b, length))

    # 去掉被更长的块完整包含的
    maximal = []
    for candidate in sorted(blocks, key=lambda item: item[2], reverse=True):
        a, b, length = candidate
        if any(ca <= a and b + length <= cb + cl and (ca, cb) != (a, b)
               for ca, cb, cl in maximal):
            continue
        maximal.append(candidate)

    out = []
    for a, b, length in sorted(maximal):
        gap = cues[b].start - cues[a + length - 1].end
        if gap < min_gap:
            continue
        if length < REPEAT_MIN_CUES:
            if length != 2:
                continue
            if min(len(texts[a]), len(texts[b])) < 4:
                continue
            if len(texts[a]) + len(texts[a + 1]) < REPEAT_MIN_CHARS:
                continue
        out.append({
            "kind": "repeat",
            "first_id": cues[a].id, "first_last_id": cues[a + length - 1].id,
            "second_id": cues[b].id, "second_last_id": cues[b + length - 1].id,
            "cues": length, "seconds": round(cues[a + length - 1].end - cues[a].start, 2),
            "gap": round(gap, 1),
            "text": " / ".join(texts[a:a + min(length, 4)])[:80],
        })
    return out


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
    "被切开的词"**不自动合并**，只放进报告的 split_candidates / review，交人看
    （理由见 find_cross_splits 的说明）。
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
    splits = find_cross_splits(current)
    repeats = find_repeated_blocks(current)
    review = review_list(current, gap_threshold) + splits + repeats
    report.update({"cues_after": len(current), "short_after": count_short(current),
                   "overlaps_after": count_overlaps(current),
                   "gaps_after": len(find_long_gaps(current, gap_threshold)),
                   "split_candidates": len(splits),
                   "repeat_candidates": len(repeats), "review": review})
    return current, report
