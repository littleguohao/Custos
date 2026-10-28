# -*- coding: utf-8 -*-
"""#116 验收①对拍（v0.462，owner 2026-10-27 放宽口径）：真实数据 vs 快照的同 commit 研究产物逐字段比对。

放宽后的验收口径（写死）：

- **不依赖 V0 分的字段必须逐位一致**：全候选（collect_all）口径的 margin / 胜率 / 盈亏比 / 期望 / 笔数 / 记账 /
  分布画像 / C4 随机宽度臂池等——它们只由价格与出场规则决定，快照与真实数据输入相同就必须相同；
- **依赖 V0 分的字段允许微差**：每日 top-N 选中集与组合层（``n_taken`` / ``ret_over_dd`` / ``rdd_gate`` /
  ``objective`` / ``total_return`` / ``sel_*`` / ``portfolio`` …）。原因：as-of V0 分要信号日前 1200 根日线
  （周 / 月 MACD 腿），挖掘 B 信号往前 1200 根落进判定段，快照物理上没有这段数据；
- **判决必须一致**：顶层 ``verdict`` 与各判据 ``ok`` 不得翻转（微差若翻了判决就不叫微差）。

用法::

    uv run python scripts/dev/snapshot_acceptance_diff.py <真实侧产物.json> <快照侧产物.json>

退出码：0 = 验收通过；1 = 有不依赖分数的字段不一致或判决翻转；2 = 参数 / 读取错误。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterator

#: 依赖 V0 分（top-N 选中集 / 组合层）的叶子键名或路径片段——允许微差
SCORE_DEPENDENT_KEYS = {
    "n_taken",
    "ret_over_dd",
    "rdd_gate",
    "objective",
    "total_return",
    "cagr",
    "max_drawdown",
    "return_over_maxdd",
    "final_equity",
    "n_skipped",
    "selected_win_rate",
    "selected_expectancy",
    "n_selected",
    "stop_share",  # 组合选中子集里止损出场占比
    "passing_rdd_merged",
}
SCORE_DEPENDENT_SEGMENTS = ("portfolio", "sel_", "selected", "taken", "rdd")
#: 判决字段：必须一致
VERDICT_KEYS = {"verdict", "ok", "state"}
#: 不参与比对（运行环境 / 溯源）
IGNORED_SEGMENTS = (
    "provenance",
    "timing",
    "generated_at",
    "cmdline",
    "tag",
    "git_",
    "snapshot",
)


def _leaves(o: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _leaves(v, f"{path}.{k}" if path else str(k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _leaves(v, f"{path}[{i}]")
    else:
        yield path, o


def classify(path: str) -> str:
    """叶子路径 → ignored / verdict / score_dependent / strict。"""
    low = path.lower()
    parts = [p.split("[")[0] for p in low.split(".")]
    if any(seg in low for seg in IGNORED_SEGMENTS):
        return "ignored"
    leaf = parts[-1] if parts else ""
    if leaf in VERDICT_KEYS:
        return "verdict"
    if leaf in SCORE_DEPENDENT_KEYS or any(
        seg in p for p in parts for seg in SCORE_DEPENDENT_SEGMENTS
    ):
        return "score_dependent"
    return "strict"


def _same(a: Any, b: Any) -> bool:
    if (
        isinstance(a, float)
        and isinstance(b, float)
        and math.isnan(a)
        and math.isnan(b)
    ):
        return True
    return a == b


def compare(real: Any, snap: Any) -> dict[str, Any]:
    ra = dict(_leaves(real))
    sa = dict(_leaves(snap))
    out: dict[str, Any] = {
        "strict_mismatch": [],
        "verdict_mismatch": [],
        "score_dependent_diff": [],
        "missing": [],
        "n_compared": 0,
    }
    for p in sorted(set(ra) | set(sa)):
        cls = classify(p)
        if cls == "ignored":
            continue
        if p not in ra or p not in sa:
            out["missing"].append(p)
            continue
        out["n_compared"] += 1
        a, b = ra[p], sa[p]
        if _same(a, b):
            continue
        row = {"path": p, "real": a, "snap": b}
        if (
            isinstance(a, (int, float))
            and isinstance(b, (int, float))
            and not isinstance(a, bool)
        ):
            row["abs_diff"] = abs(float(a) - float(b))
        {"strict": out["strict_mismatch"], "verdict": out["verdict_mismatch"]}.get(
            cls, out["score_dependent_diff"]
        ).append(row)
    out["passed"] = not (
        out["strict_mismatch"] or out["verdict_mismatch"] or out["missing"]
    )
    deps = [
        r.get("abs_diff")
        for r in out["score_dependent_diff"]
        if r.get("abs_diff") is not None
    ]
    out["score_dependent_max_abs_diff"] = max(deps) if deps else 0.0
    return out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="#116 验收①对拍（放宽口径：分数相关字段允许微差）"
    )
    ap.add_argument("real")
    ap.add_argument("snap")
    ap.add_argument("--out", default="", help="对拍明细 JSON（可选）")
    a = ap.parse_args(argv)
    try:
        real = json.loads(Path(a.real).read_text(encoding="utf-8"))
        snap = json.loads(Path(a.snap).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[ERR] 读取失败: {exc}", file=sys.stderr)
        return 2
    rep = compare(real, snap)
    print(
        f"[snap-acc] 比对 {rep['n_compared']} 个字段：严格不一致 {len(rep['strict_mismatch'])}，"
        f"判决翻转 {len(rep['verdict_mismatch'])}，缺字段 {len(rep['missing'])}，"
        f"分数相关微差 {len(rep['score_dependent_diff'])}（最大绝对差 {rep['score_dependent_max_abs_diff']:.3g}）"
    )
    for k in ("strict_mismatch", "verdict_mismatch", "missing"):
        for r in rep[k][:20]:
            print(f"  ✗ {k}: {r}")
    if a.out:
        Path(a.out).write_text(
            json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    print("[snap-acc] 验收通过" if rep["passed"] else "[snap-acc] 验收不通过")
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
