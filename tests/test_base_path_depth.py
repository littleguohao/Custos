# -*- coding: utf-8 -*-
"""Guard against BASE path depth regressions in 07_tools subdirectory scripts."""
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "07_tools"


class BasePathDepthTests(unittest.TestCase):
    """Every script in 07_tools/<subdir>/ must resolve BASE to the project root."""

    def test_subdir_scripts_resolve_base_to_project_root(self):
        project_root = TOOLS.parent
        markers = {"00_governance", "01_data", "07_tools"}
        broken = []
        for p in sorted(TOOLS.rglob("*.py")):
            if p.name in ("__init__.py", "conftest.py", "paths.py"):
                continue
            if p.parent == TOOLS:
                continue  # 07_tools/*.py — parent.parent is correct
            text = p.read_text(encoding="utf-8")
            if "parent.parent" in text and "parents[2]" not in text:
                # Check if it's actually a BASE definition
                if "BASE" in text and "parent.parent" in text:
                    broken.append(f"{p.relative_to(TOOLS)}: uses parent.parent (should be parents[2])")
        self.assertEqual(broken, [], f"Scripts with wrong BASE depth:\n" + "\n".join(broken))

    def test_project_root_has_expected_markers(self):
        root = TOOLS.parent
        for marker in ["00_governance", "01_data", "07_tools", "tests"]:
            self.assertTrue((root / marker).exists(), f"Missing project marker: {marker}/")


if __name__ == "__main__":
    unittest.main()


class TestGovernanceLayout:
    """00_governance 按生命周期分四类（2026-08-06 重构）——防回归。

    ⚠️ 分开的理由是**改动风险不对等**：`contracts/` 下是代码直接依赖的运行时配置
    （`CN_TRADING_CALENDAR.json` 有 7 处引用，改错四个时点全挂），而 `strategy/`
    下是人与 LLM 读的规则。此前 6 个配置 JSON 与 22 份文档平铺在一起，无法区分。
    """

    def test_four_subdirs_exist(self):
        import paths
        for d in (paths.STRATEGY_DIR, paths.DATA_DOCS_DIR,
                  paths.RESEARCH_DIR, paths.CONTRACTS_DIR):
            assert d.is_dir(), f"{d} 不存在"

    def test_no_loose_files_at_governance_root(self):
        """治理根目录下不应再有平铺文件——新增文档必须落到四个子目录之一。"""
        import paths
        loose = [p.name for p in paths.GOVERNANCE.iterdir()
                 if p.is_file() and not p.name.startswith(".")]
        assert not loose, f"这些文件没有归类: {loose}"

    def test_config_paths_resolve(self):
        """每个配置常量都必须指向真实存在的文件——搬目录最容易漏的就是这些。"""
        import paths
        for name in ("CALENDAR_FILE", "SCREEN_FORMULA_REGISTRY_FILE",
                     "RSS_SOURCE_REGISTRY_FILE", "RSS_FILTER_CONFIG_FILE",
                     "RSSHUB_ROUTES_FILE", "CZ_SECTOR_PREFERENCE_FILE"):
            p = getattr(paths, name)
            assert p.is_file(), f"paths.{name} 指向不存在的文件: {p}"

    def test_modules_do_not_rebuild_governance_paths(self):
        """模块不得自己拼 `"00_governance"` 字符串——必须走 paths 常量。

        搬这次目录时正是靠 grep 这个字符串才找齐引用点；一旦有人绕过 paths.py，
        下次搬动就会漏。paths.py 自身与测试的 tmp 目录构造除外。
        """
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        offenders = []
        for p in (root / "07_tools").rglob("*.py"):
            if p.name == "paths.py":
                continue
            for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if '"00_governance"' in ln or "'00_governance'" in ln:
                    offenders.append(f"{p.relative_to(root)}:{i}")
        assert not offenders, ("这些地方绕过了 paths.py 自己拼治理路径: "
                               + ", ".join(offenders))
