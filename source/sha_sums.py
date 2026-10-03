"""生成 SHA256SUMS.txt 校验清单。

哈希取自「git 仓库里的字节」（git blob），而不是工作区文件：

Windows 上 core.autocrlf=true 会把工作区文件转成 CRLF，而 git archive 与
GitHub 的 Code ZIP 导出的都是仓库里的 LF 字节。若按工作区计算，下载发布包
的人校验会大面积失败；基于 blob 计算才能与发布包逐字节一致。

同时只统计 git 跟踪的文件，避免把 debug.log 之类的临时文件写进清单
（那会让校验时报“缺失”）。

注意：本脚本自身也在清单里，而哈希取自 HEAD。所以「刚改过本脚本」时，
第一次生成的清单记录的还是上一个已提交版本；提交后再运行一次即稳定，
发布前请以提交后的这一次结果为准。
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_NAMES = {"SHA256SUMS.txt"}


def git(*args):
    result = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        sys.exit("git " + " ".join(args) + " 失败：" + message)
    return result.stdout


def main():
    tracked = [name for name in git("ls-files", "-z").decode("utf-8").split("\0") if name]
    if not tracked:
        sys.exit("git 仓库里没有已跟踪的文件，请在带 git 的检出环境中运行本脚本。")
    lines = []
    for relative in sorted(tracked):
        if Path(relative).name in EXCLUDE_NAMES:
            continue
        blob = git("cat-file", "blob", "HEAD:" + relative)
        lines.append(hashlib.sha256(blob).hexdigest() + "  " + relative)
    (ROOT / "SHA256SUMS.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )
    print("generated %d checksums" % len(lines))


if __name__ == "__main__":
    main()
