"""安装根目录的唯一来源。

安装布局是 <根目录>\\Easy\\app-<版本>\\*.py，所以根目录可以直接由本文件的位置
推出来（Easy 的上一级）——不需要在任何地方留“指针文件”，C 盘也就不用再存东西。

开发、测试、或旧版直接运行 payload 时不在这种布局里，回退到
%LOCALAPPDATA%\\SubtitleStudio，与历史行为完全一致。

环境变量 SUBTITLE_STUDIO_ROOT 可强制指定，用于迁移、测试与排障。
"""
import os
from pathlib import Path


def data_root(anchor=None):
    """返回安装根目录（程序、模型、显卡组件、设置都在它下面）。

    anchor 只给测试用：传入某个模块文件的路径来模拟安装布局，不传就用本文件的位置。
    """
    override = os.environ.get("SUBTITLE_STUDIO_ROOT")
    if override:
        return Path(override)
    here = Path(anchor) if anchor is not None else Path(__file__).resolve()
    if len(here.parents) >= 3 and here.parents[0].name.startswith("app-") and here.parents[1].name == "Easy":
        return here.parents[2]
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "SubtitleStudio"
