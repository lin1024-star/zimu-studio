# -*- coding: utf-8 -*-
"""发布出去的产品代码里，不许出现「某个盘上的绝对路径」。

为什么加这一条（2026-10-16 真出过）：
`live/` 最早是当独立脚本写的，于是有几处写死了开发机的安装路径
（`find_root()` 的兜底候选、`load_key()` 的默认值），还有一行 `sys.path.insert`
指向开发时的代码仓库目录。对别人来说那些路径根本不存在，
而且等于把自己的机器布局留在了公开代码里 —— 跟「产品外壳是日语形状」是同一类问题。

扫描范围只到 `payload/` 与 `source/`（真正发出去的那部分）。
**不扫 `tests/`**：测试里本来就要造假路径（`D:/models/mine` 这种）来验行为，扫了全是噪音。

放行系统默认位置（`C:\\Windows`、`C:\\Program Files`）—— 那不是谁的私人路径，
是「没取到环境变量时」的通用兜底。

⚠ 模式里**不能出现任何真实路径**，否则这个测试文件自己就把路径带回公开仓库了。
"""
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 盘符 + 冒号 + 斜杠，且**前面不是字母数字** —— 这样 https:// 里的 `s:` 不算。
# 允许中间有空格，好让 `C:\Program Files` 整段被抓出来判放行。
DRIVE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\r\n\"')\]},;|]*")
# 「某个用户的目录」：不点名具体用户，只抓这个通用形状
USER_DIR = re.compile(r"Users[\\/][A-Za-z0-9_.\-]+")
# 系统默认位置，不是私人路径
ALLOWED_PREFIX = ("C:\\Windows", "C:/Windows", "C:\\Program Files", "C:/Program Files")


def scan(text):
    """返回这一段文字里「可疑的写死路径」。"""
    found = []
    for match in DRIVE.finditer(text):
        token = match.group(0)
        if token.startswith(ALLOWED_PREFIX):
            continue
        found.append(token)
    found.extend(match.group(0) for match in USER_DIR.finditer(text))
    return found


class NoLocalPathsTest(unittest.TestCase):

    def test_shipped_code_has_no_absolute_paths(self):
        problems = []
        for area, suffixes in (("payload", (".py",)), ("source", (".py", ".cs"))):
            for path in sorted((REPO / area).rglob("*")):
                if not path.is_file() or path.suffix not in suffixes:
                    continue
                # payload/lib 是第三方库（cffi / pycparser / soundcard），不归我们管
                if "__pycache__" in path.parts or "lib" in path.parts:
                    continue
                try:
                    text = path.read_text("utf-8")
                except (OSError, UnicodeError):
                    continue
                for token in scan(text):
                    problems.append(f"{path.relative_to(REPO)}：{token}")
        self.assertEqual(problems, [],
                         "发布出去的代码里出现了写死的本机路径：\n  " + "\n  ".join(problems))

    def test_the_scanner_actually_catches_a_planted_path(self):
        """反证：造一条假路径，扫描器必须抓到。

        没有这一条的话，哪天正则写坏了，上面那条测试会静默变成"永远通过" ✗
        （造的是假盘符假目录，不是任何真实路径。）
        """
        planted = 'ROOT = Path(r"' + "Q:" + chr(92) + "sample" + chr(92) + 'here")'
        self.assertTrue(scan(planted), "扫描器没抓到明显写死的盘符路径")

    def test_the_scanner_ignores_urls_and_system_paths(self):
        """放行两类：URL（`https://` 里的 s: 不是盘符）与系统默认位置。"""
        self.assertEqual(scan('url = "https://example.com/a/b"'), [])
        self.assertEqual(scan('root = os.environ.get("SystemRoot", r"C:' + chr(92) + 'Windows")'), [])

    def test_the_scanner_catches_a_user_directory(self):
        """反证第二条：`<盘>:\\Users\\<某个人>` 这种也要抓到。"""
        planted = "C:" + chr(92) + "Users" + chr(92) + "someone" + chr(92) + "Desktop"
        self.assertTrue(scan(planted), "扫描器没抓到用户目录")


if __name__ == "__main__":
    unittest.main()
