# -*- coding: utf-8 -*-
"""test_affected.py（受影响测试选择器）钉测。

被测对象是 scripts/dev 下的脚本（非包内模块）——importlib 按路径加载
（同 test_shadow_1800_compare 惯例）。选择逻辑全在纯函数里，用合成
src/tests 树钉：反向 import 闭包、相对导入解析、命名直配兜底、全量
触发、guard-only 退化、模块划分模式。选错=静默漏跑（fail-open），
必须钉死。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dev" / "test_affected.py"
spec = importlib.util.spec_from_file_location("test_affected", SCRIPT)
ta = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ta)


@pytest.fixture()
def tree(tmp_path):
    """合成 src/tests 树：

    paths(core) ← foo ← bar(relative import)；baz 无依赖、其测试无 import 联系
    （命名直配专用）；unrelated 与任何模块无关。
    """
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    (src / "custos" / "core").mkdir(parents=True)
    (src / "custos" / "research").mkdir(parents=True)
    tests.mkdir()
    for pkg in ("custos", "custos/core", "custos/research"):
        (src / pkg / "__init__.py").write_text("", encoding="utf-8")
    (src / "custos" / "core" / "paths.py").write_text("LOGS = 1\n", encoding="utf-8")
    (src / "custos" / "research" / "foo.py").write_text(
        "from custos.core.paths import LOGS\n", encoding="utf-8"
    )
    (src / "custos" / "research" / "bar.py").write_text(
        "from . import foo\nfrom ..core.paths import LOGS\n", encoding="utf-8"
    )
    (src / "custos" / "research" / "baz.py").write_text("X = 1\n", encoding="utf-8")
    (tests / "test_foo.py").write_text(
        "from custos.research import foo\n", encoding="utf-8"
    )
    (tests / "test_bar.py").write_text(
        "from custos.research import bar\n", encoding="utf-8"
    )
    (tests / "test_paths.py").write_text(
        "from custos.core.paths import LOGS\n", encoding="utf-8"
    )
    (tests / "test_baz.py").write_text("import subprocess\n", encoding="utf-8")
    (tests / "test_unrelated.py").write_text("import json\n", encoding="utf-8")
    return src, tests


def _names(files):
    return {f.name for f in files}


class TestGraph:
    def test_relative_import_resolution(self, tree):
        src, _ = tree
        g = ta.build_graph(src)
        assert g["custos.research.foo"] == {"custos.core.paths"}
        assert g["custos.research.bar"] == {
            "custos.research",
            "custos.research.foo",
            "custos.core.paths",
        }

    def test_from_pkg_import_submodule(self, tree):
        src, tests = tree
        tm = ta.build_test_map(set(ta.build_graph(src)), tests)
        by_name = {p.name: deps for p, deps in tm.items()}
        assert "custos.research.foo" in by_name["test_foo.py"]
        assert "custos.research.bar" in by_name["test_bar.py"]

    def test_reverse_closure(self, tree):
        src, _ = tree
        g = ta.build_graph(src)
        got = ta.reverse_closure(g, {"custos.core.paths"})
        assert {
            "custos.core.paths",
            "custos.research.foo",
            "custos.research.bar",
        } <= got


class TestSelect:
    def test_core_change_pulls_transitive_dependents(self, tree):
        src, tests = tree
        mode, files, _ = ta.select(["src/custos/core/paths.py"], src, tests)
        assert mode == "affected"
        assert {"test_foo.py", "test_bar.py", "test_paths.py"} <= _names(files)
        assert "test_unrelated.py" not in _names(files)
        assert "test_baz.py" not in _names(files)

    def test_leaf_change_scope(self, tree):
        src, tests = tree
        mode, files, _ = ta.select(["src/custos/research/foo.py"], src, tests)
        assert mode == "affected"
        assert {"test_foo.py", "test_bar.py"} <= _names(files)
        assert "test_paths.py" not in _names(files)

    def test_name_convention_fallback(self, tree):
        """测试与模块无 import 联系（subprocess/CLI 间接消费）⇒ 命名直配兜住。"""
        src, tests = tree
        _, files, _ = ta.select(["src/custos/research/baz.py"], src, tests)
        assert "test_baz.py" in _names(files)

    def test_deleted_module_name_fallback(self, tree):
        src, tests = tree
        _, files, _ = ta.select(["src/custos/research/gone.py"], src, tests)
        assert "test_gone.py" not in _names(files)  # 无对应测试不炸

    def test_changed_test_file_runs_itself(self, tree):
        src, tests = tree
        _, files, _ = ta.select(["tests/test_unrelated.py"], src, tests)
        assert "test_unrelated.py" in _names(files)

    def test_docs_only_is_guard_only(self, tree):
        src, tests = tree
        mode, files, _ = ta.select(
            ["README.md", "governance/research/R37_x.md"], src, tests
        )
        assert mode == "guard-only"
        assert files == []  # 合成树无守卫文件；真实仓库此处=基础守卫+治理守卫

    def test_governance_guard_real_tree(self):
        """治理文件变更 ⇒ 追加治理守卫集（v0.300 教训：这些测试不 import
        custos，靠闭包永远选不中——R39/R40 文档不合规本地绿、CI 才红）。"""
        _, files, why = ta.select(["governance/research/R39_x.md"])
        names = _names(files)
        assert "test_research_units.py" in names
        assert "test_architecture_layers.py" in names  # 基础守卫仍在
        assert any("治理守卫" in w for w in why)
        for trigger in ("CHANGELOG.md", "TODO.md", "AGENTS.md"):
            _, files, _ = ta.select([trigger])
            assert "test_research_units.py" in _names(files), trigger

    def test_data_json_not_governance(self):
        """运行时产物（data//artifacts/ 下的 JSON）不触发治理守卫集。"""
        _, files, why = ta.select(["data/cache/x.json"])
        assert "test_research_units.py" not in _names(files)
        assert not any("治理守卫" in w for w in why)

    @pytest.mark.parametrize(
        "trigger",
        [
            "pyproject.toml",
            "uv.lock",
            ".github/workflows/tests.yml",
            "tests/conftest.py",
        ],
    )
    def test_full_triggers(self, tree, trigger):
        src, tests = tree
        mode, files, why = ta.select(
            [trigger, "src/custos/research/foo.py"], src, tests
        )
        assert mode == "full", trigger
        assert len(files) == 5, why

    def test_module_partition(self, tree):
        src, tests = tree
        files = ta.select_module("custos.research", tests)
        # select_module 内部用全局 SRC 建图——合成树下改用局部函数验证映射语义
        tm = ta.build_test_map(set(ta.build_graph(src)), tests)
        got = {
            p.name
            for p, deps in tm.items()
            if any(d.startswith("custos.research") for d in deps)
        }
        assert got == {"test_foo.py", "test_bar.py"}
        assert files  # 真树冒烟：至少非空


class TestRealTreeSmoke:
    """真实仓库冒烟（不跑 pytest，只验证选择器对真树不炸且自洽）。"""

    def test_real_graph_and_test_map(self):
        g = ta.build_graph()
        assert "custos.research.exit_c5_terminal" in g
        tm = ta.build_test_map(set(g))
        total = sum(1 for deps in tm.values() if deps)
        assert total > 150, "绝大多数测试应能映射到 custos 模块"

    def test_real_select_self_change(self):
        """改选择器本体（非 src/tests 代码）⇒ guard-only 仍含基础守卫集。"""
        mode, files, _ = ta.select(["scripts/dev/test_affected.py"])
        assert mode == "guard-only"
        assert "test_architecture_layers.py" in _names(files)
