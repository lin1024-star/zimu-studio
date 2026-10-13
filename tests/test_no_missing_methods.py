# -*- coding: utf-8 -*-
"""静态检查：界面代码里有没有"调用了不存在的方法"。

**为什么加这个测试**（2026-10-14 真事）：
`jump_to_cue` 里调了 `self.show_selected()`，而 show_selected 根本没定义 ——
所以「双击跳到那一条」从来没成功过（时间轴体检、待确认、中文自检都受影响）。
这种错：

  · Python 不报（属性查找是运行时的事）
  · 单元测试测不到（要真去双击才会炸）
  · 冒烟测试原来也测不到（它只检查按钮在不在，不点）

结果使用者双击自检结果时才崩出来。所以加一条静态检查，把这类错在提交前就拦住。
"""
import ast
import sys
import tkinter as tk
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAYLOAD = REPO / "payload"
FILES = ("app.py", "dialogs.py", "core.py", "ja_fix.py", "chinese_check.py", "timeline_qc.py")

# tkinter 自己的方法（含 Misc/Toplevel 等），不用我们定义
TK_METHODS = set(dir(tk.Misc)) | set(dir(tk.Widget)) | set(dir(tk.Tk)) | set(dir(tk.Toplevel))
TK_METHODS |= {"tk", "master", "children", "register", "nametowidget", "wait_window",
               "wait_variable", "wait_visibility", "mainloop", "quit", "destroy"}


def assigned_names(class_node):
    """类里 self.xxx = ... 赋过值的属性名（含元组赋值 a, b = ...）。"""
    names = set()

    def collect(target):
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) \
                and target.value.id == "self":
            names.add(target.attr)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                collect(element)

    for node in ast.walk(class_node):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                collect(target)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            collect(node.target)
    return names


def defined_names(class_node):
    names = {n.name for n in class_node.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in class_node.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def scan(path):
    """返回 [(类名, 方法名, 行号)] —— 调用了但没定义、也没赋值的。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    problems = []
    for class_node in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        defined = defined_names(class_node) | assigned_names(class_node)
        for call in ast.walk(class_node):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                continue
            func = call.func
            if not (isinstance(func.value, ast.Name) and func.value.id == "self"):
                continue
            name = func.attr
            if name.startswith("__") or name in defined or name in TK_METHODS:
                continue
            problems.append((class_node.name, name, call.lineno))
    return problems


class NoMissingMethodsTest(unittest.TestCase):

    def test_no_calls_to_missing_methods(self):
        problems = []
        for name in FILES:
            path = PAYLOAD / name
            if path.is_file():
                problems += [(name, *p) for p in scan(path)]
        self.assertEqual(
            problems, [],
            "这些地方调用了不存在的方法（运行到就会 AttributeError）：\n" +
            "\n".join("  %s: %s.%s() 第 %d 行" % p for p in problems))

    def test_the_scanner_actually_catches_the_known_bug(self):
        """反证：拿 2026-10-14 那个真 bug 当样例，扫描器必须抓得到。"""
        source = (
            "import tkinter as tk\n"
            "class Demo(tk.Toplevel):\n"
            "    def jump(self, cue_id):\n"
            "        self.show_selected()\n"
        )
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")
            found = scan(path)
        self.assertEqual(found, [("Demo", "show_selected", 4)], str(found))

    def test_does_not_flag_tkinter_methods(self):
        source = (
            "import tkinter as tk\n"
            "class Demo(tk.Toplevel):\n"
            "    def go(self):\n"
            "        self.destroy()\n"
            "        self.after(200, self.go)\n"
            "        self.geometry('100x100')\n"
            "        self.bell()\n"
            "        self.option_add('*Font', 'x')\n"
        )
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")
            self.assertEqual(scan(path), [])

    def test_does_not_flag_attributes_assigned_as_tuples(self):
        """`self.a, self.b = ...` 这种元组赋值也要认。"""
        source = (
            "import tkinter as tk\n"
            "class Demo(tk.Toplevel):\n"
            "    def __init__(self, emit):\n"
            "        self.emit, self.total = emit, 1\n"
            "    def report(self):\n"
            "        self.emit('x')\n"
        )
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")
            self.assertEqual(scan(path), [])


if __name__ == "__main__":
    unittest.main()
