"""把「实时字幕_运行记录.txt」里这一场的结果读出来（给主界面导出用）。

背景（2026-10-29，使用者反馈）：
    实时字幕可以从桌面图标**单独打开**（2.3 及更早就是独立入口）。那种情况下，这一场
    的结果只在**那个进程的内存**里 —— 主界面（字幕工坊）看不到，也就导不出来。
    使用者就是这样：先单独开了实时字幕，再开字幕工坊，想让字幕工坊把这一场导出来。

    两边唯一共用的是这份运行记录：实时字幕每显示一条就写一行，格式一直是
        [10-29 20:45:40] 字幕[整句] 延迟  5.5s | 中文  〔原文〕
    本模块**只读**：不改记录、不碰正在跑的那一份进程，所以不会影响它。

时间轴怎么来的（记录里没有开始/结束时间）：
    · 起点 ≈ 这一行写下的时刻 − 延迟（延迟就是「这一句说完到显示出来」的间隔）
    · 终点 = 下一条的起点；最后一条按中文长度估一个时长
    · 相邻两条排开、不重叠，至少 0.8 秒 —— 和「这一场的结果」导出用同一套排法
    所以这是**估算**的时间轴，拿去整理/参考够用；要精确时间轴还是用字幕工坊自己跑识别。

场次怎么分：
    记录文件是追加写的，从来不删。最后一条「启动：…」之后才算这一场 —— 这样先开的
    老场次不会被混进来，只读正在跑的这一场。
"""
import re

_SESSION_RE = re.compile(r"^\[\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] 启动：")
_RECORD_RE = re.compile(
    r"^\[(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})\] 字幕\[(半句|整句)\] 延迟 *([\d.]+)s \| (.*)$"
)

MIN_SECONDS = 0.8      # 一条字幕至少显示这么久（太短会闪一下就没了）
MAX_SECONDS = 8.0      # 一条字幕最长显示这么久（直播里偶尔一条挂很久，别拉太长）
_SESSION_LABEL = re.compile(r"^\[(\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] 启动：")


def _record(line):
    """一行字幕 → dict；不是字幕行就返回 None。

    中文里可能带「〔」吗？基本不会，但仍从右边找最后一个「  〔」再拆 —— 记录里的
    分隔固定是「中文 + 两个空格 + 〔原文〕」，从右拆最不容易误伤中文。
    """
    match = _RECORD_RE.match(line.rstrip("\n"))
    if not match:
        return None
    month, day, hour, minute, second, kind, latency, rest = match.groups()
    if "  〔" not in rest:
        return None
    zh, _, tail = rest.rpartition("  〔")
    ja = tail[:-1] if tail.endswith("〕") else tail
    zh = zh.strip()
    ja = ja.strip()
    if not zh or not ja:
        return None
    if zh.startswith("[") and "〕" not in zh:
        # 极少数情况下中文里自己带「〔」导致拆错：宁可丢掉这一条，也不导出坏句子。
        return None
    return {"t": int(hour) * 3600 + int(minute) * 60 + int(second),
            "latency": float(latency), "kind": kind, "zh": zh, "ja": ja}


def _session_start(lines):
    """最后一条「启动：…」的下标；没有就从头开始。"""
    index = -1
    for i, line in enumerate(lines):
        if _SESSION_RE.match(line):
            index = i
    return index + 1


def parse_session(text):
    """把这一场（最后一次启动之后）的字幕行读出来，按时间顺序返回。

    返回值：[{"t": 当天秒数, "latency": 秒, "kind": "整句"/"半句", "zh": …,
              "ja": …}, …]。半句也留着 —— 有的场次最后一句没等到整句。
    """
    lines = (text or "").splitlines()
    records = []
    day_offset = 0
    previous = None
    for line in lines[_session_start(lines):]:
        record = _record(line)
        if record is None:
            continue
        absolute = record["t"] + day_offset * 86400
        if previous is not None and absolute < previous - 43200:
            # 跨过零点：记录里没有日期，只能看时间倒退超过 12 小时来判断。
            day_offset += 1
            absolute = record["t"] + day_offset * 86400
        previous = absolute
        record["t"] = absolute
        records.append(record)
    return records


def cues_from_records(records):
    """记录 → 可直接导出的 cues（只取整句，中文/原文相同算重复）。"""
    whole = []
    seen = None
    for record in records:
        if record["kind"] != "整句":
            continue
        key = (record["zh"], record["ja"])
        if key == seen:          # 同一句被重复写了两行（重绘/重发），只留一条
            continue
        seen = key
        whole.append(record)
    if not whole:
        return []
    # 起点 ≈ 写下时刻 − 延迟；最后按第一条对齐到 0 秒（导出不需要绝对时刻）。
    starts = [record["t"] - record["latency"] for record in whole]
    base = starts[0]
    relative = [max(0.0, start - base) for start in starts]
    for i in range(1, len(relative)):
        if relative[i] < relative[i - 1] + 0.05:
            relative[i] = relative[i - 1] + 0.05
    cues = []
    previous_end = 0.0
    for i, record in enumerate(whole):
        start = max(relative[i], previous_end)
        if i + 1 < len(relative):
            end = relative[i + 1]
        else:
            end = start + max(1.2, min(MAX_SECONDS, len(record["zh"]) * 0.18 + 0.6))
        if end <= start:
            end = start + MIN_SECONDS
        end = min(end, start + MAX_SECONDS)
        end = max(end, start + MIN_SECONDS)
        previous_end = end
        cues.append({"start": round(start, 2), "end": round(end, 2),
                     "zh": record["zh"], "ja": record["ja"]})
    return cues


def read_session(path):
    """读记录文件，返回 {"cues", "started", "last", "count"}。

    文件不存在、读不了、没有整句时都返回空结果，不抛异常 —— 调用方要负责告诉
    使用者「没找到这一场」。文件正在被另一份实时字幕写入时读取是安全的（共享读）。
    """
    result = {"cues": [], "started": "", "last": "", "count": 0}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result
    lines = text.splitlines()
    records = parse_session(text)
    result["cues"] = cues_from_records(records)
    result["count"] = len(result["cues"])
    start_index = _session_start(lines)
    for line in reversed(lines[:max(0, start_index)]):
        match = _SESSION_LABEL.match(line)
        if match:
            result["started"] = match.group(1)
            break
    for line in reversed(lines):
        match = _RECORD_RE.match(line)
        if match:
            result["last"] = "%s-%s %s:%s:%s" % match.groups()[:5]
            break
    return result
