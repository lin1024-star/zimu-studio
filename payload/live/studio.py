"""找到字幕工坊的安装位置 + 启动前体检。

打包出去之后不能写死盘符和版本号 —— 别人可能装在别的盘、别的版本。
根目录按这个顺序找：
  1. 环境变量 SUBTITLE_STUDIO_ROOT
  2. 注册表 HKCU\\Software\\SubtitleStudio 的 Root（安装器写的）
  3. D:\\字幕工坊
  4. %LOCALAPPDATA%\\SubtitleStudio（默认安装位置）
判断标准是「那个目录里有 settings.json」。
"""
import os
import re
from pathlib import Path

PREFERRED_MODEL = "turbo"
# 字幕工坊能准备的模型。实测 turbo 是速度/质量最平衡的，直播就靠它。
MODEL_ORDER = ("turbo", "small", "medium", "large-v3", "base", "tiny")


def _looks_right(p):
    return bool(p) and (Path(p) / "settings.json").is_file()


def find_root():
    env = os.environ.get("SUBTITLE_STUDIO_ROOT")
    if _looks_right(env):
        return Path(env)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\SubtitleStudio") as key:
            value = winreg.QueryValueEx(key, "Root")[0]
        if _looks_right(value):
            return Path(value)
    except (OSError, ImportError):
        pass
    for cand in (Path(r"D:\字幕工坊"),
                 Path(os.environ.get("LOCALAPPDATA", "")) / "SubtitleStudio"):
        if _looks_right(cand):
            return cand
    return Path(r"D:\字幕工坊")     # 找不到也给个位置，报错信息里能看出期望在哪


def python_exe(root=None):
    """字幕工坊自带的 Python（带 tkinter、faster-whisper、onnxruntime）。"""
    root = find_root() if root is None else Path(root)
    return root / "Easy" / "python-3.13.15" / "python.exe"


def _version_key(path):
    """app-2.0 / app-2.10 这类目录名按数字段排序（2.10 要大于 2.9）。"""
    return [int(x) for x in re.findall(r"\d+", path.name)] or [0]


def app_dir(root=None):
    """找到 Easy\\app-<版本> 程序目录。

    **绝不能写死版本号。** 原来写死成 app-2.0，升级到 2.1 之后目录改名，
    朋友的机器上就直接 ModuleNotFoundError: No module named 'winsecret'。
    现在改成：先挑「真的有 winsecret.py」的，再按版本号从高到低取一个。
    """
    root = find_root() if root is None else Path(root)
    easy = root / "Easy"
    dirs = [d for d in easy.glob("app-*") if d.is_dir()]
    if not dirs:
        return easy / "app"
    usable = [d for d in dirs if (d / "winsecret.py").is_file()]
    return max(usable or dirs, key=_version_key)


def model_dir(name=PREFERRED_MODEL, root=None):
    root = find_root() if root is None else Path(root)
    return root / "models" / "prepared" / name


def find_model(root=None):
    """找本机现成的识别模型。

    返回 (路径, 模型名, 是不是首选的 turbo)；一个都没有时路径为 None。

    为什么要有这个：实测朋友的机器上字幕工坊装了、但没下 turbo 模型
    （装的时候选了 small 或「仅字幕」），结果整个实时字幕打不开。
    其实有别的模型也能跑，只是质量差一些——能跑总比打不开强。
    """
    root = find_root() if root is None else Path(root)
    for name in MODEL_ORDER:
        for base in ("prepared", "downloaded"):
            path = root / "models" / base / name
            if path.is_dir() and any(path.iterdir()):
                return path, name, name == PREFERRED_MODEL
    return None, "", False


def lib_dirs(package_root):
    """随包带的第三方库（soundcard 等）。打包版放 lib/，开发时在 _live/lib/。"""
    return [p for p in (Path(package_root) / "lib", Path(package_root) / "_live" / "lib")
            if p.is_dir()]


def scan_models(root=None):
    """看看两个模型目录里到底有什么——用来把报错信息说到点子上。

    返回 {"prepared": [...模型名], "downloaded": [...]}；
    目录不存在时值是 None（和"空的"要区分开，两种情况的解决办法不一样）。
    """
    root = find_root() if root is None else Path(root)
    found = {}
    for base in ("prepared", "downloaded"):
        d = root / "models" / base
        if d.is_dir():
            found[base] = sorted(p.name for p in d.iterdir() if p.is_dir())
        else:
            found[base] = None
    return found


def _model_report(root):
    """把「模型到底在哪、看到了什么」写成几句人话。"""
    lines = []
    for base, names in scan_models(root).items():
        where = f"{root}\\models\\{base}\\"
        if names is None:
            lines.append(f"　　{where} —— 这个目录不存在")
        elif not names:
            lines.append(f"　　{where} —— 空的")
        else:
            lines.append(f"　　{where} —— 里面有：{'、'.join(names)}")
    return lines


def preflight(root=None, package_root=None):
    """启动前体检。

    返回一串「缺什么」的中文说明；空列表表示一切就绪。
    目的是：缺东西的时候给人一句能看懂的话，而不是甩一段 traceback。

    实测教训（两次）：
      ① 朋友把懒人版解压到别的盘，那台机器上没装字幕工坊，
         程序直接崩在 ModuleNotFoundError: winsecret 上，他不知道该怎么办。
      ② 另一位朋友的机器上字幕工坊装了、但没下 turbo 模型，
         报错只说「找不到识别模型」，没说清该怎么办。
    """
    root = find_root() if root is None else Path(root)
    problems = []
    if not (root / "settings.json").is_file():
        problems.append(f"· 找不到字幕工坊的设置文件：{root}\\settings.json")
    app = app_dir(root)
    for name in ("core.py", "winsecret.py"):
        if not (app / name).is_file():
            problems.append(f"· 找不到 {name}：{app}")
    if package_root is not None:
        # 只有打包版（懒人版/安装版）才检查随包的库；开发时在 _live/lib 下
        if not [p for p in lib_dirs(package_root) if (p / "soundcard").is_dir()]:
            problems.append("· 随包带的抓声音组件 lib\\soundcard 不见了，请重新解压一次压缩包")
    if find_model(root)[0] is None:
        problems.append("· 一个识别模型都没有。实时字幕需要 turbo 模型"
                        "（没有 turbo 时 small 等也能先顶着用）。\n"
                        "　实测看到的是：\n" + "\n".join(_model_report(root)) + "\n"
                        "　怎么办：打开字幕工坊，点「一键安装 / 修复」选 turbo 模式；"
                        "或在字幕工坊里把 turbo 模型下载下来（下到 "
                        f"{root}\\models\\downloaded\\turbo\\ 也算）。")
    if problems:
        problems.insert(0, "这个程序需要先装好「字幕工坊」才能用——识别模型、显卡组件、"
                           "术语表、API 密钥都在那边。\n\n当前找到的字幕工坊位置：\n" + str(root))
        problems.append("\n怎么办：先安装（或修复）字幕工坊，装完再打开本程序。\n"
                        "如果已经装好了，请把上面那个位置告诉开发者。")
    return problems
