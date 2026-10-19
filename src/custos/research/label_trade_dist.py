# -*- coding: utf-8 -*-
"""1800 标注因子交易分布画像（描述性副读数，v0.423；owner 2026-10-19）。

在 R27「1800 标注一览·盈亏比/胜率」的同一批格子产物上补分布：读 strategy_grid 的
``_ranked__<tag>.json`` → 逐格加载格子结果文件里的逐笔交易 → ``trade_dist.describe``。
**不重跑回测、不读新数据**；每格另报两个对照：

- ``vs_base``：同窗同出场的基底格（缺省 gate=j_low，即 1800 候选池的 J<13 基底）的同一套画像摘要；
- ``vs_complement``：基底交易里**未命中**该标注的补集（按 (code, entry_date) 去重）——
  标注把哪一段分布挑出来了（命中 vs 未命中），比「命中 vs 全体」更直接。

纪律：描述性读数，**不是判据**，不改 R27 / R7 / R8 已判结论（标注已在跨窗终审中否决，
一览只是观察记录）；入场日 ≥ 前向 holdout（2026-09-05）的交易剔除并计数（旧产物生成于 holdout
规则之前时可能含）；出场落 holdout 的交易沿 ``load_window`` 口径只注记不截。

用法::

    uv run python -m custos.research label_trade_dist \\
        --ranked artifacts/logs/strategy_grid/_ranked__rsi_family_cw.json \\
                 artifacts/logs/strategy_grid/_ranked__form_family_cw.json ... \\
        [--exit pct12_so5_bbi2] [--base-gate j_low] [--out ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from custos.core.paths import LOGS
from custos.research import load_window as lw
from custos.research import trade_dist as td

SUMMARY_KEYS = (
    "n",
    "win_rate",
    "payoff_ratio",
    "margin",
    "avg_win",
    "avg_loss",
    "expectancy",
    "big_win_share",
    "quantiles",
)


def _tag_of(ranked: Path) -> str:
    stem = ranked.stem
    return stem.split("__", 1)[1] if "__" in stem else stem


def _load_trades(cell_file: Path) -> Optional[list[dict]]:
    try:
        raw = json.loads(cell_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tr = raw.get("trades")
    return tr if isinstance(tr, list) else None


def _clip_holdout(trades: list[dict]) -> tuple[list[dict], int, int]:
    """剔入场 ≥ holdout 的交易；返回 (保留, 剔除数, 出场落 holdout 的保留笔数)。"""
    h = lw.FORWARD_HOLDOUT_START
    kept = [t for t in trades if str(t.get("entry_date", ""))[:10] < h]
    exit_in = sum(1 for t in kept if str(t.get("exit_date", ""))[:10] >= h)
    return kept, len(trades) - len(kept), exit_in


def _summary(d: dict[str, Any]) -> dict[str, Any]:
    return {k: d.get(k) for k in SUMMARY_KEYS}


def build(ranked_paths: list[Path], base_gate: str, exit_filter: str) -> dict[str, Any]:
    cells: dict[str, Any] = {}
    skipped: list[dict[str, str]] = []
    n_holdout = 0
    for rp in ranked_paths:
        payload = json.loads(rp.read_text(encoding="utf-8"))
        tag = _tag_of(rp)
        rows = payload.get("results") or []  # strategy_grid.build_json 的排名行
        loaded: dict[tuple[str, str, str], list[dict]] = {}
        meta: dict[tuple[str, str, str], dict[str, Any]] = {}
        for r in rows:
            if exit_filter and r.get("exit") != exit_filter:
                continue
            f = r.get("result_file")
            tr = _load_trades(rp.parent / f) if f else None
            if tr is None:
                skipped.append(
                    {
                        "ranked": rp.name,
                        "gate": str(r.get("gate")),
                        "why": "格子产物缺失或无逐笔",
                    }
                )
                continue
            tr, dropped, exit_in = _clip_holdout(tr)
            n_holdout += dropped
            key = (str(r.get("scorer")), str(r.get("gate")), str(r.get("exit")))
            loaded[key] = tr
            meta[key] = {"n_holdout_dropped": dropped, "n_exit_in_holdout": exit_in}
        multi_scorer = len({k[0] for k in loaded}) > 1
        for (sc, gate, ex), tr in sorted(loaded.items()):
            d = td.describe(tr)
            base = loaded.get((sc, base_gate, ex))
            blk: dict[str, Any] = {
                **d,
                **meta[(sc, gate, ex)],
                "ranked": rp.name,
                "scorer": sc,
            }
            if base is not None and gate != base_gate:
                hit = {(t["code"], t["entry_date"]) for t in tr}
                comp = [t for t in base if (t["code"], t["entry_date"]) not in hit]
                blk["n_overlap_with_base"] = sum(
                    1 for t in base if (t["code"], t["entry_date"]) in hit
                )
                blk["vs_base"] = _summary(td.describe(base))
                blk["vs_complement"] = _summary(td.describe(comp))
            cells[f"{tag}__{sc + '__' if multi_scorer else ''}{gate}__{ex}"] = blk
    return {
        "schema": "label_trade_dist/v1",
        "tool": "label_trade_dist",
        "note": "描述性副读数（v0.423）：R27 标注格子产物逐笔重读，不重跑、不作判据",
        "base_gate": base_gate,
        "exit_filter": exit_filter or None,
        "inputs": [p.name for p in ranked_paths],
        "holdout_start": lw.FORWARD_HOLDOUT_START,
        "n_holdout_dropped": n_holdout,
        "skipped": skipped,
        "cells": cells,
    }


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="1800 标注因子交易分布画像（R27 格子产物重读）"
    )
    ap.add_argument(
        "--ranked",
        nargs="+",
        required=True,
        help="strategy_grid 的 _ranked__<tag>.json（可多份）",
    )
    ap.add_argument(
        "--exit",
        default="pct12_so5_bbi2",
        help="只看该出场档（缺省=一览所用档；空串=全部）",
    )
    ap.add_argument(
        "--base-gate", default="j_low", help="基底格 gate（命中/补集对照用）"
    )
    ap.add_argument(
        "--out", default=str(LOGS / "label_trade_dist" / "label_trade_dist.json")
    )
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = _build_parser().parse_args(argv)
    rep = build([Path(p) for p in args.ranked], args.base_gate, args.exit)
    if not rep["cells"]:
        print(
            "[FAIL] 0 格可读（ranked 路径 / 格子产物 / --exit 不对？）——空结果不写产物",
            file=sys.stderr,
        )
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    for k, c in rep["cells"].items():
        print(
            f"{k}: n={c.get('n')} 胜率={c.get('win_rate')} 盈亏比={c.get('payoff_ratio')} "
            f"≥3×均亏占比={(c.get('big_win_share') or {}).get('ge_3x_avg_loss')}"
        )
    if rep["skipped"]:
        print(
            f"[WARN] {len(rep['skipped'])} 格跳过（产物缺失/无逐笔），见 skipped",
            file=sys.stderr,
        )
    print(f"[OK] 写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
