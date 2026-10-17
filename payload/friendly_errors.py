"""把「技术报错」翻译成「下一步该干什么」的中文。

## 为什么要这个（2026-10-18）

对照朋友那个 B 站音频下载器看出来的差距：他的报错是一句话 + 具体下一步
（"站拒绝了请求 → 请稍后重试 → 如果仍然失败就在设置里选已登录的浏览器"），
我们这边有些路径还是「处理失败（TimeoutError）。请检查文件权限、磁盘空间或重新打开项目后再试。」
—— 把三种完全不同的原因塞进一句话，等于什么都没说。

所以加这一层：**异常进来，人话出去**。分两层：

- `explain(exc)`  完整版（标题 + 我实际看到的是 + 你可以这样办），给弹窗用
- `hint(exc)`     一行版，给实时字幕那种小地方用

格式沿用 `errfmt`（那份模块写了格式的三条硬规矩）。

## 设计上的两条线，别越界

1. **不猜原因。** 拿不准就归到「程序内部出错」，让他导诊断包，而不是编一个"可能网络不好"。
2. **不带秘密、不带原始 traceback。** 异常文本里可能混着请求头或密钥，
   出去之前先脱敏（`sk-...`、长令牌一律替换）。
"""
import re

import errfmt

# 长短两条都要有，界面自己挑
MAX_TEXT = 1200

_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,}|"
                     r"[A-Fa-f0-9]{32,})")


def redact(text):
    """把可能混进来的密钥/令牌抹掉。"""
    return _SECRET.sub("（已隐去）", str(text))


def _status_of(exc):
    """从异常里抠 HTTP 状态码：有的异常自己带 code/status，有的只在文字里。"""
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    found = re.search(r"\b(401|402|403|404|408|429|5\d\d)\b", str(exc))
    return int(found.group(1)) if found else None


def _classify(exc):
    """判断这是哪一类问题。顺序有讲究：越具体的放前面。"""
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()
    status = _status_of(exc)
    errno = getattr(exc, "errno", None)
    winerror = getattr(exc, "winerror", None)

    if status in (401, 403) or "api key" in low or ("密钥" in text and "无效" in text):
        return "api_key"
    if status == 402 or "余额不足" in text or "insufficient" in low:
        return "balance"
    if status == 429 or "频繁" in text or "rate limit" in low:
        return "rate_limit"
    if status and status >= 500:
        return "server_down"
    if isinstance(exc, UnicodeDecodeError) or isinstance(exc, UnicodeEncodeError):
        return "encoding"
    # 显卡放内存前面：CUDA out of memory 是「显存不够」，不是系统内存不够
    if "cuda" in low or "cudnn" in low or "ctranslate" in low or "显卡" in text:
        return "gpu"
    if isinstance(exc, MemoryError) or "内存不足" in text or "out of memory" in low:
        return "memory"
    # 文件被占用要排在 permission 前面：Windows 上这两种都是 PermissionError(13)
    if winerror in (32, 33) or "正被" in text or "另一个程序" in text:
        return "file_busy"
    if isinstance(exc, PermissionError) or winerror == 5 or errno == 13:
        return "permission"
    if errno == 28 or winerror == 112 or "磁盘空间" in text or "no space" in low:
        return "disk_full"
    if isinstance(exc, FileNotFoundError) or errno == 2:
        return "not_found"
    if isinstance(exc, ConnectionError) or isinstance(exc, TimeoutError):
        return "network"
    if winerror in (10060, 10054, 10061, 11001) or errno in (10060, 10054, 10061, 11001):
        return "network"
    if "ssl" in low or "certificate" in low:
        return "network"
    if isinstance(exc, OSError) and errno in (11001, 111):
        return "network"
    if "ffmpeg" in low or "解码" in text or "decode" in low:
        return "media"
    if isinstance(exc, (KeyError, IndexError, AttributeError, TypeError, ValueError)):
        return "internal"
    if isinstance(exc, OSError):
        return "file_io"
    return "unknown"


# 每一类：headline + steps。checks 里能加"我实际看到的是"。
PLANS = {
    "api_key": ("DeepSeek 说这个 API 密钥不对。",
                ["打开「翻译与识别设置」，把密钥重新粘一遍（前后不要带空格）",
                 "密钥要在 platform.deepseek.com 的「API keys」里，点一下复制出来",
                 "还不行就再建一条新密钥换上去试"]),
    "balance": ("DeepSeek 账户余额不够，翻译没法继续。",
                ["打开 platform.deepseek.com，看「用量」和余额",
                 "充值后再点「识别并翻译 / 继续」——已经从上次失败的地方接着来",
                 "已经翻译好的部分存在项目里，不会重复花钱"]),
    "rate_limit": ("DeepSeek 说请求太密了，先缓一下。",
                   ["等 1～2 分钟再点「识别并翻译 / 继续」",
                    "别连着点，连着点只会更慢",
                    "已翻译好的部分会保留，不会重复花钱"]),
    "server_down": ("DeepSeek 那边服务器出问题了（不是你的错）。",
                    ["等几分钟再点「识别并翻译 / 继续」",
                     "如果一直这样，去 platform.deepseek.com 看有没有公告",
                     "已翻译好的部分会保留"]),
    "network": ("网络这边没连上 DeepSeek。",
                ["先确认浏览器能打开 platform.deepseek.com",
                 "如果你开着代理或 VPN：先关掉，或改成「全局」再试",
                 "等几分钟再点继续——已翻译好的部分不会重复花钱"]),
    "permission": ("没有权限动这个文件或文件夹。",
                   ["把输出目录换到自己新建的文件夹（比如 D 盘或 E 盘上的一个文件夹）",
                    "不要放在系统盘的程序目录里，也不要放在下载目录的临时位置",
                    "如果这个文件正被别的程序打开（播放器、Premiere、预览窗格），先关掉再试"]),
    "file_busy": ("这个文件正被别的程序占用。",
                  ["关掉可能打开它的程序：Premiere、播放器、资源管理器预览窗格",
                   "关掉后再试一次",
                   "或者把输出目录换成另一个文件夹"]),
    "disk_full": ("磁盘满了，文件写不进去。",
                  ["清理一下空间：至少留 2 GB",
                   "把输出目录换到空间大的盘（D 盘）",
                   "旧项目里 exports 目录下不开心的中间文件可以删"]),
    "not_found": ("要用的文件不在了。",
                  ["重新选一次素材或项目文件",
                   "如果刚把它移动到别处，先移回去，或者重新打开新位置",
                   "导出前确认输出目录没有被手动改过"]),
    "encoding": ("这个字幕文件的编码不对，中文读不出来。",
                 ["用记事本打开它，「另存为」时把编码选成 UTF-8，再导入",
                  "不要用 ANSI / GBK 保存日文或中文",
                  "如果文件本来就不是这里导出的，先确认它是 UTF-8"]),
    "memory": ("内存不够了。",
               ["关掉别的程序再试（浏览器开一堆标签最占内存）",
                "识别模型换成 small（快、省内存，质量够用）",
                "素材很长的话，可以分成几段处理"]),
    "gpu": ("显卡组件没准备好，这次没法用它。",
            ["先在「翻译与识别设置」里把设备改成 CPU，照样能识别（慢一点）",
             "想用显卡就点「启用显卡加速」把组件重新下一次",
             "4 GB 显存的老显卡用 turbo 或 small 更稳"]),
    "media": ("这个素材解不开（可能加密、损坏，或格式太新）。",
              ["先确认素材本身能正常播放",
               "可以先用别的工具转成 mp4 或 wav 再试",
               "也可以先点「仅识别原文」看看是不是同样失败"]),
    "file_io": ("读写这个文件时出错了。",
                ["换一个输出目录试试（比如 D 盘）",
                 "确认文件没有被别的程序占用",
                 "还不行就导出诊断包发来"]),
    "internal": ("程序内部出了个错（不是你操作的问题）。",
                 ["点「诊断与资源 → 导出诊断包」，把 ZIP 发来",
                  "顺便说一下最后点的是哪个按钮",
                  "已经识别/翻译的字幕都保存在项目里，不会丢"]),
    "unknown": ("处理的时候出错了。",
                ["先重试一次；如果是偶发问题，第二次可能就过去了",
                 "还不行就点「诊断与资源 → 导出诊断包」，把 ZIP 发来",
                 "已经完成的部分都保存在项目里，不会丢"]),
}


def _headline_steps(key):
    return PLANS.get(key, PLANS["unknown"])


def _technical_checks(exc, context=None, include_message=True):
    """「我实际看到的是」——给能核对、且不含秘密的那几行。

    include_message=False 时**不把异常原文带出去**：后台任务那条路上，异常消息里
    可能夹着请求内容或字幕原文（`test_diagnostics` 专门守这条），只留类型/状态码/错误号。
    """
    checks = []
    if context:
        checks.append(("这件事发生在", context))
    name = type(exc).__name__
    status = _status_of(exc)
    if status:
        checks.append(("对方返回的状态码", str(status)))
    path = getattr(exc, "filename", None)
    if path:
        checks.append(("涉及的文件", errfmt.describe_path(path)))
    winerror = getattr(exc, "winerror", None)
    if winerror:
        checks.append(("Windows 错误号", str(winerror)))
    elif getattr(exc, "errno", None):
        checks.append(("系统错误号", str(exc.errno)))
    message = redact(str(exc)).strip().replace("\n", " ")
    if include_message and message:
        checks.append(("程序给的原话", message[:200]))
    if not checks:
        checks.append(("异常类型", name))
    return checks


def explain(exc, context=None, include_message=True):
    """完整版报错（弹窗用）：标题 + 我实际看到的是 + 你可以这样办。

    - 已经是给人看的中文异常（UserError 及其子类）就原样返回，不重复包装。
    - include_message=False：不把异常原文带出去（后台任务用，防夹带字幕/请求内容）。
    """
    text = redact(str(exc)).strip()
    if type(exc).__name__ == "Cancelled":
        return "任务已停止。已完成的识别结果和翻译批次都保留，可以随时接着做。"
    if type(exc).__name__ in ("UserError", "ResponseError"):
        return text if text else "处理没有完成。"
    key = _classify(exc)
    headline, steps = _headline_steps(key)
    return errfmt.diagnose(headline,
                           checks=_technical_checks(exc, context, include_message),
                           steps=steps)


def hint(exc_or_text):
    """一行版（状态栏、实时字幕那种小地方用）：一句话说清是什么问题。"""
    if not isinstance(exc_or_text, BaseException):
        text = str(exc_or_text or "").strip()
        return text[:60] if text else "出错了"
    type_name = type(exc_or_text).__name__
    if type_name in ("UserError", "ResponseError"):
        return redact(str(exc_or_text)).strip()[:60] or "处理没有完成"
    if type_name == "Cancelled":
        return "已停止"
    headline, _ = _headline_steps(_classify(exc_or_text))
    return headline.rstrip("。")


def brief(exc):
    """短版（小弹窗用）：一句话 + 一句「怎么办」+ 一句「还不行怎么办」。

    ## 为什么要短版（2026-10-18，学朋友那个下载器）

    他的报错观感是「站拒绝了请求 → 请稍后重试 → 如果仍然失败，就在设置里选已登录的浏览器」，
    两三行，看完就知道干什么。我们的完整版（标题 + 我实际看到的是 + 你可以这样办）信息全，
    但**弹在小窗口里像一篇说明书**；日常那些"原因清楚、动作单一"的错不需要那么多。

    所以分工：
    - `brief()`   小弹窗：打开 / 还原 / 保存 / 启动实时字幕 / 显卡组件…
    - `explain()` 处理任务失败那种大弹窗：可能要看现场，「我实际看到的是」不能丢
    """
    text = redact(str(exc)).strip()
    type_name = type(exc).__name__
    if type_name == "Cancelled":
        return "任务已停止，已完成的部分都保留。"
    if type_name in ("UserError", "ResponseError"):
        return text if text else "处理没有完成。"
    headline, steps = _headline_steps(_classify(exc))
    lines = [headline]
    first_two = [step.rstrip("。") for step in steps[:2]]
    if first_two:
        lines.append("你可以这样办：" + "；".join(first_two) + "。")
    lines.append("还不行：点「诊断与资源 → 导出诊断包」，把 ZIP 发来。")
    return "\n".join(lines)
