"""实时字幕管道：滑动窗口识别 → 切句 → 翻译。

识别部分的参数是实测出来的（见 _live_sim.py 的结论）：
  WINDOW=20s  每次看最近 20 秒
  RIGHT=1.5s  只提交「已经说了至少 1.5 秒」的内容（末尾还没有右文，不可靠）
  STEP=2.0s   每 2 秒识别一次 —— 显卡实测每步 1.7 秒，占用约 88%，跟得上
"""
import queue
import sys
import threading
import time
import wave
from collections import deque
from pathlib import Path

import numpy as np

DEFAULTS = {
    "window": 20.0,
    # 安全线：新出现的词要等这么久才敢交（窗口末尾没有右文，模型容易听错）。
    # 试过 1.0：实测延迟没有变好（5.9s vs 6.2s，在噪声范围内），
    # 反而增加「听错的词提前交出去、后面改不回来」的风险，所以退回 1.5。
    "right": 1.5,
    # 上一轮和这一轮识别完全一样的词，说明模型有把握，可以更早交。
    # 注意：识别周期是 2 秒时，这个优化几乎被周期量化吃掉；
    # 但如果以后把周期降到 1.5 秒以内，它就会开始起作用。
    "right_stable": 0.5,
    # 识别周期。实测：连续说话时每步要 2.29 秒。
    # step=2.0 占用 97%（几乎没余量），2.5 占用 92%（仍偏紧，实测丢了 3 次音频）。
    # 放宽到 3.0：延迟只多 0.5 秒（半句先出之后总延迟才 3 秒出头，扛得住），
    # 换来 24% 余量，显卡卡一下才不会丢内容。
    "step": 3.0,
    "gap": 0.7,            # 静音超过这么久就断句
    "maxchars": 30,        # 一句最长多少字就送去翻译（调小会让字幕变碎，保持 30）
    # 攒够这么多字就先把「半句」翻出来显示，整句好了再替换同一行。
    # 看直播的人等的是「现在在说什么」，先给半句比多等一秒半强。
    "partial_chars": 12,
    "maxwait": 4.0,        # 攒够这么久也送去翻译，避免一直等不到静音
    "maxlag": 10.0,        # 积压超过这么多秒就认了、跳掉一段，别让字幕越来越旧
}


class SentenceAssembler:
    """把陆续提交的词，按静音和长度切成句子。"""

    def __init__(self, cfg, on_sentence):
        self.cfg = cfg
        self.on_sentence = on_sentence
        self.words = []
        self.pending_since = 0.0
        self.last_feed_wall = 0.0
        self.sentence_id = 0          # 每句一个号，界面靠它把「半句」和「整句」认成同一行
        self.partial_sent = False     # 这一句的半成品已经发过了吗

    def feed(self, new_words):
        for w in new_words:
            if self.words:
                prev = self.words[-1]
                gap = w[0] - prev[1]
                text = "".join(x[2] for x in self.words)
                if gap > self.cfg["gap"] or len(text) >= self.cfg["maxchars"]:
                    self.flush()
            self.words.append(w)
            self.pending_since = w[1]
            self.last_feed_wall = time.time()

    def flush_partial(self):
        """手上这半句够长了就先发一版出去。

        看直播的人等的是「现在在说什么」，不是「一句工整的话」。
        先发半句，整句好了再把同一行替换掉 —— 最终文字和原来一样，但早了 1~1.5 秒。

        只发一次：发完就不再发，免得同一行反复跳。
        """
        if self.partial_sent or not self.words:
            return
        text = "".join(w[2] for w in self.words).strip()
        if len(text) < self.cfg["partial_chars"]:
            return
        self.partial_sent = True
        self.on_sentence({"id": self.sentence_id, "partial": True,
                          "start": self.words[0][0], "end": self.words[-1][1], "ja": text})

    def flush_if_stale(self, committed_until, now_wall=None):
        """该不该把手上这半句交出去。

        **关键：要比的是「已经提交到哪个音频时刻」，不是「当前音频时刻」。**
        提交的内容永远比当前音频晚 right 秒（安全线），拿当前时刻去比，
        「停顿了」这个条件会永远成立，于是每 2 秒就切一句 —— 实测导致
        43% 的字幕只有 1~2 个字（「好」「大」「魔」这种）。
        """
        if not self.words:
            return
        # 音频域：从那句话之后，又提交了 gap 秒的音频却没有新词 → 真的停顿了
        audio_stale = committed_until - self.words[-1][1] > self.cfg["gap"]
        # 墙钟域兜底：直播静音时音频时钟不走，不能让半句话永远挂在界面上不显示
        wall_stale = (now_wall is not None and self.last_feed_wall
                      and now_wall - self.last_feed_wall > self.cfg["maxwait"])
        if audio_stale or wall_stale:
            self.flush()

    def flush(self):
        if not self.words:
            return
        text = "".join(w[2] for w in self.words).strip()
        if text:
            self.on_sentence({"id": self.sentence_id, "partial": False,
                              "start": self.words[0][0], "end": self.words[-1][1], "ja": text})
        self.words = []
        self.pending_since = 0.0
        self.partial_sent = False
        self.sentence_id += 1


class LiveTranscriber(threading.Thread):
    """滑动窗口识别线程。每提交一句，就回调 on_sentence。"""

    def __init__(self, ring, cfg, on_sentence, on_error=None, tmp_path=None):
        super().__init__(daemon=True)
        self.ring = ring
        self.cfg = cfg
        self.on_sentence = on_sentence
        self.on_error = on_error
        self.stop_event = threading.Event()
        self.tmp = Path(tmp_path or "_live_window.wav")
        self.committed_until = 0.0
        self.recent = deque(maxlen=60)   # 最近提交过的词，用来挡掉滑窗造成的重复
        self.prev_words = []             # 上一轮识别结果，用来判断「稳不稳」
        self.model_name = ""             # load() 里填：实际用的是哪个模型
        self.model_note = ""             # load() 里填：不是 turbo 时的提醒
        self.stats = {"steps": 0, "seconds": 0.0, "worst": 0.0, "words": 0,
                      "dups": 0, "skipped": 0, "fast": 0}
        self.model = None
        self.asm = SentenceAssembler(cfg, self._emit)

    def _is_duplicate(self, a, b, text):
        """这个词是不是刚提交过？

        滑动窗口每一轮都会重新识别同一段音频，同一个词会被反复识别出来。
        之前只用一个时间指针去重，结果同一个词被提交两三次 —— 这正是
        「グッボボーイ」「お姉さんたちがが」这种重复音的来源。

        判定用「时间上是否重叠」而不是「文字是否相同」：连着说两遍同一个词
        （「ごめんごめんごめんごめん」）时间上是不重叠的，不能误杀。
        """
        dur = max(1e-6, b - a)
        for x, y, t in self.recent:
            if t != text:
                continue
            if min(b, y) - max(a, x) > 0.6 * dur:
                return True
        return False

    def _emit(self, sentence):
        self.on_sentence(sentence)

    def load(self):
        import gpu_runtime
        from live import studio
        gpu_runtime.prepare_gpu(threading.Event(), lambda k, v: None)
        from faster_whisper import WhisperModel
        from core import ASR_OPTIONS
        self.options = ASR_OPTIONS
        path, name, preferred = studio.find_model()
        if path is None:
            raise RuntimeError("本机一个识别模型都没有。\n"
                               "请打开字幕工坊的安装程序，点「一键安装 / 修复」，"
                               "安装模式选 turbo，装完再打开本程序。")
        self.model_name = name
        if not preferred:
            # 有别的模型就先用着——能跑总比打不开强，但要如实说质量会差些
            self.model_note = (f"注意：没找到 turbo 模型，正在用「{name}」代替，"
                               "识别质量会差一些。想要最好效果，请在字幕工坊里装 turbo。")
        self.model = WhisperModel(
            str(path),
            device="cuda", compute_type="int8_float16", cpu_threads=8, num_workers=1)
        return self.model

    def _transcribe_window(self, data):
        with wave.open(str(self.tmp), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(16000)
            f.writeframes((np.clip(data, -1, 1) * 32767).astype(np.int16).tobytes())
        segments, _info = self.model.transcribe(
            str(self.tmp), language="ja", task="transcribe", **self.options)
        out = []
        for s in segments:
            for w in (s.words or []):
                t = w.word.strip()
                if t:
                    out.append((w.start, w.end, t))
        return out

    def _is_stable(self, a, text):
        """这个词上一轮也识别出来了、文字完全一样吗？

        是的话说明模型对它有把握，可以少等一会儿（见 right_stable）。
        """
        for x, _y, t in self.prev_words:
            if t == text and abs(a - x) < 0.6:
                return True
        return False

    def run(self):
        try:
            if self.model is None:
                self.load()
            # 固定节拍：每 step 秒跑一轮，识别耗时算在 step 里面。
            # 写成「先睡 step 再识别」的话，实际周期会变成 step+识别耗时（实测 3.5 秒而不是 2 秒），
            # 延迟白白多一秒半。
            next_at = time.time()
            while not self.stop_event.is_set():
                wait = next_at - time.time()
                if wait > 0:
                    self.stop_event.wait(min(wait, 0.2))
                    continue
                next_at += self.cfg["step"]
                if next_at < time.time() - self.cfg["step"]:
                    next_at = time.time() + self.cfg["step"]   # 落后太多就重新对齐，别越积越多
                data, t0 = self.ring.window(self.cfg["window"])
                if data.size < 16000:
                    continue
                cost0 = time.time()
                try:
                    words = self._transcribe_window(data)
                except Exception as exc:  # noqa: BLE001
                    if self.on_error:
                        self.on_error(exc)
                    continue
                cost = time.time() - cost0
                self.stats["steps"] += 1
                self.stats["seconds"] += cost
                self.stats["worst"] = max(self.stats["worst"], cost)

                live = self.ring.now()
                # 跟不上时的退路：显卡被别的程序抢走时会积压，字幕会越来越旧。
                # 与其越拖越久，不如丢掉一段老的——观众要的是「现在在说什么」。
                #
                # 但必须先确认「真的还有话没处理」。直播静音时没有词可提交，
                # committed_until 自然不动，看起来就像积压 —— 实测一场直播停了
                # 3 分半，这个判断被触发了 29 次，直播恢复时开头可能被误丢。
                pending_speech = any(t0 + b > self.committed_until + 0.3 for a, b, t in words)
                if pending_speech and live - self.committed_until > self.cfg["maxlag"]:
                    self.stats["skipped"] += 1
                    self.committed_until = live - 3.0
                    self.asm.words = []
                    self.asm.pending_since = 0.0
                fresh = []
                fast = 0
                for a, b, t in words:
                    abs_a, abs_b = t0 + a, t0 + b
                    # 稳的词用短安全线，不稳的词用长安全线
                    stable = self.cfg["right_stable"] < self.cfg["right"] and self._is_stable(abs_a, t)
                    if abs_b > live - (self.cfg["right_stable"] if stable else self.cfg["right"]):
                        continue
                    if abs_a < self.committed_until - 2.0:   # 超出回溯范围
                        continue
                    if self._is_duplicate(abs_a, abs_b, t):
                        self.stats["dups"] += 1
                        continue
                    if stable:
                        fast += 1
                    fresh.append((abs_a, abs_b, t))
                self.stats["fast"] += fast
                self.prev_words = words
                if fresh:
                    for w in fresh:
                        self.recent.append((w[0], w[1], w[2]))
                    self.committed_until = max(self.committed_until,
                                               max(w[1] for w in fresh))
                    self.stats["words"] += len(fresh)
                    self.asm.feed(fresh)
                self.asm.flush_partial()
                # 停顿确认不能等「下一句话的词到了」才发现——那要白等 1~2 秒。
                # 直接看当前这一轮的识别结果：如果这句之后一个词都没有，
                # 而且音频已经走过「安全线 + 停顿阈值」，那就是真的静音了，立刻断句。
                # 这是「观察」而不是「推断」，所以不需要去猜识别有没有落后。
                if (self.asm.words
                        and live - self.asm.words[-1][1] > self.cfg["right"] + self.cfg["gap"]
                        and not any(t0 + b > self.asm.words[-1][1] + 0.3 for a, b, t in words)):
                    self.asm.flush()
                self.asm.flush_if_stale(self.committed_until, time.time())
        except Exception as exc:  # noqa: BLE001
            if self.on_error:
                self.on_error(exc)

    def backlog(self):
        """已经说出去、但还没提交的音频秒数。数字一直涨就说明识别跟不上了。"""
        if not self.model:
            return 0.0
        return max(0.0, self.ring.now() - self.committed_until - self.cfg["right"])

    def stop(self):
        self.stop_event.set()
        self.asm.flush()

    def resync(self):
        """暂停后继续时调用：跳过暂停期间积压的音频，别把老话又翻一遍。"""
        self.asm.words = []
        self.asm.pending_since = 0.0
        self.committed_until = self.ring.now()


class Translator(threading.Thread):
    """翻译线程。

    实测：一句一句翻的时候，翻译比识别慢，队列越积越多，字幕越来越旧
    （跑 55 秒后延迟从 3 秒涨到 14 秒）。两个办法一起用：

      · **攒批**：把已经排队的几条合成一次请求（一次请求翻多条，总耗时几乎不变，
        省掉的是每条一次的网络往返）
      · **限量**：排队超过 max_pending 就丢掉最老的——看直播要的是"现在在说什么"，
        一句十秒前的字幕没有价值

    术语表可以随时替换，下一批立刻生效。
    """

    def __init__(self, key, model, glossary_getter, on_result, on_error=None,
                 max_pending=3, batch_max=6, batch_wait=0.12):
        super().__init__(daemon=True)
        self.key = key
        self.model = model
        self.glossary_getter = glossary_getter
        self.on_result = on_result
        self.on_error = on_error
        self.max_pending = max_pending
        self.batch_max = batch_max
        self.batch_wait = batch_wait
        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.stats = {"batches": 0, "lines": 0, "dropped": 0, "seconds": 0.0, "errors": 0}

    def submit(self, sentence):
        while self.q.qsize() >= self.max_pending:
            try:
                self.q.get_nowait()
                self.stats["dropped"] += 1
            except queue.Empty:
                break
        self.q.put(sentence)

    def pending(self):
        return self.q.qsize()

    def run(self):
        import core
        client = core.DeepSeekClient(self.key, self.model)
        while not self.stop_event.is_set():
            try:
                first = self.q.get(timeout=0.3)
            except queue.Empty:
                continue
            batch = [first]
            # 稍等一下，把同时冒出来的短句凑成一次请求
            if self.batch_max > 1:
                time.sleep(self.batch_wait)
                while len(batch) < self.batch_max:
                    try:
                        batch.append(self.q.get_nowait())
                    except queue.Empty:
                        break
            cues = [core.Cue(i + 1, s["start"], s["end"], s["ja"]) for i, s in enumerate(batch)]
            t0 = time.time()
            try:
                got = client.translate(cues, "ja", "", self.glossary_getter())
            except Exception as exc:  # noqa: BLE001
                self.stats["errors"] += 1
                if self.on_error:
                    self.on_error(exc)
                # 攒批省时间，代价是失败会连坐：一次请求翻 5 条，失败就丢 5 条。
                # 所以失败的要放回去重试（最多 2 次），别让一次网络抖动吃掉一整段话。
                for s in batch:
                    s["_tries"] = s.get("_tries", 0) + 1
                    if s["_tries"] <= 2:
                        self.q.put(s)
                    else:
                        self.stats["dropped"] += 1
                continue
            self.stats["seconds"] += time.time() - t0
            self.stats["batches"] += 1
            self.stats["lines"] += len(batch)
            for i, s in enumerate(batch):
                zh = got.get(i + 1)
                if zh:
                    self.on_result({**s, "zh": zh})

    def stop(self):
        self.stop_event.set()


def load_key(root=r"D:\字幕工坊"):
    import base64
    import json
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "repo-zimu-studio" / "payload"))
    import winsecret
    path = Path(root) / winsecret.SECRET_FILE
    blob = winsecret.unprotect(base64.b64decode(path.read_text("ascii")))
    return json.loads(blob).get("key")
