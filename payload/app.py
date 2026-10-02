"""Windows-oriented desktop app. No local server and no API key written to disk."""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from core import (APP_VERSION, DEFAULT_MODEL, Cue, UserError, atomic_write, clean,
                  export_files, import_srt_project, insert_project_cue, load_project,
                  parse_time, read_srt, save_duration_change, save_project, set_project_duration,
                  store_imported_project, timestamp, worker)
from diagnostics import Diagnostics, error_info

APP_DIR = Path(__file__).resolve().parent
USER_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "SubtitleStudio"
CONFIG_PATH = USER_DIR / "settings.json"
FONT_FAMILY = "Microsoft YaHei UI" if sys.platform == "win32" else "Noto Sans CJK SC"
BG, PANEL, INK, MUTED, ACCENT = "#edf2f6", "#ffffff", "#182d41", "#5f7385", "#137b83"

HELP = """快速使用

1. 新视频可选择本地识别；已有字幕请点击「打开项目 / SRT…」，立即进入校对，不需要 API 密钥。
2. SRT 可选原文、中文、原文＋中文两份，或原文在上的混合版。两份字幕按相同起止时间配对；混合版超过两行时需确认语言分界。
3. 选择原视频语言。英语或日语最好手动指定；未知时可以自动识别。
4. 首次建议点击「仅识别原文」。完成后点选字幕条目，在下方校对原文、人名和术语，点击「保存此条修改」。
5. 点击「识别并翻译 / 继续」。已有原文不会重复识别；缺少的中文会通过 DeepSeek API 翻译。
6. 完成后自动导出六份文件：原文、中文、混合版各一份 SRT 和 TXT。混合版原文在上、中文在下，逐条对应。点击「打开输出文件夹」。
7. 点击「新增字幕…」，填写开始、结束时间和原文，也可手填中文。保存后自动按时间插入；继续翻译只补译中文为空的条目。
8. 只有中文字幕时，可直接修改左侧正文并导出中文 SRT / TXT 两份。要生成原文和混合版，还需要对应原文。
9. 只有需要翻译缺失条目时才填写 DeepSeek API 密钥。密钥只在本次运行内使用，关闭后清除，不放进项目或导出文件。

字幕持续时间

选中字幕后，点击「时长设置…」或按 F4，填写持续秒数（如 2.5），选择「当前字幕」或「全部字幕」，再应用并保存。
每条开始时间保留，结束时间按持续秒数计算，支持毫秒精度。
默认「遇到下一条开始就结束」：如果设定时长跨过下一条开始点，会提前结束；取消后严格使用完整时长，可能重叠。
同时开始的字幕保留同时显示，不会被截成零时长。调整一条时不会移动其他字幕；最后一条使用完整时长。
应用前可查看影响条数及实际时间。每次有效调整都先在项目旁保存 project_before_timing_日期_编号.json；需要恢复时，用「打开项目 / SRT…」打开该备份。
调时保留原文和译文，不需要识别或翻译，也不消耗 API tokens。已有 SRT 可先导入，再设置时长。

PR 剪辑

在 Premiere Pro 中导入所需的 .srt，再放到与原素材起点对齐的位置。
字幕时间以整个输入视频的开头为零点，不能自动跟随你在 PR 中已经剪切、变速的片段。
需要单语时选择原文或中文 SRT；需要双语时选择混合 SRT，两种语言共用每条字幕的时间轴。
TXT 是文稿，没有供 PR 同步的时间轴。要修改字幕样式，请在 PR 中操作。

识别模型

small：默认，适合普通电脑试用。medium / large-v3：适合更重视准确度的素材，耗时和内存更多。
turbo：较大的多语言模型，适合设备较好的电脑。最终成片仍需人工校对。
CPU 模式可直接使用。NVIDIA 显卡请先点击“启用显卡加速”：首次自动下载约 570 MB 官方组件，之后无需重复下载。
显卡默认使用 4 GB 省显存模式；显存不足或显卡不可用时自动改用 CPU。本程序不会安装整套 CUDA 开发工具。
首次使用某个模型需从 Hugging Face 下载。可以在设置中选择已下载完整的 faster-whisper / CTranslate2 模型文件夹。
语音识别在电脑上运行。翻译时只向官方 api.deepseek.com 发送原文、相邻上下文、部分已译内容及术语说明。
视频默认读取第一条音轨。需要其他音轨时，先在剪辑软件中导出对应音频，再导入本程序。

项目和恢复

项目保存在输出目录的素材子文件夹，包含 project.json。每次导出都有新的时间文件夹，不覆盖已有交付。
已有项目可点击「打开项目 / SRT…」选择 project.json，再点「导出六份文件」。此操作复用已有译文，无需 API 密钥，也不会重新识别或调用翻译 API。
导入 SRT 会在输出目录建立独立的 project.json，不覆盖原始 SRT。下次打开该项目，可以保留所有校对结果。
如果一整套原文、中文和混合 SRT 都还在，优先同时导入原文和中文两份；打开混合 SRT 时，只在确认内容一致后自动使用同目录配套文件。
识别完成后立即保存原文；翻译每完成一批就保存。翻译中断后可打开 project.json 继续。
识别阶段停止会丢弃尚未完成的识别，下次从头识别；模型已下载的内容仍可复用。
修改文字和时间保留当前填写的译文；如果原文含义有变，请同步校对中文，或清空该条中文后继续补译。
修改翻译模型或术语表只影响缺失的译文，不会自动重译已完成内容。
「重新翻译全部」会重新调用 API；普通继续操作只翻译缺失条目。

费用与准确度

本地识别没有按次 API 费用；首次需下载模型。DeepSeek 翻译按其 API 用量收费。
自动识别和翻译可能听错、漏字或断句不理想。发布前请核对人名、数字、术语与字幕同步。
当服务返回缺行、重复序号或截断结果时，程序会缩小批次重试，仍失败则停止，不把空译文当作完成。
网络超时重试可能重复产生调用费用，费用请查看 DeepSeek 后台。

日志与售后

从本版本启动后自动保存本机诊断日志。任务运行时约每 5 秒记录 CPU、系统内存、程序工作集和 NVIDIA 显卡数据。
在「诊断与资源」查看当前情况，或点击右上角「导出诊断包」保存 ZIP。无需停止正在运行的任务。
如遇崩溃，可重新打开软件再导出：包内包含本次及最近两次启动的记录。旧版运行情况无法补录。
诊断包记录版本、阶段、模型实际设备、错误类别与代码位置；不含密钥、字幕正文、术语表、项目或完整文件路径，也不会自动上传。
驱动不提供的指标会显示「未取得」，不代表 0；整卡显存包含其他程序，程序工作集合计可能重复共享页。
日志自动轮换并清理较旧记录。诊断功能只记录情况，不改变识别精度，也不会自动降低 CPU 或内存占用。
"""


class SrtImportDialog(tk.Toplevel):
    MODES = {"原文 SRT": "source", "中文字幕 SRT": "chinese",
             "原文 + 中文两个 SRT": "paired", "混合 SRT（原文在上）": "bilingual"}
    ENCODINGS = {"自动": "auto", "UTF-8": "utf-8-sig", "UTF-16": "utf-16",
                 "中文 GB18030 / GBK": "gb18030", "日文 Shift-JIS": "cp932"}

    def __init__(self, parent, path):
        super().__init__(parent)
        self.title("打开已有字幕 · 预览后导入校对")
        self.geometry("950x680")
        self.minsize(860, 640)
        self.transient(parent)
        self.result = None
        self.project = None
        self.raw_cues = []
        self.splits = {}
        self.pending = set()
        self.preview_signature = None
        self.mode = tk.StringVar(value="请选择字幕内容类型")
        self.file = tk.StringVar(value=str(path))
        self.chinese_file = tk.StringVar()
        self.language = tk.StringVar(value="自动判断")
        self.encoding = tk.StringVar(value="自动")
        self.split_var = tk.StringVar(value="1")
        self.hint = tk.StringVar(value="导入、校对、保存、导出都不调用 API；先检查预览中文字是否正常。")
        p = Path(path)
        if "_原文_" in p.stem:
            self.mode.set("原文 SRT")
        elif "_中文_zh" in p.stem:
            self.mode.set("中文字幕 SRT")
        elif "_混合_" in p.stem:
            self.mode.set("混合 SRT（原文在上）")
            prefix, suffix = p.stem.rsplit("_混合_", 1)
            lang = suffix[:-3] if suffix.endswith("_zh") else suffix
            source = p.with_name(prefix + "_原文_" + lang + ".srt")
            chinese = p.with_name(prefix + "_中文_zh.srt")
            if source.is_file() and chinese.is_file():
                try:
                    paired = import_srt_project(source, "paired", chinese_path=chinese)
                    mixed = read_srt(p)
                    matches = len(mixed) == len(paired["cues"]) and all(
                        (c.start, c.end, c.source) == (r["start"], r["end"], clean(r["source"] + " " + r["zh"]))
                        for c, r in zip(mixed, paired["cues"]))
                    if matches:
                        self.file.set(str(source))
                        self.chinese_file.set(str(chinese))
                        self.mode.set("原文 + 中文两个 SRT")
                except (UserError, OSError):
                    pass
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(5, weight=1)
        for r, label, var in [(0, "内容类型", self.mode), (3, "原文语言", self.language)]:
            ttk.Label(body, text=label).grid(row=r, column=0, sticky="w", pady=5, padx=(0, 10))
            values = list(self.MODES) if r == 0 else ["自动判断", "英语", "日语", "中文"]
            combo = ttk.Combobox(body, textvariable=var, values=values, state="readonly", width=30)
            combo.grid(row=r, column=1, sticky="w", pady=5)
            combo.bind("<<ComboboxSelected>>", lambda e: self.preview())
        for r, label, var in [(1, "SRT / 原文文件", self.file), (2, "对应中文文件", self.chinese_file)]:
            ttk.Label(body, text=label).grid(row=r, column=0, sticky="w", pady=5, padx=(0, 10))
            ttk.Entry(body, textvariable=var).grid(row=r, column=1, sticky="ew", pady=5)
            ttk.Button(body, text="选择…", command=lambda v=var: self.browse(v)).grid(row=r, column=2, padx=(8, 0))
        enc = ttk.Combobox(body, textvariable=self.encoding, values=list(self.ENCODINGS), state="readonly", width=23)
        enc.grid(row=3, column=2, sticky="e")
        enc.bind("<<ComboboxSelected>>", lambda e: self.preview())
        ttk.Label(body, textvariable=self.hint, wraplength=850, style="Muted.TLabel").grid(row=4, column=0, columnspan=3, sticky="w", pady=8)
        table = ttk.Frame(body)
        table.grid(row=5, column=0, columnspan=3, sticky="nsew")
        self.tree = ttk.Treeview(table, columns=("id", "source", "zh"), show="headings", selectmode="browse", height=5)
        for key, title, width in [("id", "条目 / 分行", 115), ("source", "原文 / 单语正文", 335), ("zh", "中文译文", 335)]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=80, stretch=key != "id")
        self.tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self.show_lines)
        self.raw_text = ScrolledText(body, height=5, wrap="word", font=(FONT_FAMILY, 10), state="disabled")
        self.raw_text.grid(row=6, column=0, columnspan=3, sticky="ew", pady=8)
        split_row = ttk.Frame(body)
        split_row.grid(row=7, column=0, columnspan=3, sticky="ew")
        ttk.Label(split_row, text="混合字幕：原文占前").pack(side="left")
        self.split_box = ttk.Spinbox(split_row, textvariable=self.split_var, from_=1, to=100, width=4)
        self.split_box.pack(side="left", padx=5)
        ttk.Label(split_row, text="行，余下为中文").pack(side="left")
        self.split_button = ttk.Button(split_row, text="确认此条分行", command=self.confirm_split)
        self.split_button.pack(side="left", padx=10)
        footer = ttk.Frame(body)
        footer.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        ttk.Button(footer, text="刷新预览", command=self.preview).pack(side="left")
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(footer, text="导入并开始校对", command=self.accept, style="Accent.TButton").pack(side="right", padx=10)
        self.preview()
        self.grab_set()

    def signature(self):
        return (self.file.get().strip(), self.chinese_file.get().strip(), self.mode.get(),
                self.language.get(), self.encoding.get())

    def browse(self, var):
        path = filedialog.askopenfilename(parent=self, title="选择已有 SRT", filetypes=[("SRT 字幕", "*.srt")])
        if path:
            var.set(path)
            self.preview()

    def preview(self):
        self.project = None
        self.tree.delete(*self.tree.get_children())
        self.raw_cues, self.splits, self.pending = [], {}, set()
        self.preview_signature = self.signature()
        mixed = self.MODES.get(self.mode.get()) == "bilingual"
        self.split_box.configure(state="normal" if mixed else "disabled")
        self.split_button.configure(state="normal" if mixed else "disabled")
        self.raw_text.configure(state="normal")
        self.raw_text.delete("1.0", "end")
        self.raw_text.configure(state="disabled")
        if self.mode.get() not in self.MODES:
            self.hint.set("请先选择内容类型：原文、已翻译的中文、原文＋中文两份，或混合字幕。这样可复用已有译文。")
            return
        try:
            if mixed:
                self.raw_cues = read_srt(self.file.get().strip(), self.ENCODINGS[self.encoding.get()], preserve_lines=True)
                self.pending = {c.id for c in self.raw_cues if len(c.source.splitlines()) != 2}
                self.splits = {c.id: 1 for c in self.raw_cues}
            self.project = self.read_project()
            for c in self.project["cues"]:
                self.tree.insert("", "end", iid=str(c["id"]), values=self.preview_values(c))
            self.update_hint()
            if self.tree.get_children():
                self.tree.selection_set(self.tree.get_children()[0])
                self.show_lines()
        except (UserError, OSError, ValueError) as exc:
            self.hint.set(str(exc))

    def read_project(self):
        return import_srt_project(self.file.get().strip(), self.MODES[self.mode.get()],
                                  {"英语": "en", "日语": "ja", "中文": "zh"}.get(self.language.get(), ""),
                                  self.chinese_file.get().strip(), self.ENCODINGS[self.encoding.get()], self.splits)

    def preview_values(self, c):
        return (str(c["id"]) + (" · 待确认" if c["id"] in self.pending else ""), c["source"], c["zh"])

    def update_hint(self):
        if self.pending:
            self.hint.set(f"有 {len(self.pending)} 条混合字幕超过两行。请逐条确认原文占几行，避免把换行当成语言分界。")
        elif self.mode.get() == "中文字幕 SRT":
            self.hint.set("只有中文字幕：可直接校对、导出中文 SRT 和 TXT。要恢复双语，请选择原文 + 中文两个 SRT。")
        else:
            self.hint.set(f"已读取 {len(self.project['cues'])} 条。请检查预览；乱码时切换右上方编码。导入不调用 API。")

    def show_lines(self, _event=None):
        selected = self.tree.selection()
        if not selected or not self.project:
            return
        ident = int(selected[0])
        c = self.project["cues"][ident - 1]
        if self.raw_cues:
            lines = self.raw_cues[ident - 1].source.splitlines()
            value = "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1))
            self.split_var.set(str(self.splits.get(ident, 1)))
            self.split_box.configure(to=max(1, len(lines) - 1))
        else:
            value = "正文：" + c["source"] + ("\n中文：" + c["zh"] if c["zh"] else "")
        self.raw_text.configure(state="normal")
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", timestamp(c["start"]) + " → " + timestamp(c["end"]) + "\n" + value)
        self.raw_text.configure(state="disabled")

    def confirm_split(self):
        if self.signature() != self.preview_signature:
            self.preview()
            return
        selected = self.tree.selection()
        if not selected or not self.raw_cues:
            return
        ident = int(selected[0])
        try:
            split = int(self.split_var.get())
            if not 1 <= split < len(self.raw_cues[ident - 1].source.splitlines()):
                raise ValueError()
            self.splits[ident] = split
            self.project = self.read_project()
            self.pending.discard(ident)
            self.tree.item(str(ident), values=self.preview_values(self.project["cues"][ident - 1]))
            self.update_hint()
            if self.pending:
                self.tree.selection_set(str(min(self.pending)))
                self.tree.see(str(min(self.pending)))
        except (ValueError, UserError) as exc:
            messagebox.showerror("无法分行", str(exc) or "原文和中文都必须至少有一行。", parent=self)

    def accept(self):
        if self.signature() != self.preview_signature:
            self.preview()
            return
        if not self.project:
            messagebox.showinfo("检查字幕", "请先修正提示的问题，再刷新预览。", parent=self)
            return
        if self.pending:
            self.tree.selection_set(str(min(self.pending)))
            self.tree.see(str(min(self.pending)))
            messagebox.showinfo("确认双语分行", "请先确认标有“待确认”的字幕：原文占前几行，剩余为中文。", parent=self)
            return
        self.result = self.project
        self.destroy()


class AddCueDialog(tk.Toplevel):
    def __init__(self, parent, start, end, chinese_only=False):
        super().__init__(parent)
        self.title("新增字幕 · 按时间插入")
        self.geometry("650x410")
        self.resizable(False, False)
        self.transient(parent)
        self.result = None
        self.start_var = tk.StringVar(value=timestamp(start))
        self.end_var = tk.StringVar(value=timestamp(end))
        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        row = ttk.Frame(body)
        row.pack(fill="x", pady=(0, 10))
        for label, var in [("开始", self.start_var), ("结束", self.end_var)]:
            ttk.Label(row, text=label).pack(side="left", padx=(0, 8))
            ttk.Entry(row, textvariable=var, width=17).pack(side="left", padx=(0, 16))
        ttk.Label(body, text="中文字幕" if chinese_only else "原文（必填）").pack(anchor="w")
        self.source = ScrolledText(body, height=4, wrap="word", font=(FONT_FAMILY, 10))
        self.source.pack(fill="x", pady=5)
        ttk.Label(body, text="中文译文（可选；留空时，继续翻译只补译缺失条目）").pack(anchor="w")
        self.zh = ScrolledText(body, height=3, wrap="word", font=(FONT_FAMILY, 10))
        self.zh.pack(fill="x", pady=5)
        if chinese_only:
            self.zh.configure(state="disabled")
        ttk.Label(body, text="时间格式 00:00:01,000。保存后按开始时间排序，其他条目译文保留。", style="Muted.TLabel").pack(anchor="w", pady=8)
        ttk.Button(body, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(body, text="添加并保存", command=self.accept, style="Accent.TButton").pack(side="right", padx=10)
        self.source.focus_set()
        self.grab_set()

    def accept(self):
        try:
            c = Cue(1, parse_time(self.start_var.get()), parse_time(self.end_var.get()),
                    clean(self.source.get("1.0", "end-1c")), clean(self.zh.get("1.0", "end-1c")))
            c.validate()
        except UserError as exc:
            messagebox.showerror("检查新增字幕", str(exc), parent=self)
            return
        self.result = c
        self.destroy()


class DurationDialog(tk.Toplevel):
    def __init__(self, parent, project, selected_id):
        super().__init__(parent)
        self.title("字幕持续时间 · 单条或统一设置")
        self.geometry("670x490")
        self.resizable(False, False)
        self.transient(parent)
        self.project = project
        self.selected_id = selected_id
        self.result = None
        selected = next((r for r in project["cues"] if r["id"] == selected_id), None)
        current = max(.001, round(selected["end"] - selected["start"], 3)) if selected else 2.5
        self.seconds = tk.StringVar(value=f"{current:.3f}".rstrip("0").rstrip("."))
        self.scope = tk.StringVar(value="current" if selected else "all")
        self.stop_at_next = tk.BooleanVar(value=True)
        self.hint = tk.StringVar()
        body = ttk.Frame(self, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="设置字幕在屏幕上停留多久", font=(FONT_FAMILY, 15, "bold")).pack(anchor="w", pady=(0, 14))
        row = ttk.Frame(body)
        row.pack(fill="x", pady=5)
        current_button = ttk.Radiobutton(row, text=f"当前字幕（第 {selected_id} 条）" if selected else "当前字幕", variable=self.scope, value="current")
        current_button.pack(side="left", padx=(0, 20))
        if not selected:
            current_button.configure(state="disabled")
        ttk.Radiobutton(row, text=f"全部字幕（{len(project['cues'])} 条）", variable=self.scope, value="all").pack(side="left")
        timing = ttk.Frame(body)
        timing.pack(fill="x", pady=10)
        ttk.Label(timing, text="持续时间").pack(side="left", padx=(0, 10))
        entry = ttk.Entry(timing, textvariable=self.seconds, width=12)
        entry.pack(side="left", padx=(0, 8))
        ttk.Label(timing, text="秒，例如 2.5（精确到 0.001 秒）").pack(side="left")
        ttk.Checkbutton(body, text="遇到下一条开始就结束（允许实际时长短于设定值）", variable=self.stop_at_next).pack(anchor="w", pady=8)
        ttk.Label(body, text="保留每条开始时间，仅调整结束时间。取消勾选后，每条使用完整时长，可能与后续字幕重叠。", wraplength=620, style="Muted.TLabel").pack(anchor="w")
        ttk.Label(body, textvariable=self.hint, wraplength=620).pack(fill="x", pady=14)
        footer = ttk.Frame(body)
        footer.pack(side="bottom", fill="x")
        ttk.Label(footer, text="保存前自动备份项目；调时不调用翻译 API。", style="Muted.TLabel").pack(side="left")
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right")
        self.apply_button = ttk.Button(footer, text="应用并保存", command=self.accept, style="Accent.TButton")
        self.apply_button.pack(side="right", padx=8)
        for var in (self.seconds, self.scope, self.stop_at_next):
            var.trace_add("write", self.preview)
        self.preview()
        self.bind("<Return>", lambda event: self.accept())
        self.bind("<Escape>", lambda event: self.destroy())
        entry.focus_set()
        entry.selection_range(0, "end")
        self.grab_set()

    def options(self):
        return {"seconds": self.seconds.get(), "cue_ids": [self.selected_id] if self.scope.get() == "current" else None,
                "stop_at_next": self.stop_at_next.get()}

    def preview(self, *_args):
        try:
            updated, report = set_project_duration(self.project, **self.options())
            if self.scope.get() == "current":
                row = next(r for r in updated["cues"] if r["id"] == self.selected_id)
                hint = f"预览：{timestamp(row['start'])} → {timestamp(row['end'])}，实际约 {row['end'] - row['start']:.3f} 秒。"
            else:
                hint = f"将设置 {report['target_count']} 条字幕，其中 {report['changed_count']} 条时间会改变。"
            if report["capped_count"]:
                hint += f" {report['capped_count']} 条会在下一条开始处提前结束。"
            if report["same_start_count"]:
                hint += f" {report['same_start_count']} 条与其他字幕同时开始，将保留同时显示。"
            self.hint.set(hint)
            self.apply_button.configure(state="normal")
            return True
        except UserError as exc:
            self.hint.set(str(exc))
            self.apply_button.configure(state="disabled")
            return False

    def accept(self):
        if self.preview():
            self.result = self.options()
            self.destroy()


class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("字幕工坊 · 原文、中文与混合字幕")
        width = min(1180, max(900, self.winfo_screenwidth() - 60))
        height = min(850, max(560, self.winfo_screenheight() - 110))
        self.geometry(f"{width}x{height}")
        self.minsize(min(1000, width), min(650, height))
        self.configure(bg=BG)
        self.option_add("*Font", (FONT_FAMILY, 10))
        self.project = None
        self.project_path = None
        self.selected_id = None
        self.proc = None
        self.messages = None
        self.stop_event = None
        self.stop_time = None
        self.seen_terminal = False
        self.close_when_stopped = False
        self.current_config = None
        self.active_stage = ""
        self.task_kind = "job"
        self.gpu_setup_ok = False
        self.diagnostics = Diagnostics(USER_DIR, APP_VERSION)
        self.diagnostic_results = queue.Queue()
        self.diagnostic_exporting = False
        self.last_diagnostic_refresh = 0.0
        self.settings = self.read_settings()
        self.source_var = tk.StringVar(value="")
        default_out = Path.home() / ("Videos" if (Path.home() / "Videos").exists() else "Documents") / "SubtitleStudio"
        self.out_var = tk.StringVar(value=self.settings.get("output", str(default_out)))
        self.lang_var = tk.StringVar(value=self.settings.get("language", "自动识别"))
        self.asr_var = tk.StringVar(value=self.settings.get("asr_model", "small"))
        saved_device = self.settings.get("device", "CPU（直接使用）")
        self.device_var = tk.StringVar(value="NVIDIA GPU（4 GB 省显存）" if saved_device.startswith("NVIDIA") else "CPU（直接使用）")
        self.local_model_var = tk.StringVar(value=self.settings.get("local_model", ""))
        self.api_key_var = tk.StringVar(value=os.environ.get("DEEPSEEK_API_KEY", ""))
        self.model_var = tk.StringVar(value=self.settings.get("model", DEFAULT_MODEL))
        self.force_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="选择视频，或打开已有项目 / SRT 直接校对。")
        self.summary_var = tk.StringVar(value="尚未载入字幕")
        self.time_start = tk.StringVar()
        self.time_end = tk.StringVar()
        self.busy_controls = []
        self.configure_style()
        self.build_ui()
        self.bind("<F4>", lambda event: self.set_duration())
        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self.after(150, self.poll)

    @staticmethod
    def read_settings():
        try:
            data = json.loads(CONFIG_PATH.read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_settings(self):
        data = {"output": self.out_var.get(), "language": self.lang_var.get(),
                "asr_model": self.asr_var.get(), "device": self.device_var.get(),
                "local_model": self.local_model_var.get(), "model": self.model_var.get(),
                "glossary": self.glossary.get("1.0", "end-1c")}
        try:
            atomic_write(CONFIG_PATH, json.dumps(data, ensure_ascii=False, indent=2))
        except OSError:
            self.log("本机设置未能保存；当前任务仍可继续。")

    def configure_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=INK)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=(FONT_FAMILY, 22, "bold"))
        style.configure("TButton", padding=(12, 7), background="#e2eaf0", foreground=INK)
        style.configure("Accent.TButton", background=ACCENT, foreground="white", font=(FONT_FAMILY, 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#0f626a"), ("disabled", "#9db8bb")])
        style.configure("TEntry", padding=5, fieldbackground="white")
        style.configure("TCombobox", padding=5)
        style.configure("Treeview", rowheight=29, background="white", fieldbackground="white", foreground=INK)
        style.configure("Treeview.Heading", font=(FONT_FAMILY, 10, "bold"), padding=7)
        style.map("Treeview", background=[("selected", "#d4ebed")], foreground=[("selected", INK)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 8))
        style.configure("TCheckbutton", background=BG, foreground=INK)
        style.configure("TProgressbar", background=ACCENT)

    def button(self, parent, text, command, accent=False, busy=True, **pack):
        b = ttk.Button(parent, text=text, command=command, style="Accent.TButton" if accent else "TButton")
        b.pack(**pack)
        if busy:
            self.busy_controls.append(b)
        return b

    def build_ui(self):
        header = ttk.Frame(self, padding=(22, 15, 22, 12))
        header.pack(fill="x")
        ttk.Label(header, text="字幕工坊", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="  本地识别 · DeepSeek 翻译 · PR 字幕与文稿", style="Muted.TLabel").pack(side="left", padx=12, pady=(8, 0))
        ttk.Label(header, text="v" + APP_VERSION, style="Muted.TLabel").pack(side="right")
        self.button(header, "导出诊断包", self.export_diagnostics, busy=False, side="right", padx=10)
        self.button(header, "小白指南", self.open_easy_guide, busy=False, side="right")
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=18)
        main = ttk.Frame(self.notebook, padding=12)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(6, weight=1)
        settings_page = ttk.Frame(self.notebook)
        settings_canvas = tk.Canvas(settings_page, bg=BG, highlightthickness=0)
        settings_scroll = ttk.Scrollbar(settings_page, orient="vertical", command=settings_canvas.yview)
        settings_scroll.pack(side="right", fill="y")
        settings_canvas.pack(side="left", fill="both", expand=True)
        settings_canvas.configure(yscrollcommand=settings_scroll.set)
        settings = ttk.Frame(settings_canvas, padding=24)
        settings_window = settings_canvas.create_window((0, 0), window=settings, anchor="nw")
        settings.bind("<Configure>", lambda e: settings_canvas.configure(scrollregion=settings_canvas.bbox("all")))
        settings_canvas.bind("<Configure>", lambda e: settings_canvas.itemconfigure(settings_window, width=e.width))
        help_tab = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(main, text="任务与字幕")
        self.notebook.add(settings_page, text="翻译与识别设置")
        self.notebook.add(help_tab, text="使用帮助")
        diagnostic_tab = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(diagnostic_tab, text="诊断与资源")
        toolbar = ttk.Frame(diagnostic_tab)
        toolbar.pack(fill="x", pady=(0, 10))
        self.button(toolbar, "导出诊断包", self.export_diagnostics, busy=False, side="left")
        ttk.Label(toolbar, text="  遇到问题时保存 ZIP，发来即可排查；记录只保存在本机。", style="Muted.TLabel").pack(side="left")
        self.diagnostic_text = ScrolledText(diagnostic_tab, wrap="word", font=(FONT_FAMILY, 10), padx=12, pady=12, relief="flat", state="disabled")
        self.diagnostic_text.pack(fill="both", expand=True)

        files = ttk.Frame(main)
        files.grid(row=0, column=0, sticky="ew")
        files.columnconfigure(1, weight=1)
        for row, (label, var, command) in enumerate([("视频 / 字幕", self.source_var, self.choose_source), ("输出目录", self.out_var, self.choose_output)]):
            ttk.Label(files, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
            entry = ttk.Entry(files, textvariable=var, state="readonly" if row == 0 else "normal")
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            btn = ttk.Button(files, text="选择文件" if row == 0 else "选择目录", command=command)
            btn.grid(row=row, column=2, padx=(8, 0), pady=4)
            self.busy_controls.append(btn)
            if row == 1:
                self.busy_controls.append(entry)
        options = ttk.Frame(main)
        options.grid(row=1, column=0, sticky="ew", pady=(7, 5))
        ttk.Label(options, text="原语言").pack(side="left")
        combo = ttk.Combobox(options, textvariable=self.lang_var, values=["自动识别", "英语", "日语"], state="readonly", width=12)
        combo.pack(side="left", padx=(8, 18))
        self.busy_controls.append(combo)
        ttk.Label(options, text="原文、中文、混合版，各导出 SRT 和 TXT", style="Muted.TLabel").pack(side="left")
        self.button(options, "打开项目 / SRT…", self.open_project, side="right")

        action = ttk.Frame(main)
        action.grid(row=2, column=0, sticky="ew", pady=(5, 8))
        self.button(action, "识别并翻译 / 继续", lambda: self.start_job("translate"), accent=True, side="left")
        self.button(action, "仅识别原文", lambda: self.start_job("transcribe"), side="left", padx=8)
        self.stop_button = self.button(action, "停止", self.stop_job, busy=False, side="left")
        self.stop_button.configure(state="disabled")
        self.full_export_button = self.button(action, "导出六份文件", lambda: self.manual_export(True), side="right")
        self.source_export_button = self.button(action, "仅导出原文", lambda: self.manual_export(False), side="right", padx=8)
        self.progress = ttk.Progressbar(main, mode="determinate", maximum=100)
        self.progress.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(main, textvariable=self.status_var, style="Muted.TLabel").grid(row=4, column=0, sticky="w")
        summary = ttk.Frame(main)
        summary.grid(row=5, column=0, sticky="ew", pady=(8, 5))
        ttk.Label(summary, textvariable=self.summary_var).pack(side="left")
        self.button(summary, "打开输出文件夹", self.open_output, busy=False, side="right")
        self.button(summary, "新增字幕…", self.add_cue, side="right", padx=8)
        self.button(summary, "时长设置…", self.set_duration, side="right")

        table = ttk.Frame(main)
        table.grid(row=6, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=("id", "time", "source", "zh"), show="headings", selectmode="browse", height=3)
        for col, name, width in [("id", "序号", 45), ("time", "时间段", 235), ("source", "原文字幕", 335), ("zh", "中文字幕", 335)]:
            self.tree.heading(col, text=name)
            self.tree.column(col, width=width, minwidth=45, stretch=col in ("source", "zh"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self.select_cue)

        editor = ttk.Frame(main)
        editor.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        editor.columnconfigure(0, weight=1)
        editor.columnconfigure(1, weight=1)
        self.source_label = ttk.Label(editor, text="原文校对")
        self.source_label.grid(row=0, column=0, sticky="w")
        self.zh_label = ttk.Label(editor, text="中文校对")
        self.zh_label.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.source_text = ScrolledText(editor, height=3, wrap="word", font=(FONT_FAMILY, 10), relief="solid", borderwidth=1)
        self.source_text.grid(row=1, column=0, sticky="ew", pady=4)
        self.zh_text = ScrolledText(editor, height=3, wrap="word", font=(FONT_FAMILY, 10), relief="solid", borderwidth=1)
        self.zh_text.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=4)
        row = ttk.Frame(main)
        row.grid(row=8, column=0, sticky="ew")
        ttk.Label(row, text="起止时间").pack(side="left")
        for v in (self.time_start, self.time_end):
            entry = ttk.Entry(row, textvariable=v, width=15)
            entry.pack(side="left", padx=5)
            self.busy_controls.append(entry)
        ttk.Label(row, text="修改保留译文；清空中文可补译此条", style="Muted.TLabel").pack(side="left", padx=8)
        self.button(row, "保存此条修改", self.save_current, side="right")

        self.logs = ScrolledText(main, height=2, wrap="word", font=(FONT_FAMILY, 9), bg="#e6edf3", fg=MUTED, relief="flat", state="disabled")
        self.logs.grid(row=9, column=0, sticky="ew", pady=(8, 0))

        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text="DeepSeek 翻译", font=(FONT_FAMILY, 15, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 15))
        ttk.Label(settings, text="API 密钥").grid(row=1, column=0, sticky="w", pady=6, padx=(0, 18))
        self.key_entry = ttk.Entry(settings, textvariable=self.api_key_var, show="●")
        self.key_entry.grid(row=1, column=1, sticky="ew", pady=6)
        show = tk.BooleanVar(value=False)
        show_btn = ttk.Checkbutton(settings, text="显示", variable=show, command=lambda: self.key_entry.configure(show="" if show.get() else "●"))
        show_btn.grid(row=1, column=2, padx=12)
        self.busy_controls.extend([self.key_entry, show_btn])
        ttk.Label(settings, text="只保留在本次会话；请求仅发送到 DeepSeek 官方接口。", style="Muted.TLabel").grid(row=2, column=1, sticky="w")
        ttk.Label(settings, text="翻译模型").grid(row=3, column=0, sticky="w", pady=10)
        model_combo = ttk.Combobox(settings, textvariable=self.model_var, values=[DEFAULT_MODEL, "deepseek-v4-pro"], width=30)
        model_combo.grid(row=3, column=1, sticky="w", pady=10)
        self.busy_controls.append(model_combo)
        ttk.Label(settings, text="可填写账户可用的模型 ID；使用非思考模式翻译。", style="Muted.TLabel").grid(row=4, column=1, sticky="w")
        ttk.Label(settings, text="人名 / 术语说明").grid(row=5, column=0, sticky="nw", pady=10)
        self.glossary = ScrolledText(settings, height=4, wrap="word", font=(FONT_FAMILY, 10))
        self.glossary.grid(row=5, column=1, columnspan=2, sticky="ew", pady=10)
        self.glossary.insert("1.0", self.settings.get("glossary", ""))
        ttk.Label(settings, text="例如：John Smith = 约翰·史密斯。新设置只用于缺少中文的条目；已有译文会保留。", style="Muted.TLabel").grid(row=6, column=1, columnspan=2, sticky="w")
        force = ttk.Checkbutton(settings, text="重新翻译全部（会重新消耗 API 额度）", variable=self.force_var)
        force.grid(row=7, column=1, sticky="w", pady=10)
        self.busy_controls.append(force)
        ttk.Separator(settings).grid(row=8, column=0, columnspan=3, sticky="ew", pady=14)
        ttk.Label(settings, text="本地语音识别", font=(FONT_FAMILY, 15, "bold")).grid(row=9, column=0, columnspan=3, sticky="w", pady=(2, 14))
        for r, label, var, values in [(10, "识别模型", self.asr_var, ["small", "medium", "large-v3", "turbo", "base"]),
                                       (11, "处理设备", self.device_var, ["CPU（直接使用）", "NVIDIA GPU（4 GB 省显存）"])]:
            ttk.Label(settings, text=label).grid(row=r, column=0, sticky="w", pady=6)
            c = ttk.Combobox(settings, textvariable=var, values=values, state="readonly", width=28)
            c.grid(row=r, column=1, sticky="w", pady=6)
            self.busy_controls.append(c)
        ttk.Label(settings, text="CPU 可直接使用。显卡首次下载约 570 MB 组件；下载可继续，显卡不可用时自动使用 CPU。", style="Muted.TLabel").grid(row=12, column=1, columnspan=2, sticky="w", pady=6)
        ttk.Label(settings, text="本地模型文件夹").grid(row=13, column=0, sticky="w", pady=6)
        entry = ttk.Entry(settings, textvariable=self.local_model_var)
        entry.grid(row=13, column=1, sticky="ew", pady=6)
        btn = ttk.Button(settings, text="选择…", command=self.choose_model)
        btn.grid(row=13, column=2, padx=10)
        self.busy_controls.extend([entry, btn])
        ttk.Label(settings, text="可留空。填写时优先使用该文件夹，应包含 model.bin、config.json、tokenizer.json 等模型文件。", style="Muted.TLabel").grid(row=14, column=1, columnspan=2, sticky="w")
        gpu_btn = ttk.Button(settings, text="启用显卡加速", command=self.start_gpu_setup, style="Accent.TButton")
        gpu_btn.grid(row=11, column=2, padx=10, pady=6)
        self.busy_controls.append(gpu_btn)
        for child in settings.winfo_children():
            if isinstance(child, ttk.Label) and len(str(child.cget("text"))) > 36:
                child.configure(wraplength=730)
        text = ScrolledText(help_tab, wrap="word", font=(FONT_FAMILY, 11), padx=16, pady=16, relief="flat")
        text.pack(fill="both", expand=True)
        text.insert("1.0", HELP)
        text.configure(state="disabled")

    def open_easy_guide(self):
        import webbrowser
        guide = APP_DIR / "guide.html"
        if not guide.is_file():
            messagebox.showinfo("小白指南", "请打开懒人安装包中的 guide.html，或运行安装器重新安装 / 修复。")
            return
        for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            edge = Path(os.environ.get(env, "")) / "Microsoft/Edge/Application/msedge.exe"
            if edge.is_file():
                try:
                    subprocess.Popen([str(edge), str(guide)])
                    return
                except OSError:
                    pass
        webbrowser.open(guide.as_uri())

    def log(self, message):
        self.logs.configure(state="normal")
        self.logs.insert("end", time.strftime("%H:%M:%S") + "  " + message + "\n")
        self.logs.see("end")
        self.logs.configure(state="disabled")

    @property
    def busy(self):
        return self.proc is not None

    def set_busy(self, active):
        for control in self.busy_controls:
            if active:
                control.state(["disabled"])
            else:
                control.state(["!disabled"])
        for text in (self.source_text, self.zh_text, self.glossary):
            text.configure(state="disabled" if active else "normal")
        self.stop_button.configure(state="normal" if active else "disabled")
        if not active:
            self.sync_subtitle_mode()

    def sync_subtitle_mode(self):
        chinese = (self.project or {}).get("subtitle_mode") == "chinese"
        self.source_label.configure(text="中文字幕校对" if chinese else "原文校对")
        self.zh_label.configure(text="中文预览（直接修改左侧）" if chinese else "中文校对")
        self.tree.heading("source", text="中文字幕" if chinese else "原文字幕")
        self.tree.heading("zh", text="中文预览" if chinese else "中文字幕")
        self.full_export_button.configure(text="导出中文字幕" if chinese else "导出六份文件")
        self.source_export_button.configure(text="导出中文 SRT / TXT" if chinese else "仅导出原文")
        self.zh_text.configure(state="disabled" if chinese or self.busy else "normal")

    def choose_source(self):
        path = filedialog.askopenfilename(title="选择视频、音频或已有 SRT", filetypes=[
            ("视频 / 音频 / 字幕", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.mp3 *.wav *.m4a *.flac *.srt"), ("所有文件", "*.*")])
        if path:
            if not self.save_current(silent=True):
                return
            if Path(path).suffix.lower() == ".srt":
                self.import_subtitles(path)
                return
            self.project, self.project_path, self.selected_id = None, None, None
            self.force_var.set(False)
            self.source_var.set(path)
            self.tree.delete(*self.tree.get_children())
            self.summary_var.set("尚未识别；可以先识别原文，也可以一次完成识别与翻译。")
            self.clear_editor()
            self.progress["value"] = 0
            self.status_var.set("已选择素材。")
            self.sync_subtitle_mode()

    def choose_output(self):
        path = filedialog.askdirectory(title="选择项目输出目录")
        if path:
            self.out_var.set(path)
            if self.project_path:
                self.log("当前打开的项目继续保存在原位置；新输出目录对下次选择的素材生效。")

    def choose_model(self):
        path = filedialog.askdirectory(title="选择完整的 faster-whisper 模型目录")
        if path:
            self.local_model_var.set(path)

    def open_project(self):
        path = filedialog.askopenfilename(title="打开以前保存的字幕或项目", filetypes=[("项目 / SRT 字幕", "*.json *.srt"), ("SRT 字幕", "*.srt"), ("字幕项目", "*.json")])
        if path:
            if not self.save_current(silent=True):
                return
            if Path(path).suffix.lower() == ".srt":
                self.import_subtitles(path)
                return
            try:
                self.reload_project(path)
                self.force_var.set(False)
                self.source_var.set(self.project.get("input", ""))
                lang = self.project.get("language")
                self.lang_var.set({"en": "英语", "ja": "日语"}.get(lang, "自动识别"))
                profile = self.project.get("translation_profile", {})
                if profile:
                    self.model_var.set(profile.get("model", DEFAULT_MODEL))
                    self.glossary.delete("1.0", "end")
                    self.glossary.insert("1.0", profile.get("glossary", ""))
                self.status_var.set("项目已载入，可编辑、导出或继续翻译。")
            except UserError as exc:
                messagebox.showerror("无法打开", str(exc))

    def import_subtitles(self, path):
        if self.busy:
            return
        dialog = SrtImportDialog(self, path)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        if not self.out_var.get().strip():
            self.choose_output()
            if not self.out_var.get().strip():
                return
        try:
            project_path = store_imported_project(dialog.result, self.out_var.get().strip())
            self.reload_project(project_path)
            self.source_var.set(dialog.result["input"])
            self.lang_var.set({"en": "英语", "ja": "日语"}.get(dialog.result["language"], "自动识别"))
            self.force_var.set(False)
            self.progress["value"] = 0
            self.status_var.set("已有字幕已导入并保存为项目；可直接校对和导出，未调用翻译 API。")
            self.log("已建立可继续编辑的 project.json；下次打开此项目即可保留所有校对结果。")
            self.notebook.select(0)
        except (UserError, OSError) as exc:
            messagebox.showerror("无法保存导入项目", str(exc))

    def add_cue(self):
        if self.busy:
            return
        if not self.project:
            messagebox.showinfo("先载入字幕", "请先打开项目 / SRT，或完成一次原文识别。")
            return
        if not self.save_current(silent=True):
            return
        current = next((r for r in self.project["cues"] if r["id"] == self.selected_id), None)
        start = current["end"] if current else 0.0
        following = [r["start"] for r in self.project["cues"] if r["start"] > start]
        end = min(start + 2, min(following)) if following else start + 2
        dialog = AddCueDialog(self, start, end, self.project.get("subtitle_mode") == "chinese")
        self.wait_window(dialog)
        if dialog.result is None:
            return
        c = dialog.result
        try:
            updated, selected = insert_project_cue(self.project, c.start, c.end, c.source, c.zh)
            save_project(self.project_path, updated)
            self.selected_id = selected
            self.reload_project(self.project_path)
            self.tree.see(str(selected))
            self.status_var.set("新增字幕已保存并按时间插入。其他字幕和译文已保留。")
        except (UserError, OSError) as exc:
            messagebox.showerror("无法添加字幕", str(exc))

    def set_duration(self):
        if self.busy:
            return
        if not self.project or not self.project["cues"]:
            messagebox.showinfo("先载入字幕", "请先打开项目 / SRT，或完成原文识别。")
            return
        if not self.save_current(silent=True):
            return
        dialog = DurationDialog(self, self.project, self.selected_id)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        try:
            _updated, report, backup = save_duration_change(self.project_path, self.project, **dialog.result)
            self.reload_project(self.project_path)
            if report["changed_count"]:
                self.status_var.set(f"已调整 {report['changed_count']} 条字幕时长；原文与译文保留。")
                self.log("调时前项目备份：" + str(backup))
            else:
                self.status_var.set("当前字幕时间已符合设置，无需修改。")
        except (UserError, OSError) as exc:
            messagebox.showerror("无法保存时长", str(exc))

    def reload_project(self, path):
        p = load_project(path)
        selected = self.selected_id if self.project_path == str(path) else None
        self.selected_id = None
        self.project_path, self.project = str(path), p
        rows = {str(c["id"]): c for c in p["cues"]}
        for item in self.tree.get_children():
            if item not in rows:
                self.tree.delete(item)
        for position, (ident, c) in enumerate(rows.items()):
            values = (c["id"], timestamp(c["start"]) + " — " + timestamp(c["end"]), c["source"], c["zh"] or "待翻译")
            if self.tree.exists(ident):
                self.tree.item(ident, values=values)
            else:
                self.tree.insert("", "end", iid=ident, values=values)
            self.tree.move(ident, "", position)
        count = sum(bool(clean(c["zh"])) for c in p["cues"])
        self.summary_var.set(f"原语言：{p.get('language', '—')}     原文 {len(p['cues'])} 条     中文 {count} 条")
        if selected and str(selected) in rows:
            self.selected_id = selected
            self.tree.selection_set(str(selected))
        elif rows:
            self.selected_id = int(next(iter(rows)))
            self.tree.selection_set(next(iter(rows)))
        if not rows:
            self.clear_editor()
        self.sync_subtitle_mode()
        self.select_cue()

    def clear_editor(self):
        for text in (self.source_text, self.zh_text):
            old = text.cget("state")
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.configure(state=old)
        self.time_start.set("")
        self.time_end.set("")

    def select_cue(self, _event=None):
        selected = self.tree.selection()
        if not selected or not self.project:
            return
        new_id = int(selected[0])
        if self.selected_id != new_id and not self.busy:
            if not self.save_current(silent=True, refresh=False):
                if self.selected_id and self.tree.exists(str(self.selected_id)):
                    self.tree.selection_set(str(self.selected_id))
                return
        self.selected_id = new_id
        row = next((r for r in self.project["cues"] if r["id"] == new_id), None)
        if not row:
            return
        for text, value in ((self.source_text, row["source"]), (self.zh_text, row["zh"])):
            state = text.cget("state")
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert("1.0", value)
            text.configure(state=state)
        self.time_start.set(timestamp(row["start"]))
        self.time_end.set(timestamp(row["end"]))

    def save_current(self, silent=False, refresh=True):
        if self.busy or not self.project or not self.selected_id:
            return True
        row = next((r for r in self.project["cues"] if r["id"] == self.selected_id), None)
        if not row:
            return True
        try:
            source = clean(self.source_text.get("1.0", "end-1c"))
            zh = clean(self.zh_text.get("1.0", "end-1c"))
            if self.project.get("subtitle_mode") == "chinese":
                zh = source
            c = Cue(row["id"], parse_time(self.time_start.get()), parse_time(self.time_end.get()), source, zh)
            c.validate()
            updated = {"id": c.id, "start": c.start, "end": c.end, "source": c.source, "zh": c.zh}
            if updated == row:
                return True
            old = dict(row)
            row.update(updated)
            try:
                save_project(self.project_path, self.project)
            except OSError:
                row.update(old)
                raise
            values = (c.id, timestamp(c.start) + " — " + timestamp(c.end), c.source, c.zh or "待翻译")
            self.tree.item(str(c.id), values=values)
            if self.project.get("subtitle_mode") == "chinese":
                self.zh_text.configure(state="normal")
                self.zh_text.delete("1.0", "end")
                self.zh_text.insert("1.0", zh)
                self.zh_text.configure(state="disabled")
            count = sum(bool(clean(r["zh"])) for r in self.project["cues"])
            self.summary_var.set(f"原文 {len(self.project['cues'])} 条     中文 {count} 条")
            if not silent:
                self.log(f"已保存第 {c.id} 条修改；保留当前填写的译文。")
            return True
        except (UserError, OSError) as exc:
            messagebox.showerror("无法保存修改", str(exc))
            return False

    def start_job(self, mode):
        if self.busy:
            return
        if not self.source_var.get() and not self.project_path:
            messagebox.showinfo("选择素材", "请先选择视频、音频或原文 SRT 文件。")
            return
        if not self.save_current(silent=True):
            return
        chinese = (self.project or {}).get("subtitle_mode") == "chinese"
        complete = self.project and self.project["cues"] and all(clean(r["zh"]) for r in self.project["cues"])
        if self.project and self.project.get("recognition_complete") and (mode == "transcribe" or chinese or (complete and not self.force_var.get())):
            self.manual_export(mode != "transcribe")
            return
        if mode == "translate" and self.force_var.get() and self.project:
            count = sum(bool(clean(r["zh"])) for r in self.project["cues"])
            if count and not messagebox.askyesno("重新翻译全部", f"当前已有 {count} 条中文译文。继续会重新调用 API 并替换这些译文。\n仅需补译时，请取消并关闭“重新翻译全部”。"):
                return
        if mode == "translate" and not self.api_key_var.get().strip():
            messagebox.showinfo("填写密钥", "请在「翻译与识别设置」填写 DeepSeek API 密钥，或先选择「仅识别原文」。")
            self.notebook.select(1)
            self.key_entry.focus_set()
            return
        if not self.out_var.get().strip():
            messagebox.showinfo("输出目录", "请先选择输出目录。")
            return
        local = self.local_model_var.get().strip()
        needs_asr = not (self.project or {}).get("recognition_complete") and Path(self.source_var.get()).suffix.lower() != ".srt"
        if needs_asr and local and not (Path(local) / "model.bin").is_file():
            messagebox.showerror("模型目录", "本地模型文件夹中没有 model.bin。请选择完整的 faster-whisper 模型，或清空该字段自动下载。")
            return
        self.save_settings()
        config = {"input": self.source_var.get(), "project_path": self.project_path, "output": self.out_var.get(),
                  "asr_model": local or self.asr_var.get(), "language": {"英语": "en", "日语": "ja"}.get(self.lang_var.get(), ""),
                  "device": "cpu" if self.device_var.get().startswith("CPU") else "cuda", "model_dir": str(USER_DIR / "models"),
                  "api_key": self.api_key_var.get(), "model": self.model_var.get().strip(),
                  "glossary": self.glossary.get("1.0", "end-1c").strip(), "force": self.force_var.get(), "mode": mode}
        self.diagnostics.start_task(config)
        self.launch_job(config)
        self.force_var.set(False)

    def launch_job(self, config):
        self.task_kind = "job"
        self.current_config = dict(config)
        self.active_stage = ""
        self.start_process(worker, config)

    def start_gpu_setup(self):
        if self.busy:
            return
        from gpu_runtime import setup_worker
        self.notebook.select(0)
        self.task_kind = "gpu_setup"
        self.gpu_setup_ok = False
        self.current_config = None
        self.active_stage = ""
        self.diagnostics.start_task()
        self.diagnostics.record("phase", phase="gpu_setup")
        self.start_process(setup_worker)
        self.log("开始配置显卡；首次下载约 570 MB，来自 NVIDIA 官方。无需手动安装 CUDA。")

    def start_process(self, target, config=None):
        ctx = mp.get_context("spawn")
        self.messages, self.stop_event = ctx.Queue(), ctx.Event()
        args = (self.stop_event, self.messages) if config is None else (config, self.stop_event, self.messages)
        self.proc = ctx.Process(target=target, args=args, daemon=True)
        self.stop_time, self.seen_terminal = None, False
        self.set_busy(True)
        self.progress["value"] = 0
        self.status_var.set("正在配置显卡组件…" if config is None else "正在准备任务…")
        if config is not None:
            self.log("开始处理。识别在本地运行；翻译会消耗 DeepSeek API 额度。" if config["mode"] == "translate" else "开始识别原文，不调用 DeepSeek。")
        try:
            self.proc.start()
            self.diagnostics.set_worker(self.proc.pid)
        except Exception as exc:
            self.diagnostics.record("error", phase="error", **error_info(exc))
            self.diagnostics.set_worker(None)
            self.proc = None
            self.current_config = None
            self.set_busy(False)
            messagebox.showerror("无法启动", "无法启动处理进程。请关闭程序后通过 start.bat 重新打开。")

    def stop_job(self):
        if self.proc and self.stop_time is None:
            self.stop_event.set()
            self.diagnostics.record("stop_requested", error_category="user_cancel")
            self.stop_time = time.monotonic()
            self.status_var.set("正在停止；如果当前模型或网络请求无法及时退出，将在几秒后结束进程。")
            self.stop_button.configure(state="disabled")
            self.log("已请求停止。完成的识别结果和翻译批次仍保留。")

    def poll(self):
        self.poll_diagnostics()
        if self.messages is not None:
            for _ in range(250):
                try:
                    kind, value = self.messages.get_nowait()
                except (queue.Empty, EOFError, OSError):
                    break
                self.diagnostics.observe(kind, value)
                if kind == "stage":
                    self.active_stage = value
                elif kind == "device" and value == "cpu":
                    self.device_var.set("CPU（直接使用）")
                    self.save_settings()
                elif kind == "gpu_ready":
                    self.gpu_setup_ok = True
                    self.seen_terminal = True
                    self.device_var.set("NVIDIA GPU（4 GB 省显存）")
                    self.save_settings()
                    self.status_var.set("显卡加速已启用。选择视频和本地 Turbo 模型后即可识别。")
                    self.log("显卡组件检查通过：" + value["name"] + "。后续使用省显存模式；模型文件可继续复用。")
                elif kind == "status":
                    self.status_var.set(value)
                elif kind == "log":
                    self.log(value)
                elif kind == "progress":
                    self.progress["value"] = value
                elif kind == "project":
                    try:
                        self.reload_project(value)
                    except UserError:
                        self.log("项目正在保存，稍后可重新打开。")
                elif kind == "done":
                    self.seen_terminal = True
                    self.status_var.set(f"完成：已导出 {len(value['files'])} 份文件。")
                    self.log("导出位置：" + value["folder"])
                    self.force_var.set(False)
                elif kind in ("error", "cancelled"):
                    self.seen_terminal = True
                    self.status_var.set(value)
                    self.log(value)
                    if kind == "error" and not self.close_when_stopped:
                        messagebox.showerror("任务未完成", value)
        if self.proc:
            if self.stop_time is not None and time.monotonic() - self.stop_time > 6 and self.proc.is_alive():
                self.diagnostics.record("forced_stop", phase="cancelled", error_category="user_cancel")
                self.proc.terminate()
                self.seen_terminal = True
                self.status_var.set("显卡组件下载已停止，稍后点击“启用显卡加速”可以继续。" if self.task_kind == "gpu_setup" else "任务已停止。可打开保存的项目继续翻译；未完成的识别需重新开始。")
            if not self.proc.is_alive():
                self.proc.join(timeout=0.1)
                self.diagnostics.record("worker_exited", worker_pid=getattr(self.proc, "pid", None),
                                        exit_code=getattr(self.proc, "exitcode", None), had_terminal_event=self.seen_terminal)
                self.diagnostics.set_worker(None)
                if (not self.seen_terminal and self.stop_time is None and not self.close_when_stopped
                        and self.task_kind == "job" and self.active_stage == "gpu_asr"
                        and self.current_config and self.current_config.get("device") == "cuda"):
                    retry = dict(self.current_config)
                    retry["device"] = "cpu"
                    self.device_var.set("CPU（直接使用）")
                    self.save_settings()
                    self.log("显卡识别进程未能完成，本次将自动使用 CPU 重新识别。")
                    self.diagnostics.record("cpu_fallback", actual_device="unconfirmed", error_category="worker_native_exit",
                                            exit_code=getattr(self.proc, "exitcode", None))
                    self.proc = None
                    self.launch_job(retry)
                    self.after(150, self.poll)
                    return
                if not self.seen_terminal:
                    self.diagnostics.record("error", phase="error", error_category="worker_native_exit")
                    self.log("显卡配置未完成，可先用 CPU 或稍后重试。" if self.task_kind == "gpu_setup" else "处理进程已退出。若未完成，请检查内存或改用 CPU / 较小模型后继续。")
                    self.status_var.set("处理进程已退出；已保存的项目仍可继续使用。")
                self.proc = None
                self.current_config = None
                if self.task_kind == "gpu_setup" and not self.gpu_setup_ok:
                    self.device_var.set("CPU（直接使用）")
                    self.save_settings()
                self.set_busy(False)
                if self.project_path:
                    try:
                        self.reload_project(self.project_path)
                    except UserError:
                        pass
                if self.close_when_stopped:
                    self.destroy()
                    return
        self.after(150, self.poll)

    def manual_export(self, include_zh):
        if not self.project or not self.save_current(silent=True):
            if not self.project:
                messagebox.showinfo("暂无字幕", "请先完成识别或打开已保存项目。")
            return
        try:
            self.diagnostics.start_task({"mode": "manual_export", "device": "not_used"})
            self.diagnostics.record("media_info", actual_device="not_used")
            self.diagnostics.record("phase", phase="export")
            files = export_files(self.project_path, self.project, include_zh)
            self.diagnostics.record("exported", phase="completed", export_count=len(files))
            self.status_var.set(f"已导出 {len(files)} 份独立文件。")
            self.log("导出位置：" + str(files[0].parent))
        except (UserError, OSError) as exc:
            self.diagnostics.record("error", phase="error", **error_info(exc))
            messagebox.showerror("无法导出", str(exc))

    def export_diagnostics(self):
        if self.diagnostic_exporting:
            messagebox.showinfo("诊断包", "正在保存诊断包，请稍候。")
            return
        path = filedialog.asksaveasfilename(title="保存诊断包，随后可发来排查", defaultextension=".zip",
                                          initialfile=time.strftime("SubtitleStudio_Diagnostics_%Y%m%d_%H%M%S.zip"),
                                          filetypes=[("诊断压缩包", "*.zip")])
        if not path: return
        self.diagnostic_exporting = True
        def save():
            try:
                self.diagnostics.export_bundle(path)
                self.diagnostic_results.put((True, path))
            except Exception as exc:
                self.diagnostics.record("error", **error_info(exc))
                self.diagnostic_results.put((False, ""))
        threading.Thread(target=save, name="subtitle-diagnostic-export", daemon=True).start()

    def poll_diagnostics(self):
        try:
            ok, path = self.diagnostic_results.get_nowait()
            self.diagnostic_exporting = False
            if ok:
                messagebox.showinfo("诊断包已保存", "把这个 ZIP 发来即可排查：\n" + path + "\n\n未包含字幕正文、API 密钥或完整文件路径；没有自动上传。")
            else:
                messagebox.showerror("诊断包未保存", "请检查目标目录和系统盘是否有空间、是否允许写入，再尝试导出。识别任务不受影响。")
        except queue.Empty:
            pass
        if time.monotonic() - self.last_diagnostic_refresh >= 1 and self.notebook.index(self.notebook.select()) == 3:
            self.last_diagnostic_refresh = time.monotonic()
            self.diagnostic_text.configure(state="normal")
            self.diagnostic_text.delete("1.0", "end")
            self.diagnostic_text.insert("1.0", self.diagnostics.screen_text())
            self.diagnostic_text.configure(state="disabled")

    def report_callback_exception(self, exc, value, tb):
        if hasattr(self, "diagnostics"):
            self.diagnostics.record("ui_error", **error_info(value.with_traceback(tb)))
        messagebox.showerror("界面提示", "界面操作遇到异常。请点击“导出诊断包”保存记录，发来排查。")

    def destroy(self):
        if hasattr(self, "diagnostics"): self.diagnostics.close()
        super().destroy()

    def open_output(self):
        folder = (self.project or {}).get("last_export")
        folder = Path(folder or (str(Path(self.project_path).parent) if self.project_path else self.out_var.get()))
        if not folder.is_dir():
            messagebox.showinfo("输出目录", "任务完成后即可打开导出目录。")
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(folder))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError:
            messagebox.showinfo("输出位置", str(folder))

    def close_app(self):
        if self.busy:
            if messagebox.askyesno("任务正在运行", "停止当前任务并退出？已完成的翻译批次会保留。"):
                self.save_settings()
                self.close_when_stopped = True
                self.stop_job()
            return
        if self.save_current(silent=True):
            self.save_settings()
            self.api_key_var.set("")
            self.destroy()


if __name__ == "__main__":
    mp.freeze_support()
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    app = Application()
    if "--setup-gpu" in sys.argv:
        app.after(600, app.start_gpu_setup)
    app.mainloop()
