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


class TestNoPatchingDefaultArgConstants:
    """**不要 monkeypatch「被用作函数默认参数」的路径常量** —— patch 不会生效。

    Python 的默认参数在 **`def` 执行时**求值：

        def list_bundles(root=DEFAULT_Q_ROOT):   # ← 这里就绑定成一个具体 Path 了
            ...

    之后 `monkeypatch.setattr(s_data, "DEFAULT_Q_ROOT", tmp)` 对它**毫无影响**。
    2026-08-06 实测踩到：`gap_report` 调 `list_bundles()` 不传 root，
    测试怎么 patch 都读的是 `E:\\S_DATA` —— 而且**症状是"测试绿或行为不符"**，
    不是报错，属于最难查的一类。

    这份检查动态算出「哪些常量被用作默认参数」，再扫测试里有没有 patch 它们。
    正确做法是**把值做成显式参数**（`gap_report(sample, root=None)`，
    `None` 时才回落到模块默认 ⇒ 默认值在**调用时**才解析）。

    与「连接永不重连」（跨两天犯三次）同理：文档挡不住重犯，要靠可执行检查。
    见 `00_governance/data/DATA_SOURCE_PRINCIPLE.md`「模块级常量 + 运行时替换 = 陷阱」。
    """

    def _default_arg_constants(self, root):
        """AST 扫 07_tools：``{模块名: {被当作函数默认参数用的模块级常量}}``。

        ⚠️ **必须按模块分开**。第一版把常量名跨模块收成一个集合，于是
        `amv_state.LEDGER`（在**函数体内**读、patch 完全有效）被
        `fetch_market_cap.LEDGER`（确实是默认参数）连坐误报 6 处。
        同名常量在不同模块里的用法可以完全不同。
        """
        import ast
        out: dict[str, set[str]] = {}
        for p in (root / "07_tools").rglob("*.py"):
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            got: set[str] = set()
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                a = fn.args
                defaults = list(a.defaults) + [d for d in a.kw_defaults if d is not None]
                for d in defaults:
                    for n in ast.walk(d):
                        # 只关心全大写的模块级常量（路径/配置），忽略字面量与局部名
                        if isinstance(n, ast.Name) and n.id.isupper() and len(n.id) > 3:
                            got.add(n.id)
            if got:
                out[p.stem] = got
        return out

    def test_no_test_patches_a_default_arg_constant(self):
        import ast
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        risky = self._default_arg_constants(root)
        assert risky, "没扫到任何默认参数常量，检查本身可能失效了"
        offenders = []
        for p in sorted((root / "tests").glob("*.py")):
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            alias = {}                       # 别名 → 真实模块名
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    for al in n.names:
                        if al.asname:
                            alias[al.asname] = al.name.split(".")[-1]
                elif isinstance(n, ast.ImportFrom):
                    for al in n.names:
                        if al.asname:
                            alias[al.asname] = al.name
            for n in ast.walk(tree):
                if not isinstance(n, ast.Call):
                    continue
                f = n.func
                if not (isinstance(f, ast.Attribute) and f.attr == "setattr"):
                    continue
                if not (isinstance(f.value, ast.Name) and f.value.id == "monkeypatch"):
                    continue
                if len(n.args) < 2 or not isinstance(n.args[1], ast.Constant):
                    continue
                const = n.args[1].value
                if not isinstance(const, str):
                    continue
                # 第一个实参通常就是被 patch 的模块对象；用它的名字对上模块文件名
                tgt = n.args[0]
                mod = tgt.id if isinstance(tgt, ast.Name) else (
                    tgt.attr if isinstance(tgt, ast.Attribute) else None)
                # ⚠️ 解析导入别名：`import reconcile_positions as rp` 之后
                # patch 的目标写作 `rp`，而 risky 是按**文件名**建索引的。
                # 2026-08-06 实测漏报一次（reconcile_positions 的 LEDGER 默认参数），
                # 就是因为第一版没做这一步 —— 检查器只认字面名字。
                mod = alias.get(mod, mod)
                if mod and const in risky.get(mod, set()):
                    offenders.append(
                        f"{p.name}:{n.lineno} patch 了 {mod}.{const}"
                        f"（它在该模块里被用作函数默认参数 ⇒ patch 不生效）")
        assert not offenders, (
            "这些 patch 不会生效——默认参数在 def 时已求值。"
            "把值改成显式参数（None 时回落默认）：\n  " + "\n  ".join(offenders))

    def test_check_itself_detects_a_planted_case(self, tmp_path):
        """检查函数自己要有测试——写 test_tdx_connection_hygiene 时的教训：
        第一版字符串匹配被一个残留常量骗过、反向验证全绿。"""
        (tmp_path / "07_tools").mkdir()
        (tmp_path / "07_tools" / "m.py").write_text(
            "SOME_ROOT = 1\ndef f(root=SOME_ROOT):\n    return root\n", encoding="utf-8")
        got = self._default_arg_constants(tmp_path)
        assert got.get("m") == {"SOME_ROOT"}, got
        # 短名/小写不该被收进来（避免误报）
        (tmp_path / "07_tools" / "n.py").write_text(
            "AB = 1\nlower = 2\ndef g(a=AB, b=lower):\n    return a, b\n", encoding="utf-8")
        got2 = self._default_arg_constants(tmp_path)
        assert got2.get("n") is None, got2      # 短名/小写都不该被收进来
