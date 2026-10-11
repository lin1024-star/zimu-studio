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
FG_PARTIAL = "#cfd6e6"
FONT_UI = ("微软雅黑", 9)      # 工具栏和对话框用，不跟着字幕字号变
FONT_JA = ("微软雅黑", 9)

# 字幕窗默认放在「屏幕下方居中」，像真字幕那样。
# 放左上角会被人全屏看直播时的播放器盖住——这是实测踩到的坑。
WIN_W, WIN_H = 1100, 250
BOTTOM_GAP = 150

# ---------- 外观（透明度 / 字号 / 宽度）----------
# 这三个数是使用者在「外观…」里能调的。
# **高度不给单独调** —— 它按字号算出来（见 window_height）：手调高度容易把三行切掉。
APPEARANCE_DEFAULT = {"alpha": 0.93, "width": WIN_W, "font": 20}
ALPHA_MIN, ALPHA_MAX = 0.30, 1.00      # 最低只到 30%：再低就真看不清了
FONT_MIN, FONT_MAX = 12, 40
WIDTH_MIN, WIDTH_MAX = 600, 1920
_FONT_BASE = 20.0                       # 基准：字号 20 对应现在的样子（宽 1100、高 250）


def clamp_appearance(values):
    """把一套外观值夹到合理范围内；缺的、坏的都用默认值补上。

    设置文件是使用者机器上的，可能被手工改过或者写到一半断电，所以这里不信任它。
    """
    result = dict(APPEARANCE_DEFAULT)
    if not isinstance(values, dict):
        return result
    try:
        if "alpha" in values:
            result["alpha"] = min(ALPHA_MAX, max(ALPHA_MIN, float(values["alpha"])))
        if "font" in values:
            result["font"] = min(FONT_MAX, max(FONT_MIN, int(values["font"])))
        if "width" in values:
            result["width"] = min(WIDTH_MAX, max(WIDTH_MIN, int(values["width"])))
    except (TypeError, ValueError):
        return dict(APPEARANCE_DEFAULT)
    return result


def subtitle_fonts(size):
    """按字号算出一整组字体。

    字号必须整组一起变 —— 只把窗口放大、字不变，画面会很难看。
    半句和整句的字号本来就一样（只差颜色），所以这里只有一个 zh。
    """
    size = max(FONT_MIN, min(FONT_MAX, int(size)))
    return {
        "zh": ("微软雅黑", size, "bold"),
        "zh_old": ("微软雅黑", max(10, int(round(size * 0.7)))),
    }


def window_height(font_size):
    """窗口高度跟着字号等比走：字号 20 → 250，就是原来的默认样子。"""
    size = max(FONT_MIN, min(FONT_MAX, int(font_size)))
    return int(round(WIN_H * size / _FONT_BASE))


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
                 list_sources=None, on_pick_source=None, source_id="default",
                 appearance=None, on_save_appearance=None):
        self.on_quit = on_quit
        self.on_toggle_pause = on_toggle_pause
        self.on_reload_glossary = on_reload_glossary
        self.suggest = suggest or (lambda ja: [])
        self.save_term = save_term or (lambda a, b: (False, "没接上保存"))
        self.list_sources = list_sources or (lambda: [])
        self.on_pick_source = on_pick_source or (lambda sid: None)
        self.on_save_appearance = on_save_appearance or (lambda values: None)
        self.source_id = source_id
        self.visible = visible
        self.appearance = clamp_appearance(appearance)
        self.fonts = subtitle_fonts(self.appearance["font"])
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
        self.root.configure(bg=BG)
        width = self.appearance["width"]
        height = window_height(self.appearance["font"])
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - width) // 2)
        y = max(0, sh - height - BOTTOM_GAP)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        bar = tk.Frame(self.root, bg="#1a1a1e")
        bar.pack(fill="x")
        tk.Label(bar, text=" 实时字幕", bg="#1a1a1e", fg="#cccccc", font=FONT_UI).pack(side="left", pady=3)
        self.source_btn = tk.Button(bar, text="音源", command=self._pick_source, font=FONT_UI,
                                    bg="#1a1a1e", fg="#cccccc", activebackground="#2a2a30",
                                    relief="flat", padx=10)
        for text, cmd in (("×", self._quit), ("术语表", self._reload), ("暂停", self._toggle),
                          ("外观", self._appearance)):
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
            label = tk.Label(row, text="", bg=BG, fg=FG_OLD, font=self.fonts["zh_old"],
                             anchor="w", justify="left", wraplength=max(200, width - 80))
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

        # 界面搭完了才应用外观：字号要写进每一行的 label，此时它们才存在。
        self._apply_appearance()
        self._top_job = None
        if visible:
            self._keep_top()

    # ---------- 外观 ----------
    def _apply_appearance(self, reposition=False):
        """把当前外观应用到窗口上。可以反复调用，不用重建窗口。

        `reposition=True` 时会顺便改窗口尺寸 —— 以**窗口中心**为锚点，
        免得一拖动滑块整窗就跳走。
        """
        self.appearance = clamp_appearance(self.appearance)
        values = self.appearance
        self.fonts = subtitle_fonts(values["font"])
        if self.visible:
            self.root.attributes("-alpha", values["alpha"])

        width = values["width"]
        height = window_height(values["font"])
        if reposition:
            old_w = max(1, self.root.winfo_width())
            old_h = max(1, self.root.winfo_height())
            cx = self.root.winfo_x() + old_w // 2
            cy = self.root.winfo_y() + old_h // 2
            x = max(0, min(self.root.winfo_screenwidth() - width, cx - width // 2))
            y = max(0, min(self.root.winfo_screenheight() - height, cy - height // 2))
            self.root.geometry(f"{width}x{height}+{x}+{y}")

        wrap = max(200, width - 80)
        for index, line in enumerate(self.lines):
            line["label"].configure(font=self._label_font(index), wraplength=wrap)
        self.status.configure(font=FONT_UI)

    def _label_font(self, index):
        """第 0 行是当前这句（大），往下的旧句小一号。"""
        return self.fonts["zh"] if index == 0 else self.fonts["zh_old"]

    def _appearance(self):
        """打开「外观」对话框。改动实时生效，点「完成」才记住。"""
        AppearanceDialog(self.root, self.appearance, self._preview_appearance,
                         self.on_save_appearance)

    def _preview_appearance(self, values):
        """对话框拖动滑杆时调用：立刻套用，但不写文件。"""
        self.appearance = clamp_appearance(values)
        self._apply_appearance(reposition=True)

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
        self.lines[0]["label"].configure(text=text, fg="#6f6f7a", font=self.fonts["zh_old"])

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
                font=self._label_font(0))
            return
        self.add_line(zh, item.get("ja", ""), sid=sid, partial=bool(item.get("partial")))

    def add_line(self, zh, ja="", sid=None, partial=False):
        for i in range(len(self.lines) - 1, 0, -1):
            src, dst = self.lines[i - 1], self.lines[i]
            dst.update({k: src[k] for k in ("zh", "ja", "sid", "partial")})
            dst["label"].configure(text=src["zh"], fg=FG_OLD, font=self.fonts["zh_old"])
            dst["plus"].configure(fg="#555560")
        top = self.lines[0]
        top.update({"zh": zh, "ja": ja, "sid": sid, "partial": partial})
        top["label"].configure(text=zh, fg=FG_PARTIAL if partial else FG_NEW,
                               font=self._label_font(0))
        top["plus"].configure(fg=ACCENT)

    def set_status(self, text):
        self.status.configure(text=text)

    def run(self):
        self.root.mainloop()


class AppearanceDialog(tk.Toplevel):
    """调悬浮字幕的外观：透明度 / 字号 / 宽度。

    三个滑杆**一拖就实时生效**（所见即所得，不用关掉再开）；
    点「完成」才写进设置文件，点「取消」退回打开前的样子。

    **没有高度滑杆是故意的**：高度按字号算（`window_height`），
    手调高度很容易把三行切掉 —— 窗口看着在、第三行只露出半截。
    """

    def __init__(self, parent, appearance, on_change, on_save):
        super().__init__(parent)
        self.on_change = on_change
        self.on_save = on_save
        self.original = clamp_appearance(appearance)
        self.title("字幕外观")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        try:
            self.transient(parent)
        except tk.TclError:
            pass

        self.alpha_var = tk.IntVar(value=int(round(self.original["alpha"] * 100)))
        self.font_var = tk.IntVar(value=self.original["font"])
        self.width_var = tk.IntVar(value=self.original["width"])
        self._readouts = []

        self._slider("透明度", self.alpha_var, int(ALPHA_MIN * 100), int(ALPHA_MAX * 100), "%d%%",
                     "调低字幕更透，看得见后面的画面。最低只到 30% —— 再低就真看不清了。")
        self._slider("字号", self.font_var, FONT_MIN, FONT_MAX, "%d",
                     "字号一变，行高和窗口高度会自动跟着变。")
        self._slider("宽度", self.width_var, WIDTH_MIN, WIDTH_MAX, "%d",
                     "窄一点适合小屏；长句子会自动折行，不会跑出窗口。")

        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill="x", padx=14, pady=(10, 14))
        tk.Button(buttons, text="恢复默认", font=FONT_UI, bg="#2a2a30", fg="#dddddd",
                  activebackground="#33333a", relief="flat", padx=12,
                  command=self._reset).pack(side="left")
        tk.Button(buttons, text="取消", font=FONT_UI, bg="#2a2a30", fg="#dddddd",
                  activebackground="#33333a", relief="flat", padx=12,
                  command=self._cancel).pack(side="right")
        tk.Button(buttons, text="完成", font=FONT_UI, bg="#2a2a30", fg=ACCENT,
                  activebackground="#33333a", relief="flat", padx=12,
                  command=self._done).pack(side="right", padx=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._place(beside=parent)

    def _slider(self, title, var, low, high, fmt, hint):
        tk.Label(self, text=title, bg=BG, fg="#dddddd", font=FONT_UI).pack(anchor="w", padx=14, pady=(10, 0))
        readout = tk.Label(self, text=fmt % var.get(), bg=BG, fg=ACCENT, font=FONT_UI)
        readout.pack(anchor="w", padx=14)
        self._readouts.append((readout, fmt, var))
        tk.Scale(self, from_=low, to=high, orient="horizontal", variable=var,
                 command=lambda _value: self._refresh(), showvalue=False,
                 length=330, bg=BG, fg="#dddddd", troughcolor="#2a2a30",
                 activebackground=ACCENT, highlightthickness=0, sliderrelief="flat",
                 bd=0).pack(fill="x", padx=14)
        tk.Label(self, text=hint, bg=BG, fg="#777777", font=FONT_UI,
                 wraplength=330, justify="left").pack(anchor="w", padx=14)

    def _place(self, beside):
        """默认贴在字幕窗上方；放不下就退回屏幕中间。"""
        self.update_idletasks()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        try:
            x = beside.winfo_x() + (beside.winfo_width() - width) // 2
            y = beside.winfo_y() - height - 12
        except tk.TclError:
            x = y = None
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if x is None or y is None or y < 0:
            x, y = (sw - width) // 2, max(0, (sh - height) // 2 - 120)
        self.geometry(f"+{max(0, min(sw - width, x))}+{max(0, min(sh - height, y))}")

    def _current(self):
        return {"alpha": self.alpha_var.get() / 100.0,
                "font": self.font_var.get(),
                "width": self.width_var.get()}

    def _refresh(self):
        for readout, fmt, var in self._readouts:
            readout.configure(text=fmt % var.get())
        self.on_change(self._current())

    def _reset(self):
        self.alpha_var.set(int(round(APPEARANCE_DEFAULT["alpha"] * 100)))
        self.font_var.set(APPEARANCE_DEFAULT["font"])
        self.width_var.set(APPEARANCE_DEFAULT["width"])
        self._refresh()

    def _cancel(self):
        self.on_change(dict(self.original))
        self.destroy()

    def _done(self):
        self.on_save(self._current())
        self.destroy()
