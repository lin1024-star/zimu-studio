# -*- coding: utf-8 -*-
"""悬浮字幕窗的尺寸：窗口永远不许矮到装不下内容。

为什么加这一条（2026-10-16 逐档实测出来的）：
窗口高度原来只按字号等比算（`250 × 字号 ÷ 20`），**完全没管底下还有一行状态栏**。
把真实的 Overlay 建出来（挪到屏幕外 x=-4000 量，屏幕上不闪任何东西）逐档数像素，
32 个「字号 × 宽度」组合里有 7 个装不下：

    字号 12（任意宽度）、字号 14 / 15 / 40 配最窄宽度 600

被挤掉的**不是字幕** —— 三行字幕在全部组合里都没被切 ✓ ——
而是最底下那行状态提示，只剩 1~6 像素（「正在启动…」「已暂停」就看不见了）。

这一并更正了我原来的说法：我原先以为「手调高度会把第三行切掉」，
实测不成立。所以这次不是加高度滑杆，而是把高度公式修成「不许小于内容需要的高度」。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

# 必须在 import 之前设：免得测试碰到使用者的真实数据根目录
os.environ.setdefault("SUBTITLE_STUDIO_ROOT", tempfile.mkdtemp(prefix="overlay-size-test-"))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "payload"))

import tkinter as tk                                          # noqa: E402
from live.overlay import (FONT_MAX, FONT_MIN, Overlay,        # noqa: E402
                          WIDTH_MIN, window_height)

ZH = "对啊，你看这个。等一下，我现在就说明，说完再问你一句行不行"
JA = "そうだよ、これ見てよ。ちょっと待って、今から説明するからね"

# 改动前实测**装不下**的那 7 个组合，一个都不能漏
WAS_BROKEN = [(12, 600), (12, 800), (12, 1100), (12, 1920),
              (14, 600), (15, 600), (40, 600)]


def make(font, width):
    """建一个不显示的字幕窗（屏幕上不会闪）。"""
    return Overlay(visible=False, appearance={"font": font, "width": width, "alpha": 0.93})


class WindowFitsContentTest(unittest.TestCase):
    def test_the_window_is_never_shorter_than_its_content(self):
        """每一个字号档都不能矮到装不下「工具条 + 三行 + 状态栏」。"""
        for font in range(FONT_MIN, FONT_MAX + 1):
            for width in (WIDTH_MIN, 1100):
                ov = make(font, width)
                try:
                    self.assertGreaterEqual(
                        ov._height, ov._min_height(),
                        f"字号 {font} + 宽 {width}：窗口 {ov._height} 比内容 "
                        f"{ov._min_height()} 还矮 —— 会有东西被挤掉")
                finally:
                    ov.root.destroy()

    def test_the_default_look_does_not_change(self):
        """字号 20 + 宽 1100 必须还是设计高度 —— 使用者已经看惯的样子不许动。"""
        ov = make(20, 1100)
        try:
            self.assertEqual(ov._height, window_height(20))
            self.assertEqual(window_height(20), 250)
        finally:
            ov.root.destroy()

    def test_the_smallest_fonts_get_lifted(self):
        """字号 12 那一档原来只有 150，装不下 —— 现在必须被撑高。"""
        for font in (12, 13, 14):
            ov = make(font, 1100)
            try:
                self.assertGreater(ov._height, window_height(font),
                                   f"字号 {font} 还是设计高度 {window_height(font)}，没撑起来")
            finally:
                ov.root.destroy()

    def test_the_status_line_reserves_its_space_first(self):
        """状态栏必须先于内容区分配空间。

        pack 按调用顺序分配：谁后打包谁先被挤掉。状态栏原来最后打包，
        所以窗口一不够高，第一个消失的就是它。
        """
        ov = make(20, 1100)
        try:
            order = ov.root.pack_slaves()
            self.assertLess(order.index(ov.status), order.index(ov.lines_box),
                            "状态栏又排到内容区后面了 —— 一挤就会先消失")
        finally:
            ov.root.destroy()

    def test_nothing_is_clipped_in_the_real_layout(self):
        """真排一遍：把这 7 个原本装不下的组合 + 默认档放出来量像素。

        这里必须让窗口真正排布（`deiconify`）才量得准 —— withdraw 的窗口
        `winfo_height()` 只报 1。所以把它放到 x=-4000（可视桌面之外），
        使用者屏幕上不会出现任何东西。
        """
        for font, width in WAS_BROKEN + [(20, 1100), (16, 600), (40, 1920)]:
            with self.subTest(font=font, width=width):
                ov = make(font, width)
                try:
                    ov.lines[0]["label"].configure(text=ZH)
                    ov.lines[1]["label"].configure(text=JA)
                    try:
                        ov.root.geometry(f"{width}x{ov._height}-4000+100")
                        ov.root.deiconify()
                        ov.root.update()
                    except tk.TclError as exc:          # 没有显示环境就跑不了这一条
                        self.skipTest(f"这台机器没法真实排布窗口：{exc}")
                    top = ov.root.winfo_rooty()
                    win_h = ov.root.winfo_height()
                    for index, line in enumerate(ov.lines):
                        bottom = line["row"].winfo_rooty() - top + line["row"].winfo_height()
                        self.assertLessEqual(bottom, win_h, f"第 {index} 行被切了")
                    status_h = ov.status.winfo_height()
                    self.assertGreater(status_h, 6,
                                       f"状态栏只剩 {status_h} 像素 —— 提示看不见了")
                finally:
                    ov.root.destroy()

    def test_the_fit_is_recomputed_when_the_appearance_changes(self):
        """拖滑杆改字号之后要重新算高度，不能只在开窗时算一次。"""
        ov = make(20, 1100)
        try:
            ov._preview_appearance({"font": 12, "width": 1100, "alpha": 0.93})
            self.assertGreaterEqual(ov._height, ov._min_height())
            self.assertGreater(ov._height, window_height(12))
            ov._preview_appearance({"font": 20, "width": 1100, "alpha": 0.93})
            self.assertEqual(ov._height, window_height(20))
        finally:
            ov.root.destroy()


if __name__ == "__main__":
    unittest.main()
