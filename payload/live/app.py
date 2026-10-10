"""实时中文字幕 · 启动程序。

用法：
    python live/app.py               抓系统声音（看直播用这个）
    python live/app.py --file xx.wav 从文件模拟（开发/测试用，全程静音）

依赖（都在字幕工坊里，不额外装东西）：
    faster-whisper + turbo 模型（识别）、DeepSeek API（翻译）、soundcard（抓声音）
"""
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live import studio  # noqa: E402

DATA_ROOT = studio.find_root()
APP_DIR = studio.app_dir(DATA_ROOT)

# 必须在导入 core / gpu_runtime / paths 之前设好。
# 这些模块用 paths.data_root() 找显卡组件、模型和设置，而它只看「自己这个文件在哪」：
# 从别处运行时它会以为根目录是 %LOCALAPPDATA%\SubtitleStudio，
# 于是把 540 MB 的显卡组件重新下载一遍（真发生过，白占 1.3 GB）。
os.environ.setdefault("SUBTITLE_STUDIO_ROOT", str(DATA_ROOT))

for p in [str(ROOT), str(APP_DIR), str(DATA_ROOT)] + [str(d) for d in studio.lib_dirs(ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from live import glossary as gl  # noqa: E402
from live.capture import (AudioRing, DEFAULT_SOURCE, FileAudioSource, SystemAudioCapture,
                          list_sources, source_label)  # noqa: E402
from live.overlay import Overlay  # noqa: E402
from live.pipeline import DEFAULTS, LiveTranscriber, Translator  # noqa: E402

FLASH_MODEL = "deepseek-flash"
# 这两个文件放在「数据根目录」，不要放程序目录：
# 程序目录（Easy\app-2.x）在每次「一键安装 / 修复」时会被整个替换掉，
# 放里面的话，运行记录和你辛苦加的词会在重装时全没了。
LOG_PATH = DATA_ROOT / "实时字幕_运行记录.txt"
GLOSSARY_EXTRA = DATA_ROOT / "live_glossary_extra.txt"
# 记住你选的音频来源。放在数据根目录，重装不会被清掉。
SETTINGS_PATH = DATA_ROOT / "实时字幕_设置.json"


def load_source_id():
    """读出上次选的音源。读不到就用默认（跟着系统默认播放设备走）。"""
    try:
        value = json.loads(SETTINGS_PATH.read_text("utf-8")).get("source_id")
        return value if isinstance(value, str) and value else DEFAULT_SOURCE
    except Exception:  # noqa: BLE001
        return DEFAULT_SOURCE


def save_source_id(source_id):
    try:
        SETTINGS_PATH.write_text(json.dumps({"source_id": source_id}, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
        return True
    except OSError as exc:
        log(f"音频来源没有记住（{exc}）")
        return False


def log(msg):
    """把运行情况记下来。

    抓声音模式下界面在屏幕上、命令行关了就什么都看不到；留一份记录，
    跑失败了才有据可查，跑成功了也能核对延迟和稳定性。
    只往数据根目录写这一个文本文件，不碰别的东西。
    """
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


class LiveApp:
    def __init__(self, use_device=True, file_path=None, verbose=False):
        self.use_device = use_device
        self.file_path = file_path
        self.verbose = verbose
        self.out = queue.Queue()          # 工作线程 → 界面线程
        self.base_glossary = self._read_settings_glossary()
        self.glossary_lock = threading.Lock()
        self.glossary_text = gl.merged_glossary(self.base_glossary)
        self.ring = AudioRing(16000, 30)
        self.capture = None
        self.transcriber = None
        self.translator = None
        self.paused = False
        self.started_at = None
        self.latencies = []
        self.ja_lengths = []
        self.zh_lengths = []
        self.seen_ids = set()
        self.source_id = load_source_id()
        self.overlay = Overlay(
            on_quit=self.shutdown,
            on_toggle_pause=self.toggle_pause,
            on_reload_glossary=self.reload_glossary,
            suggest=self.suggest,
            save_term=self.save_term,
            list_sources=list_sources,
            on_pick_source=self.switch_source,
            source_id=self.source_id,
        )

    # ---------- 音频来源 ----------
    def switch_source(self, source_id):
        """运行中换音源：停掉旧的采集线程，用新设备重开一个。

        音频环（ring）**不清空**——时间轴是接着走的，字幕不会因此错位；
        只是从这一刻起，进来的是另一个设备的声音。
        """
        if not self.use_device:
            return
        old = getattr(self.capture, "device", None)
        try:
            if self.capture is not None:
                self.capture.stop()
                self.capture.join(timeout=2)
            ring_now = self.ring.now()
            self.capture = SystemAudioCapture(self.ring, source_id=source_id)
            self.capture.start()
        except Exception as exc:  # noqa: BLE001
            log(f"换音源失败：{exc}")
            self.out.put(("status", f"换音源失败：{exc}"))
            return
        save_source_id(source_id)
        label = source_label(source_id)
        log(f"音频来源已切换：{old} → {label}（音频时钟 {ring_now:.1f}s 处）")
        self.out.put(("status", f"已切换到：{label}"))

    # ---------- 术语表 ----------
    def _read_settings_glossary(self):
        path = DATA_ROOT / "settings.json"
        try:
            return json.loads(path.read_text("utf-8")).get("glossary", "")
        except Exception:  # noqa: BLE001
            return ""

    def current_glossary(self):
        with self.glossary_lock:
            return self.glossary_text

    def reload_glossary(self):
        with self.glossary_lock:
            self.glossary_text = gl.merged_glossary(self.base_glossary, GLOSSARY_EXTRA)
            n = len(self.glossary_text)
        self.out.put(("status", f"术语表已重新读取（{n} 字）"))

    def suggest(self, ja_text):
        with self.glossary_lock:
            terms = [t[0] for t in __import__("core").parse_glossary(self.glossary_text)]
        return gl.suggest_terms(ja_text, terms)

    def save_term(self, term, value):
        ok, msg = gl.save_term(term, value, GLOSSARY_EXTRA)
        if ok:
            self.reload_glossary()
        return ok, msg

    # ---------- 启停 ----------
    def toggle_pause(self, paused):
        self.paused = paused
        if not paused and self.transcriber:
            self.transcriber.resync()

    def start(self):
        mode = "抓系统声音" if self.use_device else f"文件模拟 {self.file_path}"
        log("=" * 50)
        log(f"启动：{mode}")
        self.overlay.set_status("正在准备…")
        self.overlay.show_placeholder("正在启动…")
        autoclose = float(os.environ.get("LIVE_AUTOCLOSE", "0") or 0)
        if autoclose:
            # 测试用：跑够时间自己关窗，不用人守着
            self.overlay.root.after(int(autoclose * 1000), self.overlay._quit)
        if self.use_device:
            self.capture = SystemAudioCapture(self.ring, source_id=self.source_id)
        else:
            self.capture = FileAudioSource(self.ring, self.file_path)
        self.capture.start()
        # 延迟必须从「音频开始流进来」那一刻算，不能从「模型加载完」算：
        # 加载模型要好几秒，这几秒音频已经在走了，用 started_at 算会凭空差出一个加载时间。
        self.capture_wall = time.time()

        self.translator = Translator(
            self._key(), FLASH_MODEL, self.current_glossary,
            on_result=lambda r: self.out.put(("subtitle", r)),
            on_error=lambda e: self.out.put(("warn", f"翻译出错：{type(e).__name__}")))
        self.translator.start()

        threading.Thread(target=self._boot, daemon=True).start()
        self.overlay.root.after(150, self._pump)

    def _boot(self):
        try:
            time.sleep(0.5)
            if getattr(self.capture, "error", None) and self.source_id != DEFAULT_SOURCE:
                # 上次选的设备不在了（耳机拔了、蓝牙断了）→ 自动退回默认，别让人干等
                bad = source_label(self.source_id)
                log(f"音源「{bad}」打不开（{self.capture.error}），自动退回默认扬声器")
                self.capture.stop()
                self.capture.join(timeout=2)
                self.source_id = DEFAULT_SOURCE
                self.capture = SystemAudioCapture(self.ring, source_id=DEFAULT_SOURCE)
                self.capture.start()
                time.sleep(0.5)
                save_source_id(DEFAULT_SOURCE)
                self.out.put(("overlay_source", DEFAULT_SOURCE))
                self.out.put(("warn", f"原来的音源「{bad}」用不了，已自动改回默认扬声器。"
                                      f"想换别的，点右上角「音源」。"))
            if getattr(self.capture, "error", None):
                log(f"抓声音失败：{self.capture.error}")
                self.out.put(("fatal", f"抓声音失败：{self.capture.error}"))
                return
            log(f"音频来源：{self.capture.device}（{source_label(self.source_id)}）")
            log(f"术语表：{len(self.current_glossary())} 字")
            self.out.put(("status", f"正在加载识别模型…（音频来源：{self.capture.device}）"))
            self.out.put(("placeholder", "正在加载识别模型…（约 10 秒）"))
            self.transcriber = LiveTranscriber(
                self.ring, dict(DEFAULTS), on_sentence=self.translator.submit,
                on_error=lambda e: (log(f"识别出错：{type(e).__name__}: {e}"),
                                    self.out.put(("warn", f"识别出错：{type(e).__name__}"))))
            self.transcriber.load()
            self.transcriber.start()
            log(f"识别模型：{self.transcriber.model_name or '（未记录）'}")
            if getattr(self.transcriber, "model_note", ""):
                log(self.transcriber.model_note)
                self.out.put(("warn", self.transcriber.model_note))
            self.started_at = time.time()
            log("模型加载完成，开始识别")
            self.out.put(("status", "运行中"))
            self.out.put(("placeholder", "正在听…（播放有声音就会出字幕）"))
        except Exception as exc:  # noqa: BLE001
            import traceback
            log("启动失败：\n" + traceback.format_exc())
            self.out.put(("fatal", f"启动失败：{type(exc).__name__}: {exc}"))

    def _key(self):
        from live.pipeline import load_key
        return load_key(DATA_ROOT)

    def shutdown(self):
        for obj in (self.transcriber, self.translator, self.capture):
            try:
                if obj:
                    obj.stop()
            except Exception:  # noqa: BLE001
                pass

    # ---------- 界面轮询 ----------
    def _pump(self):
        try:
            while True:
                kind, payload = self.out.get_nowait()
                if kind == "subtitle":
                    self.overlay.show_subtitle(payload)
                    lat = time.time() - self.capture_wall - payload["end"]
                    mark = "半句" if payload.get("partial") else "整句"
                    # 延迟只按「这一句第一次出现」算 —— 那才是观众看到内容的时间。
                    # 把后面的「整句替换」也算进去的话，中位数会被拉高，反映不了真实体验。
                    sid = payload.get("id")
                    if sid is None or sid not in self.seen_ids:
                        if sid is not None:
                            self.seen_ids.add(sid)
                        self.latencies.append(lat)
                    log(f"字幕[{mark}] 延迟{lat:5.1f}s | {payload['zh']}  〔{payload['ja']}〕")
                    self.ja_lengths.append(len(payload["ja"]))
                    self.zh_lengths.append(len(payload["zh"]))
                    if self.verbose:
                        print(f"  [{mark} 延迟 {lat:5.1f}s] {payload['zh']}", flush=True)
                elif kind == "status":
                    self.overlay.set_status(payload)
                elif kind == "overlay_source":
                    self.overlay.set_source(payload)
                elif kind == "placeholder":
                    self.overlay.show_placeholder(payload)
                elif kind == "warn":
                    self.overlay.set_status(payload)
                elif kind == "fatal":
                    from tkinter import messagebox
                    messagebox.showerror("实时字幕", payload)
                    self.overlay.set_status(payload)
        except queue.Empty:
            pass
        if self.started_at and not self.paused:
            recent = self.latencies[-20:]
            med = sorted(recent)[len(recent) // 2] if recent else 0
            mins = (time.time() - self.started_at) / 60
            step_txt = ""
            if self.transcriber and self.transcriber.stats["steps"]:
                st = self.transcriber.stats
                step_txt = (f"｜每步 {st['seconds']/st['steps']:.2f}s/{DEFAULTS['step']:.1f}s"
                            f"｜积压 {self.transcriber.backlog():.0f}s")
            self.overlay.set_status(f"运行中 {mins:.1f} 分钟｜延迟中位 {med:.1f}s"
                                    f"｜已出 {len(self.latencies)} 条{step_txt}")
            if self.verbose and self.transcriber and self.transcriber.stats["steps"]:
                st = self.transcriber.stats
                now = int(time.time())
                if now != getattr(self, "_last_report", None):
                    self._last_report = now
                    print(f"  · 每步 {st['seconds']/st['steps']:.2f}s（预算 {DEFAULTS['step']}s）"
                          f"｜最慢 {st['worst']:.2f}s｜积压 {self.transcriber.backlog():.1f}s"
                          f"｜提交 {st['words']} 词，挡掉重复 {st['dups']}", flush=True)
            # 每 30 秒往记录里写一次运行状况，跑两小时也能事后核对稳不稳
            if self.transcriber and self.transcriber.stats["steps"]:
                now = int(time.time())
                if now - getattr(self, "_last_beat", 0) >= 30:
                    self._last_beat = now
                    st = self.transcriber.stats
                    recent = self.latencies[-20:]
                    med = sorted(recent)[len(recent) // 2] if recent else 0
                    ts = self.translator.stats if self.translator else {}
                    recent_ja = self.ja_lengths[-20:]
                    avg_ja = sum(recent_ja) / len(recent_ja) if recent_ja else 0
                    tiny = sum(1 for n in recent_ja if n <= 3)
                    log(f"状况：已出 {len(self.latencies)} 条｜延迟中位 {med:.1f}s"
                        f"｜原文长度中位 {avg_ja:.0f} 字（≤3字的碎片 {tiny}/{len(recent_ja)}）"
                        f"｜每步 {st['seconds']/st['steps']:.2f}s（预算 {DEFAULTS['step']}s）"
                        f"｜最慢 {st['worst']:.2f}s｜积压 {self.transcriber.backlog():.1f}s"
                        f"｜挡重复 {st['dups']}｜跳过 {st['skipped']}"
                        f"｜翻译 {ts.get('batches',0)} 批/{ts.get('lines',0)} 条"
                        f" 平均 {ts.get('seconds',0)/max(1,ts.get('batches',1)):.1f}s"
                        f"｜待翻 {self.translator.pending() if self.translator else 0}"
                        f"｜丢旧 {ts.get('dropped',0)}")
        self.overlay.root.after(200, self._pump)

    def run(self):
        self.start()
        self.overlay.run()


def _show_fatal(text):
    """无控制台（pythonw）启动时，出错要弹窗，不能一声不响地什么都不发生。"""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("实时中文字幕 · 启动失败", text)
        root.destroy()
    except Exception:  # noqa: BLE001
        pass


def main():
    # 先体检：缺东西的时候给人一句能看懂的话，而不是甩一段 traceback。
    # （实测教训：朋友把懒人版解压到别的盘、机器上没装字幕工坊，
    #   程序直接崩在 ModuleNotFoundError 上，他完全不知道该怎么办。）
    missing = studio.preflight(package_root=ROOT)
    if missing:
        text = "\n".join(missing)
        log("启动前检查没通过：\n" + text)
        print("\n" + "=" * 60, flush=True)
        print(text, flush=True)
        print("=" * 60 + "\n", flush=True)
        _show_fatal(text)
        return 2
    try:
        use_device = "--file" not in sys.argv
        file_path = None
        if not use_device:
            i = sys.argv.index("--file")
            file_path = sys.argv[i + 1] if len(sys.argv) > i + 1 else ""
        LiveApp(use_device=use_device, file_path=file_path, verbose=not use_device).run()
        return 0
    except Exception:  # noqa: BLE001
        import traceback
        tb = traceback.format_exc()
        log("启动失败：\n" + tb)
        _show_fatal("实时字幕没能启动。\n\n" + tb[-1200:] +
                    f"\n\n完整信息已写到：\n{LOG_PATH}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
