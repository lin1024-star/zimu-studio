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


class BriefTests(unittest.TestCase):
    """短版：学朋友那种「两三行看完就知道干什么」的写法。

    小弹窗用它（打开 / 还原 / 保存 / 启动实时字幕 / 显卡组件）；
    处理任务失败那种大弹窗仍用完整版，因为「我实际看到的是」不能丢。
    """

    def test_brief_is_short(self):
        text = fe.brief(PermissionError(13, "拒绝访问"))
        self.assertLessEqual(len(text.splitlines()), 4, text)
        self.assertLess(len(text), 260, text)

    def test_brief_has_the_three_parts(self):
        text = fe.brief(TimeoutError("x"))
        self.assertIn("没连上", text)          # 一句话：什么事
        self.assertIn("你可以这样办", text)
        self.assertIn("诊断包", text)          # 还不行怎么办

    def test_brief_never_shows_exception_name(self):
        for exc in (TimeoutError("x"), MemoryError(), KeyError("k"), OSError(28, "满")):
            self.assertNotIn(type(exc).__name__, fe.brief(exc))

    def test_brief_passes_through_chinese_errors(self):
        self.assertEqual(fe.brief(UserError("字幕为空，请先识别。")), "字幕为空，请先识别。")

    def test_brief_for_cancelled_is_a_notice_not_an_error(self):
        self.assertIn("已停止", fe.brief(Cancelled("stop")))

    def test_brief_does_not_repeat_the_diagnostics_line(self):
        """实测踩到过：短版尾巴出现两句一模一样的「还不行就导出诊断包」。

        原因是从长清单里截前两条截出了半截话。现在短版有自己的一套短步骤。
        """
        for exc in (RuntimeError("x"), KeyError("k"), TimeoutError("t")):
            text = fe.brief(exc)
            self.assertLessEqual(text.count("导出诊断包"), 1, text)

    def test_brief_has_a_fallback_line_when_steps_dont_mention_diagnostics(self):
        text = fe.brief(TimeoutError("t"))
        self.assertIn("先", text)
        self.assertIn("还不行", text)


class WiringTests(unittest.TestCase):
    """接线测试：真实路径上真的用到这一层了吗（不是只有模块自己测得好）。"""

    def test_gpu_worker_uses_brief(self):
        """显卡组件那条路（gpu_runtime.setup_worker）也要说人话。"""
        import threading
        from types import SimpleNamespace
        from unittest import mock

        import gpu_runtime

        box = []
        with mock.patch.object(gpu_runtime, "prepare_gpu",
                               side_effect=RuntimeError("cuda init failed")):
            gpu_runtime.setup_worker(threading.Event(),
                                     SimpleNamespace(put=lambda item: box.append(item)))
        errors = [v for k, v in box if k == "error"]
        self.assertTrue(errors)
        self.assertIn("显卡", errors[0])
        self.assertNotIn("RuntimeError", errors[0])


def _dialogs_leaking_type_name(source):
    """找出「用户能看到的提示」里还在甩异常类名的调用。

    覆盖两类：messagebox.showerror / MessageBoxW（弹窗），
    以及 emit("error"/"fatal", …)（这串会一路显示到「任务未完成」弹窗里）。
    """
    import ast

    tree = ast.parse(source)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        segment = ast.get_source_segment(source, node) or ""
        if name in ("showerror", "MessageBoxW") and "__name__" in segment:
            bad.append(node.lineno)
        if name == "emit" and node.args and "__name__" in segment:
            first = ast.get_source_segment(source, node.args[0]) or ""
            if "error" in first or "fatal" in first:
                bad.append(node.lineno)
    return bad


class NoRawTypeNameInUiTests(unittest.TestCase):
    """回归护栏：产品代码里不许再有「甩异常类名给人看」的提示。

    2026-10-18 清了一轮（实时字幕启动/导出、显卡组件、启动失败等），
    这条测试守着，免得哪天又写回去。
    """

    def test_product_code_has_no_type_names_in_user_messages(self):
        import ast  # noqa: F401  （给下面的反证用）
        payload = Path(__file__).resolve().parents[1] / "payload"
        problems = []
        for path in sorted(payload.rglob("*.py")):
            if "lib" in path.parts or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text("utf-8")
                for line in _dialogs_leaking_type_name(text):
                    problems.append(f"{path.relative_to(payload)}:{line}")
            except (OSError, UnicodeError, SyntaxError):
                continue
        self.assertEqual(problems, [], "用户能看到的提示里还在甩异常类名：\n  " + "\n  ".join(problems))

    def test_the_scanner_actually_catches_a_planted_leak(self):
        """反证：造一段明知故犯的代码，扫描器必须抓到（否则它会静默永远通过）。"""
        planted = (
            "import messagebox as mb\n"
            "def f(exc):\n"
            "    mb.showerror('失败', f'{type(exc).__name__}: {exc}')\n"
        )
        self.assertTrue(_dialogs_leaking_type_name(planted))
        clean = (
            "import messagebox as mb\n"
            "def f(exc):\n"
            "    mb.showerror('失败', friendly_errors.brief(exc))\n"
        )
        self.assertEqual(_dialogs_leaking_type_name(clean), [])


if __name__ == "__main__":
    unittest.main()
