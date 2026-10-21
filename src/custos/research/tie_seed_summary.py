# -*- coding: utf-8 -*-
"""TODO #107 · R1/R2 组合层 baseline 对照的 tie-seed 多种子汇总（v0.441）。

背景（审计 2026-10-15，R1/R2 头部注记）：组合层「baseline(恒 0 分=随机 top-5)
vs reversal_quality」对照的平分原按代码升序截断（任意规则，单次抽签）——合成实测
同池 CAGR 区间 −15.9%~+21.2%。本工具把**逐笔交易已落盘**的产物按组合同参对
``tie_seed`` 0..N-1 逐种子重放 ``simulate_portfolio_topn``，报分布区间与
**逐种子配对对照**（同种子两 scorer 相减，平分噪声两边同担）。

**为什么是「4 条跑数 + 1 条汇总」而不是 80 条**：tie_seed 只进组合层排序键
（``_tie_key``），逐笔交易与种子无关 ⇒ 每（scorer, 窗）只需一条全量跑数。
内置一致性校验：产物若带 portfolio 块，按其记录的 tie_seed 重放必须逐位等于
块内读数（不等 ⇒ WARN，说明产物口径与本工具组合同参不一致）。

生产机命令（写死，R1 §5 组合口径；窗口 = v2 挖掘 A/B——原对照窗 2019~2020
已属判定段，不可再用）::

  P="--trade-sim --entry-filter j_low --stop-mode pct --stop-pct 8 --bbi-consec 2 \
     --cost-bps 25 --seed 0 --portfolio --risk-pct 1 --max-concurrent 15 --max-pos 6 --top-n 5"
  for S in baseline reversal_quality; do
    uv run python -m custos.research backtest_factors $P --scorer $S \
      --start 2000-01-01 --end 2016-12-31 --count 7200 --allow-tail-clip --load-workers 4 \
      --out artifacts/logs/tie_seed_r1r2/${S}_segA.json
    uv run python -m custos.research backtest_factors $P --scorer $S \
      --start 2022-01-01 --end 2026-09-04 --count 1600 --load-workers 4 \
      --out artifacts/logs/tie_seed_r1r2/${S}_segB.json
  done
  uv run python -m custos.research tie_seed_summary \
    artifacts/logs/tie_seed_r1r2/baseline_segA.json \
    artifacts/logs/tie_seed_r1r2/reversal_quality_segA.json \
    artifacts/logs/tie_seed_r1r2/baseline_segB.json \
    artifacts/logs/tie_seed_r1r2/reversal_quality_segB.json \
    --out artifacts/logs/tie_seed_r1r2/summary.json

读数：每（scorer, 窗）cagr / max_drawdown / return_over_maxdd 的 min·p50·max；
配对对照报告每窗「reversal_quality − baseline」的逐种子 Δ 中位数与胜率
（Δ>0 的种子占比）。结论段按区间写回 R1/R2 审计注记。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

SEEDS_DEFAULT = 20
#: R1 §5 组合口径（原对照参数，写死为缺省）
TOP_N = 5
RISK_PCT = 0.01
MAX_CONCURRENT = 15
MAX_POS_FRAC = 0.06


def _portfolio_per_seed(
    trades: list[dict], seeds: range, args: argparse.Namespace
) -> dict[int, dict[str, Any]]:
    """逐种子重放组合（引擎同函数同参 ⇒ 与 CLI --portfolio 逐位一致）。"""
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    out: dict[int, dict[str, Any]] = {}
    for sd in seeds:
        pf = bt.simulate_portfolio_topn(
            trades,
            top_n=args.top_n,
            risk_pct=args.risk_pct,
            max_concurrent=args.max_concurrent,
            max_pos_frac=args.max_pos_frac,
            tie_seed=sd,
        )
        out[sd] = {
            "total_return": pf.get("total_return"),
            "cagr": pf.get("cagr"),
            "max_drawdown": pf.get("max_drawdown"),
            "return_over_maxdd": pf.get("return_over_maxdd"),
            "n_taken": pf.get("n_taken"),
        }
    return out


def _verify_stored(
    name: str, stored: dict[str, Any], per_seed: dict[int, dict[str, Any]]
) -> Optional[str]:
    """产物 portfolio 块与重放的一致性校验；不一致返回 WARN 文本。"""
    if not stored:
        return None
    sd = int(stored.get("tie_seed") or 0)
    mine = per_seed.get(sd)
    if mine is None:
        return None
    diffs = {
        k: (stored.get(k), mine.get(k))
        for k in ("total_return", "cagr", "max_drawdown", "n_taken")
        if stored.get(k) != mine.get(k)
    }
    if diffs:
        return f"{name}: 产物 portfolio(tie_seed={sd}) 与重放不一致 {diffs}"
    return None


def _dist(vals: list[Optional[float]]) -> dict[str, Optional[float]]:
    xs = [v for v in vals if isinstance(v, (int, float))]
    if not xs:
        return {"min": None, "p50": None, "max": None}
    return {"min": min(xs), "p50": statistics.median(xs), "max": max(xs)}


def summarize(
    products: list[dict[str, Any]], seeds: range, args: argparse.Namespace
) -> dict[str, Any]:
    """逐产物分布 + 同窗配对对照（products 为已加载 JSON）。"""
    rows: list[dict[str, Any]] = []
    warns: list[str] = []
    per_seed_map: dict[str, dict[int, dict[str, Any]]] = {}
    for p in products:
        name = str(p.get("scorer") or "?")
        win = f"{p.get('start')}~{p.get('end')}"
        trades = p.get("trades") or []
        if not trades or not all("score" in t for t in trades):
            raise ValueError(f"{name} {win}：产物无逐笔交易或缺 score——无法重放组合")
        per_seed = _portfolio_per_seed(trades, seeds, args)
        per_seed_map[f"{name}|{win}"] = per_seed
        w = _verify_stored(f"{name} {win}", p.get("portfolio") or {}, per_seed)
        if w:
            warns.append(w)
        rows.append(
            {
                "scorer": name,
                "window": win,
                "n_trades": len(trades),
                "expectancy": (p.get("trade_summary") or {}).get("expectancy"),
                "cagr": _dist([s["cagr"] for s in per_seed.values()]),
                "max_drawdown": _dist([s["max_drawdown"] for s in per_seed.values()]),
                "return_over_maxdd": _dist(
                    [s["return_over_maxdd"] for s in per_seed.values()]
                ),
                "n_taken": _dist([s["n_taken"] for s in per_seed.values()]),
            }
        )
    # 同窗两两配对（按传入顺序相邻两两；同 start~end 为一窗）
    pairs: list[dict[str, Any]] = []
    by_win: dict[str, list[str]] = {}
    for key in per_seed_map:
        by_win.setdefault(key.split("|", 1)[1], []).append(key)
    for win, keys in sorted(by_win.items()):
        if len(keys) != 2:
            continue
        ka, kb = sorted(keys)
        ds = []
        for sd in seeds:
            a = per_seed_map[ka][sd]["total_return"]
            b = per_seed_map[kb][sd]["total_return"]
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                ds.append(b - a)
        if ds:
            pairs.append(
                {
                    "window": win,
                    "a": ka.split("|", 1)[0],
                    "b": kb.split("|", 1)[0],
                    "delta_total_return_p50": statistics.median(ds),
                    "delta_total_return_min": min(ds),
                    "delta_total_return_max": max(ds),
                    "b_win_share": sum(1 for d in ds if d > 0) / len(ds),
                    "n_seeds": len(ds),
                }
            )
    return {"rows": rows, "pairs": pairs, "warns": warns, "seeds": list(seeds)}


def render_md(rep: dict[str, Any]) -> str:
    """汇总表（行=scorer×窗；配对对照一窗一行）。"""
    lines = [
        "| scorer | 窗 | n_trades | expectancy | CAGR min/p50/max | 最大回撤 min/p50/max | 收益回撤 min/p50/max |",
        "|---|---|---|---|---|---|---|",
    ]

    def _f(d: dict, scale: float = 1.0, pct: bool = False) -> str:
        def g(v: Optional[float]) -> str:
            if v is None:
                return "—"
            x = v * scale
            return f"{x * 100:.1f}%" if pct else f"{x:.2f}"

        return f"{g(d['min'])}/{g(d['p50'])}/{g(d['max'])}"

    for r in rep["rows"]:
        lines.append(
            f"| {r['scorer']} | {r['window']} | {r['n_trades']} | "
            f"{(r['expectancy'] or 0) * 100:+.2f}% | "
            f"{_f(r['cagr'], pct=True)} | {_f(r['max_drawdown'], pct=True)} | "
            f"{_f(r['return_over_maxdd'])} |"
        )
    if rep["pairs"]:
        lines += [
            "",
            "逐种子配对对照（Δtotal_return = 后者 − 前者，同种子相减）：",
            "",
            "| 窗 | 对照 | Δ min/p50/max | 后者胜率 |",
            "|---|---|---|---|",
        ]
        for p in rep["pairs"]:
            lines.append(
                f"| {p['window']} | {p['b']} − {p['a']} | "
                f"{p['delta_total_return_min'] * 100:+.1f}%/"
                f"{p['delta_total_return_p50'] * 100:+.1f}%/"
                f"{p['delta_total_return_max'] * 100:+.1f}% | "
                f"{p['b_win_share'] * 100:.0f}%（{p['n_seeds']} 种子） |"
            )
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R1/R2 组合层对照 tie-seed 多种子汇总（TODO #107）"
    )
    ap.add_argument("products", nargs="+", help="backtest_factors 逐笔产物 JSON")
    ap.add_argument(
        "--n-seeds", type=int, default=SEEDS_DEFAULT, help="种子数 0..N-1（默认 20）"
    )
    ap.add_argument("--top-n", type=int, default=TOP_N)
    ap.add_argument("--risk-pct", type=float, default=RISK_PCT)
    ap.add_argument("--max-concurrent", type=int, default=MAX_CONCURRENT)
    ap.add_argument("--max-pos-frac", type=float, default=MAX_POS_FRAC)
    ap.add_argument("--out", default="", help="汇总 JSON 落盘（缺省只打印）")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = _build_parser().parse_args(argv)
    products = []
    for raw in args.products:
        p = Path(raw)
        if not p.exists():
            print(f"[FAIL] 产物不存在：{p}", file=sys.stderr)
            return 2
        products.append(json.loads(p.read_text(encoding="utf-8")))
    try:
        rep = summarize(products, range(args.n_seeds), args)
    except ValueError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    for w in rep["warns"]:
        print(f"[WARN] {w}", file=sys.stderr)
    print(render_md(rep))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n汇总 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
