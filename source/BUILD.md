# 字幕工坊 2.0 构建说明

普通使用请返回上一级，打开「字幕工坊_安装与启动.exe」。无需阅读本文件或运行源码。

本包是新的完整安装入口，保留 1.5 的 SRT 导入、项目继续、添加字幕、时长设置、资源诊断与六份导出。GUI 使用 Windows Forms；应用使用官方 Python 3.13.15 x64 完整运行 ZIP（带 Tcl/Tk 与 pip），不使用缺少 tkinter 的 embeddable ZIP。安装只写当前用户目录；需要补齐 VC++ 时调用微软官方运行库安装器。

## 编译

1. 使用 C# 5 或以上编译器，目标 .NET Framework 4.5、x64、Windows GUI。
2. 参考库必须是 Microsoft.NETFramework.ReferenceAssemblies.net45 1.0.3 或微软对应参考程序集；不要使用新版 Mono 实现库替代参考库。Windows 10/11 的 .NET Framework 4.x 提供运行支持。
3. 可用 Python 3 运行：

```text
python source/build.py --compiler PATH_TO_CSC_EXE --refs PATH_TO_NET45_REFERENCE_ASSEMBLIES
```

Linux 构建时增加 `--mono PATH_TO_MONO`，`--compiler` 指向 mcs.exe。编译器、参考程序集和大型依赖不随包提供。build.py 以参数数组启动编译器，不通过 shell 拼接命令。

脚本将 payload 下的完整程序（排除 pycache）压缩并作为 payload.zip 资源嵌入 EXE，同时嵌入 source/manifest.json。修改程序或指南后必须重新编译。FAQ 主数据位于 make_guide.py，运行它会同时更新两个 HTML、副本 TXT 与 faq-data.json。

## 人声分离模型

`payload/models/Kim_Vocal_2.onnx`（约 64 MB）随包分发，运行时不联网下载。它来自 Ultimate Vocal Remover (UVR) 项目，署名与来源见 `THIRD_PARTY.txt`。

该文件被 `.gitignore` 的 `*.onnx` 规则排除在版本库之外：从全新克隆构建时它不存在，做出来的安装包不含模型。这种情况下程序不会静默降级——勾选「先分离人声再识别」会明确提示「安装包里没有找到人声分离模型」。`InstallerTests` 会检查 `payload.zip` 中是否存在该条目，缺模型时构建验证直接失败，避免把残缺的包发出去。

## 验证

Python 应用的无付费 API 回归检查：

```text
python -m unittest discover -s tests -p "test_*.py" -v
```

测试使用隔离的临时目录和模拟 API 响应，不读取用户真实密钥，不调用付费接口。

安装器测试：将 source/Json.cs、source/InstallerCore.cs 与 tests/InstallerTests.cs 编译为控制台程序；引用 mscorlib、System、System.Core、System.IO.Compression、System.IO.Compression.FileSystem 的 net45 参考 DLL，并嵌入相同 manifest.json/payload.zip。运行无参数时检查下载状态机与解压等逻辑；可传入官方 python-3.13.15-amd64.zip 的路径，增加真实运行包解压检查。符号链接相关检查只在 Linux 测试环境执行。

tests/CompatibilityAudit.cs 对编译产物做 String.Split/Trim 家族的 IL 调用签名检查，防止旧版 Mono 与微软 Framework 的重载差异重现。该检查只覆盖这类已知签名风险，不代替 Windows 兼容性实测。

## 下载与安装目录

- 主程序：%LOCALAPPDATA%\SubtitleStudio\Easy\app-<版本>（安装根目录可用注册表 HKCU\Software\SubtitleStudio 的 Root 覆盖）
- 独立 Python：同目录上级的 python-3.13.15
- 安装缓存：%LOCALAPPDATA%\SubtitleStudio\Easy\cache
- 已准备模型：%LOCALAPPDATA%\SubtitleStudio\models\prepared\small 或 turbo
- 旧设置与项目保留。项目输出目录由用户在主程序选择。

manifest.json 固定每个二进制文件的 URL、大小和 SHA-256。模型 URL 固定 Hugging Face commit；只有完整校验所有模型文件后才写 ready.json。C# 下载使用 Windows 的正常 HTTPS 证书验证；不安装额外根证书，不关闭证书校验，不执行远程脚本。已经下载但未完成的文件以 .part 保存。

repair 开始修改运行文件时撤销完成标记，全部成功后才重新写入，避免失败修复被认为已经完成。已打开的新启动器应用会阻止修复；旧版直接运行 app.py 的进程不使用这个锁，修复前仍应关闭旧程序。

启动使用 pythonw.exe -X utf8 -E -s start_app.py；不能给该脚本使用 -I，否则脚本所在目录可能不在模块导入路径。pip 使用独立模式和本地 wheel 缓存，以及哈希锁定。

本版没有代码签名；不包含真实 API 凭据、个人字幕、视频或系统环境转储。未完成的 Windows/GPU 实测范围见 VALIDATION.txt。
