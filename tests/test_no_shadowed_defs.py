"""同一模块内不得重复定义同名顶层函数/类。

⚠️ 后定义的会**静默遮蔽**前者，前者变成永不执行的死代码 ——
而它的 docstring 仍会被人读到，产生「代码写了但没生效」的错觉。

2026-08-06 实例：`s_data.py`（当时在 `screening/`）里 `load_bars_qlib` 被定义了两次
（我当天加 `allow_unverified` 时没删干净）。第一份只有 def + docstring、**返回 None**，
而那份 docstring 挂着 ⚠️ 价格口径警示 —— 幸好活的那份有更完整的版本，
但读者若先看到死的那份，就会以为警示已经在生效的代码里。

这类问题 Python 不报错、测试也不一定失败（被遮蔽的通常不被调用），
只能靠结构检查抓。
"""
from __future__ import annotations

import ast
import collections
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FILES = sorted(p for p in (ROOT / "07_tools").rglob("*.py") if p.name != "__init__.py")


def _dups(path: pathlib.Path) -> dict[str, list[int]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return {}
    seen: dict[str, list[int]] = collections.defaultdict(list)
    for node in tree.body:                      # 只看**顶层**，嵌套/条件分支里的重名是合法模式
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            seen[node.name].append(node.lineno)
    return {k: v for k, v in seen.items() if len(v) > 1}


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT / "07_tools")))
def test_no_duplicate_toplevel_defs(path):
    d = _dups(path)
    assert not d, ("同名顶层定义会静默遮蔽，前者成死代码："
                   + "; ".join(f"{k} @ 行 {v}" for k, v in d.items()))


def test_check_itself_detects_a_planted_case(tmp_path):
    """检查函数自己要有测试 —— 写 test_tdx_connection_hygiene 时的教训。"""
    f = tmp_path / "m.py"
    f.write_text("def a():\n    pass\n\n\ndef a():\n    pass\n", encoding="utf-8")
    assert _dups(f) == {"a": [1, 5]}
    g = tmp_path / "n.py"
    g.write_text("def a():\n    def a():\n        pass\n    return a\n", encoding="utf-8")
    assert _dups(g) == {}, "嵌套函数重名是合法的，不该报"
