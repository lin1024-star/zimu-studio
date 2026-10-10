"""报错信息的统一格式 —— 「我实际看到的是」那一段是灵魂。

## 这套格式长什么样

```
<一句话：出了什么事>

我实际看到的是：
　· <我检查的东西> —— <实际是什么状态>
　· <我检查的东西> —— <实际是什么状态>

你可以这样办：
　1) <具体到点哪里、选什么>
　2) ...

（还不行：把上面这一整段发给开发者。）
```

## 为什么值得统一

实测对比过两种写法的效果：

| 写法 | 用户要做什么 | 开发者能拿到什么 |
|---|---|---|
| **「失败了，请检查网络」** | 只能回一句"检查了，没用" | 什么都拿不到，来回问三五轮 |
| **这套格式** | **截一张图** | 直接看到现场，一眼定位 |

真实案例：朋友的机器上报「找不到识别模型」。
旧写法只会说"请检查模型是否安装"，我们只能猜；
新写法列出了「models\\prepared\\ —— 这个目录不存在」「models\\downloaded\\ —— 里面有 small、tiny」，
一眼就看出**他把模型下到了另一个目录**。

## 三条硬规矩

1. **「实际看到的是」不能省。** 哪怕是"我检查了 X，X 不存在"也比不说强。
2. **状态要区分「不存在」「空的」「里面有 abc」**——三种情况的解决办法完全不同。
3. **「怎么办」要具体到能照着点**：「点安装器的一键安装/修复，模式选 turbo」，
   而不是「请检查配置」。
"""
import os


def check_line(item, state):
    """一行「检查项 —— 状态」。"""
    return f"　· {item} —— {state}"


def describe_path(path):
    """把一个目录/文件说清楚：不存在？空的？里面有啥？

    这是整套格式里最有用的一句 —— 光说「找不到」没用，
    要说清是「目录不存在」还是「目录在但是空的」，二者解决办法不一样。
    """
    path = str(path)
    if not os.path.exists(path):
        return f"{path}（这个路径不存在）"
    if os.path.isfile(path):
        size = os.path.getsize(path)
        return f"{path}（文件在，{size} 字节）"
    try:
        names = sorted(os.listdir(path))
    except OSError as exc:
        return f"{path}（打不开：{type(exc).__name__}）"
    if not names:
        return f"{path}（目录在，但是空的）"
    shown = "、".join(names[:8])
    more = f" 等 {len(names)} 项" if len(names) > 8 else ""
    return f"{path}（里面有：{shown}{more}）"


def diagnose(headline, checks=(), steps=(), tail=True, extra=None):
    """拼出统一格式的报错文本。

    headline : 一句话说清出了什么事
    checks   : [(检查项, 实际状态), ...] —— 也可以直接给字符串
    steps    : ["1) ...", ...] 或者 ["...", "..."]（会自动编号）
    tail     : 要不要加"还不行就发给开发者"那行
    extra    : 额外的收尾说明（比如"已完成的翻译不受影响"）
    """
    parts = [str(headline).rstrip()]
    if checks:
        parts.append("")
        parts.append("我实际看到的是：")
        for item in checks:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                parts.append(check_line(item[0], item[1]))
            else:
                parts.append(f"　· {item}")
    if steps:
        parts.append("")
        parts.append("你可以这样办：")
        for i, step in enumerate(steps, 1):
            text = str(step).strip()
            # 已经自己编好号的就别再加一遍
            if text[:2] in ("1)", "2)", "3)", "4)", "5)", "1．", "2．", "3．"):
                parts.append(f"　{text}")
            else:
                parts.append(f"　{i}) {text}")
    if extra:
        parts.append("")
        parts.append(str(extra).rstrip())
    if tail:
        parts.append("")
        parts.append("（还不行：把上面这一整段发给开发者。）")
    return "\n".join(parts)
