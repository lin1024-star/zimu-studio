# -*- coding: utf-8 -*-
"""静态检查：有没有"用了 module.name 但那个 module 根本没绑定"的地方。

**为什么加这个**（2026-10-14 真事）：
app.py 里写了 7 处 `core.xxx`，可是它只有 `from core import (...)` ——
`from core import X` 绑定的是 **X**，**不绑定 `core`** ✗
于是那 7 处全是 NameError：
  · 一处被 try/except 吞掉 → 「中文自检结果存进项目」**从来没存下来** ✗（发出去的 v2.3 就有）
  · 一处让整个「翻译与识别设置」页建不出来 ✗
  · 一处让「实时字幕导出」一点就炸 ✗

这种错的共同点：**语法完全合法、单元测试大多测不到**（只有走到那条分支才炸）。
所以用静态扫描把它们在提交前拦下来。

判定规则（故意保守，只报无歧义的）：
  把整个文件里**任何地方**绑定过的名字都算作"有"（模块级导入、函数内导入、
  参数、局部变量、def/class、赋值……）。
  只有当 `名字.属性` 用到的名字**在任何地方都没绑定过**、且不是内置名/双下划线时，才报 ✗
"""
import ast
import builtins
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAYLOAD = REPO / "payload"
FILES = ("app.py", "dialogs.py", "core.py", "ja_fix.py", "chinese_check.py", "timeline_qc.py",
         "ass.py", "polish.py", "live/app.py", "live/pipeline.py", "live/overlay.py",
         "live/glossary.py", "live/studio.py", "live/capture.py")

BUILTINS = set(dir(builtins))


def bound_names(tree):
    """整个文件里绑定过的所有名字（不分作用域，故意的：只求不误报）。"""
    names = set()

    def add_target(target):
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                add_target(element)
        elif isinstance(target, ast.Starred):
            add_target(target.value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                add_target(target)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            add_target(node.target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            add_target(node.target)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            add_target(node.optional_vars)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                for arg in (list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
                            + ([args.vararg] if args.vararg else [])
                            + ([args.kwarg] if args.kwarg else [])):
                    names.add(arg.arg)
        elif isinstance(node, ast.Lambda):
            args = node.args
            for arg in (list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
                        + ([args.vararg] if args.vararg else [])
                        + ([args.kwarg] if args.kwarg else [])):
                names.add(arg.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.comprehension,)):
            add_target(node.target)
        elif isinstance(node, ast.Global) or isinstance(node, ast.Nonlocal):
            names.update(node.names)
    return names


def undefined_module_refs(path):
    """返回 [(名字, 行号)] —— 用了 `名字.属性` 但名字从没绑定过。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound = bound_names(tree) | BUILTINS
    problems = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)):
            continue
        name = node.value.id
        if name in bound or name.startswith("__"):
            continue
        # 只报"根名字"（a.b.c 里的 a）；同一行重复只报一次
        problems.append((name, node.lineno))
    seen, unique = set(), []
    for name, line in sorted(problems, key=lambda item: item[1]):
        if (name, line) in seen:
            continue
        seen.add((name, line))
        unique.append((name, line))
    return unique


class UndefinedModuleRefTest(unittest.TestCase):

    def test_no_reference_to_unbound_module_names(self):
        problems = []
        for name in FILES:
            path = PAYLOAD / name
            if not path.is_file():
                continue
            for module, line in undefined_module_refs(path):
                problems.append(f"{name}:{line} 用了 `{module}.xxx`，但 `{module}` 从没绑定过")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_scanner_catches_the_known_bug(self):
        """反证：把 2026-10-14 那个真 bug 当样例，扫描器必须抓得到。"""
        source = (
            "from core import save_project\n"
            "def go(project):\n"
            "    core.save_project('x.json', project)\n"
        )
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")
            found = undefined_module_refs(path)
        self.assertEqual(found, [("core", 3)], str(found))

    def test_does_not_flag_correct_imports(self):
        source = (
            "import core\n"
            "import os.path\n"
            "from pathlib import Path\n"
            "def go():\n"
            "    core.save_project('x', {})\n"
            "    os.getcwd()\n"
            "    Path('x').is_file()\n"
            "    import json\n"
            "    json.dumps({})\n"
            "    data = {}\n"
            "    data.items()\n"
            "    for item in []:\n"
            "        item.bit_length()\n"
        )
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")
            self.assertEqual(undefined_module_refs(path), [])


if __name__ == "__main__":
    unittest.main()
