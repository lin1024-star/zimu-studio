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
"""
from pathlib import Path

from core import Cue, clean
from polish import normalize_text

# 各类阈值。都可以在调用时覆盖。
LIMITS = {
    "min_duration": 0.5,     # 短于这个来不及看（约 12 帧 @24fps）
    "max_duration": 7.0,     # 长于这个一直挂在屏幕上，累
    "min_gap": 0.08,         # 相邻两条间隙小于这个会闪一下（约 2 帧）
    "max_cps": 9.0,          # 每秒字符数上限；中文常规上限
    "long_gap": 5.0,         # 这么久没有字幕，可能漏了句子
    "head_silence": 10.0,    # 开头这么久没字幕
    "tail_silence": 30.0,    # 结尾这么久没字幕
    "min_keep": 0.10,        # 修重叠时，前一条至少留这么久，免得修没了
}

SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}
SEVERITY_LABEL = {"error": "错误", "warn": "警告", "info": "提示"}

# 每种问题的说明与建议，界面直接拿去显示
KINDS = {
    "reversed": ("时间倒着走", "开始时间晚于或等于结束时间，导出后这条会消失或错位。"),
    "empty": ("没有文字", "这条没有任何文字，空条会占着时间轴。"),
    "overlap": ("和上一条叠在一起", "两条同时显示会糊在一起；ASS 里会变成两行挤着。"),
    "tiny_gap": ("间隙太小会闪", "和下一条挨得太近，画面会闪一下，看着像抖动。"),
    "too_short": ("太短来不及看", "一闪而过，观众看不清。"),
    "too_long": ("挂得太久", "一直留在屏幕上，观众会以为卡住了。"),
    "too_fast": ("读不完", "字数太多、时间太短，按正常阅读速度看不完。"),
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
            add("too_short", cue,
                f"第 {cue.id} 条只有 {span:.2f} 秒（建议 ≥ {lim['min_duration']:.1f} 秒）"
                f"「{text[:20]}」", False)
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
            "cues": len(cues), "duration": duration}


def _severity(kind):
    if kind in ("reversed", "empty", "overlap"):
        return "error"
    if kind in ("tiny_gap", "too_short", "too_long", "too_fast"):
        return "warn"
    return "info"


def fix_safe(cues, limits=None):
    """只修「怎么修都不会修错」的三类：时间倒序、重叠、间隙过小。

    **超短 / 超长 / 读不完 一律不自动动** —— 那需要重排句子，
    机器做只会把内容搞乱，必须人看一眼。

    返回 (新列表, 改动列表)。改动列表里每条都有 前→后，供日志展示。
    """
    lim = dict(LIMITS)
    lim.update(limits or {})
    out = []
    changes = []
    for cue in sorted(cues, key=lambda c: (c.start, c.id)):
        start, end = float(cue.start), float(cue.end)
        if end <= start:
            # 倒序 / 零长度：给一个最短可见时长，别让这条消失
            end = start + lim["min_duration"]
            changes.append({"kind": "reversed", "id": cue.id,
                            "before": (cue.start, cue.end), "after": (start, end)})
        if out:
            prev = out[-1]
            gap = start - prev.end
            if gap < lim["min_gap"]:
                # 把前一条收短，给后面留出最小间隙；但不许把前一条收没了
                want_end = start - lim["min_gap"]
                floor = prev.start + lim["min_keep"]
                if want_end >= floor:
                    changes.append({"kind": "overlap" if gap < 0 else "tiny_gap",
                                    "id": prev.id,
                                    "before": (prev.start, prev.end),
                                    "after": (prev.start, want_end)})
                    out[-1] = Cue(prev.id, prev.start, want_end, prev.source, prev.zh)
                elif gap < 0:
                    # 前一条太短，收不动了 → 反过来把这一条往后推
                    new_start = prev.end + lim["min_gap"]
                    if new_start < end:
                        changes.append({"kind": "overlap", "id": cue.id,
                                        "before": (start, end), "after": (new_start, end)})
                        start = new_start
        out.append(Cue(cue.id, start, end, cue.source, cue.zh))
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

    只修倒序、重叠、间隙过小这三类；超短/超长/读不完一律不动。
    返回 (新项目, 改动列表, 备份路径)；没什么可修时备份为 None。
    """
    import os
    from datetime import datetime

    from core import save_project

    cues = cues_from_rows(project.get("cues") or [])
    fixed, changes = fix_safe(cues, limits)
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
