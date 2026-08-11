# -*- coding: utf-8 -*-
"""Guard against BASE path depth regressions in 07_tools subdirectory scripts."""
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "07_tools"


class BasePathDepthTests(unittest.TestCase):
    """Every script in 07_tools/<subdir>/ must resolve BASE to the project root."""

    def test_subdir_scripts_resolve_base_to_project_root(self):
        project_root = TOOLS.parent
        markers = {"governance", "data", "07_tools"}
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
        for marker in ["governance", "data", "07_tools", "tests"]:
            self.assertTrue((root / marker).exists(), f"Missing project marker: {marker}/")


if __name__ == "__main__":
    unittest.main()


class TestGovernanceLayout:
    """governance 按生命周期分四类（2026-08-06 重构）——防回归。

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
        """模块不得自己拼 `"governance"` 字符串——必须走 paths 常量。

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
                if '"governance"' in ln or "'governance'" in ln:
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
    见 `governance/data/DATA_SOURCE_PRINCIPLE.md`「模块级常量 + 运行时替换 = 陷阱」。
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


class SubprocessTargetTests(unittest.TestCase):
    """⚠️ 编排层用 `CONST / "script.py"` 拼 subprocess 命令 —— 这类引用
    **不是 import**，所以 import 图检查、架构分层检查、`--help` 冒烟全都看不见它。

    2026-08-07 的实际后果：把 `holdings/` 从 `market_timing/` 拆出来时，
    `daily_pipeline` 里四个 `MARKET_TIMING / "xxx.py"` 全部指向不存在的文件，
    其中三个 `required=True`（`run_stage` 的默认值）⇒ **整条 daily_pipeline
    在第一个持仓 stage 就硬失败**，09:05 与 17:00 两份报告都产不出来。
    3481 条测试全绿，因为 stage 一律被打桩、从不真的去看文件在不在。

    同一天早些时候 `run_1445` 的 `TOOLS` 未导入是同一类：**非 import 的引用没人查**。
    """

    def _iter_subprocess_targets(self):
        """产出 (文件, 行号, 常量名, 脚本名, 解析出的绝对路径)。

        ⚠️ 常量值用**运行时真值**取（import 模块后 getattr），不解析源码字符串 ——
        字符串解析看不出 `X = Y / "z"` 里 Y 又是什么。
        """
        import ast
        import importlib
        import sys
        sys.path.insert(0, str(TOOLS))
        for f in sorted(TOOLS.rglob("*.py")):
            if f.name in ("__init__.py", "conftest.py"):
                continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8-sig"))
            except SyntaxError:
                continue
            hits = []
            for node in ast.walk(tree):
                if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
                    continue
                if not (isinstance(node.left, ast.Name) and node.left.id.isupper()):
                    continue
                if not (isinstance(node.right, ast.Constant)
                        and isinstance(node.right.value, str)
                        and node.right.value.endswith(".py")):
                    continue
                hits.append((node.lineno, node.left.id, node.right.value))
            if not hits:
                continue
            try:
                mod = importlib.import_module(f.stem if f.parent == TOOLS
                                             else f"{f.parent.name}.{f.stem}")
            except Exception:
                continue          # 导入失败由别的测试负责报告
            for lineno, const, script in hits:
                base = getattr(mod, const, None)
                if isinstance(base, Path):
                    yield f, lineno, const, script, base / script

    def test_every_subprocess_script_target_exists(self):
        broken = [f"{f.relative_to(TOOLS.parent)}:{ln}  {const}/{script}"
                  for f, ln, const, script, p in self._iter_subprocess_targets()
                  if not p.exists()]
        self.assertEqual(broken, [], "subprocess 目标脚本不存在（搬迁后漏改路径）：\n  "
                                     + "\n  ".join(broken))

    def test_check_itself_catches_a_planted_break(self):
        """⚠️ 守卫必须自证能抓到 —— 上面那条测试为空也可能是**扫不到任何目标**。

        今天有四条测试因为桩不真而静默变 skip，同一个坑不能再踩。
        """
        found = list(self._iter_subprocess_targets())
        self.assertGreaterEqual(len(found), 15,
                                f"只扫到 {len(found)} 个 subprocess 目标，扫描逻辑可能失效了")
        # 植入一个必然不存在的目标，确认判据真的会判它失败
        import ast
        tree = ast.parse('X = 1\nsubprocess.run([str(TOOLS / "definitely_absent_xyz.py")])')
        hits = [n for n in ast.walk(tree)
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
                and isinstance(n.right, ast.Constant)
                and str(n.right.value).endswith(".py")]
        self.assertEqual(len(hits), 1, "AST 模式匹配不到植入的用例")
        self.assertFalse((TOOLS / "definitely_absent_xyz.py").exists())


class LocalPathRedefinitionTests(unittest.TestCase):
    """⚠️ 模块不得本地重拼 `paths.py` 已定义的 **07_tools 子目录**路径。

    原有守卫 `test_modules_do_not_rebuild_governance_paths` 只查**治理层**路径，
    所以没拦住 `daily_pipeline` 本地重定义的 `MARKET_TIMING = TOOLS / "market_timing"`
    —— 正是它在 2026-08-07 拆 holdings/ 时成了死路径：`paths.py` 改过了，
    本地副本没改，「唯一来源」形同虚设。

    **为什么只查工具目录，不查数据目录**：两者危险度差一个量级。
    `TOOLS / "子目录"` 随代码重构而变（今天一天断了两次），断了以后
    subprocess 只报「文件不存在」，而 `required=False` 的 stage 会**静默降级**。
    `DATA / "market"` 只在数据布局变更时才变，那种变更本来就会
    在所有读写点响亮地报缺文件。把 20 多处数据目录别名一起纳入只会让守卫
    带着一张长长的既有违规清单上线，那样它就不再是守卫了。
    真正接住故障的是下面 `SubprocessTargetTests` —— 它直接验目标文件在不在，
    与常量是本地拼的还是导入的无关。
    """

    def test_no_module_rebuilds_a_paths_constant(self):
        import ast
        import sys
        sys.path.insert(0, str(TOOLS))
        import paths as paths_mod

        # 只取 07_tools 下的子目录常量
        known = {v: k for k, v in vars(paths_mod).items()
                 if isinstance(v, Path) and k.isupper()
                 and v != paths_mod.TOOLS and paths_mod.TOOLS in v.parents}
        offenders = []
        for f in sorted(TOOLS.rglob("*.py")):
            if f.name in ("paths.py", "__init__.py", "conftest.py"):
                continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8-sig"))
            except SyntaxError:
                continue
            # ⚠️ 豁免 bootstrap：有些模块既当包内模块 import、也被直接当脚本跑，
            # 必须先本地算出 07_tools 再塞进 sys.path，`paths` 那时还导不进来（鸡生蛋）。
            # 判据是「该赋值出现在 `from paths import` **之前**」—— 用行号比，
            # 不猜变量名（`adjust_factors` 叫 TOOLS_DIR，别的模块叫 _TOOLS_ROOT）。
            paths_import_line = min(
                (n.lineno for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom) and n.module == "paths"),
                default=10 ** 9)
            for node in tree.body:      # 只看模块级赋值
                if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)
                        and node.targets[0].id.isupper()):
                    continue
                if node.lineno < paths_import_line:
                    continue            # bootstrap，见上
                name = node.targets[0].id
                # 形如 DATA / "market" 或 TOOLS / "market_timing"（不含 .py）
                v = node.value
                if not (isinstance(v, ast.BinOp) and isinstance(v.op, ast.Div)
                        and isinstance(v.right, ast.Constant)
                        and isinstance(v.right.value, str)
                        and not v.right.value.endswith(".py")):
                    continue
                try:
                    import importlib
                    mod = importlib.import_module(
                        f.stem if f.parent == TOOLS else f"{f.parent.name}.{f.stem}")
                    val = getattr(mod, name, None)
                except Exception:
                    continue
                if isinstance(val, Path) and val in known and known[val] != name:
                    offenders.append(
                        f"{f.relative_to(TOOLS.parent)}:{node.lineno}  "
                        f"{name} 重拼了 paths.{known[val]}")
        self.assertEqual(offenders, [],
                         "本地重拼 paths.py 已有的路径（搬迁时必然漏改）：\n  "
                         + "\n  ".join(offenders))
