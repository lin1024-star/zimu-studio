"""Bounded, local-only diagnostics. Never serialize requests, captions or paths."""
from __future__ import annotations

import csv
import ctypes as C
import importlib.metadata
import io
import json
import math
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

MIB = 1024 ** 2
PHASES = {
    "idle": "空闲", "preparing": "准备任务", "gpu_setup": "检查／配置显卡",
    "model_loading": "加载识别模型", "audio_preprocessing": "音频预处理：解码／人声检测／特征计算",
    "inference": "逐段识别字幕", "translation": "翻译字幕", "export": "导出文件",
    "completed": "已完成", "cancelled": "已停止", "error": "发生错误",
}
DEVICES = {"cpu": "CPU", "cuda": "NVIDIA GPU", "unconfirmed": "尚未确认", "not_used": "本次未运行识别"}
CATEGORIES = {
    "cuda_out_of_memory", "cuda_runtime", "memory_exhausted", "dependency_missing", "permission",
    "network", "authentication", "invalid_media", "worker_native_exit", "unknown", "user_cancel",
    "gpu_unavailable", "driver_outdated", "disk_space",
}
ENUMS = {
    "phase": set(PHASES), "actual_device": set(DEVICES), "requested_device": set(DEVICES),
    "compute_type": {"int8", "int8_float16", "float16", "float32", "int8_float32", "unknown"},
    "mode": {"transcribe", "translate", "gpu_setup", "manual_export"},
    "model_choice": {"tiny", "base", "small", "medium", "large-v2", "large-v3", "turbo", "local/custom"},
    "model_source": {"builtin", "local/custom"}, "error_category": CATEGORIES,
}
NUMBERS = {
    "worker_pid", "exit_code", "errno", "winerror", "file_size_bytes", "model_binary_bytes",
    "audio_duration_seconds", "cues_count", "translated_count", "export_count", "progress_pct",
    "cpu_threads", "beam_size", "sample_count", "task_id", "elapsed_seconds", "http_status",
}
FLAGS = {"vad_filter", "word_timestamps", "recognition_complete", "force", "had_terminal_event"}
EVENTS = {"session_started", "session_closed", "task_started", "worker_started", "worker_exited",
          "phase", "model_loaded", "media_info", "project_reused", "cpu_fallback", "error",
          "cancelled", "completed", "exported", "stop_requested", "forced_stop", "gpu_ready",
          "progress", "gpu_monitor_status", "monitor_error", "ui_error"}
CSV_FIELDS = ["time_utc", "session_elapsed_s", "task_id", "phase", "requested_device", "actual_device",
              "worker_pid", "progress_pct", "system_cpu_pct", "app_cpu_pct", "worker_cpu_pct",
              "system_ram_total_mib", "system_ram_used_mib", "system_ram_pct", "system_commit_used_mib",
              "system_commit_limit_mib", "app_working_set_mib", "worker_working_set_mib",
              "app_private_commit_mib", "processes_read", "processes_expected", "host_status",
              "gpu_status", "app_gpu_memory_mib", "app_gpu_memory_status", "gpus_json"]


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def error_info(exc, *, gpu=False):
    """Inspect the message only to classify it; never store the message/locals."""
    outer_text = str(exc).lower()
    seen = set()
    while exc.__cause__ is not None and id(exc) not in seen:
        seen.add(id(exc)); exc = exc.__cause__
    text = outer_text + " " + str(exc).lower()
    category = "unknown"
    if isinstance(exc, MemoryError): category = "memory_exhausted"
    elif "out of memory" in text: category = "cuda_out_of_memory" if gpu else "memory_exhausted"
    elif "驱动较旧" in text: category = "driver_outdated"
    elif any(s in text for s in ("没有检测到可用", "未找到可用的 nvidia", "只支持 64 位 windows")): category = "gpu_unavailable"
    elif any(s in text for s in ("空间不足", "no space left")): category = "disk_space"
    elif any(s in text for s in ("cuda", "cublas", "cudnn", "device-side")): category = "cuda_runtime"
    elif isinstance(exc, (ImportError, ModuleNotFoundError)): category = "dependency_missing"
    elif isinstance(exc, PermissionError): category = "permission"
    elif any(s in text for s in ("401", "unauthorized", "authentication")): category = "authentication"
    elif any(s in text for s in ("connection", "timeout", "timed out", "certificate", "network")): category = "network"
    elif any(s in text for s in ("invalid data", "invalid audio", "invalid video")): category = "invalid_media"
    result = {"error_category": category, "exception_type": type(exc).__name__}
    for key in ("errno", "winerror"):
        if type(getattr(exc, key, None)) is int: result[key] = getattr(exc, key)
    if type(getattr(exc, "code", None)) is int: result["http_status"] = exc.code
    frames, tb = [], exc.__traceback__
    while tb:
        code = tb.tb_frame.f_code
        # Only code location: no absolute paths, source lines or frame locals.
        filename = Path(code.co_filename).name
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", filename) and re.fullmatch(r"[A-Za-z0-9_<>]{1,80}", code.co_name):
            frames.append(f"{filename}:{tb.tb_lineno}:{code.co_name}")
        tb = tb.tb_next
    result["stack"] = frames[-12:]
    return result


def safe_fields(fields):
    """Allowlist, not redaction: arbitrary strings and nested config are discarded."""
    out = {}
    for key, value in fields.items():
        if key in ENUMS and isinstance(value, str) and value in ENUMS[key]: out[key] = value
        elif key in NUMBERS and finite(value): out[key] = value
        elif key in FLAGS and type(value) is bool: out[key] = value
        elif key == "exception_type" and isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", value): out[key] = value
        elif key == "stack" and isinstance(value, list):
            out[key] = [v for v in value[-12:] if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}:\d{1,7}:[A-Za-z0-9_<>]{1,80}", v)]
    return out


class FileTime(C.Structure):
    _fields_ = [("low", C.c_uint32), ("high", C.c_uint32)]
    def value(self): return self.low + (self.high << 32)


class MemoryStatus(C.Structure):
    _fields_ = [("length", C.c_uint32), ("load", C.c_uint32)] + [(n, C.c_uint64) for n in
                ("total_phys", "avail_phys", "total_page", "avail_page", "total_virtual", "avail_virtual", "avail_extended")]


class ProcessMemory(C.Structure):
    _fields_ = [("cb", C.c_uint32), ("faults", C.c_uint32)] + [(n, C.c_size_t) for n in
                ("peak_rss", "rss", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile", "private")]


class WindowsCounters:
    def __init__(self):
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.psapi = C.WinDLL("psapi", use_last_error=True)
        self.kernel.GetSystemTimes.argtypes = [C.POINTER(FileTime)] * 3
        self.kernel.GetSystemTimes.restype = C.c_int
        self.kernel.GlobalMemoryStatusEx.argtypes = [C.POINTER(MemoryStatus)]
        self.kernel.GlobalMemoryStatusEx.restype = C.c_int
        self.kernel.OpenProcess.argtypes = [C.c_uint32, C.c_int, C.c_uint32]
        self.kernel.OpenProcess.restype = C.c_void_p
        self.kernel.CloseHandle.argtypes = [C.c_void_p]
        self.kernel.CloseHandle.restype = C.c_int
        self.kernel.GetProcessTimes.argtypes = [C.c_void_p] + [C.POINTER(FileTime)] * 4
        self.kernel.GetProcessTimes.restype = C.c_int
        self.psapi.GetProcessMemoryInfo.argtypes = [C.c_void_p, C.POINTER(ProcessMemory), C.c_uint32]
        self.psapi.GetProcessMemoryInfo.restype = C.c_int

    def system(self):
        idle, kernel, user = FileTime(), FileTime(), FileTime()
        if not self.kernel.GetSystemTimes(C.byref(idle), C.byref(kernel), C.byref(user)): raise OSError("system counters")
        mem = MemoryStatus(); mem.length = C.sizeof(mem)
        if not self.kernel.GlobalMemoryStatusEx(C.byref(mem)): raise OSError("memory counters")
        return {"ticks": kernel.value() + user.value(), "idle": idle.value(),
                "ram_total": mem.total_phys, "ram_used": mem.total_phys - mem.avail_phys,
                "commit_used": mem.total_page - mem.avail_page, "commit_limit": mem.total_page}

    def process(self, pid):
        handle = self.kernel.OpenProcess(0x0400 | 0x0010, False, pid)
        if not handle: return None
        try:
            created, exited, kernel, user = FileTime(), FileTime(), FileTime(), FileTime()
            mem = ProcessMemory(); mem.cb = C.sizeof(mem)
            if not self.kernel.GetProcessTimes(handle, C.byref(created), C.byref(exited), C.byref(kernel), C.byref(user)): return None
            if not self.psapi.GetProcessMemoryInfo(handle, C.byref(mem), C.sizeof(mem)): return None
            return {"identity": (pid, created.value()), "cpu_seconds": (kernel.value() + user.value()) / 1e7,
                    "rss": mem.rss, "private": mem.private}
        finally:
            self.kernel.CloseHandle(handle)


class LinuxCounters:
    """Allows real sampler validation on the build host, without extra packages."""
    def system(self):
        ticks = [int(v) for v in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1); mem[k] = int(v.split()[0]) * 1024
        return {"ticks": sum(ticks), "idle": ticks[3] + ticks[4], "ram_total": mem["MemTotal"],
                "ram_used": mem["MemTotal"] - mem["MemAvailable"], "commit_used": None, "commit_limit": None}

    def process(self, pid):
        try:
            proc = Path("/proc/self") if pid == os.getpid() else Path(f"/proc/{pid}")
            # Some containers mount /proc from a different PID namespace. Do not
            # accidentally attribute an unrelated host process to our child PID.
            if pid != os.getpid() and os.readlink(proc / "ns/pid") != os.readlink("/proc/self/ns/pid"):
                return None
            fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
            return {"identity": (pid, int(fields[19])), "cpu_seconds": (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
                    "rss": int(fields[21]) * os.sysconf("SC_PAGE_SIZE"), "private": None}
        except (OSError, ValueError, IndexError): return None


class HostSampler:
    def __init__(self, counters=None, clock=time.monotonic, cpu_count=None):
        self.counters = counters or (WindowsCounters() if sys.platform == "win32" else LinuxCounters())
        self.clock = clock
        self.cpus = cpu_count or os.cpu_count() or 1
        self.previous = None
        self.processes = {}
        self.wall = None

    def sample(self, gui_pid, worker_pid):
        if worker_pid == gui_pid: worker_pid = None
        now = self.clock(); system = self.counters.system()
        result = {"system_cpu_pct": None, "app_cpu_pct": None, "worker_cpu_pct": None,
                  "system_ram_total_mib": system["ram_total"] / MIB, "system_ram_used_mib": system["ram_used"] / MIB,
                  "system_ram_pct": 100 * system["ram_used"] / max(1, system["ram_total"]),
                  "system_commit_used_mib": system["commit_used"] / MIB if system["commit_used"] is not None else None,
                  "system_commit_limit_mib": system["commit_limit"] / MIB if system["commit_limit"] is not None else None}
        if self.previous:
            total = system["ticks"] - self.previous["ticks"]
            idle = system["idle"] - self.previous["idle"]
            if total > 0 and 0 <= idle <= total: result["system_cpu_pct"] = 100 * (total - idle) / total
        current, values, cpus = {}, {}, {}
        for role, pid in (("gui", gui_pid), ("worker", worker_pid)):
            if not pid: continue
            p = self.counters.process(pid)
            if p is None: continue
            values[role] = p; current[p["identity"]] = p["cpu_seconds"]
            old = self.processes.get(p["identity"])
            if old is not None and self.wall is not None and now > self.wall:
                cpus[role] = min(100, max(0, 100 * (p["cpu_seconds"] - old) / (now - self.wall) / self.cpus))
        expected = 2 if worker_pid and worker_pid != gui_pid else 1
        result.update(processes_read=len(values), processes_expected=expected,
                      host_status="ok" if len(values) == expected else "partial")
        if len(cpus) == expected: result["app_cpu_pct"] = min(100, sum(cpus.values()))
        result["worker_cpu_pct"] = cpus.get("worker")
        result["app_working_set_mib"] = sum(p["rss"] for p in values.values()) / MIB if len(values) == expected else None
        result["worker_working_set_mib"] = values["worker"]["rss"] / MIB if "worker" in values else None
        result["app_private_commit_mib"] = sum(p["private"] for p in values.values()) / MIB if len(values) == expected and all(p["private"] is not None for p in values.values()) else None
        self.previous, self.wall, self.processes = system, now, current
        return {k: round(v, 2) if type(v) is float else v for k, v in result.items()}


def number(text):
    try:
        value = float(text.strip())
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, TypeError): return None


class NvidiaSampler:
    def __init__(self, executable=None, runner=subprocess.run):
        self.runner = runner
        if executable is not None:
            self.executable = executable
        elif sys.platform == "win32":
            candidates = [Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/nvidia-smi.exe",
                          Path(os.environ.get("ProgramW6432", r"C:\Program Files")) / "NVIDIA Corporation/NVSMI/nvidia-smi.exe"]
            self.executable = next((str(p) for p in candidates if p.is_file()), None)
        else:
            self.executable = shutil.which("nvidia-smi")

    def query(self, query):
        r = self.runner([self.executable, query, "--format=csv,noheader,nounits"], stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                        timeout=2, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode: raise OSError("GPU query unavailable")
        return list(csv.reader(io.StringIO(r.stdout[:65536]), skipinitialspace=True))

    def sample(self, pids):
        result = {"gpu_status": "tool_missing", "gpus": [], "app_gpu_memory_mib": None, "app_gpu_memory_status": "unavailable"}
        if not self.executable: return result
        try:
            rows = self.query("--query-gpu=index,name,driver_version,memory.total,memory.used,utilization.gpu,temperature.gpu")
            for row in rows:
                if len(row) != 7 or number(row[0]) is None: continue
                result["gpus"].append({"index": int(row[0]), "name": row[1].strip()[:120], "driver": row[2].strip()[:40],
                                       "memory_total_mib": number(row[3]), "memory_used_mib": number(row[4]),
                                       "util_pct": number(row[5]), "temperature_c": number(row[6])})
            result["gpu_status"] = "ok" if result["gpus"] else "unavailable"
        except subprocess.TimeoutExpired:
            result["gpu_status"] = "timeout"; return result
        except (OSError, ValueError):
            result["gpu_status"] = "unavailable"; return result
        try:
            rows = self.query("--query-compute-apps=pid,used_gpu_memory")
            ours = [number(r[1]) for r in rows if len(r) == 2 and r[0].strip().isdigit() and int(r[0]) in pids]
            if ours and all(v is not None for v in ours):
                result.update(app_gpu_memory_mib=sum(ours), app_gpu_memory_status="ok")
            else:
                # WDDM often reports N/A. Absence of a row is not proof of zero use.
                result["app_gpu_memory_status"] = "unsupported_or_not_reported"
        except (OSError, ValueError, subprocess.TimeoutExpired): pass
        return result


class ResourceSampler:
    def __init__(self):
        self.host = HostSampler()
        self.gpu = NvidiaSampler()

    def sample(self, gui_pid, worker_pid):
        try: result = self.host.sample(gui_pid, worker_pid)
        except Exception: result = {"host_status": "unavailable"}
        try:
            result.update(self.gpu.sample({gui_pid, worker_pid}) if worker_pid else
                          {"gpu_status": "idle_not_sampled", "gpus": [], "app_gpu_memory_mib": None, "app_gpu_memory_status": "idle_not_sampled"})
        except Exception:
            result.update(gpu_status="unavailable", gpus=[], app_gpu_memory_mib=None, app_gpu_memory_status="unavailable")
        return result


def environment(version):
    cpu = platform.processor()
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0]
        except OSError: pass
    versions = {}
    for package in ("faster-whisper", "ctranslate2", "av", "onnxruntime", "numpy"):
        try: versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError: versions[package] = "not_installed"
    return {"diagnostic_format": 1, "app_version": version, "python": platform.python_version(),
            "os": platform.system(), "os_release": platform.release(), "os_version": platform.version(),
            "architecture_bits": struct.calcsize("P") * 8, "cpu_name": cpu[:160],
            "logical_cpus": os.cpu_count(), "packages": versions}


BUNDLE_README = """字幕工坊诊断包

把整个 ZIP 发给协助排查的人即可；也可以先解压查看内容。
本包包含本次及最近两次启动的诊断记录，可能包含先前未正常退出的记录。
每个 session 目录：environment.json 是版本/硬件；summary.json 是采样峰值；
events.jsonl 是阶段、回退、错误类别与代码位置；resources.csv 是资源时间序列。
带 .1 的文件是同次启动中较早的轮换记录。

时间使用 UTC，并带时区。CPU 百分比按整机逻辑处理器数归一化（0—100%）。
app 表示字幕工坊界面进程＋当前识别/翻译工作进程，不包括其他应用。
工作集相加可能重复计算共享页；private_commit 表示已提交的私有内存，不等于驻留物理内存。
system 表示整机；GPU 利用率和显存总量也表示整张显卡，可能包含其他应用。
gpus_json 列保存各张 NVIDIA 显卡数据；app_gpu_memory 是驱动可提供时的本程序显存。
Windows WDDM 下本程序显存可能无法读取：空值/null/不可用不表示 0。
requested_device 是用户选择；actual_device 是模型加载后报告的设备。
模型加载在 GPU 上时，音频预处理仍主要在 CPU 上进行；请结合 phase 判断。
运行任务时约每 5 秒采样，空闲时约每 30 秒记录 CPU/内存并暂停 GPU 查询。
峰值是采样到的峰值，可能漏掉两次采样间的瞬时尖峰；异常结束前的最后一条也可能来不及保存。

隐私：不采集视频、字幕、项目、术语表、API 密钥、请求/响应正文、完整文件路径、
用户名、电脑名、环境变量列表、GPU UUID、其他进程名称或命令行。
错误仅保存类别、错误号、异常类型及不含路径/源文本的代码位置；不保存原始异常消息。
诊断包仅保存在你选择的本地位置，不会自动上传。
"""


class Diagnostics:
    def __init__(self, user_dir, version, *, sampler=None, autostart=True, interval=5):
        self.root = Path(user_dir) / "diagnostics"
        self.session = self.root / (datetime.now().strftime("session_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8])
        self.lock = threading.RLock(); self.stop = threading.Event(); self.wake = threading.Event()
        self.started = time.monotonic(); self.closed = False; self.storage_ok = True
        self.interval = interval; self.event_limit = 1024 * 1024; self.resource_limit = 2 * 1024 * 1024
        self.gui_pid = os.getpid(); self.worker_pid = None; self.task_id = 0
        self.context = {"task_id": 0, "phase": "idle", "requested_device": "unconfirmed", "actual_device": "unconfirmed", "progress_pct": 0}
        self.latest = {}; self.recent = deque(maxlen=100)
        self.summary = {"diagnostic_format": 1, "started_utc": utc_now(), "sample_count": 0,
                        "closed_cleanly": False, "peaks": {}, "phase_peaks": {}, "event_counts": {}}
        self.sampler = sampler
        self.thread = None
        try:
            self.session.mkdir(parents=True)
            self._json("environment.json", environment(version))
            self._prune()
        except Exception: self.storage_ok = False
        self.record("session_started")
        if autostart:
            self.thread = threading.Thread(target=self._loop, name="subtitle-diagnostics", daemon=True)
            self.thread.start()

    def _json(self, name, value):
        p = self.session / name; tmp = p.with_suffix(p.suffix + ".writing")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, p)

    def _sessions(self):
        return sorted([p for p in self.root.glob("session_*") if p.is_dir() and not p.is_symlink()
                       and re.fullmatch(r"session_\d{8}_\d{6}_[a-f0-9]{8}", p.name)], key=lambda p: p.name, reverse=True)

    def _prune(self):
        for folder in self._sessions()[5:]:
            try:
                # Skip other instances which are still writing, and unknown folders.
                files = [p for p in folder.iterdir() if p.is_file() and not p.is_symlink()]
                if not files or max(p.stat().st_mtime for p in files) > time.time() - 120: continue
                if json.loads((folder / "environment.json").read_text("utf-8")).get("diagnostic_format") != 1: continue
                shutil.rmtree(folder)
            except (OSError, ValueError): pass

    def _append(self, name, text, limit, header=""):
        path = self.session / name
        if path.exists() and path.stat().st_size + len(text.encode("utf-8")) > limit:
            os.replace(path, path.with_name(path.stem + ".1" + path.suffix))
        new = not path.exists()
        with path.open("a", encoding="utf-8", newline="") as f:
            if new and header: f.write(header)
            f.write(text)
            f.flush()

    def record(self, event, **fields):
        if event not in EVENTS: return
        with self.lock:
            if self.closed: return
            data = {"time_utc": utc_now(), "elapsed_seconds": round(time.monotonic() - self.started, 3),
                    "task_id": self.task_id, "event": event, **safe_fields(fields)}
            if "phase" in data: self.context["phase"] = data["phase"]
            if "actual_device" in data: self.context["actual_device"] = data["actual_device"]
            self.recent.append(data)
            counts = self.summary["event_counts"]; counts[event] = counts.get(event, 0) + 1
            if self.storage_ok:
                try:
                    self._append("events.jsonl", json.dumps(data, ensure_ascii=False) + "\n", self.event_limit)
                    self.summary["last_event"] = data
                    self._json("summary.json", self.summary)
                except OSError: self.storage_ok = False

    def start_task(self, config=None):
        cfg = config or {}
        selected = cfg.get("device", "unconfirmed")
        if selected not in DEVICES: selected = "unconfirmed"
        model = cfg.get("asr_model", "local/custom")
        builtin = model if model in ENUMS["model_choice"] else "local/custom"
        with self.lock:
            self.task_id += 1
            self.context.update(task_id=self.task_id, phase="preparing", requested_device=selected,
                                actual_device="unconfirmed", progress_pct=0)
        fields = {"requested_device": selected, "mode": cfg.get("mode", "gpu_setup"),
                  "model_choice": builtin, "model_source": "builtin" if builtin != "local/custom" else "local/custom",
                  "force": bool(cfg.get("force", False))}
        # Read metadata only; never retain the path, name, glossary or key.
        try:
            source = Path(cfg.get("input", ""))
            if source.is_file(): fields["file_size_bytes"] = source.stat().st_size
            local = Path(str(model)) / "model.bin"
            if local.is_file(): fields["model_binary_bytes"] = local.stat().st_size
        except (OSError, ValueError): pass
        self.record("task_started", **fields)

    def set_worker(self, pid):
        with self.lock: self.worker_pid = pid
        if pid: self.record("worker_started", worker_pid=pid)
        self.wake.set()

    def observe(self, kind, value):
        if kind == "diagnostic" and isinstance(value, dict):
            self.record(value.get("event", "error"), **{k: v for k, v in value.items() if k != "event"})
        elif kind == "phase": self.record("phase", phase=value)
        elif kind == "progress" and finite(value):
            with self.lock: self.context["progress_pct"] = round(min(100, max(0, value)), 2)
        elif kind == "device" and value == "cpu": self.record("cpu_fallback", actual_device="unconfirmed")
        elif kind == "done": self.record("completed", phase="completed", export_count=len(value.get("files", [])))
        elif kind == "cancelled": self.record("cancelled", phase="cancelled", error_category="user_cancel")
        elif kind == "error": self.record("error", phase="error")
        elif kind == "gpu_ready": self.record("gpu_ready", phase="completed")
        # Deliberately ignore raw log/status/project strings and unknown messages.

    def sample_once(self):
        sampled_at = utc_now()
        with self.lock:
            pid = self.worker_pid; context = dict(self.context)
        if self.sampler is None: self.sampler = ResourceSampler()
        values = self.sampler.sample(self.gui_pid, pid)
        row = {"time_utc": sampled_at, "session_elapsed_s": round(time.monotonic() - self.started, 3),
               **context, "worker_pid": pid, **values}
        with self.lock:
            if self.closed: return
            self.latest = row
            self.summary["sample_count"] += 1
            self.summary["last_sample_utc"] = row["time_utc"]
            self.summary["last_context"] = context
            peaks = self.summary["peaks"]
            phase_peaks = self.summary["phase_peaks"].setdefault(context["phase"], {})
            for k in ("system_cpu_pct", "app_cpu_pct", "system_ram_pct", "app_working_set_mib", "app_private_commit_mib"):
                v = row.get(k)
                if finite(v):
                    peaks[k] = max(peaks.get(k, v), v)
                    phase_peaks[k] = max(phase_peaks.get(k, v), v)
            for gpu in row.get("gpus", []):
                for k in ("util_pct", "memory_used_mib", "temperature_c"):
                    v = gpu.get(k); label = f"gpu{gpu['index']}_{k}"
                    if finite(v): peaks[label] = max(peaks.get(label, v), v)
            if self.storage_ok:
                try:
                    out = dict(row); out["gpus_json"] = json.dumps(out.pop("gpus", []), ensure_ascii=False)
                    buffer = io.StringIO(); writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, extrasaction="ignore")
                    writer.writeheader(); header = buffer.getvalue(); buffer.seek(0); buffer.truncate(0)
                    writer.writerow(out)
                    self._append("resources.csv", buffer.getvalue(), self.resource_limit, header)
                    self._json("summary.json", self.summary)
                except OSError: self.storage_ok = False

    def _loop(self):
        while not self.stop.is_set():
            try: self.sample_once()
            except Exception as exc: self.record("monitor_error", **error_info(exc))
            wait = self.interval if self.worker_pid else max(30, self.interval)
            self.wake.wait(wait); self.wake.clear()

    def screen_text(self):
        with self.lock:
            data = dict(self.latest); ctx = dict(self.context); peaks = dict(self.summary["peaks"])
            recent = list(self.recent)[-10:]
        def fmt(v, suffix=""):
            return f"{v:.1f}{suffix}" if finite(v) else "未取得"
        lines = ["日志状态：" + ("正在本机记录；不会自动上传" if self.storage_ok else "日志无法写入磁盘，请检查用户目录空间/权限"),
                 f"当前阶段：{PHASES.get(ctx['phase'], ctx['phase'])}",
                 f"选择设备：{DEVICES[ctx['requested_device']]}　模型实际设备：{DEVICES[ctx['actual_device']]}",
                 "", f"整机 CPU：{fmt(data.get('system_cpu_pct'), '%')}　　字幕工坊 CPU：{fmt(data.get('app_cpu_pct'), '%')}",
                 f"整机内存：{fmt(data.get('system_ram_used_mib'))} / {fmt(data.get('system_ram_total_mib'))} MiB（{fmt(data.get('system_ram_pct'), '%')}）",
                 f"程序工作集合计：{fmt(data.get('app_working_set_mib'))} MiB　　工作进程：{fmt(data.get('worker_working_set_mib'))} MiB",
                 f"程序私有提交内存：{fmt(data.get('app_private_commit_mib'))} MiB",
                 ""]
        if data.get("gpu_status") == "ok":
            for gpu in data.get("gpus", []):
                lines += [f"GPU {gpu['index']}：{gpu['name']}　驱动 {gpu['driver']}",
                          f"整卡 GPU 利用率：{fmt(gpu.get('util_pct'), '%')}　显存：{fmt(gpu.get('memory_used_mib'))} / {fmt(gpu.get('memory_total_mib'))} MiB　温度：{fmt(gpu.get('temperature_c'), '℃')}"]
            lines += [f"本程序显存：{fmt(data.get('app_gpu_memory_mib'))} MiB（WDDM 驱动可能不提供此数值）"]
        else:
            status = {"idle_not_sampled": "空闲，暂停查询", "tool_missing": "未找到驱动附带的监控工具", "timeout": "驱动查询超时", "unavailable": "驱动未提供数据"}.get(data.get("gpu_status"), "等待采样")
            lines += ["GPU 监测：" + status + "；不影响识别。"]
        lines += ["", f"本次采样峰值：程序工作集 {fmt(peaks.get('app_working_set_mib'))} MiB；整机内存 {fmt(peaks.get('system_ram_pct'), '%')}",
                  "说明：整卡数据可能包含其他应用；程序工作集求和可能重复共享页。",
                  "每约 5 秒采样；空闲约 30 秒。未取得的值不表示 0。", "", "最近诊断事件："]
        lines += [f"{r['time_utc']}　{r['event']}　{PHASES.get(r.get('phase'), r.get('error_category', ''))}" for r in recent]
        return "\n".join(lines)

    def export_bundle(self, destination):
        target = Path(destination)
        tmp = target.with_name(target.name + ".writing-" + uuid.uuid4().hex[:8])
        allowed = ("environment.json", "summary.json", "events.1.jsonl", "events.jsonl", "resources.1.csv", "resources.csv")
        try:
            with self.lock:
                if not self.storage_ok: raise OSError("diagnostic storage unavailable")
                self._json("summary.json", self.summary)
                folders = [self.session] + [p for p in self._sessions() if p != self.session][:2]
                with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                    archive.writestr("README.txt", BUNDLE_README.encode("utf-8-sig"))
                    for folder in folders:
                        for name in allowed:
                            p = folder / name
                            if p.is_file() and not p.is_symlink() and p.stat().st_size <= 3 * MIB:
                                archive.write(p, folder.name + "/" + name)
            os.replace(tmp, target)
        finally:
            if tmp.exists(): tmp.unlink()

    def close(self):
        with self.lock:
            if self.closed: return
            self.summary["closed_cleanly"] = True
            self.record("session_closed")
            self.closed = True
        self.stop.set(); self.wake.set()
        if self.thread and self.thread is not threading.current_thread(): self.thread.join(timeout=0.1)
