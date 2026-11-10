# -*- coding: utf-8 -*-
"""进场口径偏差对比（v0.505，只描述、不作判据）：B1 现行交易集在「信号日收盘成交」vs「次日开盘成交」下的读数差。

交易集 = 0AMV 做多 ∧ J<13（j_low 门）× baseline scorer「可买」× 现行出场（plan_rules_replay.LIVE_PARAMS），
collect_all（每个可买日都算一笔，同 R44/R48 口径），已实现交易，扣成本 25bp。按挖掘 A / B 分窗、按入场年分组。

用法（开发机快照）::

    CUSTOS_DATA_SNAPSHOT=$PWD/artifacts/cache/mining_snapshot_v2 \\
      uv run python scripts/dev/entry_timing_compare.py --sample 500 --procs 10

产物 ``artifacts/logs/entry_timing/<tag>.json`` + 终端表。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

WINDOWS = {
    "mining_a": ("2000-01-01", "2016-12-31"),
    "mining_b": ("2022-01-01", "2026-09-04"),
}
_CTX: dict[str, Any] = {}


def _init(regime: dict[str, str]) -> None:
    _CTX["regime"] = regime


def _one(job: tuple[str, str, str, str]) -> dict[str, list[dict]]:
    from custos.research import backtest_factors as bt
    from custos.research import plan_rules_replay as prr

    code, w, s, e = job
    df = bt._load_one_bars(code, 100000, s, e, warmup_bars=300)
    out: dict[str, list[dict]] = {"close": [], "next_open": []}
    if df is None or len(df) < 60:
        return out
    for timing in out:
        trs = bt.evaluate_trades(
            {code: df},
            scorer=bt.SCORERS["baseline"],
            entry_gate=bt.ENTRY_GATES["j_low"],
            amv_regime=_CTX["regime"],
            cost_bps=25.0,
            collect_all=True,
            entry_timing=timing,
            **prr.LIVE_PARAMS,
        )
        out[timing] = [
            {**t, "_w": w}
            for t in trs
            if s <= str(t["entry_date"])[:10] <= e
            and not bt.is_unrealized_reason(t.get("reason"))
        ]
    return out


def _stats(trs: list[dict]) -> dict[str, Any]:
    from custos.research import backtest_factors as bt
    from custos.research import m2_stop_sweep as m2

    if not trs:
        return {"n": 0}
    s = bt.summarize_trades(trs)
    return {
        "n": s["n"],
        "win_rate": s["win_rate"],
        "payoff": s["payoff_ratio"],
        "expectancy": s["expectancy"],
        "margin": m2._margin({"win": s["win_rate"], "payoff": s["payoff_ratio"]}),
        "p_big": sum(1 for t in trs if t["ret"] >= 0.2) / len(trs),
    }


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--tag", default="v1")
    a = ap.parse_args(argv)
    from custos.core.paths import LOGS
    from custos.datasource.local_tdx import local_tdx_data as l
    from custos.research import backtest_factors as bt

    codes = sorted(l.list_local_vipdoc_codes())
    random.Random(a.seed).shuffle(codes)
    codes = sorted(codes[: a.sample])
    regime = bt.load_amv_regime(since="2000-01-01")
    jobs = [(c, w, *se) for w, se in WINDOWS.items() for c in codes]
    res: dict[str, list[dict]] = {"close": [], "next_open": []}
    with ProcessPoolExecutor(a.procs, initializer=_init, initargs=(regime,)) as ex:
        for r in ex.map(_one, jobs, chunksize=4):
            for k in res:
                res[k].extend(r[k])
    rep: dict[str, Any] = {
        "sample": a.sample,
        "seed": a.seed,
        "windows": {},
        "by_year": {},
    }
    for w in WINDOWS:
        rep["windows"][w] = {
            k: _stats([t for t in v if t["_w"] == w]) for k, v in res.items()
        }
    years = sorted({str(t["entry_date"])[:4] for v in res.values() for t in v})
    for y in years:
        rep["by_year"][y] = {
            k: _stats([t for t in v if str(t["entry_date"])[:4] == y])
            for k, v in res.items()
        }
    out = LOGS / "entry_timing" / f"{a.tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    def row(lbl: str, blk: dict[str, Any]) -> str:
        c, o = blk["close"], blk["next_open"]
        if not c.get("n") or not o.get("n"):
            return f"| {lbl} | {c.get('n', 0)} | {o.get('n', 0)} | | | | |"
        return (
            f"| {lbl} | {c['n']} | {o['n']} | {c['margin']:+.2%} → {o['margin']:+.2%} | "
            f"{c['win_rate']:.1%} → {o['win_rate']:.1%} | {c['payoff']:.2f} → {o['payoff']:.2f} | "
            f"{c['expectancy']:+.2%} → {o['expectancy']:+.2%} |"
        )

    print("| 窗 / 年 | 笔数(收盘) | 笔数(次开) | margin | 胜率 | 盈亏比 | 每笔期望 |")
    print("|---|---:|---:|---|---|---|---|")
    for w, blk in rep["windows"].items():
        print(row(w, blk))
    for y, blk in rep["by_year"].items():
        print(row(y, blk))
    print(f"⇒ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
