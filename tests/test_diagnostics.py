import csv
import ctypes
import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import diagnostics as d
import core


class FixedSampler:
    def sample(self, gui_pid, worker_pid):
        return {"system_cpu_pct": 91.2, "app_cpu_pct": 76.1, "worker_cpu_pct": 75,
                "system_ram_total_mib": 16384, "system_ram_used_mib": 15000, "system_ram_pct": 91.55,
                "app_working_set_mib": 7000, "worker_working_set_mib": 6900, "app_private_commit_mib": 8000,
                "host_status": "ok", "processes_read": 2, "processes_expected": 2,
                "gpu_status": "ok", "app_gpu_memory_mib": None, "app_gpu_memory_status": "unsupported_or_not_reported",
                "gpus": [{"index": 0, "name": "NVIDIA GeForce RTX 3050 Laptop GPU", "driver": "test-driver",
                          "memory_total_mib": 4096, "memory_used_mib": 1024, "util_pct": 15, "temperature_c": 54}]}


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.diag = d.Diagnostics(self.root, "1.3.0", sampler=FixedSampler(), autostart=False)

    def tearDown(self):
        self.diag.close(); self.tmp.cleanup()

    def test_export_privacy_canaries_and_device_transition(self):
        key = "sk-CANARY-PRIVATE-API-111111111111"
        caption = "CANARY-PRIVATE-CAPTION-字幕正文"
        source = self.root / "CANARY-PRIVATE-FILENAME.mp4"; source.write_bytes(b"x" * 321)
        cfg = {"input": str(source), "api_key": key, "glossary": caption, "asr_model": str(self.root / "PRIVATE-MODEL"),
               "device": "cuda", "mode": "transcribe", "cues": [caption]}
        self.diag.start_task(cfg)
        self.diag.set_worker(456)
        self.diag.observe("diagnostic", {"event": "model_loaded", "actual_device": "cuda", "compute_type": "int8_float16",
                                          "api_key": key, "source": caption, "config": cfg})
        self.diag.observe("phase", "audio_preprocessing")
        self.diag.observe("log", key + caption + str(source))
        self.diag.observe("status", caption)
        self.diag.observe("project", str(source))
        self.diag.sample_once()
        try:
            raise RuntimeError("CUDA out of memory " + key + caption + str(source))
        except RuntimeError as exc:
            info = d.error_info(exc, gpu=True)
            self.diag.observe("diagnostic", {"event": "cpu_fallback", **info})
        self.diag.observe("device", "cpu")
        self.assertEqual(self.diag.context["actual_device"], "unconfirmed")
        self.diag.observe("diagnostic", {"event": "model_loaded", "actual_device": "cpu", "compute_type": "int8"})
        self.diag.observe("phase", "inference")
        self.diag.sample_once()
        target = self.root / "diagnostic.zip"; self.diag.export_bundle(target)
        with zipfile.ZipFile(target) as z:
            self.assertIsNone(z.testzip())
            text = "\n".join(z.read(n).decode("utf-8-sig") for n in z.namelist())
            for secret in [key, caption, str(source), "CANARY-PRIVATE-FILENAME", "PRIVATE-MODEL", str(self.root)]:
                self.assertNotIn(secret, text)
            self.assertIn('"error_category": "cuda_out_of_memory"', text)
            self.assertIn('"exception_type": "RuntimeError"', text)
            resources = next(n for n in z.namelist() if n.endswith("resources.csv"))
            rows = list(csv.DictReader(io.StringIO(z.read(resources).decode())))
            self.assertEqual([r["actual_device"] for r in rows], ["cuda", "cpu"])
            self.assertEqual([r["requested_device"] for r in rows], ["cuda", "cuda"])
            self.assertEqual(rows[0]["app_gpu_memory_mib"], "")
        self.assertIn("未取得", self.diag.screen_text())

    def test_rotation_keeps_valid_csv_header_and_latest_records(self):
        self.diag.resource_limit = 2000; self.diag.event_limit = 800
        for _ in range(20):
            self.diag.sample_once(); self.diag.record("phase", phase="inference")
        self.assertTrue((self.diag.session / "resources.1.csv").exists())
        self.assertTrue((self.diag.session / "events.1.jsonl").exists())
        self.assertFalse((self.diag.session / "resources.2.csv").exists())
        for name in ("resources.csv", "resources.1.csv"):
            with (self.diag.session / name).open(newline="") as f:
                reader = csv.DictReader(f)
                self.assertEqual(reader.fieldnames, d.CSV_FIELDS)
                self.assertTrue(list(reader))
        self.assertEqual(self.diag.summary["sample_count"], 20)

    def test_reopen_exports_prior_unclean_session(self):
        self.diag.record("phase", phase="audio_preprocessing"); self.diag.sample_once()
        # Simulate a crash by not calling close on the previous instance.
        next_run = d.Diagnostics(self.root, "1.3.0", sampler=FixedSampler(), autostart=False)
        try:
            target = self.root / "reopened.zip"; next_run.export_bundle(target)
            with zipfile.ZipFile(target) as z:
                old = json.loads(z.read(self.diag.session.name + "/summary.json"))
                self.assertFalse(old["closed_cleanly"])
                self.assertEqual(old["last_context"]["phase"], "audio_preprocessing")
                self.assertIn(next_run.session.name + "/environment.json", z.namelist())
        finally: next_run.close()

    def test_no_disk_space_does_not_stop_resource_sampling(self):
        with patch.object(self.diag, "_append", side_effect=OSError("disk full")):
            self.diag.sample_once()
        self.assertFalse(self.diag.storage_ok)
        self.diag.sample_once()
        self.assertEqual(self.diag.latest["system_cpu_pct"], 91.2)
        with self.assertRaises(OSError): self.diag.export_bundle(self.root / "bad.zip")
        self.assertFalse(list(self.root.glob("*.writing-*")))

    def test_package_contains_only_diagnostic_files(self):
        (self.diag.session / "project.json").write_text("private")
        (self.diag.session / "secret.txt").write_text("private")
        target = self.root / "safe.zip"; self.diag.export_bundle(target)
        with zipfile.ZipFile(target) as z:
            self.assertFalse(any(n.endswith(("project.json", "secret.txt")) for n in z.namelist()))

    def test_worker_exception_diagnostic_never_echoes_request_or_source(self):
        events = queue.Queue()
        with patch("core.run_job", side_effect=RuntimeError("private caption private-api-key")):
            core.worker({"device": "cuda"}, threading.Event(), events)
        rows = []
        while not events.empty(): rows.append(events.get_nowait())
        text = json.dumps(rows)
        self.assertNotIn("private caption", text); self.assertNotIn("private-api-key", text)
        self.assertEqual(rows[0][0], "diagnostic")
        self.assertIn("exception_type", rows[0][1])

    def test_asr_reports_engine_and_phase_without_changing_options(self):
        calls, events = [], []
        class Model:
            def __init__(self, *args, **kwargs):
                self.model = SimpleNamespace(device="cuda", compute_type="int8_float16")
                calls.append(kwargs)
            def transcribe(self, *args, **kwargs):
                calls.append(kwargs)
                return iter([SimpleNamespace(start=0, end=2, text="Hello world.", words=[])]), SimpleNamespace(language="en", duration=2)
        modules = {"faster_whisper": SimpleNamespace(WhisperModel=Model),
                   "onnxruntime": SimpleNamespace(disable_telemetry_events=lambda: None)}
        with patch.dict(sys.modules, modules):
            cues, language = core._transcribe_once(self.root / "fixture.mp4", "turbo", "en", "cuda", self.root,
                                                   threading.Event(), lambda k, v: events.append((k, v)))
        self.assertEqual([v for k, v in events if k == "phase"], ["model_loading", "audio_preprocessing", "inference"])
        engine = next(v for k, v in events if k == "diagnostic" and v["event"] == "model_loaded")
        self.assertEqual(engine["actual_device"], "cuda")
        self.assertEqual(engine["compute_type"], "int8_float16")
        self.assertEqual(calls[1]["beam_size"], 5)
        self.assertTrue(calls[1]["word_timestamps"] and calls[1]["vad_filter"])
        self.assertEqual(cues[0].source, "Hello world.")


class SamplerTests(unittest.TestCase):
    def test_cpu_delta_normalization_and_process_identity(self):
        class Counters:
            step = 0
            def system(self):
                return {"ticks": 100 + self.step * 200, "idle": 20 + self.step * 40,
                        "ram_total": 16*d.MIB, "ram_used": 12*d.MIB, "commit_used": 8*d.MIB, "commit_limit": 32*d.MIB}
            def process(self, pid):
                return {"identity": (pid, 1000 if self.step < 2 else 2000),
                        "cpu_seconds": (1 + self.step) if pid == 10 else (2 + self.step*5), "rss": d.MIB, "private": 2*d.MIB}
        counters = Counters(); clock = [1.0]
        sample = d.HostSampler(counters, clock=lambda: clock[0], cpu_count=4)
        self.assertIsNone(sample.sample(10, 20)["app_cpu_pct"])
        counters.step = 1; clock[0] = 3.0
        row = sample.sample(10, 20)
        self.assertEqual(row["system_cpu_pct"], 80.0)
        self.assertEqual(row["app_cpu_pct"], 75.0)
        self.assertEqual(row["worker_cpu_pct"], 62.5)
        self.assertEqual(row["app_working_set_mib"], 2)
        counters.step = 2; clock[0] = 5.0
        self.assertIsNone(sample.sample(10, 20)["app_cpu_pct"], "PID reuse must reset CPU deltas")

    def test_gpu_csv_wddm_na_and_our_pids_only(self):
        replies = ["0, NVIDIA GeForce RTX 3050 Laptop GPU, 572.83, 4096, 1024, 15, 54\n",
                   "100, [N/A]\n999, 3000\n"]
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs)); return SimpleNamespace(returncode=0, stdout=replies.pop(0))
        sample = d.NvidiaSampler(executable="trusted-nvidia-smi", runner=run).sample({100, 200})
        self.assertEqual(sample["gpus"][0]["memory_used_mib"], 1024)
        self.assertEqual(sample["gpus"][0]["util_pct"], 15)
        self.assertIsNone(sample["app_gpu_memory_mib"])
        self.assertEqual(sample["app_gpu_memory_status"], "unsupported_or_not_reported")
        self.assertEqual(calls[0][1]["timeout"], 2)
        self.assertNotIn("shell", calls[0][1])
        replies[:] = ["0, GPU, 572.83, 4096, 1200, 50, 60\n", "100, 350\n200, 400\n999, 4000\n"]
        self.assertEqual(d.NvidiaSampler(executable="trusted", runner=run).sample({100, 200})["app_gpu_memory_mib"], 750)

    def test_gpu_timeout_and_missing_tool_are_not_zero(self):
        sample = d.NvidiaSampler(executable="missing", runner=lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("nvidia-smi", 2)))
        row = sample.sample({1})
        self.assertEqual(row["gpu_status"], "timeout")
        self.assertIsNone(row["app_gpu_memory_mib"])
        sample.executable = None
        self.assertEqual(sample.sample({1})["gpu_status"], "tool_missing")

    def test_windows_structure_layout_and_64bit_handle(self):
        self.assertEqual(ctypes.sizeof(d.FileTime), 8)
        self.assertEqual(ctypes.sizeof(d.MemoryStatus), 64)
        self.assertEqual(ctypes.sizeof(d.ProcessMemory), 8 + 9 * ctypes.sizeof(ctypes.c_size_t))
        class Fn:
            def __init__(self, f): self.f = f
            def __call__(self, *a): return self.f(*a)
        closed = []
        def gettimes(handle, created, exited, kernel, user):
            ctypes.cast(created, ctypes.POINTER(d.FileTime)).contents.high = 1
            ctypes.cast(kernel, ctypes.POINTER(d.FileTime)).contents.low = 20_000_000
            ctypes.cast(user, ctypes.POINTER(d.FileTime)).contents.low = 30_000_000
            return 1
        def getmem(handle, mem, size):
            p = ctypes.cast(mem, ctypes.POINTER(d.ProcessMemory)).contents
            p.rss = 123 * d.MIB; p.private = 234 * d.MIB
            return 1
        high_handle = 0x100000001
        kernel = SimpleNamespace(GetSystemTimes=Fn(lambda *a: 1), GlobalMemoryStatusEx=Fn(lambda *a: 1),
                                 OpenProcess=Fn(lambda *a: high_handle), CloseHandle=Fn(lambda h: closed.append(h) or 1), GetProcessTimes=Fn(gettimes))
        psapi = SimpleNamespace(GetProcessMemoryInfo=Fn(getmem))
        with patch.object(ctypes, "WinDLL", side_effect=lambda name, **kwargs: kernel if name == "kernel32" else psapi, create=True):
            api = d.WindowsCounters(); p = api.process(22)
        self.assertEqual(p["rss"], 123 * d.MIB); self.assertEqual(p["private"], 234 * d.MIB)
        self.assertEqual(p["cpu_seconds"], 5); self.assertEqual(p["identity"], (22, 1 << 32))
        self.assertEqual(closed, [high_handle])
        self.assertEqual(kernel.OpenProcess.restype, ctypes.c_void_p)

    @unittest.skipUnless(sys.platform.startswith("linux"), "build-host sampler")
    def test_real_host_and_child_memory_sampling(self):
        script = "import os,json,sys,time; sys.path.insert(0,sys.argv[1]); import diagnostics as d; x=bytearray(32*1024*1024); print(json.dumps(d.HostSampler().sample(os.getpid(),None)),flush=True); time.sleep(5)"
        child = subprocess.Popen([sys.executable, "-u", "-c", script, str(Path(d.__file__).parent)], stdout=subprocess.PIPE, text=True)
        try:
            child_self = json.loads(child.stdout.readline())
            self.assertEqual(child_self["host_status"], "ok")
            self.assertGreater(child_self["app_working_set_mib"], 30)
            sampler = d.HostSampler()
            sampler.sample(os.getpid(), child.pid)
            for _ in range(100000): pass
            row = sampler.sample(os.getpid(), child.pid)
            if row["host_status"] == "ok":
                self.assertGreater(row["worker_working_set_mib"], 30)
                self.assertGreaterEqual(row["app_working_set_mib"], row["worker_working_set_mib"])
            else:
                self.assertEqual(row["processes_read"], 1)
                self.assertIsNone(row["worker_working_set_mib"], "Different-namespace PID must not report another process")
            if row["system_cpu_pct"] is not None: self.assertTrue(0 <= row["system_cpu_pct"] <= 100)
        finally:
            child.terminate(); child.wait(timeout=3); child.stdout.close()


if __name__ == "__main__": unittest.main(verbosity=2)
