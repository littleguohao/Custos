# -*- coding: utf-8 -*-
"""因子版本表（owner 2026-11-03，TODO #121 定义校准配套）。

单一来源 ``governance/contracts/FACTOR_VERSIONS.json``：每个因子模块（``core/factors/*.py`` 里声明
``FACTOR`` 的模块，键 = 模块名）与 ``b1_thresholds`` 一条 ``{version, source_digest, history}``。

- **version**：整数，从 1 起；**每升一次 = 判定语义变了一次**（owner 定）。
- **source_digest**：模块源码的规范化 AST 摘要（剔注释 / 空白 / docstring）。源码一变而版本没升 ⇒
  ``tests/test_factor_versions.py`` 红：语义变了就 ``--bump``，纯重构就 ``--refactor``（都要写原因）。
  局限：只覆盖本模块文件，被它调用的共用件（indicators 等）改动不在摘要里——改共用件时人工核对。
- **calibration_holdout_use**：用 holdout 期（≥2026-09-05）1800 结果做过定义校准的记录——这些因子
  在所记窗口内的 holdout 读数不再是独立证据（holdout 月报照抄此表）。

运行时只读（产物里写 ``versions()``）；改表只走 ``scripts/dev/factor_versions.py``。
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

from custos.core.paths import CONTRACTS_DIR

VERSIONS_FILE = CONTRACTS_DIR / "FACTOR_VERSIONS.json"
_CORE = Path(__file__).resolve().parent
_FACTORS = _CORE / "factors"
_SKIP = {"_template", "_util", "_shares", "__init__"}


def tracked_files() -> dict[str, Path]:
    """{因子名: 源文件}：声明了 FACTOR 的因子模块（AST 判，不 import）+ b1_thresholds。"""
    out: dict[str, Path] = {"b1_thresholds": _CORE / "b1_thresholds.py"}
    for p in sorted(_FACTORS.glob("*.py")):
        if p.stem in _SKIP:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        if any(
            isinstance(n, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(t, ast.Name) and t.id == "FACTOR"
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
            )
            for n in tree.body
        ):
            out[p.stem] = p
    return out


class _StripDoc(ast.NodeTransformer):
    def _strip(self, node: Any) -> Any:
        self.generic_visit(node)
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
        return node

    visit_Module = visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _strip


def source_digest(path: Path) -> str:
    """规范化 AST 摘要：注释、空白、docstring 改动不影响；任何代码改动都会变。"""
    tree = _StripDoc().visit(ast.parse(path.read_text(encoding="utf-8")))
    return hashlib.blake2b(
        ast.dump(tree, include_attributes=False).encode(), digest_size=10
    ).hexdigest()


def load() -> dict[str, Any]:
    try:
        d = json.loads(VERSIONS_FILE.read_text(encoding="utf-8-sig"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def versions() -> dict[str, int]:
    """{因子名: 版本号}（产物落盘用；表缺失 ⇒ 空 dict，不炸主链）。"""
    return {
        k: int(v.get("version", 0)) for k, v in (load().get("factors") or {}).items()
    }


def holdout_calibration_use() -> list[dict[str, Any]]:
    return list(load().get("calibration_holdout_use") or [])
