"""Import / add-cue / duration dialogs used by the main window.

Kept separate from app.py so the controller logic stays readable and the
dialogs remain testable in isolation. No API requests happen here.
"""
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from core import (Cue, UserError, clean, import_srt_project, parse_time,
                  read_ass_project, read_srt, set_project_duration,
                  set_project_offset, timestamp)
import timeline_qc

FONT_FAMILY = "Microsoft YaHei UI" if sys.platform == "win32" else "Noto Sans CJK SC"


class SrtImportDialog(tk.Toplevel):
    MODES = {"原文 SRT": "source", "中文字幕 SRT": "chinese",
             "原文 + 中文两个 SRT": "paired", "混合 SRT（原文在上）": "bilingual",
             "原文 ASS（保留原格式）": "ass_source", "中文 ASS（保留原格式）": "ass_chinese"}
    SRT_MODES = ("原文 SRT", "中文字幕 SRT", "原文 + 中文两个 SRT", "混合 SRT（原文在上）")
    ASS_MODES = ("原文 ASS（保留原格式）", "中文 ASS（保留原格式）")
    ENCODINGS = {"自动": "auto", "UTF-8": "utf-8-sig", "UTF-16": "utf-16",
                 "中文 GB18030 / GBK": "gb18030", "日文 Shift-JIS": "cp932"}

    @staticmethod
    def is_ass(path):
        return Path(str(path)).suffix.lower() == ".ass"

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
        if p.suffix.lower() == ".ass":
            # ASS 一般是别人做好的中文字幕，先按中文预选
            self.mode.set("中文 ASS（保留原格式）")
        elif "_原文_" in p.stem:
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
            if r == 0:
                self.mode_combo = combo
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
        path = filedialog.askopenfilename(parent=self, title="选择已有字幕",
                                          filetypes=[("字幕文件（SRT / ASS）", "*.srt *.ass"),
                                                     ("SRT 字幕", "*.srt"), ("ASS 字幕", "*.ass")])
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
        self.refresh_modes()
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

    def refresh_modes(self):
        """按文件类型只列出可用的内容类型：SRT 和 ASS 的选法不一样，混着选会出错。"""
        is_ass = self.is_ass(self.file.get())
        values = list(self.ASS_MODES if is_ass else self.SRT_MODES)
        if list(self.mode_combo["values"]) != values:
            self.mode_combo["values"] = values
        if self.mode.get() not in values:
            self.mode.set(values[-1] if is_ass else "请选择字幕内容类型")

    def read_project(self):
        path = self.file.get().strip()
        mode = self.MODES[self.mode.get()]
        if mode.startswith("ass_"):
            return read_ass_project(path, mode[4:])
        return import_srt_project(path, mode,
                                  {"英语": "en", "日语": "ja", "中文": "zh"}.get(self.language.get(), ""),
                                  self.chinese_file.get().strip(), self.ENCODINGS[self.encoding.get()], self.splits)

    def preview_values(self, c):
        return (str(c["id"]) + (" · 待确认" if c["id"] in self.pending else ""), c["source"], c["zh"])

    def update_hint(self):
        if self.pending:
            self.hint.set(f"有 {len(self.pending)} 条混合字幕超过两行。请逐条确认原文占几行，避免把换行当成语言分界。")
        elif self.MODES.get(self.mode.get(), "").startswith("ass_"):
            self.hint.set("ASS 按原格式编辑：只替换你改过的文字，样式、位置、特效、注释、编码和换行都照原样写回。"
                          "导入不调用 API；绘图行与注释行不会被当成字幕。")
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


class OffsetDialog(tk.Toplevel):
    """整体偏移时间轴：整条字幕轨一起提前或延后。

    使用场景：PR 里发现字幕整体快了一秒 / 慢了一秒，或者剪掉了片头。
    只平移，不改时长、不改条目之间的间隔、不动文本、不调翻译 API。
    """

    QUICK = (("提前 1 秒", -1), ("提前 0.5 秒", -0.5), ("提前 0.1 秒", -0.1),
             ("延后 0.1 秒", 0.1), ("延后 0.5 秒", 0.5), ("延后 1 秒", 1))

    def __init__(self, parent, project, selected_id):
        super().__init__(parent)
        self.title("整体偏移时间轴 · 全部或单条")
        self.geometry("670x470")
        self.resizable(False, False)
        self.transient(parent)
        self.project = project
        self.selected_id = selected_id
        self.result = None
        selected = next((r for r in project["cues"] if r["id"] == selected_id), None)
        self.seconds = tk.StringVar(value="0")
        # 默认「全部字幕」——功能就叫整体偏移，这是主用途；
        # 想只挪一条时，在上面选「当前字幕」即可。
        self.scope = tk.StringVar(value="all")
        self.hint = tk.StringVar()
        body = ttk.Frame(self, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="把字幕整体提前或延后", font=(FONT_FAMILY, 15, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(body, text="条目内容、每条时长、条目之间的间隔都不变，只是整条轨一起挪。",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 12))
        row = ttk.Frame(body)
        row.pack(fill="x", pady=5)
        current_button = ttk.Radiobutton(row, text=f"当前字幕（第 {selected_id} 条）" if selected else "当前字幕",
                                         variable=self.scope, value="current")
        current_button.pack(side="left", padx=(0, 20))
        if not selected:
            current_button.configure(state="disabled")
        ttk.Radiobutton(row, text=f"全部字幕（{len(project['cues'])} 条）", variable=self.scope, value="all").pack(side="left")
        timing = ttk.Frame(body)
        timing.pack(fill="x", pady=10)
        ttk.Label(timing, text="偏移").pack(side="left", padx=(0, 10))
        entry = ttk.Entry(timing, textvariable=self.seconds, width=12)
        entry.pack(side="left", padx=(0, 8))
        ttk.Label(timing, text="秒。正数 = 往后挪（晚了），负数 = 往前挪（早了）").pack(side="left")
        quick = ttk.Frame(body)
        quick.pack(fill="x", pady=(0, 10))
        for label, value in self.QUICK:
            ttk.Button(quick, text=label, width=11,
                       command=lambda v=value: self.seconds.set(f"{v:g}")).pack(side="left", padx=(0, 6))
        ttk.Label(body, textvariable=self.hint, wraplength=620).pack(fill="x", pady=12)
        footer = ttk.Frame(body)
        footer.pack(side="bottom", fill="x")
        ttk.Label(footer, text="保存前自动备份项目；平移不调用翻译 API。",
                  style="Muted.TLabel").pack(side="left")
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right")
        self.apply_button = ttk.Button(footer, text="应用并保存", command=self.accept, style="Accent.TButton")
        self.apply_button.pack(side="right", padx=8)
        for var in (self.seconds, self.scope):
            var.trace_add("write", self.preview)
        self.preview()
        self.bind("<Return>", lambda event: self.accept())
        self.bind("<Escape>", lambda event: self.destroy())
        entry.focus_set()
        entry.selection_range(0, "end")
        self.grab_set()

    def options(self):
        return {"seconds": self.seconds.get(),
                "cue_ids": [self.selected_id] if self.scope.get() == "current" else None}

    def rows_of(self, project):
        if self.scope.get() == "all":
            return list(project["cues"])
        return [r for r in project["cues"] if r["id"] == self.selected_id]

    def preview(self, *_args):
        try:
            updated, report = set_project_offset(self.project, **self.options())
            before, after = self.rows_of(self.project), self.rows_of(updated)
            if not after:
                raise UserError("没有找到要平移的字幕，请重新选择。")
            hint = (f"预览：最早一条 {timestamp(before[0]['start'])} → {timestamp(after[0]['start'])}；"
                    f"最晚一条 {timestamp(before[-1]['end'])} → {timestamp(after[-1]['end'])}。")
            effective = report["effective_ms"] / 1000
            if report["changed_count"]:
                if effective > 0:
                    hint += f" 全部往后挪 {effective:g} 秒。"
                else:
                    hint += f" 全部往前挪 {abs(effective):g} 秒。"
            else:
                hint += " 偏移量为 0，不会有任何改变。"
            if report["clamped"]:
                hint += (f" ⚠️ 你要挪 {report['requested_ms'] / 1000:g} 秒，"
                         f"但最早一条在 {report['earliest_ms'] / 1000:g} 秒处——"
                         f"再往前就会变成负数，所以只挪到贴住 0 秒为止。")
            self.hint.set(hint)
            self.apply_button.configure(state="normal" if report["changed_count"] else "disabled")
            return True
        except UserError as exc:
            self.hint.set(str(exc))
            self.apply_button.configure(state="disabled")
            return False

    def accept(self):
        if self.preview():
            self.result = self.options()
            self.destroy()


class TimelineQcDialog(tk.Toplevel):
    """时间轴体检：分级列出问题，双击跳过去，能修的可以一键修。

    和「待确认…」的区别：那个只列长静音和过短两条，是只读文本；
    这个是全量体检（叠字、闪帧、看不清、读不完、漏句…），
    分错误 / 警告 / 提示三级，**能双击跳到那一条**，安全的还能一键修。
    """

    def __init__(self, parent, result, on_jump=None, on_fix=None):
        super().__init__(parent)
        self.on_jump = on_jump or (lambda _id: None)
        self.on_fix = on_fix
        self.title("时间轴体检")
        self.configure(padx=12, pady=10)
        self.transient(parent)
        self.geometry("860x560")
        self.issues = result.get("issues") or []
        fixable = sum(1 for it in self.issues if it.get("fixable"))

        ttk.Label(self, text="不看音频，只查字幕本身有没有硬伤。",
                  style="Muted.TLabel").pack(anchor="w")
        counts = result.get("counts") or {}
        summary = timeline_qc.summarize(result)
        head = tk.Frame(self)
        head.pack(fill="x", pady=(6, 8))
        tk.Label(head, text=summary, font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        if self.issues:
            tk.Label(head, text="　（双击任意一行可以跳到那一条）",
                     fg="#666666").pack(side="left")

        if not self.issues:
            ttk.Label(self, text="没有发现问题 —— 这份时间轴可以直接导出。",
                      style="Muted.TLabel").pack(anchor="w", pady=20)
        else:
            table = tk.Frame(self)
            table.pack(fill="both", expand=True)
            self.tree = ttk.Treeview(table, columns=("level", "id", "time", "what"),
                                     show="headings", selectmode="browse", height=18)
            for key, title, width in (("level", "级别", 60), ("id", "第几条", 70),
                                      ("time", "时间", 120), ("what", "说明", 570)):
                self.tree.heading(key, text=title)
                self.tree.column(key, width=width, minwidth=50,
                                 stretch=(key == "what"), anchor="w")
            bar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=bar.set)
            self.tree.pack(side="left", fill="both", expand=True)
            bar.pack(side="right", fill="y")
            for it in self.issues:
                self.tree.insert("", "end", iid=str(it["id"]) + ":" + it["kind"],
                                 values=(timeline_qc.SEVERITY_LABEL[it["severity"]],
                                         f"第 {it['id']} 条",
                                         f"{timestamp(it['start'])} → {timestamp(it['end'])}",
                                         it["message"]))
            self.tree.bind("<Double-1>", self._jump)
            self.tree.bind("<Return>", self._jump)

        btns = tk.Frame(self)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="关闭", command=self.destroy).pack(side="right")
        if fixable and on_fix is not None:
            ttk.Button(btns, text=f"一键修能修的（{fixable} 处）",
                       command=self._fix).pack(side="right", padx=8)
            ttk.Label(btns, text="只修叠字、闪帧、时间倒着走；\n超短、超长、读不完要你自己看。",
                      style="Muted.TLabel", justify="left").pack(side="left")

    def _selected_id(self):
        sel = self.tree.selection()
        return int(sel[0].split(":")[0]) if sel else None

    def _jump(self, _event=None):
        cue_id = self._selected_id()
        if cue_id is not None:
            self.on_jump(cue_id)

    def _fix(self):
        if self.on_fix(self.issues):
            self.destroy()


class ExportDialog(tk.Toplevel):
    """导出前勾选要哪几份。默认全选已有的 7 项，空轴不默认勾。"""

    FORMATS = {"SRT": "srt", "ASS": "ass"}

    def __init__(self, parent, choices, checked, disabled=frozenset(), blank_format="srt", note="", ass_style=None,
                 ass_style_mode="keep"):
        super().__init__(parent)
        self.title("导出 · 勾选要哪几份")
        self.resizable(False, False)
        self.transient(parent)
        self.result = None
        self.vars = {}
        self.ass_style = ass_style
        # 必须沿用项目里已经选好的样式来源：写死成 keep 会把「ASS 样式」里选的模板改回原文件，
        # 于是改在模板上的字号和颜色落不到导出结果里，而且毫无提示。
        self.ass_style_mode = tk.StringVar(value=ass_style_mode or "keep") if ass_style else None
        self.blank_format = tk.StringVar(value="ASS" if blank_format == "ass" else "SRT")
        self.hint = tk.StringVar()
        self.note = note
        body = ttk.Frame(self, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="勾选这次要导出的内容", font=(FONT_FAMILY, 15, "bold")).pack(anchor="w", pady=(0, 6))
        ttk.Label(body, text="默认全部勾选时就等于原来的一键导出；空轴只有时间轴、没有文字，交给人工翻译用。",
                  style="Muted.TLabel", wraplength=470).pack(anchor="w", pady=(0, 12))
        for key, label in choices:
            state = "disabled" if key in disabled else "normal"
            if key == "blank":
                row = ttk.Frame(body)
                row.pack(fill="x", pady=2)
                var = tk.BooleanVar(value=False)
                self.vars[key] = var
                ttk.Checkbutton(row, text=label, variable=var, command=self.sync, state=state).pack(side="left")
                self.blank_box = ttk.Combobox(row, textvariable=self.blank_format, values=list(self.FORMATS),
                                              state="disabled", width=6)
                self.blank_box.pack(side="left", padx=(12, 4))
                ttk.Label(row, text="格式", style="Muted.TLabel").pack(side="left")
                continue
            var = tk.BooleanVar(value=key in checked and state == "normal")
            self.vars[key] = var
            ttk.Checkbutton(body, text=label, variable=var, command=self.sync, state=state).pack(anchor="w", pady=2)
        if ass_style:
            box = ttk.LabelFrame(body, text="ASS 样式", padding=10)
            box.pack(fill="x", pady=(14, 0))
            ttk.Radiobutton(box, text="保留原文件样式（推荐）", value="keep",
                            variable=self.ass_style_mode).pack(anchor="w")
            ttk.Radiobutton(box, text="换成设置里的模板样式：%s" % (ass_style.get("label") or "（还没选模板）"),
                            value="template", variable=self.ass_style_mode,
                            state="normal" if ass_style.get("available") else "disabled").pack(anchor="w")
            ttk.Label(box, wraplength=440, style="Muted.TLabel",
                      text="导入的 .ass 通常已经带着你那套样式；只有想强制换成模板时才选第二项，"
                           "换成模板会把字体、颜色、位置连同"
                           "内联标签和换行一起重新生成；字号和颜色在「ASS 样式…」里改，两种来源都生效。"
                      ).pack(anchor="w", pady=(4, 0))
        ttk.Label(body, textvariable=self.hint, wraplength=470, style="Muted.TLabel").pack(anchor="w", pady=(14, 0))
        footer = ttk.Frame(body)
        footer.pack(fill="x", pady=(16, 0))
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right")
        self.ok_button = ttk.Button(footer, text="导出", command=self.accept, style="Accent.TButton")
        self.ok_button.pack(side="right", padx=8)
        self.bind("<Return>", lambda event: self.accept())
        self.bind("<Escape>", lambda event: self.destroy())
        self.sync()
        self.grab_set()

    def selection(self):
        return {key for key, var in self.vars.items() if var.get()}

    def sync(self):
        blank = self.vars.get("blank")
        self.blank_box.configure(state="readonly" if blank is not None and blank.get() else "disabled")
        chosen = self.selection()
        self.ok_button.configure(state="normal" if chosen else "disabled")
        if not chosen:
            self.hint.set("请至少勾选一项。")
        elif self.note:
            self.hint.set(self.note)
        else:
            self.hint.set("导出到输出目录下按时间新建的文件夹，不覆盖以前的交付。")

    def accept(self):
        chosen = self.selection()
        if not chosen:
            return
        self.result = {"include": chosen, "blank_format": self.FORMATS.get(self.blank_format.get(), "srt")}
        if self.ass_style_mode is not None:
            self.result["ass_style_mode"] = self.ass_style_mode.get()
        self.destroy()


class AssStyleDialog(tk.Toplevel):
    """ASS 样式：文字、位置、颜色。

    样式来源二选一——导入文件里的样式，或设置里的模板——**不管选哪一套都能改**。
    改的是导出时用的覆盖值；原 .ass 和模板文件都不会被改动。
    """

    FIELDS = (("Fontsize", "字号", "number"), ("Fontname", "字体", "text"),
              ("PrimaryColour", "主色", "colour"), ("OutlineColour", "描边色", "colour"),
              ("Outline", "描边粗细", "number"), ("Shadow", "阴影", "number"),
              ("MarginL", "左留白", "number"), ("MarginR", "右留白", "number"),
              ("MarginV", "距边", "number"),
              ("Alignment", "位置", "align"))
    # ASS 的对齐编号沿用数字小键盘的排布，这里按同样的形状摆，好认
    ALIGN_ROWS = (("7", "上左"), ("8", "上中"), ("9", "上右")), (("4", "中左"), ("5", "正中"), ("6", "中右")), (("1", "下左"), ("2", "下中"), ("3", "下右"))
    TEXT_KEYS = ("Fontsize", "Fontname", "PrimaryColour", "OutlineColour", "Outline", "Shadow")
    MARGIN_KEYS = ("MarginL", "MarginR", "MarginV")

    def __init__(self, parent, sources, current_source="keep", current_edits=None):
        super().__init__(parent)
        import ass
        self._ass = ass
        self.title("ASS 样式 · 文字 / 位置 / 颜色")
        self.geometry("760x700")
        self.minsize(700, 640)
        self.transient(parent)
        self.result = None
        self.sources = {key: item for key, item in (sources or {}).items() if item.get("styles")}
        self.edits = {name: dict(changes) for name, changes in (current_edits or {}).items()}
        first = current_source if current_source in self.sources else next(iter(self.sources), "")
        self.source = tk.StringVar(value=first)
        self.style_name = tk.StringVar()
        self.vars = {}
        self.colour_vars = {}
        self._loaded = None

        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="样式从哪来（选哪一套都能改下面的值）").pack(anchor="w")
        row = ttk.Frame(body)
        row.pack(anchor="w", pady=(2, 10))
        for key, item in self.sources.items():
            ttk.Radiobutton(row, text=item.get("label") or key, value=key, variable=self.source,
                            command=self.reload_styles).pack(side="left", padx=(0, 18))
        pick = ttk.Frame(body)
        pick.pack(fill="x", pady=(0, 8))
        ttk.Label(pick, text="样式").pack(side="left")
        self.style_box = ttk.Combobox(pick, textvariable=self.style_name, state="readonly", width=26)
        self.style_box.pack(side="left", padx=8)
        self.style_box.bind("<<ComboboxSelected>>", lambda event: self.load_style())
        self.hint = tk.StringVar()
        ttk.Label(pick, textvariable=self.hint, style="Muted.TLabel").pack(side="left", padx=(10, 0))

        text_box = ttk.LabelFrame(body, text="文字", padding=10)
        text_box.pack(fill="x", pady=(2, 8))
        for index, (key, label, kind) in enumerate([f for f in self.FIELDS if f[0] in self.TEXT_KEYS]):
            column = (index % 2) * 3
            line = index // 2
            ttk.Label(text_box, text=label).grid(row=line, column=column, sticky="w", pady=4, padx=(0, 6))
            var = tk.StringVar()
            self.vars[key] = var
            if kind == "colour":
                holder = ttk.Frame(text_box)
                holder.grid(row=line, column=column + 1, sticky="w", padx=(0, 20))
                swatch = tk.Label(holder, width=3, relief="solid", borderwidth=1)
                swatch.pack(side="left")
                ttk.Entry(holder, textvariable=var, width=10).pack(side="left", padx=6)
                self.colour_vars[key] = swatch
                ttk.Button(holder, text="选色…", width=7,
                           command=lambda k=key: self.pick_colour(k)).pack(side="left")
            else:
                entry = ttk.Entry(text_box, textvariable=var, width=16)
                entry.grid(row=line, column=column + 1, sticky="w", padx=(0, 20))
                var.trace_add("write", lambda *_: self.update_preview())

        place = ttk.LabelFrame(body, text="位置", padding=10)
        place.pack(fill="x", pady=(2, 8))
        grid = ttk.Frame(place)
        grid.pack(side="left", padx=(0, 24))
        ttk.Label(place, text="字幕贴在画面的哪一块", style="Muted.TLabel").pack(anchor="w")
        align_var = tk.StringVar()
        self.vars["Alignment"] = align_var
        for r, line in enumerate(self.ALIGN_ROWS):
            for c, (value, label) in enumerate(line):
                ttk.Radiobutton(grid, text=label, value=value, variable=align_var, width=6).grid(
                    row=r, column=c, padx=1, pady=1)
        margins = ttk.Frame(place)
        margins.pack(side="left")
        ttk.Label(margins, text="留白（数字越大离画面边缘越远）", style="Muted.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        for index, (key, label, _kind) in enumerate([f for f in self.FIELDS if f[0] in self.MARGIN_KEYS]):
            ttk.Label(margins, text=label).grid(row=index + 1, column=0, sticky="w", pady=3, padx=(0, 6))
            var = tk.StringVar()
            self.vars[key] = var
            ttk.Entry(margins, textvariable=var, width=10).grid(row=index + 1, column=1, sticky="w", pady=3)

        ttk.Label(body, text="预览（按比例缩小，颜色和字体照实际；位置以播放器为准）",
                  style="Muted.TLabel").pack(anchor="w")
        self.sample = tk.Label(body, text="", anchor="w")
        self.sample.pack(anchor="w", pady=(2, 8))
        self.note = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.note, wraplength=700, style="Muted.TLabel").pack(anchor="w")

        footer = ttk.Frame(body)
        footer.pack(fill="x", pady=(12, 0))
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(footer, text="确定", command=self.confirm, style="Accent.TButton").pack(side="right", padx=(0, 8))
        ttk.Button(footer, text="全部还原", command=self.reset_all).pack(side="left")
        self.bind("<Return>", lambda event: self.confirm())
        self.bind("<Escape>", lambda event: self.destroy())
        self.reload_styles()
        self.grab_set()

    # ---------- 取值 ----------
    def styles(self):
        return (self.sources.get(self.source.get()) or {}).get("styles") or {}

    def current(self, name, field):
        """某样式的当前值：先看改过的，再看原值。"""
        if name in self.edits and field in self.edits[name]:
            return self.edits[name][field]
        info = self.styles().get(name) or {}
        names = info.get("format") or []
        if field in names:
            return info["fields"][names.index(field)]
        return ""

    def reload_styles(self):
        names = list(self.styles())
        self.style_box["values"] = names
        # 优先选中导出真正会用的那个样式（模板模式下就是设置里指定的样式名）。
        # 两套样式表里常有同名样式，停在名字相同但对象不同的那个上，会改了却没效果。
        preferred = (self.sources.get(self.source.get()) or {}).get("preferred")
        if preferred in names:
            self.style_name.set(preferred)
        elif self.style_name.get() not in names:
            self.style_name.set(names[0] if names else "")
        self.hint.set("这份样式表里有 %d 个样式" % len(names) if names else "这一套里没有可识别的样式")
        self.load_style()

    def load_style(self):
        name = self.style_name.get()
        for key, _label, kind in self.FIELDS:
            raw = self.current(name, key)
            if kind == "colour":
                rgb = self._ass.colour_to_rgb(str(raw)) if raw else None
                self.vars[key].set(rgb or "")
            elif kind == "align":
                self.vars[key].set(str(raw).strip())
            else:
                self.vars[key].set(str(raw))
        self._loaded = name
        self.update_preview()

    def update_preview(self):
        name = self.style_name.get()
        try:
            size = float(self.vars["Fontsize"].get())
        except (ValueError, KeyError):
            size = 48.0
        family = self.vars["Fontname"].get().strip() or "Microsoft YaHei UI"
        try:
            self.sample.configure(text="示例（%s 字号 %g）：这是一行字幕" % (name, size),
                                  font=(family, max(8, int(round(size / 4)))),
                                  fg=self.vars["PrimaryColour"].get() or "#000000")
        except tk.TclError:
            pass
        for key, swatch in self.colour_vars.items():
            try:
                swatch.configure(background=self.vars[key].get() or "#ffffff")
            except tk.TclError:
                pass

    # ---------- 改值 ----------
    def store(self):
        """把当前表单里的值记进 edits：**只记真正变了的字段**。

        表单里预填的是原值，所以必须跟原值比；否则没动过的字段也会被当成改动，
        日志会谎报，也没法「全部还原」。
        """
        name = self._loaded
        if not name:
            return
        info = self.styles().get(name) or {}
        names = info.get("format") or []
        changes = dict(self.edits.get(name) or {})
        for key, _label, kind in self.FIELDS:
            if key not in names:
                continue
            original = info["fields"][names.index(key)]
            text = self.vars[key].get().strip()
            if not text:
                changes.pop(key, None)
                continue
            if kind == "colour":
                value = self._ass.rgb_to_colour(text, original)
                if value is None or value.upper() == str(original).strip().upper():
                    changes.pop(key, None)
                else:
                    changes[key] = value
            elif kind == "number":
                try:
                    number = float(text)
                except ValueError:
                    changes.pop(key, None)
                    continue
                try:
                    same = float(str(original)) == number
                except ValueError:
                    same = False
                if same:
                    changes.pop(key, None)
                else:
                    changes[key] = self._ass.format_size(number)
            elif kind == "align":
                if text == str(original).strip():
                    changes.pop(key, None)
                else:
                    changes[key] = text
            elif text == str(original).strip():
                changes.pop(key, None)
            else:
                changes[key] = text
        if changes:
            self.edits[name] = changes
        else:
            self.edits.pop(name, None)

    def pick_colour(self, key):
        from tkinter import colorchooser
        current = self.vars[key].get() or "#ffffff"
        chosen = colorchooser.askcolor(color=current, parent=self, title="选择颜色")
        if chosen and chosen[1]:
            self.vars[key].set(chosen[1].upper())
            self.update_preview()

    def reset_all(self):
        self.edits = {}
        self.load_style()
        self.note.set("已还原成原文件的样式值。")

    # ---------- 确定 ----------
    def confirm(self):
        self.store()
        sizes, bad = {}, []
        for name, changes in self.edits.items():
            if "Fontsize" in changes:
                try:
                    value = float(changes["Fontsize"])
                except ValueError:
                    bad.append(name)
                    continue
                if not 0 < value <= 999:
                    bad.append(name)
                    continue
                sizes[name] = value
        if bad:
            messagebox.showwarning("ASS 样式", "这些样式的字号要填 1 到 999 之间的数字：\n" + "、".join(bad), parent=self)
            return
        self.result = {"source": self.source.get(), "edits": self.edits, "font_sizes": sizes}
        self.destroy()
