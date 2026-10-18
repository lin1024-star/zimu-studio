"""Build with .NET Framework 4.5 reference assemblies; no dependency downloads.

## 版本号不再手写（2026-10-18 修）

以前 `source/AssemblyInfo.cs` 里写死 `AssemblyVersion("2.0.0.0")`，
结果**发出去的是 2.4.1，exe 右键属性里一直写 2.0.0.0**（从 2.0 之后再没改过）。
这既是《发版约定》里"版本号必须等于发出去的代码"的反面，也踩了 360 软件检测标准第 15 条
（"软件所有文件需要有版本信息、产品名称、内部名称、描述信息，并保证内容与软件性质一致"）。

现在改成：构建时从 `payload/core.py` 的 `APP_VERSION` 现读，生成
- `AssemblyVersion` / `AssemblyFileVersion` / `AssemblyInformationalVersion`
- win32 manifest 里 `assemblyIdentity` 的版本

所以「exe 属性里显示的版本」永远等于「软件自己报的版本」，不可能再漂。
"""
import argparse
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_VERSION_RE = re.compile(r'^APP_VERSION\s*=\s*"([^"]+)"', re.M)
MANIFEST_VERSION_RE = re.compile(r'(<assemblyIdentity\s+version=")[^"]+(")')


def app_version(root=ROOT):
    """读软件本体的版本号（唯一来源：payload/core.py）。"""
    text = (root / "payload" / "core.py").read_text(encoding="utf-8")
    match = APP_VERSION_RE.search(text)
    if not match:
        raise SystemExit("payload/core.py 里找不到 APP_VERSION，无法生成版本信息")
    return match.group(1)


def assembly_version(version):
    """2.4.1 → 2.4.1.0；不足四段补 0，超过四段丢掉多余部分（.NET 只认四段数字）。"""
    parts = [p for p in str(version).strip().split(".") if p != ""]
    if not parts or any(not p.isdigit() for p in parts):
        raise ValueError(f"版本号不像数字：{version!r}")
    parts = (parts + ["0", "0", "0"])[:4]
    return ".".join(parts)


def version_source(version):
    """生成只包含版本属性的 C# 文件内容（构建时临时写盘，不进仓库）。"""
    numbers = assembly_version(version)
    return (
        "using System.Reflection;\n"
        f'[assembly: AssemblyVersion("{numbers}")]\n'
        f'[assembly: AssemblyFileVersion("{numbers}")]\n'
        f'[assembly: AssemblyInformationalVersion("{version}")]\n'
    )


def manifest_with_version(manifest_text, version):
    """把 app.manifest 里 assemblyIdentity 的版本换成当前版本（模板文件里仍是旧值）。"""
    numbers = assembly_version(version)
    updated, count = MANIFEST_VERSION_RE.subn(
        lambda m: m.group(1) + numbers + m.group(2), manifest_text, count=1)
    if count != 1:
        raise SystemExit("source/app.manifest 里找不到 assemblyIdentity version 属性")
    return updated


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--compiler', help='Path to csc.exe or mcs.exe')
    parser.add_argument('--refs', required=True,
                        help='Microsoft .NET Framework 4.5 reference-assembly directory')
    parser.add_argument('--mono', help='Mono executable for running mcs on Linux')
    parser.add_argument('--out', default=str(ROOT / '字幕工坊_安装与启动.exe'))
    a = parser.parse_args(argv)

    compiler = a.compiler or shutil.which('csc')
    if not compiler:
        parser.error('Specify --compiler for the C# compiler.')
    refs = Path(a.refs)
    assemblies = ['mscorlib', 'System', 'System.Core', 'System.Drawing', 'System.Windows.Forms',
                  'System.Management', 'System.IO.Compression', 'System.IO.Compression.FileSystem']
    for name in assemblies:
        if not (refs / (name + '.dll')).is_file():
            parser.error('Missing official reference assembly: ' + name)

    version = app_version()
    print(f'版本（取自 payload/core.py）：{version} → exe 版本 {assembly_version(version)}')

    with tempfile.TemporaryDirectory(prefix='subtitle-build-') as td:
        bundle = Path(td) / 'payload.zip'
        with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for p in sorted((ROOT / 'payload').rglob('*')):
                if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
                    z.write(p, p.relative_to(ROOT / 'payload').as_posix())

        # 版本属性 + manifest 都是**构建时生成**的临时文件，仓库里的源文件不再写死版本
        version_cs = Path(td) / 'VersionInfo.g.cs'
        version_cs.write_text(version_source(version), encoding='utf-8')
        manifest_src = (ROOT / 'source' / 'app.manifest').read_text(encoding='utf-8')
        manifest = Path(td) / 'app.manifest'
        manifest.write_text(manifest_with_version(manifest_src, version), encoding='utf-8')

        command = ([a.mono] if a.mono else []) + [
            compiler, '-noconfig', '-nostdlib', '-platform:x64', '-codepage:65001',
            '-optimize+', '-target:winexe', '-win32manifest:' + str(manifest),
            '-resource:' + str(bundle) + ',payload.zip',
            '-resource:' + str(ROOT / 'source/manifest.json') + ',manifest.json',
            '-out:' + a.out]
        command += ['-r:' + str(refs / (name + '.dll')) for name in assemblies]
        command += [str(ROOT / 'source' / name)
                    for name in ['AssemblyInfo.cs', 'Json.cs', 'InstallerCore.cs', 'Launcher.cs']]
        command.append(str(version_cs))
        subprocess.run(command, check=True)

    print('Built:', a.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
