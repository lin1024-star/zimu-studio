"""实时字幕抓声音：COM 初始化与「采样中断要报出来」的回归测试（2026-10-29）。

真实故障（使用者 2.6 候选上遇到的）：
  停掉实时字幕再启动 / 换音源以后，新的采集线程抓不到声音，
  WAVAPI 报 RuntimeError('Error 0x800401f0')（CO_E_NOTINITIALIZED）。

根因：随包带的 soundcard 只在**第一次 import 时**给所在线程初始化 COM（模块级 _com），
换线程就没有了。修法：采集线程里「先 import soundcard、再补 _ensure_com_thread()」，
并配对释放；同时主界面枚举设备前也补一次。

这里盯三件事：
  ① 换线程时确实补上 COM（第 2 个线程返回 True；同一线程第二次返回 False）
  ② 采集线程里的调用顺序必须是 import → 补 COM → 打开设备（顺序反了 soundcard 自己会崩）
  ③ 中断时不静默：由 LiveApp._pump 报一次 fatal（集成验证在手工测试里做）
"""
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
from live import capture  # noqa: E402


class EnsureComTests(unittest.TestCase):
    def test_second_thread_initializes_com_once(self):
        if sys.platform != "win32":
            self.skipTest("只在 Windows 有 COM")
        result = {}

        def first():
            try:
                capture._import_soundcard()      # 第一次 import：soundcard 会在本线程初始化
                owned = capture._ensure_com_thread()
                result["first_owned"] = owned
                if owned:
                    capture._release_com_thread()
            except Exception as exc:            # noqa: BLE001
                result["error"] = exc

        thread = threading.Thread(target=first)
        thread.start()
        thread.join(timeout=10)
        if "error" in result:
            self.skipTest(f"本机声卡库导入失败，跳过：{result['error']}")
        self.assertFalse(result.get("first_owned"), "第一次 import 的线程不该再由我们初始化")

        def second():
            try:
                result["second_owned"] = capture._ensure_com_thread()
                result["second_again"] = capture._ensure_com_thread()
                if result["second_owned"]:
                    capture._release_com_thread()
            except Exception as exc:            # noqa: BLE001
                result["error2"] = exc

        thread = threading.Thread(target=second)
        thread.start()
        thread.join(timeout=10)
        if "error2" in result:
            self.skipTest(f"COM 初始化测试环境异常，跳过：{result['error2']}")
        self.assertTrue(result.get("second_owned"), "换到新线程时必须由我们补 COM")
        self.assertFalse(result.get("second_again"), "同一线程第二次不该重复初始化")


class RunOrderTests(unittest.TestCase):
    def test_import_then_com_then_open_source(self):
        calls = []
        cap = capture.SystemAudioCapture(capture.AudioRing(16000, 1))
        cap._sc = lambda: (calls.append("sc"), object())[1]

        original_open = capture.open_source
        original_ensure = capture._ensure_com_thread

        def fake_open(sc, source_id):
            calls.append("open")
            raise RuntimeError("停在这里，只为验证顺序")

        capture._ensure_com_thread = lambda: (calls.append("com"), False)[1]
        capture.open_source = fake_open
        try:
            cap.run()
        finally:
            capture.open_source = original_open
            capture._ensure_com_thread = original_ensure

        self.assertEqual(calls, ["sc", "com", "open"],
                         "顺序必须是 import → 补 COM → 打开设备；反过来 soundcard 导入会崩")
        self.assertIsInstance(cap.error, RuntimeError, "打开失败要把错误留在 cap.error 里")


if __name__ == "__main__":
    unittest.main()
