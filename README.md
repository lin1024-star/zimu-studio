# 字幕工坊 SubtitleStudio（Windows 桌面懒人包）

本地语音识别 + DeepSeek 翻译 + 字幕校对打轴的一键安装工具。面向 Windows 10/11 64 位电脑，为不懂编程的朋友设计：双击安装、一键修复、断点续传、常见问题可搜索。

## 它能做什么

- **本地识别**：用 faster-whisper（small / Turbo 模型）把视频语音转成原文字幕，全程在本机运行，不需要 API 密钥
- **翻译**：调用 DeepSeek 官方 API 翻译成简体中文，密钥由用户自己提供、按官方账户计费，本软件不保存密钥到磁盘
- **字幕编辑**：导入 / 校对 / 添加字幕 / 打轴，支持六份导出（PR 中使用 SRT）

## 第一次安装

1. 下载 Release 中的 ZIP，右键「全部解压缩」
2. 双击 `字幕工坊_安装与启动.exe`
3. 保持推荐的 small + CPU 设置，点「一键安装 / 修复」
4. 首次需联网下载约 0.61 GB，等待「安装完成」
5. 以后双击桌面上的「字幕工坊」快捷方式

详细说明见包内 `先读我_快速开始.txt` 和 `小白指南.html`（含 110 条可搜索 FAQ）。

## 目录结构

```
payload/    Python 主程序（Tk 界面、识别、翻译、导出）
source/     C# 安装器源码（含内嵌 manifest.json 与 payload.zip 的构建工程）
tests/      安装器与核心逻辑测试（Python 75 项 + C# 20 项）
*.txt/html  许可证、第三方声明、验证记录、快速开始与 FAQ
```

## 安全与隐私设计

- 所有下载 HTTPS-only，SHA-256 逐文件锁定，断点续传带 Range 校验
- pip 安装使用 `--no-index --require-hashes --only-binary`
- 解压带路径穿越 / 符号链接防护；日志与诊断自动脱敏
- 禁用 HuggingFace / ONNX 遥测；唯一外部接口为 `api.deepseek.com`
- API 密钥默认不落盘；诊断包不包含密钥、视频或字幕

## 构建与验证

构建安装器见 `source/BUILD.md`；发布包校验清单见 `SHA256SUMS.txt`；验证范围与测试输出见 `VALIDATION.txt`。

## 许可

本项目代码与文档采用 MIT 协议（`LICENSE.txt`）。Python 运行时、第三方 wheel 与语音模型在安装时从各自官方渠道下载，保留其自身许可，详见 `THIRD_PARTY.txt`。
