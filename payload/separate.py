"""人声分离：把音轨拆成人声与伴奏，再送去识别。

GTA 日语直播这类素材里游戏音效、音乐和说话声混在一起，Whisper 会漏听。
先分离出人声再识别，实测两段素材分别多听出 38% 和 6.6% 的内容（详见
VALIDATION.txt）。伴奏轨单独识别得到 0 条字幕，说明分离干净、模型也没有
对着纯音效编内容。

模型 Kim_Vocal_2 来自 Ultimate Vocal Remover (UVR) 项目，随本软件分发并按其
要求署名，见 THIRD_PARTY.txt。只依赖 onnxruntime 与 PyAV，不需要 torch。

模型输入 (1, 4, 3072, 256)：4 = 左右声道各 real/imag；3072 = n_fft/2；
256 = 帧数。对应 n_fft=6144、hop=1024、44.1kHz 立体声，即 UVR MDX-Net 的
标准配置。

处理按块流式进行：频谱与中间缓冲只跟块大小有关，不随音频时长增长，
因此一小时的直播不会把内存吃光。
"""
from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100
N_FFT = 6144
HOP = 1024
DIM_F = N_FFT // 2          # 3072，丢掉 Nyquist 频点
DIM_T = 256                 # 每次喂给模型的频谱帧数
PAD = N_FFT // 2
MODEL_NAME = "Kim_Vocal_2.onnx"
BLOCK_FRAMES = 2048         # 每块 2048 帧 ≈ 47.6 秒音频
MARGIN_FRAMES = PAD // HOP + 2   # 块边界多算几帧，避免窗能量归一化出现缺口


def thread_count():
    """推理线程数。

    ONNX Runtime 不是线程越多越快：在 20 逻辑核的机器上实测，开满线程每块
    7.9 秒，限制到 8 线程只要 3.6 秒——线程互相抢锁的开销超过了并行收益。
    取核数并封顶 8（少核机器按核数一对一，不额外超配）；可用环境变量
    SUBTITLE_SEPARATE_THREADS 覆盖。
    """
    override = os.environ.get("SUBTITLE_SEPARATE_THREADS", "")
    if override.isdigit() and int(override) > 0:
        return int(override)
    return max(2, min(8, os.cpu_count() or 4))


def model_file(app_dir):
    """随包附带的人声分离模型；缺失时返回 None。"""
    path = Path(app_dir) / "models" / MODEL_NAME
    return path if path.is_file() else None


def load_session(path):
    """建立 onnxruntime 会话。CPU 即可，不要求显卡。"""
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.log_severity_level = 3
    options.intra_op_num_threads = thread_count()
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def decode(path, rate=SAMPLE_RATE):
    """解码成 (2, N) float32 立体声；没有音轨时抛 ValueError。"""
    import av
    parts = []
    with av.open(str(path)) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise ValueError("这个文件里没有音轨")
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=rate)
        for frame in container.decode(stream):
            parts.extend(resampler.resample(frame))
        parts.extend(resampler.resample(None))
    if not parts:
        raise ValueError("没有从音轨里解出音频")
    return np.concatenate([part.to_ndarray() for part in parts], axis=1).astype(np.float32)


def frame_count(length):
    """整段音频做 STFT 会得到多少帧。"""
    return 1 + (length + 2 * PAD - N_FFT) // HOP


def _source(audio, start, stop):
    """取出 [start, stop) 采样，越界处按 reflect 补齐，与整段处理的结果一致。"""
    length = audio.shape[1]
    left = max(0, -start)
    right = max(0, stop - length)
    piece = audio[:, max(0, start):min(length, stop)]
    if left:
        piece = np.concatenate([audio[:, 1:left + 1][:, ::-1], piece], axis=1)
    if right:
        tail = audio[:, -2:-right - 2:-1]
        if tail.shape[1] < right:          # 音频太短，无法反射，补零
            tail = np.pad(tail, ((0, 0), (0, right - tail.shape[1])))
        piece = np.concatenate([piece, tail], axis=1)
    return piece


def _stft(audio, first, last, window):
    """帧区间 [first, last) 的频谱，shape (2, DIM_F, last-first)。"""
    start = first * HOP - PAD
    stop = (last - 1) * HOP - PAD + N_FFT
    segment = _source(audio, start, stop)
    count = last - first
    index = np.arange(N_FFT)[None, :] + HOP * np.arange(count)[:, None]
    spec = np.fft.rfft(segment[:, index] * window, axis=-1)[:, :, :DIM_F]
    return spec.transpose(0, 2, 1)


def _istft(spec, window):
    """频谱还原成波形，shape (2, (frames-1)*HOP + N_FFT)；这是加权重叠相加。"""
    channels, _, count = spec.shape
    full = np.zeros((channels, count, N_FFT // 2 + 1), dtype=np.complex64)
    full[:, :, :DIM_F] = spec.transpose(0, 2, 1)
    blocks = np.fft.irfft(full, n=N_FFT, axis=-1) * window
    out = np.zeros((channels, (count - 1) * HOP + N_FFT), dtype=np.float32)
    weight = np.zeros_like(out)
    square = window ** 2
    for i in range(count):
        out[:, i * HOP:i * HOP + N_FFT] += blocks[:, i, :]
        weight[:, i * HOP:i * HOP + N_FFT] += square
    return out / np.maximum(weight, 1e-8)


def _run_model(spec, session):
    """把频谱喂给分离模型，返回同样形状的人声频谱。"""
    channels, _, frames = spec.shape
    out = np.zeros_like(spec)
    chunks = int(np.ceil(frames / DIM_T))
    for n in range(chunks):
        lo, hi = n * DIM_T, min((n + 1) * DIM_T, frames)
        piece = spec[:, :, lo:hi]
        if piece.shape[2] < DIM_T:                        # 最后一块补零
            piece = np.pad(piece, ((0, 0), (0, 0), (0, DIM_T - piece.shape[2])))
        net_in = np.stack([piece.real, piece.imag], axis=1).astype(np.float32).reshape(1, 4, DIM_F, DIM_T)
        net_out = session.run(None, {"input": net_in})[0].reshape(2, 2, DIM_F, DIM_T)
        out[:, :, lo:hi] = (net_out[:, 0] + 1j * net_out[:, 1])[:, :, :hi - lo]
    return out


def separate_to_wav(audio, session, out_path, on_progress=None):
    """把 (2, N) 音频里的人声写成 16 位 WAV，返回人声与原始的整体响度比。

    分块处理并在每块结束时就写盘，所以内存占用与音频时长无关。
    """
    length = audio.shape[1]
    total = frame_count(length)
    window = np.hanning(N_FFT).astype(np.float32)
    energy_in = 0.0
    energy_out = 0.0
    written = 0
    with wave.open(str(out_path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        for lo in range(0, total, BLOCK_FRAMES):
            hi = min(lo + BLOCK_FRAMES, total)
            # 对齐到 DIM_T 的全局网格。模型按 256 帧一组处理，块边界若与这个网格
            # 错位，同一段音频会因为切法不同算出不同的人声；对齐后结果与整段处理
            # 一致，也不随 BLOCK_FRAMES 变化。
            first = max(0, ((lo - MARGIN_FRAMES) // DIM_T) * DIM_T)
            last = min(total, -(-hi // DIM_T) * DIM_T)
            spec = _stft(audio, first, last, window)
            block = _istft(_run_model(spec, session), window)
            begin = (lo - first) * HOP
            span = (hi - lo) * HOP
            if hi == total:                               # 最后一块补到最后一帧窗的末尾
                span = (total - 1 - lo) * HOP + N_FFT
            piece = block[:, begin:begin + span]
            start = lo * HOP - PAD
            if start < 0:                                 # 开头多出的反射部分丢掉
                piece = piece[:, -start:]
                start = 0
            if start + piece.shape[1] > length:           # 结尾超出音频长度的裁掉
                piece = piece[:, :length - start]
            energy_in += float((audio[:, start:start + piece.shape[1]] ** 2).sum())
            energy_out += float((piece ** 2).sum())
            written += piece.shape[1]
            pcm = np.clip(piece.T, -1.0, 1.0)
            handle.writeframes((pcm * 32767.0).astype("<i2").tobytes())
            if on_progress:
                on_progress(hi, total)
    if written != length:                                 # 内部一致性检查，正常不会触发
        raise RuntimeError("分离结果长度不对：写出 %d，应为 %d" % (written, length))
    return float(np.sqrt(energy_out / max(length, 1)) / max(np.sqrt(energy_in / max(length, 1)), 1e-9))


def separate_media(media, out_wav, app_dir, on_progress=None):
    """一步到位：读媒体文件 → 分离人声 → 写成 WAV。返回人声占比。"""
    path = model_file(app_dir)
    if path is None:
        raise FileNotFoundError("随包的人声分离模型缺失：" + MODEL_NAME)
    session = load_session(path)
    return separate_to_wav(decode(media), session, out_wav, on_progress)
