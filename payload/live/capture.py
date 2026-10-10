"""抓系统正在播放的声音（Windows WASAPI 回环）。

不录麦克风，只录"扬声器里出来的声音"，所以看直播、看视频、任何播放器都能用。
录到的音频进一个带时间轴的环形缓冲，供识别线程随时取「最近 N 秒」。

缓冲用「分块列表」而不是一整块数组：录音线程每 0.1 秒推一块，
如果每次都把整条缓冲重新拼接（30 秒 = 48 万个采样），录音线程会被拖慢，
实测会出现丢帧（soundcard 报 data discontinuity）。分块是 O(1) 追加。
"""
import threading
import time

import numpy as np


class AudioRing:
    """带绝对时间轴的音频缓冲。时间从开始采集算起（秒）。"""

    def __init__(self, rate, keep_seconds):
        self.rate = rate
        self.keep = int(keep_seconds * rate)
        self.chunks = []
        # total = 历史上一共推入多少个采样，等于「末尾」的绝对采样号。**只能加，不能减**：
        # 清理旧数据时如果连它一起减，时间轴就会跟着往前跑（曾经因此把 108 秒算成 30 秒）。
        self.total = 0
        self.held = 0           # 当前还留在 chunks 里的采样数
        self.lock = threading.Lock()
        self.dropped = 0        # 采到的块里，明显短于预期或长于预期的次数

    def push(self, block, expect=None):
        b = np.asarray(block, dtype=np.float32).reshape(-1)
        if b.size == 0:
            return
        if expect and abs(b.size - expect) >= expect * 0.5:
            self.dropped += 1
        with self.lock:
            self.chunks.append(b)
            self.total += b.size
            self.held += b.size
            # 只保留最近 keep 个采样；total 不动
            while len(self.chunks) > 1 and self.held - self.chunks[0].size >= self.keep:
                self.held -= self.chunks.pop(0).size

    def window(self, seconds):
        """返回 (最近 seconds 秒的音频, 这段音频起点对应的绝对秒数)。"""
        n = int(seconds * self.rate)
        with self.lock:
            picked, need = [], n
            for ch in reversed(self.chunks):
                picked.append(ch)
                need -= ch.size
                if need <= 0:
                    break
            if not picked:
                return np.zeros(0, dtype=np.float32), self.total / self.rate
            data = np.concatenate(list(reversed(picked)))
            if data.size > n:
                data = data[-n:]
            t0 = (self.total - data.size) / self.rate
            return data, t0

    def now(self):
        """已经采到的音频到哪个绝对秒数了。"""
        with self.lock:
            return self.total / self.rate


DEFAULT_SOURCE = "default"


def _import_soundcard():
    """把随包带的 soundcard 加进搜索路径再导入。"""
    import sys
    from pathlib import Path
    here = Path(__file__).resolve()
    for cand in (here.parents[1] / "lib", here.parents[1] / "_live" / "lib"):
        if cand.is_dir() and str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
    import soundcard as sc
    return sc


def _safe(call, fallback):
    try:
        value = call()
        return value if value is not None else fallback
    except Exception:  # noqa: BLE001
        return fallback


def list_sources(sc=None):
    """列出所有可选的音频来源。

    返回 [{"id", "label", "kind"}]，**第一条固定是「系统声音（默认）」**——
    它是唯一永远可用的选项；别的设备拔了就没了。

    抓声音失败（没有声卡、被安全软件拦）时不会抛异常，只返回默认那一条。
    """
    if sc is None:
        sc = _import_soundcard()
    items = [{"id": DEFAULT_SOURCE, "label": "系统声音（默认扬声器）", "kind": "loopback"}]
    seen = set()
    for sp in _safe(sc.all_speakers, []):
        sid = str(getattr(sp, "id", "") or getattr(sp, "name", "") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        items.append({"id": f"loopback:{sid}",
                      "label": f"系统声音 · {getattr(sp, 'name', sid)}",
                      "kind": "loopback"})
    for mic in _safe(lambda: sc.all_microphones(include_loopback=False), []):
        mid = str(getattr(mic, "id", "") or getattr(mic, "name", "") or "")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        items.append({"id": f"mic:{mid}",
                      "label": f"麦克风 · {getattr(mic, 'name', mid)}",
                      "kind": "mic"})
    return items


def source_label(source_id, sc=None):
    """把一个 source_id 翻译成人能看懂的名字。找不到就照原样返回。"""
    if not source_id or source_id == DEFAULT_SOURCE:
        return "系统声音（默认扬声器）"
    for item in list_sources(sc):
        if item["id"] == source_id:
            return item["label"]
    kind, _, key = source_id.partition(":")
    return f"{'麦克风' if kind == 'mic' else '系统声音'}（{key}，当前不可用）"


def open_source(sc, source_id):
    """按 source_id 打开录音设备。

    默认音源 = 默认扬声器的回环；设备不在了会抛出带中文说明的错误。
    """
    if not source_id or source_id == DEFAULT_SOURCE:
        sp = sc.default_speaker()
        if sp is None:
            raise RuntimeError("找不到默认扬声器。请确认电脑有可用的播放设备。")
        dev = (sc.get_microphone(id=str(getattr(sp, "id", "")), include_loopback=True)
               or sc.get_microphone(id=str(sp.name), include_loopback=True))
        if dev is None or not getattr(dev, "isloopback", False):
            raise RuntimeError(f"拿不到「{sp.name}」的回环录音设备。")
        return dev
    kind, _, key = source_id.partition(":")
    if kind == "loopback":
        dev = sc.get_microphone(id=key, include_loopback=True)
        if dev is None or not getattr(dev, "isloopback", False):
            raise RuntimeError(f"拿不到这个扬声器的回环录音设备。它可能已经被拔掉或改过名了。\n\n{key}")
        return dev
    if kind == "mic":
        dev = sc.get_microphone(id=key, include_loopback=False)
        if dev is None:
            raise RuntimeError(f"拿不到这个麦克风。它可能已经被拔掉或改过名了。\n\n{key}")
        return dev
    raise RuntimeError(f"不认识的音频来源：{source_id}")


class SystemAudioCapture(threading.Thread):
    """后台线程：不断从选定的音频来源录音，推进 AudioRing。

    音源可以是：
      · 某个扬声器的「回环」——抓电脑正在放的声音（默认就是这个）
      · 某个麦克风——抓你对着麦说的话
    用 source_id 指定，见 list_sources()。
    """

    def __init__(self, ring, rate=16000, block=0.05, source_id=DEFAULT_SOURCE):
        super().__init__(daemon=True)
        self.ring = ring
        self.rate = rate
        self.block = block
        self.source_id = source_id or DEFAULT_SOURCE
        self.stop_event = threading.Event()
        self.error = None
        self.device = None
        self.blocks = 0

    def _sc(self):
        return _import_soundcard()

    def _pick(self):
        return open_source(self._sc(), self.source_id)

    def run(self):
        import warnings
        try:
            lb = self._pick()
            self.device = lb.name
            n = max(1, int(self.rate * self.block))
            # 麦克风是单声道/多声道输入，回环也可能不止一路；统一取第一路。
            channels = 1
            with warnings.catch_warnings():
                # soundcard 在系统音频流短暂中断时会刷这个警告；不影响使用，只记次数。
                warnings.simplefilter("ignore")
                with lb.recorder(samplerate=self.rate, channels=channels) as rec:
                    while not self.stop_event.is_set():
                        data = rec.record(numframes=n)
                        if getattr(data, "ndim", 1) > 1:
                            data = data[:, 0]
                        self.ring.push(np.asarray(data, dtype=np.float32), expect=n)
                        self.blocks += 1
        except Exception as exc:  # noqa: BLE001
            self.error = exc

    def stop(self):
        self.stop_event.set()


class FileAudioSource(threading.Thread):
    """测试/开发用的音频来源：从 wav 文件按真实速度喂进 AudioRing。

    **完全不碰声卡、不发出任何声音。** 除了"从扬声器里抓"这一步之外，
    识别、断句、翻译、界面全都跑的是同一套代码，可以安静地反复回归测试。
    """

    def __init__(self, ring, wav_path, rate=16000, block=0.05, duration=None, speed=1.0):
        super().__init__(daemon=True)
        self.ring = ring
        self.wav_path = wav_path
        self.rate = rate
        self.block = block
        self.duration = duration
        self.speed = speed
        self.stop_event = threading.Event()
        self.error = None
        self.device = "（文件模拟，未使用声卡）"
        self.blocks = 0

    def run(self):
        import wave
        try:
            with wave.open(str(self.wav_path), "rb") as f:
                src_rate = f.getframerate()
                total = f.getnframes() / src_rate
                limit = min(total, self.duration) if self.duration else total
                n = int(src_rate * self.block)
                step = self.block / max(0.05, self.speed)
                next_at = time.time()
                pos = 0
                while not self.stop_event.is_set() and pos / src_rate < limit:
                    wait = next_at - time.time()
                    if wait > 0:
                        self.stop_event.wait(min(wait, 0.1))
                        continue
                    next_at += step
                    raw = f.readframes(n)
                    if not raw:
                        break
                    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                    if src_rate != self.rate:
                        # 线性重采样够用了：这里只是拿它冒充 16k 输入
                        idx = np.linspace(0, data.size - 1, int(data.size * self.rate / src_rate))
                        data = np.interp(idx, np.arange(data.size), data).astype(np.float32)
                    self.ring.push(data, expect=int(self.rate * self.block))
                    self.blocks += 1
                    pos += n
        except Exception as exc:  # noqa: BLE001
            self.error = exc

    def stop(self):
        self.stop_event.set()


def probe(seconds=1.5):
    """启动一小段采集，用来判断设备是否可用。返回 (设备名, 秒数, 峰值, 块数)。"""
    ring = AudioRing(16000, 5)
    cap = SystemAudioCapture(ring)
    cap.start()
    time.sleep(seconds)
    cap.stop()
    cap.join(timeout=3)
    if cap.error:
        raise cap.error
    data, _ = ring.window(seconds)
    return cap.device, data.size / 16000, (float(np.abs(data).max()) if data.size else 0.0), cap.blocks
