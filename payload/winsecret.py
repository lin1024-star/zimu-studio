"""Optional, off-by-default local storage for the DeepSeek API key.

Uses Windows DPAPI (CryptProtectData), so the blob can only be decrypted by
the same Windows user on the same machine. The key never goes into
settings.json, project files or diagnostic packages.
"""
from pathlib import Path
import base64
import ctypes
import json
import os

from core import atomic_write

SECRET_FILE = "api_key.dpapi"
DATA_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SubtitleStudio"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.c_void_p)]


_kernel32 = ctypes.windll.kernel32
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p


def _blob(data):
    buf = ctypes.create_string_buffer(data)
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.c_void_p))


def _unblob(blob):
    return ctypes.string_at(blob.pbData, blob.cbData)


def _free(blob):
    _kernel32.LocalFree(blob.pbData)


def protect(text_bytes):
    if os.name != "nt":
        raise OSError("仅 Windows 支持记住密钥。")
    data_in = _blob(text_bytes)
    data_out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(data_in), None, None, None, None, 1, ctypes.byref(data_out)):
        raise OSError(f"系统加密保存失败（错误码 {ctypes.GetLastError()}）。")
    try:
        return _unblob(data_out)
    finally:
        _free(data_out)


def unprotect(blob):
    data_in = _blob(blob)
    data_out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(data_in), None, None, None, None, 1, ctypes.byref(data_out)):
        raise OSError(f"系统解密失败（错误码 {ctypes.GetLastError()}）。")
    try:
        return _unblob(data_out).decode("utf-8")
    finally:
        _free(data_out)


def save_key(api_key):
    payload = json.dumps({"v": 1, "key": api_key}, ensure_ascii=False).encode("utf-8")
    blob = protect(payload)
    atomic_write(DATA_ROOT / SECRET_FILE, base64.b64encode(blob).decode("ascii"))


def load_key():
    path = DATA_ROOT / SECRET_FILE
    if not path.is_file():
        return None
    try:
        payload = unprotect(base64.b64decode(path.read_text("ascii")))
        return json.loads(payload).get("key")
    except Exception:
        # A stale or unreadable blob simply means "not remembered".
        return None


def forget_key():
    try:
        (DATA_ROOT / SECRET_FILE).unlink(missing_ok=True)
    except OSError:
        pass
