# -*- coding: utf-8 -*-
"""构建版本信息的测试：exe 属性里的版本必须永远等于软件本体版本。

## 为什么加这个（2026-10-18 真事）

`source/AssemblyInfo.cs` 里从 2.0 之后一直写死 `AssemblyVersion("2.0.0.0")`，
所以发出去的是 2.4.1，用户右键看 exe 属性却写着 2.0.0.0。
这既是《发版约定》"版本号必须等于发出去的代码"的反面，也踩了 360 软件检测标准第 15 条
（所有文件要有版本信息、产品名称、内部名称、描述信息，且与软件性质一致）。

修法：构建时从 payload/core.py 的 APP_VERSION 现读、生成版本属性与 manifest。
这里把"生成规则"和"源文件不许再写死版本"两条都钉住。
"""
import importlib.util
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "payload"))


def _load_build_module():
    spec = importlib.util.spec_from_file_location("zimu_build", REPO / "source" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # build.py 的 main() 有 __main__ 保护，可以安全导入
    return module


build = _load_build_module()
ASSEMBLY_INFO = (REPO / "source" / "AssemblyInfo.cs").read_text(encoding="utf-8")
MANIFEST = (REPO / "source" / "app.manifest").read_text(encoding="utf-8")


class VersionSourceTests(unittest.TestCase):
    def test_app_version_matches_core_py(self):
        text = (REPO / "payload" / "core.py").read_text(encoding="utf-8")
        expected = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.M).group(1)
        self.assertEqual(build.app_version(), expected)
        self.assertRegex(expected, r"^\d+(\.\d+){1,3}$", "版本号得像 2.4.1 这样")

    def test_assembly_version_pads_to_four_parts(self):
        self.assertEqual(build.assembly_version("2.4.1"), "2.4.1.0")
        self.assertEqual(build.assembly_version("2.5"), "2.5.0.0")
        self.assertEqual(build.assembly_version("2.4.1.3"), "2.4.1.3")

    def test_assembly_version_drops_extra_parts(self):
        self.assertEqual(build.assembly_version("2.4.1.3.9"), "2.4.1.3")

    def test_assembly_version_rejects_non_numbers(self):
        with self.assertRaises(ValueError):
            build.assembly_version("2.4.beta")

    def test_generated_source_carries_all_three_versions(self):
        source = build.version_source("2.4.1")
        self.assertIn('AssemblyVersion("2.4.1.0")', source)
        self.assertIn('AssemblyFileVersion("2.4.1.0")', source)
        self.assertIn('AssemblyInformationalVersion("2.4.1")', source)

    def test_manifest_version_is_replaced(self):
        updated = build.manifest_with_version(MANIFEST, "2.4.1")
        self.assertIn('version="2.4.1.0"', updated)
        self.assertNotIn('version="2.0.0.0"', updated)
        # 其他内容不能被动到
        self.assertIn("processorArchitecture=\"amd64\"", updated)
        self.assertIn("dpiAware", updated)

    def test_manifest_without_identity_version_fails_loudly(self):
        with self.assertRaises(SystemExit):
            build.manifest_with_version("<assembly></assembly>", "2.4.1")


class SourceFileGuards(unittest.TestCase):
    """源文件里不许再出现写死的版本号 —— 那是这次要根治的问题。"""

    def test_assembly_info_has_no_hardcoded_versions(self):
        for bad in ('AssemblyVersion("', 'AssemblyFileVersion("', 'AssemblyInformationalVersion("'):
            self.assertNotIn(bad, ASSEMBLY_INFO,
                             "版本属性只许由 source/build.py 生成，不许写死在 AssemblyInfo.cs")

    def test_assembly_info_carries_the_fields_360_asks_for(self):
        """360 检测标准第 15 条：版本信息、产品名称、内部名称、描述信息。"""
        for attr in ("AssemblyTitle", "AssemblyProduct", "AssemblyDescription", "AssemblyCompany"):
            self.assertIn(attr, ASSEMBLY_INFO, f"AssemblyInfo.cs 缺 {attr}")
        for empty in ('AssemblyTitle("")', 'AssemblyProduct("")', 'AssemblyCompany("")'):
            self.assertNotIn(empty, ASSEMBLY_INFO)

    def test_build_script_really_generates_versions(self):
        source = (REPO / "source" / "build.py").read_text(encoding="utf-8")
        self.assertIn("version_source(", source)
        self.assertIn("manifest_with_version(", source)
        # 生成的版本文件必须真的传给编译器
        self.assertIn("VersionInfo.g.cs", source)


if __name__ == "__main__":
    unittest.main()
