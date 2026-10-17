# -*- coding: utf-8 -*-
"""报错翻译层（friendly_errors）的测试。

为什么有这层：有些失败路径原来甩的是「处理失败（TimeoutError）」，把
网络、权限、磁盘三种完全不同的原因塞进一句话，等于没说。
这里把每一类的原因都钉死：给的是**下一步能照着点的**话，而不是异常名。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import friendly_errors as fe  # noqa: E402
from core import Cancelled, ResponseError, UserError  # noqa: E402


class ClassifyTests(unittest.TestCase):
    def test_network_timeout(self):
        text = fe.explain(TimeoutError("timed out"))
        self.assertIn("没连上", text)
        self.assertIn("代理", text)

    def test_connection_reset(self):
        self.assertIn("没连上", fe.explain(ConnectionResetError(10054, "reset")))

    def test_permission(self):
        text = fe.explain(PermissionError(13, "拒绝访问"))
        self.assertIn("权限", text)
        self.assertIn("D 盘", text)

    def test_disk_full(self):
        exc = OSError(28, "No space left on device")
        text = fe.explain(exc)
        self.assertIn("磁盘满", text)
        self.assertIn("2 GB", text)

    def test_file_busy(self):
        exc = PermissionError(13, "另一个程序正在使用此文件")
        exc.winerror = 32
        self.assertIn("占用", fe.explain(exc))

    def test_disk_full_windows(self):
        exc = OSError(0, "磁盘空间不足")
        exc.winerror = 112
        self.assertIn("磁盘满", fe.explain(exc))

    def test_file_not_found(self):
        text = fe.explain(FileNotFoundError(2, "No such file", "D:\\素材\\a.mp4"))
        self.assertIn("文件不在了", text)
        self.assertIn("a.mp4", text)      # 涉及的文件要说出来

    def test_memory(self):
        self.assertIn("内存不够", fe.explain(MemoryError()))

    def test_encoding(self):
        exc = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        self.assertIn("编码", fe.explain(exc))

    def test_http_status_comes_from_message(self):
        self.assertIn("余额", fe.explain(RuntimeError("HTTP 402 payment required")))

    def test_api_key(self):
        self.assertIn("密钥", fe.explain(RuntimeError("error 401 unauthorized")))

    def test_rate_limit(self):
        self.assertIn("请求太密", fe.explain(RuntimeError("429 Too Many Requests")))

    def test_server_error(self):
        self.assertIn("服务器", fe.explain(RuntimeError("503 Service Unavailable")))

    def test_gpu(self):
        self.assertIn("显卡", fe.explain(RuntimeError("CUDA out of memory")))

    def test_media(self):
        self.assertIn("解不开", fe.explain(RuntimeError("ffmpeg error: moov atom not found")))

    def test_internal_error_says_export_diagnostics(self):
        text = fe.explain(KeyError("glossary"))
        self.assertIn("诊断包", text)
        self.assertIn("不是你操作的问题", text)


class UserFacingErrorTests(unittest.TestCase):
    def test_user_error_is_passed_through(self):
        text = fe.explain(UserError("第 3 条字幕结束时间必须晚于开始时间。"))
        self.assertEqual(text, "第 3 条字幕结束时间必须晚于开始时间。")

    def test_response_error_is_passed_through(self):
        text = fe.explain(ResponseError("DeepSeek 未返回有效的 JSON 响应，请稍后继续。"))
        self.assertIn("JSON", text)
        self.assertNotIn("你可以这样办", text)      # 已经是人话，不再包一层

    def test_cancelled_is_not_an_error(self):
        text = fe.explain(Cancelled("stop"))
        self.assertIn("已停止", text)
        self.assertNotIn("出错了", text)


class FormatTests(unittest.TestCase):
    def test_explain_follows_errfmt_shape(self):
        text = fe.explain(PermissionError(13, "拒绝访问"), context="导出字幕时")
        self.assertIn("我实际看到的是", text)
        self.assertIn("你可以这样办", text)
        self.assertIn("导出字幕时", text)
        self.assertIn("把上面这一整段发给开发者", text)

    def test_headline_has_no_exception_class_name(self):
        """标题不许出现英文异常名 —— 那是给开发者看的，不是给使用者的。"""
        for exc in (TimeoutError("x"), MemoryError(), KeyError("k"),
                    PermissionError(13, "拒绝访问"), FileNotFoundError(2, "no", "a")):
            headline = fe.explain(exc).splitlines()[0]
            self.assertNotIn(type(exc).__name__, headline, headline)

    def test_technical_detail_is_kept_but_redacted(self):
        exc = RuntimeError("request failed with sk-abcdefgh12345678 in header")
        text = fe.explain(exc)
        self.assertIn("已隐去", text)
        self.assertNotIn("sk-abcdefgh", text)

    def test_output_is_not_too_long(self):
        for exc in (TimeoutError("x"), MemoryError(), KeyError("k")):
            self.assertLess(len(fe.explain(exc)), 900, type(exc).__name__)

    def test_include_message_false_hides_exception_text(self):
        """后台任务那条路：异常原文可能夹着请求内容/字幕原文，不许带出去。"""
        exc = RuntimeError("private caption private-api-key")
        text = fe.explain(exc, context="处理任务时", include_message=False)
        self.assertNotIn("private caption", text)
        self.assertIn("处理任务时", text)
        self.assertIn("你可以这样办", text)

    def test_hint_is_one_line(self):
        for exc in (TimeoutError("x"), MemoryError(), UserError("字幕为空")):
            hint = fe.hint(exc)
            self.assertNotIn("\n", hint)
            self.assertLessEqual(len(hint), 40, hint)

    def test_hint_accepts_plain_text(self):
        self.assertEqual(fe.hint("抓声音失败：没有默认设备"), "抓声音失败：没有默认设备")


if __name__ == "__main__":
    unittest.main()
