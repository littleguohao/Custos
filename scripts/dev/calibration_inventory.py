# -*- coding: utf-8 -*-
"""信号参数定义校准清单生成器（owner 2026-11-03：先做「定义校准」，live 用到的优先）。

扫描 ``core/factors/*.py`` 与 ``core/b1_thresholds.py`` 的模块级数值常量（含数值元组），
按因子注册表元数据分优先级：

- **P0 live 判定**：``live_use ∈ {scorer, gate}`` 且 ``stage=release``（进技术分 / 进池门 / 封顶）；
- **P1 live 展示**：``live_use=evidence_only`` 且 ``stage=release``（1800 报告标注，不进判定）；
- **P2 研究**：其余（``none`` / ``debug``）。

用法::

    uv run python scripts/dev/calibration_inventory.py            # 打印 Markdown 表
    uv run python scripts/dev/calibration_inventory.py --check    # 核对清单文档覆盖全部 P0/P1 参数

清单文档 ``governance/research/calibration_inventory.md`` 的「定义校准」列是人工维护的状态
（未校准 / 进行中 / ✅ commit xxx），本脚本 ``--check`` 只管覆盖率，不覆盖状态。
"""

from __future__ import annotations

import argparse
import ast
import importlib
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parents[2]
FACTORS = BASE / "src" / "custos" / "core" / "factors"
THRESH = BASE / "src" / "custos" / "core" / "b1_thresholds.py"
DOC = BASE / "governance" / "research" / "calibration_inventory.md"

#: b1_thresholds 的使用方（不是注册表因子，按 live 判定处理）
THRESH_FACTOR = "b1_thresholds"
#: 注册表外的 live 持仓检测器（P0H：驱动持仓 P0/P1 清仓 / 减仓评估）
HOLDING_MODULES = {
    "technical_monitor": BASE
    / "src"
    / "custos"
    / "pipeline"
    / "market_timing"
    / "technical_monitor.py",
}


def _num(v: ast.AST) -> Any:
    if (
        isinstance(v, ast.Constant)
        and isinstance(v.value, (int, float))
        and not isinstance(v.value, bool)
    ):
        return v.value
    if isinstance(v, ast.UnaryOp) and isinstance(v.op, ast.USub):
        x = _num(v.operand)
        return -x if isinstance(x, (int, float)) else None
    if isinstance(v, ast.Tuple) and v.elts and all(_num(e) is not None for e in v.elts):
        return tuple(_num(e) for e in v.elts)
    if (
        isinstance(v, ast.Call)
        and getattr(v.func, "id", "") == "_f"
        and len(v.args) == 2
    ):
        return _num(v.args[1])  # b1_thresholds 的 _f("ENV", 默认值)
    return None


def constants(path: Path) -> list[tuple[str, Any, str, int]]:
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    out = []
    for n in ast.parse(src).body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            t = n.targets[0] if isinstance(n, ast.Assign) else n.target
            if not (isinstance(t, ast.Name) and t.id.isupper()) or t.id == "FACTOR":
                continue
            if n.value is None:
                continue
            v = _num(n.value)
            if v is None:
                continue
            line = lines[n.lineno - 1]
            cm = line.split("#", 1)[1].strip() if "#" in line else ""
            out.append((t.id, v, cm.replace("|", "/"), n.lineno))
    return out


def tier_of(meta: dict) -> str:
    lu, st = meta.get("live_use"), meta.get("stage")
    if st == "release" and lu in ("scorer", "gate"):
        return "P0"
    if st == "release" and lu == "evidence_only":
        return "P1"
    return "P2"


def inventory() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for mod, path in HOLDING_MODULES.items():
        for name, v, cm, ln in constants(path):
            rows.append(
                {
                    "tier": "P0H",
                    "factor": mod,
                    "param": name,
                    "value": v,
                    "note": cm,
                    "line": ln,
                    "live_use": "holding_state",
                    "status": "-",
                    "refs": "",
                }
            )
    for name, v, cm, ln in constants(THRESH):
        rows.append(
            {
                "tier": "P0",
                "factor": THRESH_FACTOR,
                "param": name,
                "value": v,
                "note": cm,
                "line": ln,
                "live_use": "gate/scorer",
                "status": "-",
                "refs": "",
            }
        )
    for f in sorted(FACTORS.glob("*.py")):
        if f.name.startswith("_"):
            continue
        mod = importlib.import_module(f"custos.core.factors.{f.stem}")
        meta = getattr(mod, "FACTOR", None) or {}
        refs = meta.get("research_ref") or []
        for name, v, cm, ln in constants(f):
            rows.append(
                {
                    "tier": tier_of(meta),
                    "factor": f.stem,
                    "param": name,
                    "value": v,
                    "note": cm,
                    "line": ln,
                    "live_use": meta.get("live_use"),
                    "status": meta.get("status"),
                    "refs": ",".join(refs) if isinstance(refs, list) else str(refs),
                }
            )
    return rows


def render(rows: list[dict[str, Any]]) -> str:
    out = []
    for tier in ("P0H", "P0", "P1", "P2"):
        sel = [r for r in rows if r["tier"] == tier]
        out.append(f"### {tier}（{len(sel)} 个参数）\n")
        out.append(
            "| 因子 | 参数 | 当前值 | 代码注释 | 因子状态 / 效果证据 | 定义校准 |"
        )
        out.append("|---|---|---|---|---|---|")
        for r in sel:
            ev = f"{r['status']}" + (f"；{r['refs']}" if r["refs"] else "")
            out.append(
                f"| `{r['factor']}` | `{r['param']}` | {r['value']} | {r['note'] or '-'} | {ev} | 未校准 |"
            )
        out.append("")
    return "\n".join(out)


def check(rows: list[dict[str, Any]]) -> list[str]:
    doc = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
    return [
        f"{r['factor']}.{r['param']}"
        for r in rows
        if r["tier"] in ("P0H", "P0", "P1")
        and f"| `{r['factor']}` | `{r['param']}` |" not in doc
    ]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="信号参数定义校准清单")
    ap.add_argument(
        "--check", action="store_true", help="核对清单文档覆盖全部 P0/P1 参数"
    )
    a = ap.parse_args(argv)
    rows = inventory()
    if a.check:
        miss = check(rows)
        for m in miss:
            print(f"[MISS] {m}")
        print(f"[calib-inv] P0/P1 缺登记 {len(miss)} 个")
        return 1 if miss else 0
    print(render(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
