"""时间轴自查：不听音频，只查字幕本身合不合理。

## 为什么做这个（实测记录，别重复踩）

本来要做的是「与音频对齐校验」——独立扫一遍音频，看字幕有没有整体偏移。
在真实素材（GTA 日语直播，BGM 铺满）上试了两种做法，**都不成立**：

1. **用 VAD 算覆盖率**（faster_whisper 自带 silero VAD，不用装东西）
   VAD 说 **95% 的时长都是说话声**（BGM 一直响），
   把字幕整体挪 2 秒，覆盖率才从 93.6% 掉到 91.2% —— 曲线几乎是平的，
   argmax 落在哪全靠噪声。**5 个已知偏移一个都找不回**。
   （注意：中间有一版"5/5 全找回"是 bug —— 甲.ass 有两条重叠字幕轨，
     我把时间段长度直接相加，算出 127% 的覆盖率，假峰。）

2. **改用能量起音做边界比对**（不看覆盖面积，看每条字幕的开头/结尾对不对得上起音）
   **223 秒里检出 552 个"起音"** —— 平均每 0.4 秒一个，那是 BGM 的节拍不是说话。
   任何一条字幕开头附近总能找到"起音"，于是永远显示"对齐"，
   **8 个已知偏移还是 0 个找回**。

**结论**：不引入额外依赖（torch 约 2.5 GB）的前提下，在 BGM 铺满的素材上
做不了"和音频对齐校验"。所以改做这个 —— **不碰音频，只查字幕本身的硬伤**。
这几项才是真正会让成片出事故的：叠字、闪帧、看不清、读不完。

## 2026-10-18：按 B站 官方要求加了「交付体检」

B站官方（哔哩哔哩客服《UP主投稿功能系列·稿件字幕管理篇》）写明三条：
**只收 srt / 单行最多 300 字 / 单条不宜短于 0.5 秒 / 时间段尽量不要重叠**。
所以这里多了 `bilibili_audit()` —— 它只回答一个问题：**这份能不能交**。
同时「一键修」现在会把太短的补到 0.5 秒（只往后延结束时间，不碰文字；
后面紧挨着就如实不补）。

Netflix 的"每行 16 字 / 9 字每秒"**不在** B站 条文里，因此不作为交付判据，
只保留在时间轴体检的"读不完"那一项里当建议（两者别混着用）。
"""
from pathlib import Path

from core import Cue, clean
from polish import normalize_text

# 各类阈值。都可以在调用时覆盖。
LIMITS = {
    "min_duration": 0.5,     # 短于这个来不及看（约 12 帧 @24fps）；B站官方同款下限
    "max_duration": 7.0,     # 长于这个一直挂在屏幕上，累
    "min_gap": 0.08,         # 相邻两条间隙小于这个会闪一下（约 2 帧）
    "max_cps": 9.0,          # 每秒字符数上限；中文常规上限
    "max_line_chars": 300,   # 单行字数上限：B站上传就是卡 300 字
    "long_gap": 5.0,         # 这么久没有字幕，可能漏了句子
    "head_silence": 10.0,    # 开头这么久没字幕
    "tail_silence": 30.0,    # 结尾这么久没字幕
    "min_keep": 0.10,        # 修重叠时，前一条至少留这么久，免得修没了
}

# —— B站官方投稿要求（2026-10-18 查证，原始出处与截图见桌面资料《今晚成果_20261018》）——
# 来源：哔哩哔哩客服《UP主投稿功能系列·稿件字幕管理篇》
#   https://www.bilibili.com/opus/1228554809298124881
#   · 支持 srt 格式字幕文件上传
#   · 视频单行字幕文字存在上限，最多 300 字
#   · 字幕时间段尽量不要重叠，单条字幕展示时长不宜短于 0.5 秒
#
# 注意：B站**没有**规定"每行几个字"，也没有规定"阅读速度" —— 那是 Netflix 的口径
# （每行 16 字 / 9 字每秒）。所以"能不能交 B站"只按上面三条判，
# 不把 Netflix 的加进来充数（两者混用会让人以为超了字数是违规）。
BILIBILI = {
    "min_duration": 0.5,       # 官方：不宜短于 0.5 秒
    "max_line_chars": 300,     # 官方：单行最多 300 字
    "allow_overlap": False,    # 官方：尽量不要重叠
    "format": "srt",           # 官方：只收 srt
}

SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}
SEVERITY_LABEL = {"error": "错误", "warn": "警告", "info": "提示"}

# 每种问题的说明与建议，界面直接拿去显示
KINDS = {
    "reversed": ("时间倒着走", "开始时间晚于或等于结束时间，导出后这条会消失或错位。"),
    "empty": ("没有文字", "这条没有任何文字，空条会占着时间轴。"),
    "overlap": ("和上一条叠在一起", "两条同时显示会糊在一起；ASS 里会变成两行挤着。"),
    "tiny_gap": ("间隙太小会闪", "和下一条挨得太近，画面会闪一下，看着像抖动。"),
    "too_short": ("太短来不及看", "一闪而过，观众看不清。B站的建议下限是每条 0.5 秒 —— 后面有空就点「一键修」补上。"),
    "too_long": ("挂得太久", "一直留在屏幕上，观众会以为卡住了。"),
    "too_fast": ("读不完", "字数太多、时间太短，按正常阅读速度看不完。"),
    "too_many_chars": ("单行超过 300 字", "B站上传上限是单行 300 字，超了可能被截断或传不上去；建议拆成两条。"),
    "long_gap": ("这段一直没字幕", "可能是漏识别，也可能主播真的停了几秒 —— 建议听一下。"),
    "head_silence": ("开头很久没字幕", "片头这么久没有字幕，确认是有意留白。"),
    "tail_silence": ("结尾很久没字幕", "片尾这么久没有字幕，确认后面没内容了。"),
}


def _text_of(cue):
    return clean(cue.source or "")


def _meaningful(cue):
    """去掉标点后剩下的内容。

    为什么不用 clean()：clean("。。。") 还是 "。。。",
    可观众看到的只是几个点 —— 这种等于空条，得算出来。
    """
    return normalize_text(cue.source or "")


def _reading_text(cue):
    """阅读速度按**观众真正要看的那一行**算：有中文就按中文，没中文才按原文。

    为什么要这样：AutoKiri 那种原文 SRT 的字/秒 中位数是 6.6，
    拿中文标准（9 字/秒）去卡它就会报一堆假警报 —— 那不是给人读的行，
    是待翻译的原料。真正要管的，是屏幕上最终显示的那一行。
    """
    zh = clean(cue.zh or "")
    return zh if zh else clean(cue.source or "")


def _chars(text):
    """算阅读量用的字数：去掉标点和空白。

    标点不占阅读时间 —— 把它算进去会让「次の動画でお会いしましょう。」
    这种 13 个字的句子虚高一大截。
    """
    return len(normalize_text(text))


def _extend_room(cue, nxt, lim, duration=None):
    """太短的那条最多能往后延到哪：不许盖住下一条，也不许超过片长。

    补不补得上，取决于"后面有没有空"。补不动（下面紧挨着）就如实说补不了，
    不许为了凑够 0.5 秒去压别人的时间 —— 那等于用一条的错换另一条的错。
    """
    want = cue.start + lim["min_duration"]
    if nxt is not None:
        want = min(want, nxt.start - lim["min_gap"])
    if duration:
        want = min(want, float(duration))
    return want


def check_timeline(cues, duration=None, limits=None):
    """全量自查。**只报告，不改任何东西。**

    返回 {"issues": [...], "counts": {...}}。
    issues 每条：{"kind", "severity", "id", "start", "end", "message", "fixable"}
    """
    lim = dict(LIMITS)
    lim.update(limits or {})
    cues = sorted(cues, key=lambda c: (c.start, c.id))
    issues = []

    def add(kind, cue, message, fixable=False):
        issues.append({"kind": kind, "severity": _severity(kind), "id": cue.id,
                       "start": cue.start, "end": cue.end, "message": message,
                       "fixable": fixable})

    for i, cue in enumerate(cues):
        span = cue.end - cue.start
        text = _text_of(cue)
        if cue.end <= cue.start:
            add("reversed", cue,
                f"第 {cue.id} 条：{cue.start:.2f} → {cue.end:.2f}（时长 {span:.2f} 秒）", True)
            continue
        if not _meaningful(cue):
            # 只有标点或空白：观众看到的是空的
            shown = text[:12] if text else "（空白）"
            add("empty", cue, f"第 {cue.id} 条：{cue.start:.2f} → {cue.end:.2f}"
                              f"「{shown}」去掉标点后没有内容", False)
        if span < lim["min_duration"]:
            nxt = cues[i + 1] if i + 1 < len(cues) else None
            can = _extend_room(cue, nxt, lim, duration) > cue.end + 1e-9
            add("too_short", cue,
                f"第 {cue.id} 条只有 {span:.2f} 秒（B站建议 ≥ {lim['min_duration']:.1f} 秒）"
                f"「{text[:20]}」" + ("，后面有空可以自动补足" if can else "，后面紧挨着，补不了"),
                can)
        if span > lim["max_duration"]:
            add("too_long", cue,
                f"第 {cue.id} 条有 {span:.1f} 秒（建议 ≤ {lim['max_duration']:.0f} 秒）"
                f"「{text[:20]}」", False)
        if span > 0:
            reading = _reading_text(cue)
            n_chars = _chars(reading)
            cps = n_chars / span
            if cps > lim["max_cps"] and n_chars >= 4:
                which = "中文" if clean(cue.zh or "") else "原文（还没翻译）"
                add("too_fast", cue,
                    f"第 {cue.id} 条 {span:.1f} 秒里 {n_chars} 字"
                    f"（{cps:.1f} 字/秒，建议 ≤ {lim['max_cps']:.0f}）"
                    f"按{which}算「{reading[:20]}」", False)

        # B站上传上限：单行 300 字。混合版是「原文一行 + 中文一行」，
        # 所以要分别量两行，不能只量屏幕上最后显示的那一行。
        cap = lim.get("max_line_chars")
        if cap:
            over = [(label, value) for label, value in
                    (("原文", clean(cue.source or "")), ("中文", clean(cue.zh or "")))
                    if len(value) > cap]
            if over:
                detail = "、".join(f"{label} {len(value)} 字" for label, value in over)
                add("too_many_chars", cue,
                    f"第 {cue.id} 条 {detail}（B站单行上限 {cap} 字）"
                    f"「{over[0][1][:20]}」", False)

        if i + 1 < len(cues):
            nxt = cues[i + 1]
            gap = nxt.start - cue.end
            if gap < 0:
                add("overlap", cue,
                    f"第 {cue.id} 条和第 {nxt.id} 条重叠 {-gap:.2f} 秒", True)
            elif gap < lim["min_gap"]:
                add("tiny_gap", cue,
                    f"第 {cue.id} 条和第 {nxt.id} 条只隔 {gap*1000:.0f} 毫秒"
                    f"（建议 ≥ {lim['min_gap']*1000:.0f} 毫秒）", True)
            elif gap > lim["long_gap"]:
                issues.append({"kind": "long_gap", "severity": _severity("long_gap"),
                               "id": cue.id, "start": cue.end, "end": nxt.start,
                               "message": f"第 {cue.id} 条和第 {nxt.id} 条之间空了 {gap:.1f} 秒",
                               "fixable": False})

    if cues and duration:
        if cues[0].start > lim["head_silence"]:
            issues.append({"kind": "head_silence", "severity": _severity("head_silence"),
                           "id": cues[0].id, "start": 0.0, "end": cues[0].start,
                           "message": f"开头 {cues[0].start:.1f} 秒没有任何字幕",
                           "fixable": False})
        tail = duration - cues[-1].end
        if tail > lim["tail_silence"]:
            issues.append({"kind": "tail_silence", "severity": _severity("tail_silence"),
                           "id": cues[-1].id, "start": cues[-1].end, "end": duration,
                           "message": f"最后一条之后还有 {tail:.1f} 秒没有字幕",
                           "fixable": False})

    issues.sort(key=lambda it: (SEVERITY_ORDER[it["severity"]], it["start"]))
    counts = {}
    for it in issues:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    return {"issues": issues, "counts": counts,
            "cues": len(cues), "duration": duration,
            "bilibili": bilibili_audit(cues, lim)}


def _severity(kind):
    if kind in ("reversed", "empty", "overlap", "too_many_chars"):
        return "error"
    if kind in ("tiny_gap", "too_short", "too_long", "too_fast"):
        return "warn"
    return "info"


def bilibili_audit(cues, limits=None):
    """按 B站 官方投稿要求体检：这份字幕能不能直接交上去。

    只查 B站 自己写明的三条（srt 是导出格式的事，不在时间轴里查）：
      1. 单条时长不短于 0.5 秒
      2. 时间段不重叠
      3. 单行不超过 300 字

    为什么要单独有这么一张"交付体检"：时间轴体检是"尽量做得好"（阅读速度、
    最长时长、成段漏句都管），B站 这张只回答一个问题 —— **能不能交**。
    所以它只认 B站 官方写明的数，不把 Netflix 的每行字数/阅读速度混进来。
    """
    lim = dict(LIMITS)
    lim.update(limits or {})
    ordered = sorted(cues, key=lambda c: (c.start, c.id))
    short, too_many = [], []
    overlap = 0
    for i, cue in enumerate(ordered):
        if cue.end - cue.start < lim["min_duration"]:
            short.append(cue.id)
        if i + 1 < len(ordered) and ordered[i + 1].start < cue.end:
            overlap += 1
        longest = max(len(clean(cue.source or "")), len(clean(cue.zh or "")))
        if longest > lim["max_line_chars"]:
            too_many.append(cue.id)
    items = []
    if short:
        items.append(f"{len(short)} 条短于 {lim['min_duration']:.1f} 秒")
    if overlap:
        items.append(f"{overlap} 处时间段重叠")
    if too_many:
        items.append(f"{len(too_many)} 条单行超过 {lim['max_line_chars']} 字")
    return {"ok": not items, "items": items, "short": short,
            "overlap": overlap, "too_many": too_many, "cues": len(ordered)}


def bilibili_summary(audit):
    """把 B站 交付体检说成一句人话，直接进日志和体检窗口。"""
    audit = audit or {}
    if audit.get("ok"):
        return (f"B站交付体检：可以交（{audit.get('cues', 0)} 条）。"
                "B站只收 SRT —— 导出时选「原文 / 中文 / 混合版」里的 SRT 就行。")
    return ("B站交付体检：还不能直接交 —— " + "、".join(audit.get("items") or []) +
            "。短条和重叠点「一键修」能处理大部分；时间太挤、后面没空的，机器补不动，"
            "要你在体检列表里手工挪一下。单行超过 300 字的要自己拆句。")


_EPS = 1e-9   # 时间比较的容差：SRT 是毫秒级，1 纳秒的差不算差


def _fix_pass(items, lim):
    """一轮：把倒序、重叠、间隙过小修一遍。返回 (新列表, 改动列表)。

    为什么拆成"一轮一轮跑到不动"：把某条的**开始时间往后推**之后，它可能跑到
    后面那一条的后面去 —— 列表就不再按时间排了。真实素材上第二次跑还会动 47 处，
    就是因为第一遍留下的顺序是乱的，第二遍重新排序后又能发现新的重叠。
    所以这里每轮结束都重新排序，跑到没有改动为止（有上限，防死循环）。
    """
    out, changes = [], []
    for cue in sorted(items, key=lambda c: (c.start, c.id)):
        start, end = float(cue.start), float(cue.end)
        if end <= start:
            # 倒序 / 零长度：给一个最短可见时长，别让这条消失
            end = start + lim["min_duration"]
            changes.append({"kind": "reversed", "id": cue.id,
                            "before": (cue.start, cue.end), "after": (start, end)})
        if out:
            prev = out[-1]
            gap = start - prev.end
            if gap < lim["min_gap"] - _EPS:
                # 把前一条收短，给后面留出最小间隙；但不许把前一条收没了
                want_end = start - lim["min_gap"]
                floor = prev.start + lim["min_keep"]
                if want_end >= floor and want_end < prev.end - _EPS:
                    changes.append({"kind": "overlap" if gap < 0 else "tiny_gap",
                                    "id": prev.id,
                                    "before": (prev.start, prev.end),
                                    "after": (prev.start, want_end)})
                    out[-1] = Cue(prev.id, prev.start, want_end, prev.source, prev.zh)
                elif gap < -_EPS:
                    # 前一条太短，收不动了 → 反过来把这一条往后推
                    new_start = prev.end + lim["min_gap"]
                    if new_start < end - _EPS:
                        changes.append({"kind": "overlap", "id": cue.id,
                                        "before": (start, end), "after": (new_start, end)})
                        start = new_start
        out.append(Cue(cue.id, start, end, cue.source, cue.zh))
    return out, changes


def fix_safe(cues, limits=None, duration=None):
    """只修「怎么修都不会修错」的四类：时间倒序、重叠、间隙过小、
    **太短补到 0.5 秒**（B站官方：单条时长不宜短于 0.5 秒）。

    - 太短只往后延结束时间，不碰文字；有下一条时绝不盖住它（保留最小间隙），
      后面没空就如实不动。
    - 超长 / 读不完 / 单行超过 300 字 一律不自动动 —— 那要重排句子或拆条，
      机器做只会把内容搞乱，必须人看一眼。
    - **结果是幂等的**：跑完再跑一次不会再动（真实素材上专门验过）。

    返回 (新列表, 改动列表)。改动列表里每条都有 前→后，供日志展示。
    """
    lim = dict(LIMITS)
    lim.update(limits or {})
    out, changes = [], []
    working = list(cues)
    for _round in range(30):
        out, round_changes = _fix_pass(working, lim)
        if not round_changes:
            break
        changes.extend(round_changes)
        working = out
    else:
        # 30 轮还没收敛，说明数据本身很怪；如实返回当前结果，不再空转
        pass

    # 最后一遍：太短的往后补齐（B站 0.5 秒下限）。
    # 放在最后：得等重叠都修完，才知道"后面还有没有空"。
    for i, cue in enumerate(out):
        if cue.end - cue.start >= lim["min_duration"] - _EPS:
            continue
        nxt = out[i + 1] if i + 1 < len(out) else None
        want = _extend_room(cue, nxt, lim, duration)
        if want > cue.end + _EPS:
            changes.append({"kind": "too_short", "id": cue.id,
                            "before": (cue.start, cue.end), "after": (cue.start, want)})
            out[i] = Cue(cue.id, cue.start, want, cue.source, cue.zh)
    return out, changes


def cues_from_rows(rows):
    """项目里的字幕条目（dict）→ Cue 对象。"""
    return [Cue(int(r["id"]), float(r["start"]), float(r["end"]),
                r.get("source", "") or "", r.get("zh", "") or "") for r in rows]


def rows_from_cues(cues):
    """Cue 对象 → 项目里的字幕条目（dict）。"""
    return [{"id": c.id, "start": c.start, "end": c.end,
             "source": c.source, "zh": c.zh} for c in cues]


def save_fix(path, project, limits=None):
    """一键修安全的项并存盘。**改之前先备份**，和调时长、整体偏移同一套做法。

    只修倒序、重叠、间隙过小、太短补到 0.5 秒这四类；
    超长 / 读不完 / 单行超过 300 字 一律不动。
    返回 (新项目, 改动列表, 备份路径)；没什么可修时备份为 None。
    """
    import os
    from datetime import datetime

    from core import save_project

    cues = cues_from_rows(project.get("cues") or [])
    # 最后一条补时长时不能超过片长 —— 片长记在识别信息里，有就用，没有就不限制
    duration = None
    info = project.get("recognition") or {}
    if isinstance(info, dict) and info.get("duration"):
        try:
            duration = float(info["duration"])
        except (TypeError, ValueError):
            duration = None
    fixed, changes = fix_safe(cues, limits, duration=duration)
    if not changes:
        return project, [], None
    updated = dict(project)
    updated["cues"] = rows_from_cues(fixed)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = Path(path).with_name(f"project_before_timeline_fix_{stamp}_{os.urandom(4).hex()}.json")
    save_project(backup, project)
    save_project(path, updated)
    return updated, changes, backup


def summarize(result):
    """把自查结果写成几句人话，供状态栏和日志用。"""
    counts = result.get("counts") or {}
    if not counts:
        return f"时间轴自查通过：{result.get('cues', 0)} 条没有发现问题。"
    errs = sum(n for k, n in counts.items() if _severity(k) == "error")
    warns = sum(n for k, n in counts.items() if _severity(k) == "warn")
    infos = sum(n for k, n in counts.items() if _severity(k) == "info")
    parts = []
    if errs:
        parts.append(f"{errs} 处错误")
    if warns:
        parts.append(f"{warns} 处警告")
    if infos:
        parts.append(f"{infos} 处提示")
    return f"时间轴自查：{result.get('cues', 0)} 条里发现 " + "、".join(parts) + "。"
