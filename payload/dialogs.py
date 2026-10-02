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
                  read_srt, set_project_duration, timestamp)

FONT_FAMILY = "Microsoft YaHei UI" if sys.platform == "win32" else "Noto Sans CJK SC"


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
