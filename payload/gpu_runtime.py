"""Private, resumable Windows NVIDIA runtime setup. No system-wide changes."""
from __future__ import annotations

import ctypes
import hashlib
import http.client
import importlib.metadata
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from core import Cancelled, UserError, check_cancel
from paths import data_root

MANIFEST = json.loads(Path(__file__).with_name("gpu_manifest.json").read_text("utf-8"))
_HANDLES = []


class SetupError(UserError):
    pass


def cache_root():
    return data_root() / "gpu-runtime"


def file_hash(path, stop=None):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(1024 * 1024):
            check_cancel(stop)
            h.update(chunk)
    return h.hexdigest()


def driver_info():
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise SetupError("自动显卡配置适用于 64 位 Windows；当前可以继续使用 CPU。")
    try:
        system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        driver = ctypes.WinDLL(str(system / "nvcuda.dll"))
        driver.cuInit.argtypes = [ctypes.c_uint]
        driver.cuInit.restype = ctypes.c_int
        if driver.cuInit(0) != 0:
            raise SetupError("NVIDIA 驱动未能启动显卡。已保留 CPU 模式；可通过 NVIDIA App 更新显卡驱动后重试。")
        count, version = ctypes.c_int(), ctypes.c_int()
        for name in ("cuDeviceGetCount", "cuDriverGetVersion"):
            fn = getattr(driver, name)
            fn.argtypes = [ctypes.POINTER(ctypes.c_int)]
            fn.restype = ctypes.c_int
        if driver.cuDeviceGetCount(ctypes.byref(count)) != 0 or count.value < 1:
            raise SetupError("没有检测到可用的 NVIDIA 显卡，继续使用 CPU 即可。")
        if driver.cuDriverGetVersion(ctypes.byref(version)) != 0 or version.value < 12000:
            raise SetupError("显卡驱动较旧，暂不能使用此加速组件。可通过 NVIDIA App 更新驱动；CPU 模式仍可使用。")
        name = ctypes.create_string_buffer(256)
        driver.cuDeviceGetName.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        driver.cuDeviceGetName.restype = ctypes.c_int
        driver.cuDeviceGetName(name, len(name), 0)
        total = ctypes.c_size_t()
        driver.cuDeviceTotalMem_v2.argtypes = [ctypes.POINTER(ctypes.c_size_t), ctypes.c_int]
        driver.cuDeviceTotalMem_v2.restype = ctypes.c_int
        driver.cuDeviceTotalMem_v2(ctypes.byref(total), 0)
        return {"name": name.value.decode("utf-8", "replace"), "memory": total.value, "driver_cuda": version.value}
    except SetupError:
        raise
    except (OSError, AttributeError) as exc:
        raise SetupError("未找到可用的 NVIDIA 驱动。CPU 模式仍可使用；请通过 NVIDIA App 检查显卡驱动。") from exc


@contextmanager
def setup_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "setup.lock").open("a+b") as f:
        if f.tell() == 0:
            f.write(b"0")
            f.flush()
        f.seek(0)
        locked = False
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            yield
        except OSError as exc:
            if not locked:
                raise SetupError("另一个字幕工坊正在配置显卡，请等它完成后再试。") from exc
            raise
        finally:
            if locked:
                f.seek(0)
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def download(spec, directory, stop, emit):
    """Only completed, SHA-256 verified archives are returned to callers."""
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / spec["filename"]
    part = final.with_name(final.name + ".part")
    size = spec["size"]
    if final.is_file():
        if final.stat().st_size == size and file_hash(final, stop) == spec["sha256"]:
            return final
        final.unlink()
    for attempt in range(4):
        check_cancel(stop)
        if part.exists() and part.stat().st_size > size:
            part.unlink()
        start = part.stat().st_size if part.exists() else 0
        try:
            if start < size:
                headers = {"User-Agent": "SubtitleStudio/1.1", "Accept-Encoding": "identity"}
                if start:
                    headers["Range"] = f"bytes={start}-"
                request = urllib.request.Request(spec["url"], headers=headers)
                with urllib.request.urlopen(request, timeout=20) as response:
                    code = response.getcode()
                    if code == 206:
                        import re
                        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                        if not match or int(match[1]) != start or int(match[3]) != size:
                            raise OSError("range response mismatch")
                    elif code == 200:
                        start = 0
                    else:
                        raise OSError("unexpected response")
                    done, last = start, 0.0
                    with part.open("ab" if start else "wb") as f:
                        while True:
                            check_cancel(stop)
                            chunk = response.read(256 * 1024)
                            if not chunk:
                                break
                            done += len(chunk)
                            if done > size:
                                raise OSError("response too large")
                            f.write(chunk)
                            if time.monotonic() - last > .25:
                                emit("progress", 95.0 * done / size)
                                emit("status", f"正在下载显卡组件：{done / 1e6:.1f} / {size / 1e6:.1f} MB；可停止，下次继续")
                                last = time.monotonic()
            if part.stat().st_size != size:
                raise OSError("incomplete response")
            emit("status", "下载完成，正在校验显卡组件…")
            if file_hash(part, stop) != spec["sha256"]:
                part.unlink()
                raise OSError("checksum mismatch")
            os.replace(part, final)
            return final
        except Cancelled:
            raise
        except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError) as exc:
            if attempt == 3:
                raise SetupError("显卡组件下载暂未完成，已保留下载进度。稍后再点“启用显卡加速”即可继续；也可先用 CPU。") from exc
            emit("log", f"下载连接中断，正在重试（{attempt + 1}/3）；已下载部分会尽量复用。")
            if stop is not None:
                stop.wait(1 + attempt)
            else:
                time.sleep(1 + attempt)
    raise SetupError("显卡组件下载未完成。")


def extract_component(archive, spec, destination, stop):
    """Extract only declared DLLs and license text; never execute an archive."""
    expected = set(spec["dlls"])
    found = set()
    records = {}
    with zipfile.ZipFile(archive) as z:
        for entry in z.infolist():
            check_cancel(stop)
            name = PurePosixPath(entry.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in entry.filename or ":" in entry.filename:
                raise SetupError("显卡组件压缩包的路径无效，请重新下载。")
            if entry.is_dir():
                continue
            is_dll = name.name in expected and "bin" in name.parts
            is_license = name.name.lower() in ("license", "license.txt", "eula", "eula.txt")
            if not (is_dll or is_license):
                continue
            if is_dll and name.name in found:
                raise SetupError("显卡组件中存在重复的运行库。")
            target = destination / (name.name if is_dll else spec["name"] + "_" + name.name)
            h = hashlib.sha256()
            with z.open(entry) as src, target.open("wb") as dst:
                while chunk := src.read(1024 * 1024):
                    check_cancel(stop)
                    h.update(chunk)
                    dst.write(chunk)
            if is_dll:
                found.add(name.name)
                records[name.name] = {"size": target.stat().st_size, "sha256": h.hexdigest()}
    if found != expected:
        raise SetupError("显卡组件不完整，请重新下载。")
    return records


def ready(root=None, stop=None):
    root = root or cache_root()
    target = root / MANIFEST["runtime_id"]
    try:
        data = json.loads((target / "installed.json").read_text("utf-8"))
        if data["runtime_id"] != MANIFEST["runtime_id"]:
            return False
        for spec in MANIFEST["components"]:
            for dll in spec["dlls"]:
                record = MANIFEST.get("dll_hashes", {}).get(dll) or data["files"][dll]
                path = target / dll
                if path.stat().st_size != record["size"] or file_hash(path, stop) != record["sha256"]:
                    return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def ensure_runtime(stop, emit, root=None):
    root = root or cache_root()
    target = root / MANIFEST["runtime_id"]
    if ready(root, stop):
        return target
    with setup_lock(root):
        if ready(root, stop):
            return target
        if shutil.disk_usage(root).free < 2_500_000_000:
            raise SetupError("系统盘剩余空间不足，首次配置显卡建议留出至少 2.5 GB 空间。CPU 模式仍可使用。")
        stage = Path(tempfile.mkdtemp(prefix="unpacking-", dir=root))
        try:
            records = {}
            for i, spec in enumerate(MANIFEST["components"], 1):
                emit("log", f"准备显卡组件 {i}/{len(MANIFEST['components'])}；来源：NVIDIA 官方下载站。")
                archive = download(spec, root / "downloads", stop, emit)
                emit("status", f"正在解压显卡组件 {i}/{len(MANIFEST['components'])}…")
                records.update(extract_component(archive, spec, stage, stop))
            for dll, expected in MANIFEST.get("dll_hashes", {}).items():
                if records.get(dll) != expected:
                    raise SetupError("解压后的显卡组件校验不一致，请稍后重试。")
            (stage / "installed.json").write_text(json.dumps({"runtime_id": MANIFEST["runtime_id"], "files": records}), "utf-8")
            check_cancel(stop)
            if target.exists():
                # Keep an old/corrupt install instead of deleting a directory in use.
                backup = root / (MANIFEST["runtime_id"] + ".old-" + str(time.time_ns()))
                target.rename(backup)
            stage.rename(target)
            return target
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)


def ensure_engine(stop, emit, root=None):
    try:
        if importlib.metadata.version("ctranslate2") == MANIFEST["ctranslate2_version"]:
            return
    except importlib.metadata.PackageNotFoundError:
        pass
    if "ctranslate2" in sys.modules:
        raise SetupError("请关闭字幕工坊后重开，再配置显卡组件。")
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    spec = MANIFEST["wheels"].get(tag)
    if not spec:
        raise SetupError("当前 Python 版本暂无对应加速组件，请先继续使用 CPU。")
    emit("log", "正在更新本软件的识别引擎；不会更改系统 Python。")
    root = root or cache_root()
    wheel = download(spec, root / "downloads", stop, emit)
    with tempfile.TemporaryFile() as output:
        proc = subprocess.Popen([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--disable-pip-version-check", str(wheel)],
                                stdout=output, stderr=output, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            started = time.monotonic()
            while proc.poll() is None:
                check_cancel(stop)
                if time.monotonic() - started > 180:
                    raise SetupError("识别引擎更新超时，请关闭其他字幕工坊窗口后重试。")
                if stop is not None:
                    stop.wait(.2)
                else:
                    time.sleep(.2)
            if proc.returncode:
                raise SetupError("识别引擎更新未完成，请关闭其他字幕工坊窗口后重试。")
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()


def activate(path):
    path = Path(path).resolve()
    if hasattr(os, "add_dll_directory"):
        _HANDLES.append(os.add_dll_directory(str(path)))
    os.environ["PATH"] = str(path) + os.pathsep + os.environ.get("PATH", "")
    # DLL lookup changes are confined to this worker process.
    for name in ("cudart64_12.dll", "cublasLt64_12.dll", "cublas64_12.dll"):
        _HANDLES.append(ctypes.WinDLL(str(path / name)))


def prepare_gpu(stop, emit):
    check_cancel(stop)
    info = driver_info()
    emit("log", f"检测到 {info['name']}，专用显存约 {info['memory'] / 1024**3:.1f} GB。")
    ensure_engine(stop, emit)
    path = ensure_runtime(stop, emit)
    check_cancel(stop)
    try:
        activate(path)
        import ctranslate2
        if "int8_float16" not in ctranslate2.get_supported_compute_types("cuda", 0):
            raise SetupError("当前显卡暂不支持此省显存模式，将继续使用 CPU。")
    except (OSError, RuntimeError) as exc:
        raise SetupError("显卡运行库未能初始化。CPU 模式仍可使用；如果 NVIDIA 驱动较旧，请通过 NVIDIA App 更新驱动。") from exc
    return info


def setup_worker(stop, messages):
    from diagnostics import error_info
    emit = lambda kind, value: messages.put((kind, value))
    try:
        info = prepare_gpu(stop, emit)
        # Force real CUDA/cuBLAS initialization without loading a user model.
        blas = _HANDLES[-1]
        handle = ctypes.c_void_p()
        blas.cublasCreate_v2.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        blas.cublasCreate_v2.restype = ctypes.c_int
        if blas.cublasCreate_v2(ctypes.byref(handle)) != 0:
            raise SetupError("显卡计算初始化失败，可能是显存被其他程序占用。可先继续用 CPU。")
        try:
            check_cancel(stop)
        finally:
            blas.cublasDestroy_v2.argtypes = [ctypes.c_void_p]
            blas.cublasDestroy_v2.restype = ctypes.c_int
            blas.cublasDestroy_v2(handle)
        emit("progress", 100)
        emit("gpu_ready", info)
    except Cancelled as exc:
        emit("cancelled", "显卡组件配置已停止；下载进度已保留，稍后点“启用显卡加速”可继续。")
    except (SetupError, UserError) as exc:
        emit("diagnostic", {"event": "error", **error_info(exc, gpu=True)})
        emit("error", str(exc))
    except Exception as exc:
        emit("diagnostic", {"event": "error", **error_info(exc, gpu=True)})
        emit("error", f"显卡配置尚未完成（{type(exc).__name__}）。可先使用 CPU，稍后再点“启用显卡加速”重试。")
