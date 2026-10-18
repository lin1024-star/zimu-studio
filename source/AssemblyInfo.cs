using System.Reflection;
using System.Runtime.Versioning;
[assembly: AssemblyTitle("字幕工坊 安装与启动")]
[assembly: AssemblyDescription("字幕工坊 · Windows 懒人安装器（MIT 开源，源码见 GitHub）")]
[assembly: AssemblyProduct("字幕工坊")]
[assembly: AssemblyCompany("lin1024-star")]
[assembly: AssemblyCopyright("MIT License · github.com/lin1024-star/zimu-studio")]
[assembly: TargetFramework(".NETFramework,Version=v4.5",FrameworkDisplayName=".NET Framework 4.5")]

// 版本号（AssemblyVersion / AssemblyFileVersion / AssemblyInformationalVersion）**故意不写在这里**。
// 由 source/build.py 从 payload/core.py 的 APP_VERSION 现场生成，
// 避免"发出去是 2.4.1、exe 属性却写 2.0.0.0"再发生。
