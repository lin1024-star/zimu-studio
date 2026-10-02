"""Build with .NET Framework 4.5 reference assemblies; no dependency downloads."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--compiler', help='Path to csc.exe or mcs.exe')
parser.add_argument('--refs', required=True, help='Microsoft .NET Framework 4.5 reference-assembly directory')
parser.add_argument('--mono', help='Mono executable for running mcs on Linux')
parser.add_argument('--out', default=str(ROOT / '字幕工坊_安装与启动.exe'))
a = parser.parse_args()
compiler = a.compiler or shutil.which('csc')
if not compiler:
    parser.error('Specify --compiler for the C# compiler.')
refs = Path(a.refs)
assemblies = ['mscorlib', 'System', 'System.Core', 'System.Drawing', 'System.Windows.Forms', 'System.IO.Compression', 'System.IO.Compression.FileSystem']
for name in assemblies:
    if not (refs / (name + '.dll')).is_file():
        parser.error('Missing official reference assembly: ' + name)
with tempfile.TemporaryDirectory(prefix='subtitle-build-') as td:
    bundle = Path(td) / 'payload.zip'
    with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted((ROOT / 'payload').rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
                z.write(p, p.relative_to(ROOT / 'payload').as_posix())
    command = ([a.mono] if a.mono else []) + [compiler, '-noconfig', '-nostdlib', '-platform:x64', '-codepage:utf8', '-optimize+', '-target:winexe', '-win32manifest:' + str(ROOT / 'source/app.manifest'), '-resource:' + str(bundle) + ',payload.zip', '-resource:' + str(ROOT / 'source/manifest.json') + ',manifest.json', '-out:' + a.out]
    command += ['-r:' + str(refs / (name + '.dll')) for name in assemblies]
    command += [str(ROOT / 'source' / name) for name in ['AssemblyInfo.cs', 'Json.cs', 'InstallerCore.cs', 'Launcher.cs']]
    subprocess.run(command, check=True)
print('Built:', a.out)
