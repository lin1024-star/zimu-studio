"""Installer helpers. No API request, transcript or credential is used here."""
from pathlib import Path
import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import sys
import threading

from paths import data_root

ROOT = data_root()


def initialize_settings(model, gpu=False):
    from core import atomic_write, DEFAULT_MODEL
    path = ROOT / "settings.json"
    if path.exists():
        return  # Preserve existing users' choices and projects.
    output = Path.home() / ("Videos" if (Path.home() / "Videos").exists() else "Documents") / "SubtitleStudio"
    data = {"asr_model": model if model in {"small", "turbo", "tiny"} else "small",
            "device": "NVIDIA GPU（4 GB 省显存）" if gpu else "CPU（直接使用）",
            "local_model": "", "language": "自动识别", "output": str(output),
            "model": DEFAULT_MODEL, "glossary": "", "alert_when_done": True}
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


def probe(kind):
    import struct
    import tkinter
    assert sys.version_info[:2] == (3, 13) and struct.calcsize("P") == 8
    window = tkinter.Tk()
    window.withdraw()
    window.update_idletasks()
    window.destroy()
    if kind == "asr":
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        import onnxruntime
        onnxruntime.disable_telemetry_events()
        import av, ctranslate2, faster_whisper
    print("READY " + kind, flush=True)


def gpu_setup():
    from gpu_runtime import prepare_gpu
    def emit(kind, value):
        if kind in {"status", "log"}:
            print(str(value), flush=True)
    prepare_gpu(threading.Event(), emit)
    print("READY gpu", flush=True)


def diagnose():
    result = {"app_version": "1.6.1", "python": platform.python_version(),
              "os": platform.system() + " " + platform.release(), "machine": platform.machine(),
              "free_disk_gb": round(shutil.disk_usage(ROOT).free / 1e9, 2), "packages": {}}
    for name in ["faster-whisper", "ctranslate2", "av", "onnxruntime", "numpy", "huggingface-hub"]:
        try:
            result["packages"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result["packages"][name] = "not installed"
    for alias in ["small", "turbo"]:
        result[alias + "_prepared"] = (ROOT / "models/prepared" / alias / "ready.json").is_file()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["probe-basic", "probe-asr", "finalize", "gpu", "diagnose"])
    parser.add_argument("--model", choices=["small", "turbo", "srt"], default="small")
    parser.add_argument("--gpu", action="store_true")
    args = parser.parse_args()
    try:
        if args.action.startswith("probe-"): probe(args.action[6:])
        elif args.action == "finalize": initialize_settings(args.model, args.gpu)
        elif args.action == "gpu": gpu_setup()
        elif args.action == "diagnose": diagnose()
    except Exception as exc:
        # Error class and numeric Windows error are sufficient for setup diagnosis.
        print("FAILED " + type(exc).__name__ + " winerror=" + str(getattr(exc, "winerror", "")), flush=True)
        raise SystemExit(2)
