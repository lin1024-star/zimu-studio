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

from core import (APP_VERSION, DEFAULT_MODEL, GLOSSARY_LIMIT, Cue, DeepSeekClient, UserError,
                  atomic_write, clean,
                  export_files, glossary_hits, import_srt_project, insert_project_cue, load_project,
                  parse_glossary, parse_time, read_srt, restore_axis_cues, save_duration_change,
                  save_offset_change, save_project, set_project_duration,
                  store_imported_project, timestamp, worker, write_blocked_hint,
                  CHINESE_ONLY_KEYS, EXPORT_ITEMS, EXPORT_LABELS, SOURCE_ONLY_KEYS, ZH_KEYS,
                  BUILTIN_MODEL_REPOS, local_model_status, model_download_mb,
                  MODEL_ORDER, MODEL_PROFILES, hardware_profile, model_pros_cons, model_table,
                  profile_text, recommend_model,
                  default_export_keys, default_export_selection, export_choices, should_alert)
from diagnostics import PHASES, Diagnostics, error_info
from dialogs import (AddCueDialog, AssStyleDialog, ChineseCheckDialog, DurationDialog,
                     ExportDialog, JaFixConfirmDialog,
                     JaIssueDialog, OffsetDialog, SrtImportDialog, TimelineQcDialog)
from paths import data_root
import chinese_check
import ja_fix
import timeline_qc

APP_DIR = Path(__file__).resolve().parent
USER_DIR = data_root()
CONFIG_PATH = USER_DIR / "settings.json"
FONT_FAMILY = "Microsoft YaHei UI" if sys.platform == "win32" else "Noto Sans CJK SC"
BG, PANEL, INK, MUTED, ACCENT = "#edf2f6", "#ffffff", "#182d41", "#5f7385", "#137b83"


def save_error_text(exc):
    """保存失败时给使用者看的话：权限类错误换成可操作提示，其余保留原文。"""
    return write_blocked_hint(exc) or str(exc)

HELP = """快速使用

1. 新视频可选择本地识别；已有字幕请点击「打开项目 / SRT…」，立即进入校对，不需要 API 密钥。
2. SRT 可选原文、中文、原文＋中文两份，或原文在上的混合版。两份字幕按相同起止时间配对；混合版超过两行时需确认语言分界。
3. 选择原视频语言。英语或日语最好手动指定；未知时可以自动识别。
4. 首次建议点击「仅识别原文」。完成后点选字幕条目，在下方校对原文、人名和术语，点击「保存此条修改」。
5. 点击「识别并翻译 / 继续」。已有原文不会重复识别；缺少的中文会通过 DeepSeek API 翻译。
6. 完成后自动导出七份文件：原文、中文、混合版各一份 SRT 和 TXT，以及一份中文 ASS（可在设置里导入自己的 .ass 样式模板）。混合版原文在上、中文在下，逐条对应。点击「打开输出文件夹」。
7. 手动导出点「导出文件…」会先弹出勾选面板：现有七项默认全勾，也可以只勾要的那几项。勾「空轴」会多导出一份只有时间轴、没有文字的字幕，用来交给人工翻译，格式可选 SRT 或 ASS（ASS 会套用设置里选的样式模板）。
8. 点击「新增字幕…」，填写开始、结束时间和原文，也可手填中文。保存后自动按时间插入；继续翻译只补译中文为空的条目。
9. 只有中文字幕时，可直接修改左侧正文并导出中文 SRT / TXT 两份。要生成原文和混合版，还需要对应原文。
10. 只有需要翻译缺失条目时才填写 DeepSeek API 密钥。默认只在本次运行内使用，关闭后清除；勾选「记住密钥」时用 Windows 账户级加密保存在本机（仅当前系统用户可解），仍不放进项目或导出文件。
11. 多个文件可点「添加多个文件…」加入队列，再点识别或翻译；队列依次处理，单个失败自动跳过，完成后统一提示。翻译前会按待译内容弹出费用预估。
12. 主界面按「① 选素材 → ② 开始 → ③ 校对 → ④ 导出」四步排。不常用的（待确认、时间轴体检、ASS 样式、新增字幕、时长设置、整体偏移、添加多个文件、清空队列）收在「更多操作 ▾」里，点一下展开、再点收起。

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

识别模型（不知道选哪个就看这里）

设置页最下面有「怎么选识别模型」：软件自动检测显卡和显存，给出一个推荐，并把六个模型的大小、速度、质量、适合什么时候用并排列出来。
**它只给依据，不会自动改设置** —— 想用哪个自己选；想一键采用推荐，点「用推荐的模型」。下拉框旁边也直接写着当前模型要不要下载、速度几颗星、质量几颗星。

实测（6 分 22 秒朗读剧，同一台机器、同一份术语表）：
· turbo 与 large-v3 在真正有分歧的 48 处里各赢 10 与 8，另有 30 处 12 次重听票数打平 —— 两者准确度基本持平。
· turbo 整轮快约 1.9 倍（识别部分快 2.5-3 倍）。
· large-v3 的长处是汉字写法更规范（時 / 本当に / 分かる 这类写汉字而不是假名），对中文翻译没有影响；代价是慢，显存小吃紧。
· small 实测会漏字、截断，只在配置很低时用；tiny / base 只适合先看效果；medium 内置但没实测过。

推荐档位：没有 NVIDIA 显卡 → small（走 CPU）；显存小于 5 GB → turbo；显存 5 GB 以上 → large-v3。最终成片仍需人工校对。

CPU 模式可直接使用。NVIDIA 显卡请先点击“启用显卡加速”：首次自动下载约 570 MB 官方组件，之后无需重复下载。
显卡默认使用 4 GB 省显存模式；显存不足或显卡不可用时自动改用 CPU。本程序不会安装整套 CUDA 开发工具。
首次使用某个模型需联网下载：tiny 来自魔搭 ModelScope（国内直连），其余来自 Hugging Face（安装器支持国内镜像）。可以在设置中选择已下载完整的 faster-whisper / CTranslate2 模型文件夹。
语音识别在电脑上运行。翻译时只向官方 api.deepseek.com 发送原文、相邻上下文、部分已译内容及术语说明。
视频默认读取第一条音轨。需要其他音轨时，先在剪辑软件中导出对应音频，再导入本程序。

识别听错自动改对（日文纠正表）

同一个音被识别成不同汉字，翻译就会跟着错。例：原音「おしょく」被写成「お食」，中文翻成「吃饭」；
正确写法「汚職」应是「贪污」。换模型解决不了 —— 实测三个模型都写「お食」，音频里就没有区分这两个词的信息。

纠正表在数据根目录的「日文纠正表.txt」，一行一条：识别错的 = 改对的。识别之后、翻译之前自动替换；
改完立刻生效，不用重启，不联网、不消耗 API。表不存在就什么都不做，只改日文原文，中文、时间轴、编号一个字都不动。

发现哪句不对：在字幕列表里选中它 → 点「这句识别错了」→ 用中文写一句「应该是什么意思」（例如「应该是贪污」）。
软件会自己推出正确的日文、当场试翻一遍核对，然后写进纠正表，并把本片同错的条目一起改对、中文重新翻。
需要填 API 密钥；没填就只把问题记下来。它拿不准时不会硬写：会弹窗把两种中文摆出来，由你决定用不用。

先分离人声再识别（默认关闭）

素材里游戏音效、音乐盖过说话声时，在设置页勾选「先分离人声再识别」：程序先把说话声与音效拆开，只拿说话声去识别。
实测素材很吵时能明显多听出内容；极端情况下不分离几乎识别不出东西。素材本身安静时改善有限，却一样要多等。
代价是识别前多等约 3 倍时间（1 小时素材约多等 37 分钟）。分离全程在本机完成，不联网、不上传，也不额外下载模型。
分离只影响识别，不改变翻译；术语表只影响翻译，不改变识别，两者互不相干。

项目和恢复

项目保存在输出目录的素材子文件夹，包含 project.json。每次导出都有新的时间文件夹，不覆盖已有交付。
已有项目可点击「打开项目 / SRT…」选择 project.json，再点「导出文件…」。此操作复用已有译文，无需 API 密钥，也不会重新识别或调用翻译 API。
导入 SRT 会在输出目录建立独立的 project.json，不覆盖原始 SRT。下次打开该项目，可以保留所有校对结果。
如果一整套原文、中文和混合 SRT 都还在，优先同时导入原文和中文两份；打开混合 SRT 时，只在确认内容一致后自动使用同目录配套文件。
识别完成后立即保存原文；翻译每完成一批就保存。翻译中断后可打开 project.json 继续。
识别阶段停止会丢弃尚未完成的识别，下次从头识别；模型已下载的内容仍可复用。
修改文字和时间保留当前填写的译文；如果原文含义有变，请同步校对中文，或清空该条中文后继续补译。
修改翻译模型或术语表只影响缺失的译文，不会自动重译已完成内容。
「重新翻译全部」会重新调用 API；普通继续操作只翻译缺失条目。

费用与准确度

本地识别没有按次 API 费用；首次需下载模型。DeepSeek 翻译按其 API 用量收费，并且分高峰 / 空闲两档单价；翻译前会按待译内容预估金额并同时列出两档价格，实际以官方账单为准。
日文纠正表在本地做替换，不调用 API、不产生费用；「这句识别错了」里推正确写法与试翻核对会用一两次翻译模型（一条大约两三厘钱）。
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

关于与署名

本软件作者：lin1024-star（心非）。主页与源码：https://github.com/lin1024-star/zimu-studio
本软件免费公开给朋友使用，不收费、不出售；代码为 MIT 许可。
点击标题栏右侧的版本号，可以打开完整的作者与第三方组件说明。
人声分离模型 Kim_Vocal_2 来自 Ultimate Vocal Remover (UVR) 项目，开发者 Anjok07、aufr33 等。UVR 要求使用其模型的第三方开发者署名，在此致谢。
其余第三方组件与许可证清单见安装包内的 THIRD_PARTY.txt。
"""


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
        self.checking = False          # 中文自检是否正在跑
        self.current_config = None
        self.active_stage = ""
        self.task_kind = "job"
        self.gpu_setup_ok = False
        self.queue_files = []
        self.queue_list = None
        self.in_queue = False
        self.queue_mode = ""
        self.queue_index = 0
        self.queue_ok = 0
        self.queue_fail = 0
        self.last_job_ok = False
        self.pending_translate = False
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
        remembered_key = ""
        try:
            from winsecret import load_key
            remembered_key = load_key() or ""
        except Exception:
            remembered_key = ""
        self.api_key_var = tk.StringVar(value=os.environ.get("DEEPSEEK_API_KEY", "") or remembered_key)
        self.remember_var = tk.BooleanVar(value=bool(remembered_key))
        self.model_var = tk.StringVar(value=self.settings.get("model", DEFAULT_MODEL))
        self.ass_style_var = tk.StringVar(value=self.settings.get("ass_style_file", ""))
        self.ass_style_name_var = tk.StringVar(value=self.settings.get("ass_style_name", "Default"))
        self.alert_var = tk.BooleanVar(value=bool(self.settings.get("alert_when_done", True)))
        self.tidy_var = tk.BooleanVar(value=bool(self.settings.get("tidy_axis", True)))
        self.separate_var = tk.BooleanVar(value=bool(self.settings.get("separate_vocals", False)))
        # 翻译完自动做一遍中文自检（找"可能翻错、会让你剪错"的句子）。默认开。
        self.ccheck_var = tk.BooleanVar(value=bool(self.settings.get("chinese_check", True)))
        self.model_hint_var = tk.StringVar()
        # 「怎么选模型」那一块用：检测结果、推荐、当前选中模型的长短处
        self.hardware_var = tk.StringVar()
        self.recommend_var = tk.StringVar()
        self.selected_model_var = tk.StringVar()
        self.recommended_model = ""
        self.force_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="选择视频，或打开已有项目 / SRT 直接校对。")
        self.summary_var = tk.StringVar(value="尚未载入字幕")
        self.time_start = tk.StringVar()
        self.time_end = tk.StringVar()
        self.busy_controls = []
        self.configure_style()
        self.build_ui()
        self.refresh_model_hint()
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

    def refresh_glossary_hint(self, _event=None):
        """术语表字数实时提示：粘完立刻看见进去了多少，不用猜有没有存上。"""
        self.glossary.edit_modified(False)
        count = len(self.glossary.get("1.0", "end-1c").strip())
        if count > GLOSSARY_LIMIT:
            self.glossary_hint_var.set("术语表 %d 字，超过 %d 字上限，超出部分不会发给翻译模型" % (count, GLOSSARY_LIMIT))
        elif count:
            self.glossary_hint_var.set("术语表 %d 字（上限 %d 字）" % (count, GLOSSARY_LIMIT))
        else:
            self.glossary_hint_var.set("术语表未填写（可选，上限 %d 字）" % GLOSSARY_LIMIT)
        # 术语表一改就重算列表里的术语标记，不用重新翻译
        self.apply_glossary_marks()

    def save_settings_clicked(self):
        """手动保存并给出明确回执；自动保存是静默的，这里让使用者看得见结果。"""
        count = len(self.glossary.get("1.0", "end-1c").strip())
        if self.save_settings():
            messagebox.showinfo("设置已保存", "已保存到本机：\n%s\n\n术语表 %d 字。下次翻译会把它一起发给翻译模型。"
                                % (CONFIG_PATH, count))
        else:
            messagebox.showwarning("设置没有保存成功", "写入本机设置文件失败，多半是磁盘或权限问题。\n"
                                   "当前填写的术语表仍会在本次运行内有效；建议先不要关闭软件，"
                                   "在「诊断与资源」导出诊断包查看原因。")

    def save_settings(self):
        """保存设置。顺带把密钥也存了。

        为什么在这里存密钥（2026-10-13 真事）：密钥原来只在「开始任务」和「关软件」时
        才写进 Windows 凭据管理器，于是使用者粘了密钥、点了「保存设置」，
        以为已经存好了 —— 结果没存，重启又得粘一遍。
        现在点保存就存；而且 persist_api_key 已经改成"框空着就不动"，不会误删。
        """
        self.persist_api_key()
        data = {"output": self.out_var.get(), "language": self.lang_var.get(),
                "asr_model": self.asr_var.get(), "device": self.device_var.get(),
                "local_model": self.local_model_var.get(), "model": self.model_var.get(),
                "glossary": self.glossary.get("1.0", "end-1c").strip(),
                "ass_style_file": self.ass_style_var.get(), "ass_style_name": self.ass_style_name_var.get(),
                "alert_when_done": bool(self.alert_var.get()),
                "tidy_axis": bool(self.tidy_var.get()),
                "chinese_check": bool(self.ccheck_var.get()),
                "separate_vocals": bool(self.separate_var.get())}
        try:
            atomic_write(CONFIG_PATH, json.dumps(data, ensure_ascii=False, indent=2))
            return True
        except OSError as exc:
            self.log("本机设置未能保存；当前任务仍可继续。")
            hint = write_blocked_hint(exc)
            if hint:
                self.log(hint)
            return False

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
        credit = ttk.Label(header, text="v" + APP_VERSION + " · lin1024-star（心非）", style="Muted.TLabel", cursor="hand2")
        credit.pack(side="right", padx=(8, 0))
        credit.bind("<Button-1>", lambda _event: self.open_about())
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
        ttk.Label(files, text="① 选素材", font=(FONT_FAMILY, 11, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        for row, (label, var, command) in enumerate([("视频 / 字幕", self.source_var, self.choose_source),
                                                     ("输出目录", self.out_var, self.choose_output)],
                                                    start=1):
            ttk.Label(files, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
            entry = ttk.Entry(files, textvariable=var, state="readonly" if row == 1 else "normal")
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            btn = ttk.Button(files, text="选择文件" if row == 1 else "选择目录", command=command)
            btn.grid(row=row, column=2, padx=(8, 0), pady=4)
            self.busy_controls.append(btn)
            if row == 2:
                self.busy_controls.append(entry)
        pick = ttk.Frame(files)
        pick.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(2, 0))
        ttk.Label(pick, text="原语言").pack(side="left")
        combo = ttk.Combobox(pick, textvariable=self.lang_var, values=["自动识别", "英语", "日语"],
                             state="readonly", width=10)
        combo.pack(side="left", padx=(8, 16))
        self.busy_controls.append(combo)
        self.button(pick, "打开项目 / SRT…", self.open_project, side="left")

        action = ttk.Frame(main)
        action.grid(row=1, column=0, sticky="ew", pady=(14, 6))
        ttk.Label(action, text="② 开始", font=(FONT_FAMILY, 11, "bold")).pack(side="left", padx=(0, 10))
        self.button(action, "识别并翻译 / 继续", lambda: self.start_job("translate"), accent=True, side="left")
        self.button(action, "仅识别原文", lambda: self.start_job("transcribe"), side="left", padx=8)
        self.stop_button = self.button(action, "停止", self.stop_job, busy=False, side="left")
        self.stop_button.configure(state="disabled")
        self.more_button = ttk.Button(action, text="更多操作 ▾", command=self.toggle_more)
        self.more_button.pack(side="right")

        self.progress = ttk.Progressbar(main, mode="determinate", maximum=100)
        self.progress.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(main, textvariable=self.status_var, style="Muted.TLabel").grid(row=3, column=0, sticky="w")

        # 不常用的都收在这里，默认不显示 —— 主界面只留一条主线
        self.more_frame = ttk.Frame(main)
        self.more_frame.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.more_frame.columnconfigure(0, weight=1)
        more_tools = ttk.Frame(self.more_frame)
        more_tools.grid(row=0, column=0, sticky="ew")
        ttk.Label(more_tools, text="检查与微调", style="Muted.TLabel").pack(side="left", padx=(0, 8))
        self.axis_button = self.button(more_tools, "待确认…", self.show_axis_review, side="left", padx=4)
        self.button(more_tools, "时间轴体检…", self.check_timeline, side="left", padx=4)
        self.ass_style_button = self.button(more_tools, "ASS 样式…", self.edit_ass_style, side="left", padx=4,
                                            busy=False)
        ttk.Label(more_tools, text="　").pack(side="left")
        self.button(more_tools, "新增字幕…", self.add_cue, side="left", padx=4)
        self.button(more_tools, "时长设置…", self.set_duration, side="left", padx=4)
        self.button(more_tools, "整体偏移…", self.set_offset, side="left", padx=4)
        self.source_export_button = self.button(more_tools, "仅导出原文…", lambda: self.manual_export(False),
                                                side="left", padx=4)
        more_queue = ttk.Frame(self.more_frame)
        more_queue.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(more_queue, text="批量队列", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.queue_list = tk.Listbox(more_queue, height=2, font=(FONT_FAMILY, 9), relief="solid", borderwidth=1)
        self.queue_list.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        more_queue.columnconfigure(0, weight=1)
        qbtns = ttk.Frame(more_queue)
        qbtns.grid(row=1, column=1, sticky="nsew", padx=(8, 0), pady=(2, 0))
        qbtn_add = ttk.Button(qbtns, text="添加多个文件…", command=self.add_queue_files)
        qbtn_add.pack(fill="x")
        qbtn_clear = ttk.Button(qbtns, text="清空队列", command=self.clear_queue)
        qbtn_clear.pack(fill="x", pady=(2, 0))
        self.busy_controls.extend([qbtn_add, qbtn_clear])
        self.refresh_queue_list()
        self.more_frame.grid_remove()

        table_head = ttk.Frame(main)
        table_head.grid(row=5, column=0, sticky="ew", pady=(16, 4))
        ttk.Label(table_head, text="③ 校对", font=(FONT_FAMILY, 11, "bold")).pack(side="left")
        ttk.Label(table_head, textvariable=self.summary_var, style="Muted.TLabel").pack(side="left", padx=12)

        table = ttk.Frame(main)
        table.grid(row=6, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=("id", "time", "source", "zh", "term"), show="headings", selectmode="browse", height=8)
        for col, name, width in [("id", "序号", 45), ("time", "时间段", 185), ("source", "原文字幕", 270),
                                 ("zh", "中文字幕", 270), ("term", "术语", 200)]:
            self.tree.heading(col, text=name)
            self.tree.column(col, width=width, minwidth=45, stretch=col in ("source", "zh"))
        # 术语命中用底色标出来：淡黄=命中了且译名在译文里，橙红=命中了但译文里没有（可能翻漏了）
        self.tree.tag_configure("term_ok", background="#FFF8DC")
        self.tree.tag_configure("term_miss", background="#FFE0CC")
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
        self.source_text = ScrolledText(editor, height=2, wrap="word", font=(FONT_FAMILY, 10), relief="solid", borderwidth=1)
        self.source_text.grid(row=1, column=0, sticky="ew", pady=4)
        self.zh_text = ScrolledText(editor, height=2, wrap="word", font=(FONT_FAMILY, 10), relief="solid", borderwidth=1)
        self.zh_text.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=4)
        row = ttk.Frame(main)
        row.grid(row=8, column=0, sticky="ew")
        ttk.Label(row, text="起止时间").pack(side="left")
        for v in (self.time_start, self.time_end):
            entry = ttk.Entry(row, textvariable=v, width=13)
            entry.pack(side="left", padx=5)
            self.busy_controls.append(entry)
        self.button(row, "保存此条修改", self.save_current, side="left", padx=(10, 0))
        self.button(row, "这句识别错了", self.report_issue, side="left", padx=8, busy=False)
        ttk.Label(row, text="（清空中文可补译此条）", style="Muted.TLabel").pack(side="left", padx=6)

        ccheck_row = ttk.Frame(main)
        ccheck_row.grid(row=8, column=0, sticky="ew", pady=(6, 0))
        self.ccheck_button = ttk.Button(ccheck_row, text="中文自检（找可能翻错的句子）",
                                        command=self.run_chinese_check)
        self.ccheck_button.pack(side="left")
        ttk.Label(ccheck_row, text="翻译完自动跑一遍；这里可以随时重跑。只报不改，点结果能跳到那一条。",
                  style="Muted.TLabel").pack(side="left", padx=8)

        export = ttk.Frame(main)
        export.grid(row=9, column=0, sticky="ew", pady=(14, 6))
        ttk.Label(export, text="④ 导出", font=(FONT_FAMILY, 11, "bold")).pack(side="left", padx=(0, 10))
        self.full_export_button = self.button(export, "导出文件…", lambda: self.manual_export(True), side="left")
        self.button(export, "打开输出文件夹", self.open_output, busy=False, side="left", padx=8)
        ttk.Label(export, text="可勾选：原文 / 中文 / 混合版（SRT、TXT）、中文 ASS、空轴",
                  style="Muted.TLabel").pack(side="left", padx=10)

        self.logs = ScrolledText(main, height=3, wrap="word", font=(FONT_FAMILY, 9), bg="#e6edf3", fg=MUTED, relief="flat", state="disabled")
        self.logs.grid(row=10, column=0, sticky="ew", pady=(10, 0))

        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text="DeepSeek 翻译", font=(FONT_FAMILY, 15, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 15))
        ttk.Label(settings, text="API 密钥").grid(row=1, column=0, sticky="w", pady=6, padx=(0, 18))
        self.key_entry = ttk.Entry(settings, textvariable=self.api_key_var, show="●")
        self.key_entry.grid(row=1, column=1, sticky="ew", pady=6)
        show = tk.BooleanVar(value=False)
        show_btn = ttk.Checkbutton(settings, text="显示", variable=show, command=lambda: self.key_entry.configure(show="" if show.get() else "●"))
        show_btn.grid(row=1, column=2, padx=12)
        self.busy_controls.extend([self.key_entry, show_btn])
        remember_btn = ttk.Checkbutton(settings, text="记住密钥（Windows 账户级加密，仅本机可解；取消勾选即清除）", variable=self.remember_var)
        remember_btn.grid(row=2, column=1, sticky="w", pady=(2, 0))
        self.busy_controls.append(remember_btn)
        ttk.Label(settings, text="翻译模型").grid(row=3, column=0, sticky="w", pady=10)
        model_combo = ttk.Combobox(settings, textvariable=self.model_var, values=[DEFAULT_MODEL, "deepseek-v4-pro"], width=30)
        model_combo.grid(row=3, column=1, sticky="w", pady=10)
        self.busy_controls.append(model_combo)
        ttk.Label(settings, text="可填写账户可用的模型 ID；使用非思考模式翻译。", style="Muted.TLabel").grid(row=4, column=1, sticky="w")
        ttk.Label(settings, text="人名 / 术语说明").grid(row=5, column=0, sticky="nw", pady=10)
        self.glossary_hint_var = tk.StringVar()
        self.glossary = ScrolledText(settings, height=4, wrap="word", font=(FONT_FAMILY, 10))
        self.glossary.grid(row=5, column=1, columnspan=2, sticky="ew", pady=10)
        self.glossary.insert("1.0", self.settings.get("glossary", ""))
        self.glossary.edit_modified(False)
        self.glossary.bind("<<Modified>>", self.refresh_glossary_hint)
        self.refresh_glossary_hint()
        ttk.Label(settings, text="例如：John Smith = 约翰·史密斯。新设置只用于缺少中文的条目；已有译文会保留。", style="Muted.TLabel").grid(row=6, column=1, columnspan=2, sticky="w")
        force = ttk.Checkbutton(settings, text="重新翻译全部（会重新消耗 API 额度）", variable=self.force_var)
        force.grid(row=7, column=1, sticky="w", pady=10)
        self.busy_controls.append(force)
        ttk.Separator(settings).grid(row=8, column=0, columnspan=3, sticky="ew", pady=14)
        ttk.Label(settings, text="本地语音识别", font=(FONT_FAMILY, 15, "bold")).grid(row=9, column=0, columnspan=3, sticky="w", pady=(2, 14))
        for r, label, var, values in [(10, "识别模型", self.asr_var, list(MODEL_ORDER)),
                                       (11, "处理设备", self.device_var, ["CPU（直接使用）", "NVIDIA GPU（4 GB 省显存）"])]:
            ttk.Label(settings, text=label).grid(row=r, column=0, sticky="w", pady=6)
            c = ttk.Combobox(settings, textvariable=var, values=values, state="readonly", width=28)
            c.grid(row=r, column=1, sticky="w", pady=6)
            self.busy_controls.append(c)
            if r == 10:
                self.asr_combo = c
                c.bind("<<ComboboxSelected>>", lambda _e: self.refresh_model_hint())
        ttk.Label(settings, textvariable=self.model_hint_var, style="Muted.TLabel").grid(row=10, column=2, sticky="w", padx=10)
        ttk.Label(settings, text="CPU 可直接使用。显卡首次下载约 570 MB 组件；下载可继续，显卡不可用时自动使用 CPU。", style="Muted.TLabel").grid(row=12, column=1, columnspan=2, sticky="w", pady=6)
        ttk.Label(settings, text="本地模型文件夹").grid(row=13, column=0, sticky="w", pady=6)
        entry = ttk.Entry(settings, textvariable=self.local_model_var)
        entry.grid(row=13, column=1, sticky="ew", pady=6)
        btn = ttk.Button(settings, text="选择…", command=self.choose_model)
        btn.grid(row=13, column=2, padx=10)
        self.busy_controls.extend([entry, btn])
        ttk.Label(settings, text="可留空。填写时优先使用该文件夹，应包含 model.bin、config.json、tokenizer.json 等模型文件。", style="Muted.TLabel").grid(row=14, column=1, columnspan=2, sticky="w")
        ttk.Separator(settings).grid(row=15, column=0, columnspan=3, sticky="ew", pady=14)
        ttk.Label(settings, text="ASS 导出样式（可选）", font=(FONT_FAMILY, 15, "bold")).grid(row=16, column=0, columnspan=3, sticky="w", pady=(2, 10))
        ttk.Label(settings, text="样式文件").grid(row=17, column=0, sticky="w", pady=6, padx=(0, 18))
        ass_entry = ttk.Entry(settings, textvariable=self.ass_style_var)
        ass_entry.grid(row=17, column=1, sticky="ew", pady=6)
        ttk.Button(settings, text="选择…", command=self.choose_ass_style).grid(row=17, column=2, padx=(12, 0))
        self.busy_controls.append(ass_entry)
        ttk.Label(settings, text="样式名").grid(row=18, column=0, sticky="w", pady=6, padx=(0, 18))
        self.ass_style_combo = ttk.Combobox(settings, textvariable=self.ass_style_name_var, state="readonly", width=28)
        self.ass_style_combo.grid(row=18, column=1, sticky="w", pady=6)
        self.busy_controls.append(self.ass_style_combo)
        self.refresh_ass_styles()
        ttk.Label(settings, text="导入 .ass 模板即可使用其字体/颜色/位置；留空用内置默认样式（白字黑边）。导出含中文时自动生成“中文_zh.ass”。", style="Muted.TLabel").grid(row=19, column=1, columnspan=2, sticky="w", pady=6)
        ttk.Checkbutton(settings, text="翻译完成后提醒（还原并置顶窗口 + 提示音）", variable=self.alert_var).grid(row=20, column=0, columnspan=3, sticky="w", pady=(14, 2))
        ttk.Label(settings, text="翻译要跑几分钟到几十分钟；跑完时把窗口拉到前台提醒一次。取消勾选则不打扰，可在状态栏和日志里查看结果。", style="Muted.TLabel").grid(row=21, column=1, columnspan=2, sticky="w")
        ttk.Checkbutton(settings, text="识别后自动整理轴（合并重复、并入语气碎片、修正拖音）", variable=self.tidy_var).grid(row=22, column=0, columnspan=3, sticky="w", pady=(14, 2))
        ttk.Label(settings, text="只动重复文本和时长，不重跑识别、不改变独一无二的内容；整理前的原文会存进项目，随时可还原。", style="Muted.TLabel").grid(row=23, column=1, columnspan=2, sticky="w")
        separate_check = ttk.Checkbutton(
            settings, text="先分离人声再识别（素材很吵时打开，能多听出内容；识别前多等约 3 倍时间）",
            variable=self.separate_var)
        separate_check.grid(row=24, column=0, columnspan=3, sticky="w", pady=(14, 2))
        self.busy_controls.append(separate_check)
        ttk.Label(settings, text="把说话声与游戏音效、音乐拆开，只拿说话声去识别。素材安静时改善有限，"
                  "很吵时是决定性的（实测极端情况下不分离会几乎识别不出内容）；随包模型约 60 MB，不联网、不上传。",
                  style="Muted.TLabel").grid(row=25, column=1, columnspan=2, sticky="w")
        save_row = ttk.Frame(settings)
        save_row.grid(row=26, column=0, columnspan=3, sticky="ew", pady=(20, 2))
        ttk.Button(save_row, text="保存设置", command=self.save_settings_clicked, style="Accent.TButton").pack(side="left")
        ttk.Label(save_row, textvariable=self.glossary_hint_var, style="Muted.TLabel").pack(side="left", padx=12)
        ttk.Label(settings, text="这一页的改动也会在开始任务、关闭软件时自动保存，不点按钮也不会丢。"
                  "术语表只影响翻译，不改变语音识别结果。", style="Muted.TLabel").grid(row=27, column=0, columnspan=3, sticky="w")

        # ---- 怎么选模型：自动检测这台机器 → 给推荐 → 长短处摆出来（选择权仍在使用者）----
        ttk.Separator(settings).grid(row=28, column=0, columnspan=3, sticky="ew", pady=14)
        ttk.Label(settings, text="怎么选识别模型", font=(FONT_FAMILY, 15, "bold")).grid(
            row=29, column=0, columnspan=3, sticky="w", pady=(2, 10))
        ttk.Label(settings, text="这台机器", style="Muted.TLabel").grid(row=30, column=0, sticky="nw", pady=6)
        ttk.Label(settings, textvariable=self.hardware_var).grid(row=30, column=1, columnspan=2, sticky="w", pady=6)
        ttk.Label(settings, text="推荐", style="Muted.TLabel").grid(row=31, column=0, sticky="nw", pady=6)
        ttk.Label(settings, textvariable=self.recommend_var, wraplength=640,
                  justify="left").grid(row=31, column=1, columnspan=2, sticky="w", pady=6)
        hw_row = ttk.Frame(settings)
        hw_row.grid(row=32, column=1, columnspan=2, sticky="w", pady=(2, 8))
        self.button(hw_row, "用推荐的模型", self.apply_recommended_model, busy=False, side="left")
        self.button(hw_row, "重新检测", self.refresh_hardware_hint, busy=False, side="left", padx=8)
        ttk.Label(hw_row, text="选哪个都行，这里只是给依据", style="Muted.TLabel").pack(side="left", padx=10)
        ttk.Label(settings, text="各模型的长短处").grid(row=33, column=0, sticky="nw", pady=(6, 2))
        table = tk.Text(settings, height=7, width=96, font=("Consolas", 9), relief="solid",
                        borderwidth=1, wrap="none")
        table.insert("1.0", model_table())
        table.configure(state="disabled")
        table.grid(row=33, column=1, columnspan=2, sticky="w", pady=(6, 2))
        ttk.Label(settings, textvariable=self.selected_model_var, style="Muted.TLabel").grid(
            row=34, column=1, columnspan=2, sticky="w", pady=(4, 0))
        self.refresh_hardware_hint()

        # ---- 中文自检（放在设置页最后一行，避免打乱上面的行号）----
        ttk.Separator(settings).grid(row=35, column=0, columnspan=3, sticky="ew", pady=14)
        ttk.Checkbutton(settings, text="翻译完成后自动检查中文（挑出可能翻错、会让你剪错的地方）",
                        variable=self.ccheck_var).grid(row=36, column=0, columnspan=3, sticky="w", pady=(0, 2))
        ttk.Label(settings, text="只挑会让你剪错的错：专名听错、意思反了、中文不成话、前后矛盾；"
                  "措辞不同不算。只报不改，改还是你说了算。一期素材约几分钱，不想等就取消勾选。",
                  style="Muted.TLabel").grid(row=37, column=1, columnspan=2, sticky="w")

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

    def toggle_more(self):
        """「更多操作」展开 / 收起 —— 不常用的东西默认不占地方。

        ⚠️ 这个方法必须放在 build_ui **之外**。
        之前它被插在 build_ui 中间，把后面半页代码（设置页 / 帮助页 / 诊断页）
        全变成了它的方法体 —— 那几页根本没建出来，一点按钮就 NameError。
        （使用者的诊断包直接指出了：app.py:458 toggle_more，NameError）

        用记状态的 self.more_open 而不是 winfo_ismapped()：
        后者在窗口没显示时永远是假，展开/收起会失灵，而且没法自动测。
        """
        self.more_open = not getattr(self, "more_open", False)
        if self.more_open:
            self.more_frame.grid()
            self.more_button.configure(text="收起 ▴")
        else:
            self.more_frame.grid_remove()
            self.more_button.configure(text="更多操作 ▾")

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

    def open_about(self):
        """作者署名与第三方组件说明；随包附带时用系统默认程序打开。"""
        about = APP_DIR / "关于本软件.txt"
        if about.is_file():
            try:
                os.startfile(about)
                return
            except (AttributeError, OSError):
                import webbrowser
                webbrowser.open(about.as_uri())
                return
        messagebox.showinfo(
            "关于本软件",
            "字幕工坊  v" + APP_VERSION + "\n\n"
            "作者：lin1024-star（心非）\n"
            "https://github.com/lin1024-star/zimu-studio\n\n"
            "本软件免费公开使用。人声分离模型 Kim_Vocal_2 来自 Ultimate Vocal "
            "Remover (UVR) 项目（开发者 Anjok07、aufr33 等），此处致谢。\n"
            "完整第三方组件清单见安装包内的 THIRD_PARTY.txt。",
        )

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
        self.full_export_button.configure(text="导出中文字幕…" if chinese else "导出文件…")
        self.source_export_button.configure(text="仅导出中文…" if chinese else "仅导出原文…")
        self.zh_text.configure(state="disabled" if chinese or self.busy else "normal")
        if getattr(self, "ass_style_button", None) is not None:
            self.ass_style_button.configure(
                text="ASS 样式…" if (self.project or {}).get("ass") else "ASS 样式（需先导入 .ass）…")

    def refresh_queue_list(self):
        if self.queue_list is None:
            return
        self.queue_list.delete(0, "end")
        for p in self.queue_files:
            self.queue_list.insert("end", Path(p).name)

    def add_queue_files(self):
        paths = filedialog.askopenfilenames(title="添加多个视频 / 音频 / 字幕到队列", filetypes=[
            ("视频 / 音频 / 字幕", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.mp3 *.wav *.m4a *.flac *.srt *.ass"), ("所有文件", "*.*")])
        for p in paths:
            if p not in self.queue_files:
                self.queue_files.append(p)
        self.refresh_queue_list()
        if paths:
            self.log(f"队列现有 {len(self.queue_files)} 个文件。")

    def clear_queue(self):
        self.queue_files = []
        self.refresh_queue_list()
        self.log("队列已清空。")

    def refresh_ass_styles(self):
        names = ["Default"]
        path = self.ass_style_var.get().strip()
        if path and Path(path).is_file():
            try:
                from core import parse_ass_template
                parts = parse_ass_template(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
                if parts and parts[2]:
                    names = list(parts[2].keys())
            except (OSError, ValueError):
                pass
        self.ass_style_combo.configure(values=names)
        if self.ass_style_name_var.get() not in names:
            self.ass_style_name_var.set(names[0])

    def choose_ass_style(self):
        path = filedialog.askopenfilename(title="选择 ASS 样式模板（可选）", filetypes=[("ASS 字幕样式", "*.ass"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            from core import parse_ass_template
            parts = parse_ass_template(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
            if not parts or not parts[2]:
                messagebox.showerror("样式模板", "这个文件里没有可用的 [V4+ Styles] 样式，请换一个 .ass 模板。")
                return
        except (OSError, ValueError):
            messagebox.showerror("样式模板", "无法读取该文件。")
            return
        self.ass_style_var.set(path)
        self.refresh_ass_styles()
        self.log(f"已导入 ASS 样式模板：{Path(path).name}，可选样式 {len(parts[2])} 个。")

    def _ass_styles(self, path=None):
        """读一份 .ass 的样式表。path 为空就读当前项目导入的那份。"""
        import ass
        try:
            if path:
                return ass.Document.load(path, require_events=False).styles
            meta = (self.project or {}).get("ass") or {}
            document = ass.Document.from_parts(meta["text"], meta["encoding"],
                                               meta.get("bom", ""), meta.get("eol", "lf"))
            return document.styles
        except (KeyError, ValueError, OSError):
            return {}

    def edit_ass_style(self):
        """改样式：字号、字体、颜色、描边。样式来源二选一，选哪一套都能改。"""
        meta = (self.project or {}).get("ass")
        if not meta:
            messagebox.showinfo("ASS 样式",
                                "改样式需要先从 .ass 导入字幕：\n\n"
                                "点「打开项目 / SRT…」，选一个 .ass 文件，"
                                "内容类型选「中文 ASS（保留原格式）」，确定后再点这个按钮。\n\n"
                                "样式表是从那份 .ass 里读出来的，所以必须先导入。")
            return
        sources = {}
        own = self._ass_styles()
        if own:
            sources["keep"] = {"label": "导入文件里的样式（%d 个）" % len(own), "styles": own}
        template = self.ass_style_var.get().strip()
        if template:
            found = self._ass_styles(template)
            if found:
                sources["template"] = {"label": "我的模板：%s（%d 个）" % (Path(template).name, len(found)),
                                       "styles": found, "preferred": self.ass_style_name_var.get().strip()}
        if not sources:
            messagebox.showinfo("ASS 样式", "这份文件里没有能识别的样式表，无法改样式。")
            return
        dialog = AssStyleDialog(self, sources, self.project.get("ass_style_mode", "keep"), meta.get("style_edits"))
        self.wait_window(dialog)
        if dialog.result is None:
            return
        meta["style_edits"] = dialog.result["edits"]
        meta["font_sizes"] = dialog.result["font_sizes"]
        self.project["ass_style_mode"] = dialog.result["source"]
        self.save_current(silent=True)
        changed = []
        for name, changes in dialog.result["edits"].items():
            changed.append("%s（%s）" % (name, "、".join(sorted(changes))))
        self.log("ASS 样式已保存：" + ("；".join(changed) if changed else "没有改动")
                 + "。导出时写入，原 .ass 与模板文件都不会被改动。")
        self.status_var.set("ASS 样式已保存，导出时生效。")

    def choose_source(self):
        path = filedialog.askopenfilename(title="选择视频、音频或已有字幕", filetypes=[
            ("视频 / 音频 / 字幕", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.mp3 *.wav *.m4a *.flac *.srt *.ass"), ("所有文件", "*.*")])
        if path:
            if not self.save_current(silent=True):
                return
            if Path(path).suffix.lower() in (".srt", ".ass"):
                self.import_subtitles(path)
                return
            self._load_source(path)

    def _load_source(self, path):
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
        path = filedialog.askopenfilename(title="打开以前保存的字幕或项目", filetypes=[("项目 / 字幕", "*.json *.srt *.ass"), ("字幕（SRT / ASS）", "*.srt *.ass"), ("字幕项目", "*.json")])
        if path:
            if not self.save_current(silent=True):
                return
            if Path(path).suffix.lower() in (".srt", ".ass"):
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
            messagebox.showerror("无法保存导入项目", save_error_text(exc))

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
            messagebox.showerror("无法添加字幕", save_error_text(exc))

    def report_issue(self):
        """「这句识别错了」：把这条抄下来，攒着交给懂日语的人。

        使用者判断不了「正确的日文该怎么写」，但一眼看得出中文不对
        （"喜欢吃饭的警察"）。所以这里只让他用中文写一句哪里不对，
        日文原文由程序自己抄进文件 —— 他一个字日文都不用打。
        """
        row = next((r for r in (self.project or {}).get("cues", []) if r["id"] == self.selected_id), None)
        if row is None:
            messagebox.showinfo("先选一条", "在上面的列表里点一下不对劲的那条字幕，再来标错。")
            return
        name = (self.project or {}).get("name") or Path(self.project_path or "").stem
        dialog = JaIssueDialog(self, row, name, saved_count=ja_fix.count_issues())
        self.wait_window(dialog)
        if dialog.result is None:
            return
        note = dialog.result.get("note", "")
        # 他写清楚了「应该是什么意思」→ 软件自己反推正确日文、核对、写进纠正表
        if ja_fix.note_keywords(note) and self.api_key_var.get().strip():
            self.auto_fix_issue(row, name, note)
        else:
            if ja_fix.note_keywords(note) is False and not self.api_key_var.get().strip():
                self.log("没有填 API 密钥，跳过自动推写法，改为记下来。")
            self.save_issue(row, name, note)

    def auto_fix_issue(self, row, name, note):
        """自动那一步：拿他的中文期望反推正确日文 → 试翻核对 → 写进纠正表并立刻生效。

        为什么能行（2026-10-12 实测，SURGECITY 的真错句）：
          · 只给这一句+上下文让模型自己猜   → 0/5（它猜 おしょく / お食事 / 追い…）
          · 给「这一句 + 现在译文 + 他期望的中文」→ **5/5 全对**
        所以他的中文反馈就是那把钥匙 —— 他不用懂日文。
        """
        key = self.api_key_var.get().strip()
        model = self.model_var.get().strip() or DEFAULT_MODEL
        glossary = self.glossary.get("1.0", "end-1c").strip()
        cues = (self.project or {}).get("cues") or []
        idx = next((i for i, r in enumerate(cues) if r["id"] == row["id"]), None)
        context = ([cues[i].get("source") or "" for i in range(max(0, idx - 3), min(len(cues), idx + 4))
                    if i != idx] if idx is not None else [])
        language = (self.project or {}).get("language") or "ja"
        before_zh = row.get("zh") or ""
        keywords = ja_fix.note_keywords(note)
        self.status_var.set("正在问模型：这句正确的日文该怎么写…")
        self.log(f"第 {row['id']} 条：拿你说的「{note}」去反推正确写法，并试翻一次核对。")
        box = {"done": False, "fix": None, "zh": "", "ok": None, "error": None}

        def work():
            try:
                client = DeepSeekClient(key, model)
                box["fix"] = ja_fix.guess_fix(row.get("source") or "", before_zh, note, context, client)
                if box["fix"]:
                    cue = Cue(row["id"], row["start"], row["end"], box["fix"]["fixed"])
                    got = client.translate_safe([cue], language, [], glossary)
                    box["zh"] = got.get(row["id"], "")
                    try:
                        # 让模型当判官：改后的译文对不对得上他说的意思。
                        # （本地关键词匹配在"旧译文本来就有这个词"和"换个同义词"时会误判，实测踩过）
                        box["ok"] = ja_fix.judge_fix(note, before_zh, box["zh"], client)
                    except Exception:                        # noqa: BLE001
                        box["ok"] = None
            except Exception as exc:                     # noqa: BLE001
                box["error"] = exc
            finally:
                box["done"] = True

        threading.Thread(target=work, daemon=True).start()
        self.after(200, lambda: self._wait_auto_fix(box, row, name, note, before_zh, keywords))

    def _wait_auto_fix(self, box, row, name, note, before_zh, keywords):
        """等后台那几下跑完（不卡界面），然后决定是写表还是记下来。"""
        if not box["done"]:
            self.after(200, lambda: self._wait_auto_fix(box, row, name, note, before_zh, keywords))
            return
        self.status_var.set("")
        fix = box["fix"]
        if box["error"] is not None:
            self.log(f"自动推写法失败（{type(box['error']).__name__}），改为记下来。")
            self.save_issue(row, name, note)
            return
        if not fix:
            self.log("模型看不出这句哪里听错了，改为记下来留给懂日语的人。")
            self.save_issue(row, name, note)
            return
        approved = box["ok"]
        if approved is None:      # 判官没答上来时，退回本地关键词兜底
            approved = ja_fix.matches_note(before_zh, box["zh"], keywords)
            self.log("判官没答上来，改用本地关键词兜底核对。")
        if not approved:
            # 机器自己不确定 —— 但**使用者说了算**：把改法和试翻结果摆给他，让他决定。
            self.log(f"机器不确定（试翻出来是「{box['zh']}」），交给他决定要不要用。")
            dialog = JaFixConfirmDialog(
                self, row, fix, box["zh"],
                f"按这个改法试翻出来的中文是「{box['zh']}」，跟你要的意思对不上。")
            self.wait_window(dialog)
            if dialog.result != "apply":
                if dialog.result == "record":
                    self.save_issue(row, name, note)
                return
            self.log(f"他说了算：按 {fix['wrong']} = {fix['right']} 写进纠正表。")
        self.write_rule(row, fix, box["zh"], note)

    def write_rule(self, row, fix, trial_zh, note):
        """把规则写进纠正表，并立刻套到当前项目上。"""
        state = ja_fix.append_rule(fix["wrong"], fix["right"], note)
        if state == "conflict":
            messagebox.showwarning("纠正表里有冲突",
                                   f"表里已经有「{fix['wrong']} = 别的写法」，没有覆盖它。\n\n"
                                   f"这次的改法是：{fix['wrong']} = {fix['right']}\n"
                                   "想改的话，请手动编辑 D:\\字幕工坊\\日文纠正表.txt。")
            return
        if state == "error":
            self.log("写纠正表失败（可能没有写入权限），改为记下来。")
            self.save_issue(row, "", note)
            return
        changed = self.apply_rule_now(fix["wrong"], fix["right"])
        if state == "exists":
            self.log(f"纠正表里本来就有「{fix['wrong']} = {fix['right']}」，这次只把项目里剩下的改成一致。")
        else:
            self.log(f"已写进纠正表：{fix['wrong']} = {fix['right']}（{fix['why'][:60]}）")
        # 中文也要跟着变 —— 否则他看到的中文还是旧的（纠正表只改日文原文）
        redone = self.retranslate_cues(set(changed) | {row["id"]})
        messagebox.showinfo(
            "改好了",
            f"这句的原句：{row.get('source') or ''}\n"
            f"改对之后：{fix['fixed']}\n"
            f"试翻结果：{trial_zh}\n\n"
            f"已经写进纠正表（{fix['wrong']} = {fix['right']}）。\n"
            f"本片改了 {len(changed)} 条原文，中文重翻了 {redone} 条。\n"
            "以后所有视频遇到同一个错，都会自动改对。")

    def retranslate_cues(self, ids):
        """把指定条目的中文重新翻一遍。

        为什么必须做：纠正表改的是**日文原文**，而项目里存着的中文是以前翻好的，
        不重翻就不会变 —— 使用者会以为"标了没用"。
        """
        ids = {int(i) for i in ids if i is not None}
        rows = [r for r in (self.project or {}).get("cues") or [] if r["id"] in ids]
        key = self.api_key_var.get().strip()
        if not rows or not key or not self.project_path:
            return 0
        try:
            client = DeepSeekClient(key, self.model_var.get().strip() or DEFAULT_MODEL)
            glossary = self.glossary.get("1.0", "end-1c").strip()
            language = (self.project or {}).get("language") or "ja"
            got = client.translate_safe(
                [Cue(r["id"], r["start"], r["end"], r.get("source") or "") for r in rows],
                language, [], glossary)
        except Exception as exc:                             # noqa: BLE001
            self.log(f"中文重翻失败（{type(exc).__name__}），原文已经改对了，中文可以稍后补翻。")
            return 0
        count = 0
        for r in rows:
            zh = got.get(r["id"])
            if zh:
                r["zh"] = zh
                count += 1
        if count:
            try:
                save_project(self.project_path, self.project)
            except OSError as exc:
                self.log(f"中文重翻后存盘失败：{exc}")
                return 0
            self.reload_project(self.project_path)
        return count

    def apply_rule_now(self, wrong, right):
        """把刚确认的规则立刻套到当前项目上（只动日文原文），并存盘。

        返回**被改动的条号列表**（中文要不要跟着重翻由调用方决定）。
        """
        if not self.project or not self.project_path:
            return []
        cues = [Cue(r["id"], r["start"], r["end"], r.get("source") or "", r.get("zh") or "")
                for r in self.project["cues"]]
        fixed, report = ja_fix.fix_cues(cues, table=[(wrong, right)])
        if not report["changes"]:
            return []
        changed = set()
        by_id = {c.id: c for c in fixed}
        for row in self.project["cues"]:
            c = by_id.get(row["id"])
            if c is not None and c.source != (row.get("source") or ""):
                row["source"] = c.source
                changed.add(row["id"])
        try:
            save_project(self.project_path, self.project)
        except OSError as exc:
            self.log(f"改好了但存盘失败：{exc}")
            return []
        self.reload_project(self.project_path)
        return sorted(changed)

    def save_issue(self, row, name, note):
        """真正写文件那一步（和弹窗分开，方便单独测）。返回有没有记下。"""
        path, block, status = ja_fix.record_issue(name, row, note)
        if status == "duplicate":
            messagebox.showinfo("已经记过了", f"这一条之前就记过，这次没有重复写。\n\n文件：{path}")
            return False
        if status != "new":
            messagebox.showwarning("没写进去", f"没能写进文件（可能被占用、或者没有写入权限）。\n\n文件：{path}")
            return False
        copied = ""
        try:
            self.clipboard_clear()
            self.clipboard_append(block)
            copied = "，并复制到了剪贴板"
        except tk.TclError:
            pass
        total = ja_fix.count_issues()
        self.log(f"已记下第 {row.get('id')} 条「识别可能有误」（待处理共 {total} 条）：{path}")
        messagebox.showinfo(
            "记下来了",
            f"第 {row.get('id')} 条已经记下{copied}。\n\n"
            f"文件在：{path}\n目前一共记了 {total} 条。\n\n"
            "怎么处理：把里面「日文原文」那一行拿给懂日语的人看一眼，\n"
            "问出正确写法后，填进同目录的「日文纠正表.txt」，写成「错的 = 对的」。\n"
            "填完以后，同样的错在以后的项目里就不会再出现了。")
        return True

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
            messagebox.showerror("无法保存时长", save_error_text(exc))

    def set_offset(self):
        """整体偏移时间轴：整条轨一起提前或延后，不改时长也不动文本。"""
        if not self.project:
            messagebox.showinfo("先载入字幕", "请先打开项目 / SRT，或完成原文识别。")
            return
        if not self.save_current(silent=True):
            return
        dialog = OffsetDialog(self, self.project, self.selected_id)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        try:
            _updated, report, backup = save_offset_change(self.project_path, self.project, **dialog.result)
            self.reload_project(self.project_path)
            if report["changed_count"]:
                moved = report["effective_ms"] / 1000
                direction = "往后挪" if moved > 0 else "往前挪"
                self.status_var.set(f"已把 {report['changed_count']} 条字幕整体{direction} "
                                    f"{abs(moved):g} 秒；原文与译文保留。")
                self.log("平移前项目备份：" + str(backup))
                if report["clamped"]:
                    self.log(f"注意：请求偏移 {report['requested_ms'] / 1000:g} 秒，"
                             f"但最早一条在 {report['earliest_ms'] / 1000:g} 秒处，"
                             f"为避免出现负数，实际只挪了 {abs(moved):g} 秒。")
            else:
                self.status_var.set("偏移量为 0，字幕没有变化。")
        except (UserError, OSError) as exc:
            messagebox.showerror("无法保存偏移", save_error_text(exc))

    def glossary_marks(self):
        """按术语表算出每条字幕的术语命中：{id: (显示文字, 底色标签)}。

        术语表里按「原文 = 译名」一行一条写；命中而译文里没有对应的译名，就标出来——
        那才是真正要核对的东西。整个过程纯本地计算，不联网、不花钱，也不改字幕本身。
        """
        terms = parse_glossary(self.glossary.get("1.0", "end-1c"))
        marks = {}
        if not terms or not self.project:
            return marks
        for row in self.project["cues"]:
            matched, missing = glossary_hits(terms, row.get("source") or "", row.get("zh") or "")
            if not matched and not missing:
                continue
            parts = ["%s=%s" % (term, value) for term, value in matched]
            parts += ["⚠%s=%s" % (term, value) for term, value in missing]
            shown = parts[:3]
            if len(parts) > 3:
                shown.append("等 %d 个" % len(parts))
            marks[row["id"]] = ("、".join(shown), "term_miss" if missing else "term_ok")
        return marks

    def apply_glossary_marks(self):
        """刷新列表里的术语标记。改术语表会立刻重算，不需要重新翻译。"""
        if not getattr(self, "tree", None):
            return
        marks = self.glossary_marks()
        for row in (self.project or {}).get("cues", []):
            ident = str(row["id"])
            if not self.tree.exists(ident):
                continue
            label, tag = marks.get(row["id"], ("", ""))
            values = (row["id"], timestamp(row["start"]) + " — " + timestamp(row["end"]),
                      row.get("source") or "", row.get("zh") or "待翻译", label)
            self.tree.item(ident, values=values, tags=(tag,) if tag else ())

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
        self.apply_glossary_marks()
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
            values = (c.id, timestamp(c.start) + " — " + timestamp(c.end), c.source, c.zh or "待翻译", "")
            self.tree.item(str(c.id), values=values)
            self.apply_glossary_marks()
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
            messagebox.showerror("无法保存修改", save_error_text(exc))
            return False

    def refresh_model_hint(self):
        """把本机已有的模型标出来，并顺手把**当前选中模型的长短处**写在旁边。

        下拉里六个模型长得一样，选到没下的那个会先下几百 MB 到 3 GB，
        使用者有权在下拉旁边就看见"这个多大、多快、多准、要不要先下"。
        """
        ready = [name for name in BUILTIN_MODEL_REPOS
                 if local_model_status(name, str(USER_DIR / "models"))]
        chosen = self.asr_var.get().strip()
        pros = model_pros_cons(chosen) if chosen in MODEL_PROFILES else ""
        if ready:
            line = "本机已有：" + "、".join(ready) + "；选其他的需要先下载"
        else:
            line = "本机还没有模型，首次使用会自动下载"
        if chosen in MODEL_PROFILES:
            info = MODEL_PROFILES[chosen]
            state = "已下载" if chosen in ready else f"未下载（约 {info['mb']} MB）"
            # 下拉旁边只放"一眼能比"的：下载状态 + 速度 + 质量；完整说明在下面的对照表
            line = (f"{chosen}：{state}　·　速度 {'★' * info['speed']}{'☆' * (5 - info['speed'])}"
                    f"　质量 {'★' * info['quality']}{'☆' * (5 - info['quality'])}")
        self.model_hint_var.set(line)
        if hasattr(self, "selected_model_var"):
            self.selected_model_var.set(f"当前选择：{model_pros_cons(chosen)}" if pros else "")

    def refresh_hardware_hint(self):
        """自动检测这台机器，并给出推荐（只给依据，选哪个由使用者决定）。"""
        profile = hardware_profile()
        self.hardware_var.set(profile_text(profile))
        model, reason = recommend_model(profile)
        self.recommended_model = model
        self.recommend_var.set(f"{model}　——　{reason}")
        self.refresh_model_hint()

    def apply_recommended_model(self):
        """把识别模型切到推荐的那个（使用者点的，不是自动改的）。"""
        model = getattr(self, "recommended_model", "") or recommend_model(hardware_profile())[0]
        self.asr_var.set(model)
        self.refresh_model_hint()
        self.log(f"识别模型已切换为 {model}（按本机配置推荐）。")
        messagebox.showinfo("已切换", f"识别模型已设成「{model}」。\n\n"
                                      f"{model_pros_cons(model)}\n\n"
                                      "不改也没关系 —— 点「保存设置」或直接开始任务即可。")

    def confirm_model_download(self):
        """选到本机还没有的模型时，先把要下多少、能不能中断说清楚再动手。

        几百 MB 到 3 GB 的下载不能悄悄开始，也不能等失败了才让人知道。
        """
        chosen = self.asr_var.get().strip()
        if chosen not in BUILTIN_MODEL_REPOS:
            return True
        if local_model_status(chosen, str(USER_DIR / "models")):
            return True
        megabytes = model_download_mb(chosen)
        if messagebox.askyesno(
                "需要先下载模型",
                f"“{chosen}”这台电脑上还没有，需要联网下载约 {megabytes} MB。\n\n"
                "只下这一次，以后再用就不用下了。中途断网也不要紧，下次接着下。\n\n"
                "现在开始下载吗？"):
            return True
        self.log(f"已取消：“{chosen}”模型还没下载。可以把“识别模型”换成本机已有的模型。")
        return False

    def start_job(self, mode):
        if self.busy:
            return
        if self.queue_files and not self.in_queue:
            if mode == "translate" and not self.api_key_var.get().strip():
                messagebox.showinfo("填写密钥", "请在「翻译与识别设置」填写 DeepSeek API 密钥，或先选择「仅识别原文」。")
                self.notebook.select(1)
                self.key_entry.focus_set()
                return
            if mode == "translate" and not messagebox.askyesno("开始队列翻译", f"队列共 {len(self.queue_files)} 个文件，将依次识别并翻译。\n翻译费用按 DeepSeek 官方账单结算；每个文件的费用预估会写入日志。\n\n必须确认后才会开始。是否开始？"):
                return
            self.in_queue = True
            self.queue_mode = mode
            self.queue_index = 0
            self.queue_ok = 0
            self.queue_fail = 0
            self.project = None
            self.project_path = None
            self.source_var.set("")
            self.log(f"开始队列处理：共 {len(self.queue_files)} 个文件，模式：" + ("识别并翻译" if mode == "translate" else "仅识别原文") + "。")
        if not self.source_var.get() and not self.project_path:
            if self.in_queue and self.queue_index < len(self.queue_files):
                path = self.queue_files[self.queue_index]
                if Path(path).suffix.lower() in (".srt", ".ass"):
                    self.import_subtitles(path)
                else:
                    self._load_source(path)
            else:
                messagebox.showinfo("选择素材", "请先选择视频、音频或原文 SRT / ASS 字幕文件。")
                return
        if not self.save_current(silent=True):
            return
        chinese = (self.project or {}).get("subtitle_mode") == "chinese"
        complete = self.project and self.project["cues"] and all(clean(r["zh"]) for r in self.project["cues"])
        if self.project and self.project.get("recognition_complete") and (mode == "transcribe" or chinese or (complete and not self.force_var.get())):
            self.manual_export(mode != "transcribe", ask=False)
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
        if mode == "translate":
            if not self.persist_api_key():
                self.log("密钥本地保存未生效，本次翻译不受影响。")
            cues_for_estimate = None
            if self.project and self.project.get("recognition_complete"):
                cues_for_estimate = self.project["cues"]
            elif Path(self.source_var.get()).suffix.lower() == ".srt" and Path(self.source_var.get()).is_file():
                try:
                    from core import read_srt
                    cues_for_estimate = read_srt(self.source_var.get())
                except UserError:
                    cues_for_estimate = None
            if cues_for_estimate:
                from core import cost_message, estimate_cost
                model = self.model_var.get().strip()
                estimate = estimate_cost(cues_for_estimate, model, self.force_var.get())
                current = estimate["peak_cost"] if estimate["is_peak"] else estimate["off_cost"]
                if estimate["count"] and self.in_queue:
                    self.log(f"费用预估（队列模式自动继续）：约 {estimate['count']} 条字幕，"
                             f"{estimate['period_note']}，预计约 ¥{current:.2f}；实际以官方账单为准。")
                elif estimate["count"] and not messagebox.askyesno(
                        "翻译费用预估", cost_message(estimate, model) + "\n\n是否继续？"):
                    return
            elif not self.in_queue:
                # 全新素材：先本地识别（免费），拿到真实原文后再弹精确的费用确认
                self.pending_translate = True
                self.log("全新素材：先进行本地识别（不产生费用）；识别完成后会按真实原文弹出翻译费用确认。")
        if not self.out_var.get().strip():
            messagebox.showinfo("输出目录", "请先选择输出目录。")
            return
        local = self.local_model_var.get().strip()
        needs_asr = not (self.project or {}).get("recognition_complete") and Path(self.source_var.get()).suffix.lower() not in (".srt", ".ass")
        if needs_asr and local and not (Path(local) / "model.bin").is_file():
            messagebox.showerror("模型目录", "本地模型文件夹中没有 model.bin。请选择完整的 faster-whisper 模型，或清空该字段自动下载。")
            return
        if needs_asr and not local and not self.confirm_model_download():
            return
        self.save_settings()
        effective_mode = "transcribe" if self.pending_translate else mode
        config = {"input": self.source_var.get(), "project_path": self.project_path, "output": self.out_var.get(),
                  "asr_model": local or self.asr_var.get(), "language": {"英语": "en", "日语": "ja"}.get(self.lang_var.get(), ""),
                  "device": "cpu" if self.device_var.get().startswith("CPU") else "cuda", "model_dir": str(USER_DIR / "models"),
                  "api_key": self.api_key_var.get(), "model": self.model_var.get().strip(),
                  "glossary": self.glossary.get("1.0", "end-1c").strip(), "force": self.force_var.get(), "mode": effective_mode,
                  "ass_style_file": self.ass_style_var.get().strip() or None, "ass_style_name": self.ass_style_name_var.get(),
                  "tidy_axis": bool(self.tidy_var.get()),
                "separate_vocals": bool(self.separate_var.get())}
        self.diagnostics.start_task(config)
        self.launch_job(config)
        self.force_var.set(False)

    def launch_job(self, config):
        self.task_kind = "job"
        self.current_config = dict(config)
        self.active_stage = ""
        self.last_job_ok = False
        self.start_process(worker, config)

    def confirm_translate_after_asr(self):
        """首阶段（仅识别）完成后，按真实原文弹费用硬确认，确认后才翻译。"""
        try:
            if not self.project or not self.project.get("recognition_complete"):
                raise UserError("识别结果不可用，请重试。")
            from core import cost_message, estimate_cost
            model = self.model_var.get().strip()
            estimate = estimate_cost(self.project["cues"], model, False)
            if not estimate["count"]:
                self.log("原文已全部识别，没有需要翻译的条目。")
                return
            if not messagebox.askyesno("翻译费用预估", "本地识别已完成。\n\n" + cost_message(estimate, model) + "\n\n是否继续？"):
                self.log("已取消翻译；原文已保存，可稍后打开项目继续。")
                return
            if not self.api_key_var.get().strip():
                messagebox.showinfo("填写密钥", "请在「翻译与识别设置」填写 DeepSeek API 密钥，或打开项目后继续。")
                self.notebook.select(1)
                self.key_entry.focus_set()
                return
            self.persist_api_key()
            local = self.local_model_var.get().strip()
            config = {"input": self.source_var.get(), "project_path": self.project_path, "output": self.out_var.get(),
                      "asr_model": local or self.asr_var.get(), "language": {"英语": "en", "日语": "ja"}.get(self.lang_var.get(), ""),
                      "device": "cpu" if self.device_var.get().startswith("CPU") else "cuda", "model_dir": str(USER_DIR / "models"),
                      "api_key": self.api_key_var.get(), "model": self.model_var.get().strip(),
                      "glossary": self.glossary.get("1.0", "end-1c").strip(), "force": False, "mode": "translate",
                      "separate_vocals": bool(self.separate_var.get())}
            self.diagnostics.start_task(config)
            self.launch_job(config)
        except (UserError, OSError) as exc:
            self.log(str(exc))

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
                elif kind == "phase":
                    self.status_var.set(PHASES.get(value, value))
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
                    self.last_job_ok = True
                    self.status_var.set(f"完成：已导出 {len(value['files'])} 份文件。")
                    self.log("导出位置：" + value["folder"])
                    self.force_var.set(False)
                    self.alert_when_finished(value)
                    self.announce_axis_review()
                    self.refresh_model_hint()
                    self.maybe_auto_chinese_check()
                elif kind in ("error", "cancelled"):
                    self.seen_terminal = True
                    self.last_job_ok = False
                    self.pending_translate = False
                    self.status_var.set(value)
                    self.log(value)
                    if kind == "cancelled" and self.in_queue:
                        self.in_queue = False
                        self.queue_files = []
                        self.refresh_queue_list()
                        self.log("队列已停止，剩余文件未处理。")
                    elif kind == "error" and not self.close_when_stopped and not self.in_queue:
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
                if self.pending_translate and self.task_kind == "job" and self.last_job_ok:
                    self.pending_translate = False
                    self.after(60, self.confirm_translate_after_asr)
                if self.in_queue and self.task_kind == "job" and self.seen_terminal:
                    if self.last_job_ok:
                        self.queue_ok += 1
                        self.log(f"队列完成第 {self.queue_index + 1}/{len(self.queue_files)} 个。")
                    else:
                        self.queue_fail += 1
                        self.log(f"队列第 {self.queue_index + 1}/{len(self.queue_files)} 个未完成，自动继续下一个。")
                    self.queue_index += 1
                    if self.queue_index < len(self.queue_files):
                        self.log(f"开始队列第 {self.queue_index + 1}/{len(self.queue_files)} 个：{Path(self.queue_files[self.queue_index]).name}")
                        self.project = None
                        self.project_path = None
                        self.start_job(self.queue_mode)
                        self.after(150, self.poll)
                        return
                    self.in_queue = False
                    self.queue_files = []
                    self.refresh_queue_list()
                    summary = f"队列处理完成：成功 {self.queue_ok} 个，未完成 {self.queue_fail} 个。"
                    self.log(summary)
                    messagebox.showinfo("队列完成", summary)
                if self.close_when_stopped:
                    self.destroy()
                    return
        self.after(150, self.poll)

    def announce_axis_review(self):
        """跑完后提醒还有待确认的位置，指向「待确认…」。"""
        review = (self.project or {}).get("axis_review") or []
        gaps = sum(1 for row in review if row.get("kind") == "gap")
        shorts = sum(1 for row in review if row.get("kind") == "short")
        parts = []
        if gaps:
            parts.append(f"{gaps} 处长静音")
        if shorts:
            parts.append(f"{shorts} 条过短")
        if parts:
            self.log("待确认：" + "、".join(parts) + "。点「待确认…」查看，程序不会替你删改。")

    def maybe_auto_chinese_check(self):
        """翻译完自动跑一遍中文自检（设置里可以关）。

        为什么不在 done 里直接弹窗：排队处理多个文件时，每导出一个就弹一个框会打断人。
        所以自动模式只写日志 + 把按钮文字改成「⚠ N 处可能翻错」，想看再点。
        """
        if not self.ccheck_var.get():
            return
        if not any((c.get("zh") or "").strip() for c in (self.project or {}).get("cues") or []):
            return
        self.after(600, lambda: self.run_chinese_check(auto=True))

    def run_chinese_check(self, auto=False):
        """中文自检：挑出「可能翻错、会让你剪错」的句子。

        为什么做（实测记录见 chinese_check 模块开头）：使用者不懂日语，
        翻译错了他是看不出来的 —— 会照着错的意思剪片子。所以翻译完再扫一遍。
        成绩：朗读剧召回 7/7；干净素材 112 条只报 3 处且全中；一期约几分钱。

        auto=True 时不弹窗（排队处理多个文件时不能打断），只写日志 + 更新按钮。
        """
        if self.checking:
            return
        if not self.project or not self.project.get("cues"):
            if not auto:
                messagebox.showinfo("先载入字幕", "请先完成识别 / 翻译，或打开项目。")
            return
        key = self.api_key_var.get().strip()
        if not key:
            if not auto:
                messagebox.showinfo("需要 API 密钥", "中文自检要问模型，请先在设置里填好 API 密钥。")
            return
        if not self.save_current(silent=True):
            return
        cues = [dict(c) for c in self.project["cues"] if (c.get("zh") or "").strip()]
        if not cues:
            if not auto:
                self.log("还没有中文字幕，自检没有可检查的内容。")
            return
        model = self.model_var.get().strip() or DEFAULT_MODEL
        glossary = self.glossary.get("1.0", "end-1c").strip()
        batches = (len(cues) + chinese_check.BATCH - 1) // chinese_check.BATCH
        self.checking = True
        self.status_var.set("正在检查中文…")
        self.log(f"中文自检开始：{len(cues)} 条，分 {batches} 批问模型。")
        box = {"done": False, "result": None, "error": None}

        def work():
            try:
                client = DeepSeekClient(key, model)
                box["result"] = chinese_check.check(cues, glossary, client)
            except Exception as exc:                          # noqa: BLE001
                box["error"] = exc
            finally:
                box["done"] = True

        threading.Thread(target=work, daemon=True).start()
        self.after(300, lambda: self._wait_chinese_check(box, auto))

    def _wait_chinese_check(self, box, auto):
        """等自检跑完（不卡界面），把结果存进项目、写日志、更新按钮。"""
        if not box["done"]:
            self.after(300, lambda: self._wait_chinese_check(box, auto))
            return
        self.checking = False
        self.status_var.set("")
        if box["error"] is not None:
            self.log(f"中文自检没跑成（{type(box['error']).__name__}），不影响导出。")
            if not auto:
                messagebox.showwarning("中文自检没跑成",
                                       f"{box['error']}\n\n自检只是帮忙看一眼，失败不影响字幕和导出。")
            return
        result = box["result"] or {"items": []}
        items = result.get("items") or []
        total = len(self.project.get("cues") or [])
        # 存进项目：下次打开还在（导出的字幕文件不受影响）
        try:
            self.project["chinese_check"] = {
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "items": items,
            }
            if self.project_path:
                core.save_project(self.project_path, self.project)
        except Exception:                                     # noqa: BLE001
            self.log("自检结果没能存进项目（不影响使用）。")
        self.log(chinese_check.summarize(items, total))
        self.update_ccheck_button(len(items))
        if not auto:
            dialog = ChineseCheckDialog(self, items, total=total,
                                        batches=result.get("batches", 0),
                                        failed=result.get("failed", 0),
                                        on_jump=self.jump_to_cue,
                                        on_rerun=self.run_chinese_check)
            self.wait_window(dialog)

    def update_ccheck_button(self, count):
        """按钮文字跟着结果走：有可疑就写几处，让人一眼看见。"""
        if not hasattr(self, "ccheck_button"):
            return
        self.ccheck_button.configure(
            text=("⚠ %d 处可能翻错（点开看）" % count) if count else "中文自检（没发现问题）")

    def check_timeline(self):
        """时间轴体检：不听音频，只查字幕本身的硬伤。

        为什么不做「和音频对齐」：实测过两种不需要额外依赖的做法
        （VAD 覆盖率、能量起音比对），在 BGM 铺满的素材上都找不回已知偏移，
        详见 timeline_qc 模块开头的记录。所以改查这些真正会出事故的：
        叠字、闪帧、时间倒着走、看不清、读不完、成段漏句。
        """
        if not self.project or not self.project.get("cues"):
            messagebox.showinfo("先载入字幕", "请先打开项目 / SRT，或完成原文识别。")
            return
        if not self.save_current(silent=True):
            return
        cues = timeline_qc.cues_from_rows(self.project["cues"])
        result = timeline_qc.check_timeline(cues, duration=self._timeline_duration())
        self.log(timeline_qc.summarize(result))
        for kind, count in sorted(result["counts"].items(), key=lambda kv: -kv[1]):
            title = timeline_qc.KINDS[kind][0]
            level = timeline_qc.SEVERITY_LABEL[timeline_qc._severity(kind)]
            self.log(f"    [{level}] {title}：{count} 处")
        dialog = TimelineQcDialog(self, result, on_jump=self.jump_to_cue,
                                  on_fix=self.fix_timeline)
        self.wait_window(dialog)

    def _timeline_duration(self):
        """片长：优先用识别时记下的时长，没有就用最后一条的结束时间。"""
        info = (self.project or {}).get("recognition") or {}
        if isinstance(info, dict) and info.get("duration"):
            try:
                return float(info["duration"])
            except (TypeError, ValueError):
                pass
        cues = (self.project or {}).get("cues") or []
        return max((float(c["end"]) for c in cues), default=None)

    def jump_to_cue(self, cue_id):
        """跳到指定条目并选中，让体检 / 自检结果能一条条看过去。

        2026-10-14 修：这里原来调的是 `self.show_selected()` —— **那个方法根本不存在**，
        所以「双击跳到那一条」从来没成功过（时间轴体检、待确认、中文自检都受影响）。
        因为只有真去双击才会炸，单元测试和冒烟都测不到；是使用者双击自检结果时才暴露的。
        现在改调已有的 select_cue()（它会选中、并把原文/中文/时间填进编辑器）。
        """
        ident = str(cue_id)
        if not self.tree.exists(ident):
            return
        self.tree.selection_set(ident)
        self.tree.focus(ident)
        self.tree.see(ident)
        self.select_cue()

    def fix_timeline(self, issues):
        """一键修安全的项：时间倒着走、叠字、间隙过小。改前自动备份。"""
        count = sum(1 for it in issues if it.get("fixable"))
        if not count:
            return False
        if not messagebox.askyesno(
                "一键修时间轴",
                f"要修 {count} 处只涉及时间的毛病吗？\n\n"
                "· 只修：时间倒着走、两条叠在一起、间隙太小会闪\n"
                "· 不修：太短、太长、读不完 —— 那些要重排句子，得你自己看\n"
                "· 文字和译文一个字都不会动\n\n"
                "改之前会自动备份一份项目文件。"):
            return False
        try:
            _updated, changes, backup = timeline_qc.save_fix(self.project_path, self.project)
        except (UserError, OSError) as exc:
            messagebox.showerror("无法保存", save_error_text(exc))
            return False
        if not changes:
            messagebox.showinfo("不用修", "检查了一遍，没有可以自动修的地方。")
            return False
        self.reload_project(self.project_path)
        self.status_var.set(f"时间轴已修 {len(changes)} 处；原文与译文未改动。")
        self.log(f"时间轴体检：修了 {len(changes)} 处（倒序 / 叠字 / 闪帧）。")
        self.log("修改前项目备份：" + str(backup))
        remaining = timeline_qc.check_timeline(
            timeline_qc.cues_from_rows(self.project["cues"]), duration=self._timeline_duration())
        self.log(timeline_qc.summarize(remaining))
        messagebox.showinfo("修好了",
                            f"修了 {len(changes)} 处。\n\n" + timeline_qc.summarize(remaining) +
                            "\n\n超短、超长、读不完这些没动 —— 那些要重排句子，请看「时间轴体检」的列表。")
        return True

    def show_axis_review(self):
        """列出识别后要人工确认的位置，并允许还原整理前的轴。

        机器只标位置，不替人做决定：像「ほんと?」这种说得很快的完整台词
        不该合并，孤零零一个字的碎片也不该乱删，都留给使用者看一眼。
        """
        if not self.project:
            messagebox.showinfo("待确认", "还没有载入字幕。")
            return
        review = self.project.get("axis_review") or []
        raw = self.project.get("axis_raw") or []
        gaps = [row for row in review if row.get("kind") == "gap"]
        shorts = [row for row in review if row.get("kind") == "short"]
        if not gaps and not shorts:
            messagebox.showinfo("待确认", "当前项目没有记录到需要人工确认的位置。")
            return
        win = tk.Toplevel(self)
        win.title("待确认的位置")
        win.transient(self)
        win.geometry("780x430")
        ttk.Label(win, text="程序只标出位置，不替你改。逐条看过再决定是否手动删改。",
                  style="Muted.TLabel").pack(anchor="w", padx=10, pady=(10, 4))
        box = ScrolledText(win, wrap="word", height=18)
        box.pack(fill="both", expand=True, padx=10)
        lines = []
        if gaps:
            lines.append(f"长静音：{len(gaps)} 处连续 3 秒以上没有字幕，可能漏识别。")
            for gap in gaps:
                lines.append("    {} → {}（{:.1f} 秒），在「第 {} 条」和「第 {} 条」之间".format(
                    timestamp(gap["start"]), timestamp(gap["end"]), gap["seconds"],
                    gap["after_id"], gap["before_id"]))
        if shorts:
            if lines:
                lines.append("")
            lines.append(f"过短条目：{len(shorts)} 条短于 0.5 秒。")
            lines.append("    「ほんと?」这类是说很快的完整台词，不用管；孤零零一个字的可以自己判断。")
            for short in shorts:
                lines.append("    第 {} 条：{:.2f} 秒「{}」".format(
                    short["id"], short["seconds"], short["text"]))
        box.insert("1.0", "\n".join(lines))
        box.configure(state="disabled")
        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=10, pady=8)
        if raw:
            ttk.Button(bar, text=f"还原整理前的轴（{len(raw)} 条）",
                       command=lambda: self.revert_axis(win)).pack(side="left")
            ttk.Label(bar, text="已翻译的中文会按时间保留。", style="Muted.TLabel").pack(side="left", padx=8)
        ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right")
        win.bind("<Escape>", lambda event: win.destroy())
        win.focus_set()

    def revert_axis(self, win=None):
        """把轴换回整理前保存的 axis_raw，并尽量保住已完成的翻译。"""
        raw = (self.project or {}).get("axis_raw") or []
        if not raw or not self.project_path:
            messagebox.showinfo("还原", "当前项目没有可还原的整理前轴。")
            return
        if not messagebox.askyesno("还原", f"把字幕还原成整理前的 {len(raw)} 条？已翻译的中文会按时间保留。"):
            return
        updated = dict(self.project)
        updated["cues"] = restore_axis_cues(raw, self.project["cues"])
        updated.pop("axis_raw", None)
        updated.pop("axis_review", None)
        try:
            save_project(self.project_path, updated)
        except (OSError, UserError) as exc:
            messagebox.showerror("还原失败", str(exc))
            return
        self.reload_project(self.project_path)
        self.log(f"已还原整理前的轴：{len(raw)} 条。")
        if win is not None:
            win.destroy()

    def alert_when_finished(self, done):
        """翻译跑完时把窗口拉到前台提醒。

        翻译要跑几分钟到几十分钟，窗口在后台时使用者否则完全不知道跑完了。
        判定交给 core.should_alert，这里只负责动作。
        """
        mode = (self.current_config or {}).get("mode")
        if not should_alert(mode, True, self.in_queue, self.close_when_stopped, self.alert_var.get()):
            return
        self.deiconify()
        try:
            self.lift()
            self.attributes("-topmost", True)
            self.focus_force()
        except tk.TclError:
            pass
        try:
            self.bell()
        except tk.TclError:
            pass
        try:
            messagebox.showinfo("翻译完成",
                                f"中文翻译已完成，共导出 {len(done['files'])} 份文件。\n\n导出位置：\n{done['folder']}")
        finally:
            try:
                self.attributes("-topmost", False)
            except tk.TclError:
                pass

    def ass_style_choice(self):
        """导出对话框里那一段「ASS 样式」：只对从 .ass 导入的项目显示。"""
        if not (self.project or {}).get("ass"):
            return None
        path = self.ass_style_var.get().strip()
        label = ""
        if path:
            label = "%s · %s" % (Path(path).name, self.ass_style_name_var.get().strip() or "Default")
        return {"label": label, "available": bool(path)}

    def style_edit_warning(self):
        """改了样式、但这次导出用不上时说清楚原因。

        样式改动是按样式名记账的，而「导入文件的样式」和「我的模板」各有一套名字。
        改在 A 上却用 B 导出，改动会静默失效——这是最难查的一种失败，所以要主动拦一下。
        """
        meta = (self.project or {}).get("ass")
        edits = (meta or {}).get("style_edits") or {}
        if not edits:
            return ""
        if self.project.get("ass_style_mode") == "template":
            template = self.ass_style_var.get().strip()
            names = set(self._ass_styles(template)) if template else set()
            where = "你的模板"
        else:
            names = set(self._ass_styles())
            where = "导入的那份文件"
        missing = sorted(name for name in edits if name not in names)
        if not missing:
            return ""
        return ("你改过样式「%s」，但这次导出用的是%s的样式（%s），里面没有这个名字，"
                "改动的字号和颜色不会生效。\n\n"
                "请点「ASS 样式…」，把「样式从哪来」切到包含它的那一套，再导出。"
                % ("、".join(missing), where, "、".join(sorted(names)) or "空"))

    def manual_export(self, include_zh, ask=True):
        if not self.project or not self.save_current(silent=True):
            if not self.project:
                messagebox.showinfo("暂无字幕", "请先完成识别或打开已保存项目。")
            return
        if not self.project.get("cues"):
            messagebox.showinfo("暂无字幕", "还没有字幕，请先识别或导入 SRT。")
            return
        chinese_only = self.project.get("subtitle_mode") == "chinese"
        missing = sum(1 for row in self.project["cues"] if not clean(row["zh"]))
        translated = missing == 0
        include, blank_format = None, getattr(self, "last_blank_format", "srt")
        if ask:
            choices = export_choices(chinese_only)
            disabled = {key for key, _ in choices if key in ZH_KEYS and not translated}
            note = f"还有 {missing} 条没有中文译文，中文相关项已暂时禁用；可以先导出原文或空轴。" if missing else ""
            dialog = ExportDialog(self, choices, default_export_selection(include_zh, chinese_only, translated),
                                  disabled=disabled, blank_format=blank_format, note=note,
                                  ass_style=self.ass_style_choice(),
                                  ass_style_mode=self.project.get("ass_style_mode", "keep"))
            self.wait_window(dialog)
            if dialog.result is None:
                return
            include = dialog.result["include"]
            self.last_blank_format = blank_format = dialog.result["blank_format"]
            if "ass_style_mode" in dialog.result:
                self.project["ass_style_mode"] = dialog.result["ass_style_mode"]
        warning = self.style_edit_warning()
        if warning and not messagebox.askyesno("样式改动对不上", warning + "\n\n仍然要导出吗？"):
            return
        try:
            self.diagnostics.start_task({"mode": "manual_export", "device": "not_used"})
            self.diagnostics.record("media_info", actual_device="not_used")
            self.diagnostics.record("phase", phase="export")
            files = export_files(self.project_path, self.project, include_zh,
                                 self.ass_style_var.get().strip() or None, self.ass_style_name_var.get(),
                                 include=include, blank_format=blank_format)
            self.diagnostics.record("exported", phase="completed", export_count=len(files))
            self.status_var.set(f"已导出 {len(files)} 份独立文件。")
            self.log("导出位置：" + str(files[0].parent))
        except (UserError, OSError) as exc:
            self.diagnostics.record("error", phase="error", **error_info(exc))
            messagebox.showerror("无法导出", save_error_text(exc))

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

    def persist_api_key(self):
        """按勾选状态保存或清除本机密钥；返回是否成功。

        **只在明确取消勾选时清除。** 勾着但输入框是空的 → 什么都不做。

        为什么（真出过两次事）：原来是 `if 勾着 and 框里有内容: 存 else: 删`，
        于是任何「把密钥框清空」的路径（界面异常、测试脚本）在退出时都会把
        存好的密钥删掉，使用者得重新粘一遍。2026-10-13 被这个坑了两次。
        """
        try:
            from winsecret import forget_key, save_key
            if not self.remember_var.get():
                forget_key()                      # 明确取消勾选 → 清除（说明里就是这么写的）
            elif self.api_key_var.get().strip():
                save_key(self.api_key_var.get().strip())
            # 勾着但框是空的：不动已存的密钥
            return True
        except Exception:
            return False

    def safe_log(self, message):
        """写日志，但连日志本身坏了也不能抛异常（退出路径上用）。"""
        try:
            self.log(message)
        except Exception:                                    # noqa: BLE001
            pass

    def close_app(self):
        """关窗口。

        ⚠️ 这里的铁律：**不管出什么错，窗口都必须关得上**。
        真出过事：设置页因为一个 bug 没建出来，`save_settings()` 读不到控件抛异常，
        `self.destroy()` 那一行就永远执行不到 —— 使用者只能去任务管理器结束进程。
        所以保存都是"尽力而为"：存不下也照关，异常只写进日志。
        """
        try:
            self.persist_api_key()
        except Exception as exc:                             # noqa: BLE001
            self.safe_log(f"退出时保存密钥失败（不影响关闭）：{type(exc).__name__}")
        if self.busy:
            try:
                stop = messagebox.askyesno("任务正在运行", "停止当前任务并退出？已完成的翻译批次会保留。")
            except Exception:                                # noqa: BLE001
                stop = True
            if stop:
                self.safe_save_settings()
                self.close_when_stopped = True
                self.stop_job()
            return
        try:
            saved = self.save_current(silent=True)
        except Exception as exc:                             # noqa: BLE001
            self.safe_log(f"退出时保存当前条目失败（不影响关闭）：{type(exc).__name__}")
            saved = True
        if saved:
            self.safe_save_settings()
            try:
                self.api_key_var.set("")
            except Exception:                                # noqa: BLE001
                pass
        self.destroy()

    def safe_save_settings(self):
        """存设置，但绝不让它挡住关闭。"""
        try:
            self.save_settings()
        except Exception as exc:                             # noqa: BLE001
            self.safe_log(f"设置没能保存（不影响关闭）：{type(exc).__name__}")


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
