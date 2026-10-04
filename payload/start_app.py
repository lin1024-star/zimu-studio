"""Stable desktop entry point; no system Python or shell command is required."""
from pathlib import Path
import ctypes
import multiprocessing
import os
import sys
import traceback

from paths import data_root


def main():
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    lock = None
    try:
        if sys.platform == "win32":
            import msvcrt
            lock = (root / "application.lock").open("a+b")
            lock.seek(0)
            if not lock.read(1):
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                ctypes.windll.user32.MessageBoxW(0, "字幕工坊已经打开，请切换到已有窗口。", "字幕工坊", 64)
                return
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        from app import Application
        window = Application()
        window.mainloop()
    except Exception as exc:
        # Do not include exception values, transcripts, environment or API secrets.
        rows = ["SubtitleStudio startup: " + type(exc).__name__]
        rows += [f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in traceback.extract_tb(exc.__traceback__)]
        (root / "startup-error.txt").write_text("\n".join(rows), encoding="utf-8")
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(0, "软件启动失败（" + type(exc).__name__ + "）。请打开安装器点“一键安装 / 修复”，或点“导出安装诊断”后反馈。", "字幕工坊", 16)
        raise SystemExit(1)
    finally:
        if lock:
            lock.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
