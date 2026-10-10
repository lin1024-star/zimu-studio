"""屏幕悬浮字幕窗。

无边框、置顶、半透明、可拖动。每行字幕右边有个「＋」，点开就能把这一句里
翻得不对的词记进术语表，后面立刻按新的翻。

界面上不显示日文原文（看直播的人不需要），但每行内部保留原文，
因为「加词」对话框要拿它来推荐候选词。
"""
import tkinter as tk
from tkinter import messagebox

MAX_LINES = 3
BG = "#0e0e10"
FG_NEW = "#ffffff"
FG_OLD = "#a8a8b2"
ACCENT = "#7fd1ff"
FONT_ZH = ("微软雅黑", 20, "bold")
FONT_ZH_PARTIAL = ("微软雅黑", 20, "bold")   # 半句：字号一样，颜色淡一点表示还没定型
FG_PARTIAL = "#cfd6e6"
FONT_ZH_OLD = ("微软雅黑", 14)
FONT_UI = ("微软雅黑", 9)
FONT_JA = ("微软雅黑", 9)

# 字幕窗默认放在「屏幕下方居中」，像真字幕那样。
# 放左上角会被人全屏看直播时的播放器盖住——这是实测踩到的坑。
WIN_W, WIN_H = 1100, 250
BOTTOM_GAP = 150


class SourceDialog(tk.Toplevel):
    """选音频来源：抓哪个扬声器的声音，还是抓麦克风。

    场景：笔记本插了耳机，声音就不从扬声器出来了——默认扬声器的回环会变成静音。
    这时候要能手动切到「正在出声的那个设备」。
    """

    def __init__(self, parent, sources, current_id, on_pick, on_refresh=None):
        super().__init__(parent)
        self.on_pick = on_pick
        self.title("选音频来源")
        self.configure(bg=BG, padx=16, pady=14)
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.variable = tk.StringVar(value=current_id or "default")

        tk.Label(self, text="抓哪个声音来识别？", bg=BG, fg="#dddddd",
                 font=(FONT_UI[0], FONT_UI[1] + 3, "bold")).pack(anchor="w")
        tk.Label(self, text="换了耳机 / 换了播放设备之后，声音不从原来的设备出来了，就得来这里切一下。",
                 bg=BG, fg="#888888", font=FONT_UI, wraplength=520,
                 justify="left").pack(anchor="w", pady=(4, 10))

        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="x")
        self._render(sources)

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", pady=(14, 0))
        tk.Button(btns, text="用这个", command=self._apply, font=FONT_UI,
                  bg=ACCENT, fg="#00222f", relief="flat", padx=16, pady=3).pack(side="right")
        tk.Button(btns, text="取消", command=self.destroy, font=FONT_UI,
                  bg="#2a2a30", fg="#cccccc", relief="flat", padx=12, pady=3).pack(side="right", padx=6)
        if on_refresh is not None:
            self.on_refresh = on_refresh
            tk.Button(btns, text="重新扫描设备", command=self._refresh, font=FONT_UI,
                      bg="#2a2a30", fg="#cccccc", relief="flat", padx=12, pady=3).pack(side="left")
        tk.Label(self, text="「系统声音（默认扬声器）」会跟着系统的默认播放设备走；"
                            "固定选某个设备则不会跟着变。",
                 bg=BG, fg="#666666", font=FONT_UI, wraplength=520,
                 justify="left").pack(anchor="w", pady=(10, 0))
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()

    def _render(self, sources):
        for child in self.body.winfo_children():
            child.destroy()
        if not sources:
            tk.Label(self.body, text="一个音频设备都没扫到。", bg=BG, fg="#ff9c9c",
                     font=FONT_UI).pack(anchor="w")
            return
        for item in sources:
            tk.Radiobutton(self.body, text=item["label"], variable=self.variable,
                           value=item["id"], bg=BG, fg="#dddddd", font=FONT_UI,
                           selectcolor="#2a2a30", activebackground=BG, activeforeground=ACCENT,
                           anchor="w", justify="left", wraplength=500).pack(anchor="w", pady=2)

    def _refresh(self):
        sources = self.on_refresh()
        self._render(sources)

    def _apply(self):
        self.on_pick(self.variable.get())
        self.destroy()


class GlossaryDialog(tk.Toplevel):
    """给这一句加一条术语。日文候选用按钮点选，不用自己找。"""

    def __init__(self, parent, ja_text, zh_text, suggestions, on_save):
        super().__init__(parent)
        self.on_save = on_save
        self.title("把这句话里的词记下来")
        self.configure(bg=BG, padx=14, pady=12)
        self.attributes("-topmost", True)
        self.resizable(False, False)

        tk.Label(self, text="这句话翻得不对？把那个词记下来，后面就照这个翻。",
                 bg=BG, fg="#dddddd", font=FONT_UI).pack(anchor="w")
        tk.Label(self, text="日文原文", bg=BG, fg="#777777", font=FONT_UI).pack(anchor="w", pady=(10, 2))
        tk.Label(self, text=ja_text or "（没有原文）", bg="#1a1a1e", fg="#cccccc", font=FONT_JA,
                 wraplength=460, justify="left", padx=8, pady=6).pack(anchor="w", fill="x")
        tk.Label(self, text="现在翻成", bg=BG, fg="#777777", font=FONT_UI).pack(anchor="w", pady=(8, 2))
        tk.Label(self, text=zh_text or "（还没翻出来）", bg="#1a1a1e", fg="#cccccc", font=FONT_JA,
                 wraplength=460, justify="left", padx=8, pady=6).pack(anchor="w", fill="x")

        if suggestions:
            tk.Label(self, text="这一句里可能是专有名词的（点一下填进下面）：",
                     bg=BG, fg="#777777", font=FONT_UI).pack(anchor="w", pady=(10, 4))
            chips = tk.Frame(self, bg=BG)
            chips.pack(anchor="w", fill="x")
            for i, s in enumerate(suggestions):
                tk.Button(chips, text=s, font=FONT_JA, bg="#2a2a30", fg=ACCENT,
                          activebackground="#3a3a44", relief="flat", padx=8, pady=1,
                          command=lambda w=s: self._fill_ja(w)).grid(row=i // 4, column=i % 4,
                                                                     padx=2, pady=2, sticky="w")

        row = tk.Frame(self, bg=BG)
        row.pack(anchor="w", fill="x", pady=(12, 0))
        tk.Label(row, text="日文词", bg=BG, fg="#aaaaaa", font=FONT_UI).grid(row=0, column=0, sticky="w")
        self.ja_var = tk.StringVar()
        tk.Entry(row, textvariable=self.ja_var, width=26, font=FONT_JA,
                 bg="#1a1a1e", fg="#ffffff", insertbackground="#ffffff").grid(row=1, column=0, padx=(0, 10))
        tk.Label(row, text="要翻成", bg=BG, fg="#aaaaaa", font=FONT_UI).grid(row=0, column=1, sticky="w")
        self.zh_var = tk.StringVar()
        ent = tk.Entry(row, textvariable=self.zh_var, width=26, font=FONT_JA,
                       bg="#1a1a1e", fg="#ffffff", insertbackground="#ffffff")
        ent.grid(row=1, column=1)

        btns = tk.Frame(self, bg=BG)
        btns.pack(anchor="e", pady=(14, 0))
        tk.Button(btns, text="保存", command=self._save, font=FONT_UI,
                  bg=ACCENT, fg="#00222f", relief="flat", padx=16, pady=3).pack(side="right")
        tk.Button(btns, text="取消", command=self.destroy, font=FONT_UI,
                  bg="#2a2a30", fg="#cccccc", relief="flat", padx=12, pady=3).pack(side="right", padx=6)
        ent.focus_set()
        self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self.destroy())

    def _fill_ja(self, word):
        self.ja_var.set(word)

    def _save(self):
        ok, msg = self.on_save(self.ja_var.get(), self.zh_var.get())
        if ok:
            messagebox.showinfo("已记住", msg, parent=self)
            self.destroy()
        else:
            messagebox.showwarning("没记住", msg, parent=self)


class Overlay:
    """悬浮字幕窗。所有界面操作都在主线程；工作线程只往队列里放东西。"""

    def __init__(self, on_quit=None, on_toggle_pause=None, on_reload_glossary=None,
                 suggest=None, save_term=None, visible=True,
                 list_sources=None, on_pick_source=None, source_id="default"):
        self.on_quit = on_quit
        self.on_toggle_pause = on_toggle_pause
        self.on_reload_glossary = on_reload_glossary
        self.suggest = suggest or (lambda ja: [])
        self.save_term = save_term or (lambda a, b: (False, "没接上保存"))
        self.list_sources = list_sources or (lambda: [])
        self.on_pick_source = on_pick_source or (lambda sid: None)
        self.source_id = source_id
        self.paused = False
        self._drag = (0, 0)

        self.root = tk.Tk()
        self.root.title("实时字幕")
        if not visible:
            # 自检用：建好但不显示，屏幕上不会闪出任何东西
            self.root.attributes("-alpha", 0.0)
            self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        if visible:
            self.root.attributes("-alpha", 0.93)
        self.root.configure(bg=BG)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - WIN_W) // 2)
        y = max(0, sh - WIN_H - BOTTOM_GAP)
        self.root.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

        bar = tk.Frame(self.root, bg="#1a1a1e")
        bar.pack(fill="x")
        tk.Label(bar, text=" 实时字幕", bg="#1a1a1e", fg="#cccccc", font=FONT_UI).pack(side="left", pady=3)
        self.source_btn = tk.Button(bar, text="音源", command=self._pick_source, font=FONT_UI,
                                    bg="#1a1a1e", fg="#cccccc", activebackground="#2a2a30",
                                    relief="flat", padx=10)
        for text, cmd in (("×", self._quit), ("术语表", self._reload), ("暂停", self._toggle)):
            tk.Button(bar, text=text, command=cmd, font=FONT_UI, bg="#1a1a1e", fg="#cccccc",
                      activebackground="#2a2a30", relief="flat", padx=10).pack(side="right")
        self.source_btn.pack(side="right")
        self._refresh_source_btn()
        bar.bind("<Button-1>", self._drag_start)
        bar.bind("<B1-Motion>", self._drag_move)

        self.lines_box = tk.Frame(self.root, bg=BG)
        self.lines_box.pack(fill="both", expand=True, padx=10, pady=(8, 0))

        self.status = tk.Label(self.root, text="正在启动…", bg=BG, fg="#777777",
                               font=FONT_UI, anchor="w")
        self.status.pack(fill="x", padx=10, pady=(0, 6))

        self.lines = []
        for _ in range(MAX_LINES):
            row = tk.Frame(self.lines_box, bg=BG)
            label = tk.Label(row, text="", bg=BG, fg=FG_OLD, font=FONT_ZH_OLD,
                             anchor="w", justify="left", wraplength=620)
            label.pack(side="left", fill="x", expand=True)
            plus = tk.Button(row, text="＋", font=FONT_UI, bg=BG, fg="#555560",
                             activebackground="#2a2a30", relief="flat", padx=4,
                             command=lambda i=len(self.lines): self._add_term(i))
            plus.pack(side="right", padx=(6, 0))
            # 这一行必须要有：之前漏了 row.pack()，标签一直存在但从来没被画出来，
            # 界面上就是「窗口在、里面空的」。
            row.pack(fill="x", pady=3)
            self.lines.append({"row": row, "label": label, "plus": plus,
                               "zh": "", "ja": "", "sid": None, "partial": False})

        self._top_job = None
        if visible:
            self._keep_top()

    # ---------- 保住置顶 ----------
    def _keep_top(self):
        """有些全屏播放器会把别人的置顶顶掉，隔几秒再举一次，保证字幕看得见。"""
        try:
            self.root.attributes("-topmost", True)
            self.root.lift()
        except tk.TclError:
            return
        self._top_job = self.root.after(3000, self._keep_top)

    def show_placeholder(self, text):
        """还没出字幕时先显示一句提示，让人确认窗口在、而且活着。"""
        self.lines[0]["label"].configure(text=text, fg="#6f6f7a", font=FONT_ZH_OLD)

    # ---------- 拖动 ----------
    def _drag_start(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    # ---------- 按钮 ----------
    def _quit(self):
        if self.on_quit:
            self.on_quit()
        self.root.destroy()

    def _toggle(self):
        self.paused = not self.paused
        if self.on_toggle_pause:
            self.on_toggle_pause(self.paused)
        self.set_status("已暂停" if self.paused else "运行中")

    def _reload(self):
        if self.on_reload_glossary:
            self.on_reload_glossary()
        messagebox.showinfo("术语表", "已重新读取术语表。\n\n要手动编辑的话，"
                                      "打开工作区里的 live_glossary_extra.txt。", parent=self.root)

    # ---------- 选音频来源 ----------
    def _refresh_source_btn(self):
        """把当前音源的名字缩到按钮上。

        标题栏很窄，所以：先去掉设备名后面括号里的型号说明
        （「麦克风阵列 (适用于数字麦克风的英特尔® 智音技术)」→「麦克风阵列」），
        再截断，麦克风加个「麦·」前缀好和设备区分。
        """
        from live.capture import source_label
        text = source_label(self.source_id)
        if text.startswith("系统声音（默认"):
            short = "默认"
        else:
            short = text.split("·", 1)[-1].strip() if "·" in text else text
            for sep in ("（", "("):
                if sep in short:
                    short = short.split(sep, 1)[0].strip()
            if text.startswith("麦克风"):
                short = "麦·" + short
        self.source_btn.configure(text=f"音源：{short[:8]}")

    def _pick_source(self):
        SourceDialog(self.root, self.list_sources(), self.source_id,
                     self._apply_source, self.list_sources)

    def _apply_source(self, source_id):
        if not source_id or source_id == self.source_id:
            return
        self.source_id = source_id
        self._refresh_source_btn()
        self.on_pick_source(source_id)

    def set_source(self, source_id):
        """由主程序告知音源变了（比如设备掉了自动退回默认）。"""
        self.source_id = source_id
        self._refresh_source_btn()

    def _add_term(self, index):
        item = self.lines[index]
        if not item["zh"] and not item["ja"]:
            messagebox.showinfo("这一行是空的", "还没有字幕可以加词。", parent=self.root)
            return
        GlossaryDialog(self.root, item["ja"], item["zh"],
                       self.suggest(item["ja"]), self.save_term)

    # ---------- 显示 ----------
    def show_subtitle(self, item):
        """显示一条字幕。

        「半句」和它后面「整句」是同一行：半句先显示出来让人早点看到，
        整句翻好之后原地替换掉，不新起一行。
        """
        zh = item.get("zh") or ""
        sid = item.get("id")
        top = self.lines[0]
        # 顶行现在是「半句」，而且新来的是同一句 → 原地替换（整句来了也走这里）。
        # 判断依据是「顶行原本是半句」，不是「新来的是半句」——否则整句会另起一行。
        if sid is not None and top.get("sid") == sid and top.get("partial") and zh:
            top["zh"] = zh
            top["ja"] = item.get("ja", "")
            top["partial"] = bool(item.get("partial"))
            top["label"].configure(
                text=zh,
                fg=FG_PARTIAL if top["partial"] else FG_NEW,
                font=FONT_ZH_PARTIAL if top["partial"] else FONT_ZH)
            return
        self.add_line(zh, item.get("ja", ""), sid=sid, partial=bool(item.get("partial")))

    def add_line(self, zh, ja="", sid=None, partial=False):
        for i in range(len(self.lines) - 1, 0, -1):
            src, dst = self.lines[i - 1], self.lines[i]
            dst.update({k: src[k] for k in ("zh", "ja", "sid", "partial")})
            dst["label"].configure(text=src["zh"], fg=FG_OLD, font=FONT_ZH_OLD)
            dst["plus"].configure(fg="#555560")
        top = self.lines[0]
        top.update({"zh": zh, "ja": ja, "sid": sid, "partial": partial})
        top["label"].configure(text=zh, fg=FG_PARTIAL if partial else FG_NEW,
                               font=FONT_ZH_PARTIAL if partial else FONT_ZH)
        top["plus"].configure(fg=ACCENT)

    def set_status(self, text):
        self.status.configure(text=text)

    def run(self):
        self.root.mainloop()
