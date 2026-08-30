# -*- coding: utf-8 -*-
"""研究：1800「三面共振」候选人的胜率与盈亏比（owner 2026-08-30）。

> ⚠️ **R11**：量级不作数，读数仅供相对排序。**R21 警示**：画像富集 ≠ 可交易，
> 本研究是交易层验证（gate 化后真实回测），不是画像复述。

**三面共振** = 1800 候选表「可买/待0AMV做多」定义（score_candidates.four_leg_resonance
的 bull_candidate 三腿）：基本面优（fundamental_quality 品质档=优）∧ 技术强
（live technical_score ≥60）∧ 市场腿（0AMV 做多——v0.93 基底）。

- **臂 A（对照）** = j_low 基底（与 v0.118/v0.119 主臂同引擎同出场，重跑保同批）
- **臂 B** = j_low ∧ 基本面优（PIT as-of）∧ 技术强（as-of 技术分 ≥60）
- 出场（两臂一致）：stop12 + 保本 0.05 + 分批止盈 0.5 + BBI 连破 2 根 + 25bps
- 宇宙 seed=0 400 只全历史；0AMV 做多区间

**PIT 正确性**（`fetch_pit_financials` 产物 data/fundamentals/pit_financials.jsonl）：
只用 notice_date < 信号日（公告次一交易日才可见，as_of 默认口径）的最新一期记录；
同报告期多版本取 notice_date 最大者。**已知边界（如实）**：台账首期 2014Q1
（2014-04-24 起可见）⇒ 此前信号无基本面证据 ⇒ 按「不可证优 ⇒ 不进场」处理
（live 同语义：tier 未知 ≠ 优）。

**预注册判读线**（跑前写死）：B−A margin > 0 且两臂胜率 Wilson 95% 不重叠
且前后半窗 Δmargin 同正 ⇒ 三面共振过滤加值；否则不加值/证据不足
（margin = 胜率 − 盈亏平衡胜率 1/(1+盈亏比)，m2/v0.128 口径）。

CLI::

    uv run python src/custos/research/resonance3_study.py --max-stocks 400 --seed 0
"""

from __future__ import annotations

import argparse
import bisect
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from custos.core.factors.fundamentals import fundamental_quality  # noqa: E402
from custos.research import backtest_factors as bf  # noqa: E402
from custos.research import score_return_study as srs  # noqa: E402
from custos.research import score_variants_study as svs  # noqa: E402
from custos.research import winner_factor_study as wfs  # noqa: E402
from custos.research.m2_stop_sweep import _margin  # noqa: E402

TECH_STRONG = 60  # 技术强 = live 技术分 ≥60（score_candidates.TECH_STRONG_FALLBACK）
ARM_STOP_PCT = 12.0
ARM_BREAKEVEN = 0.05
ARM_SCALE_OUT = 0.5

PREREG_CRITERION = (
    "预注册判读线：B−A margin > 0 且两臂胜率 Wilson 95% 不重叠 且 "
    "前后半窗 Δmargin 同正 ⇒ 三面共振过滤加值；否则不加值/证据不足。"
)


# ---------------------------------------------------------------------------
# PIT 基本面腿（as-of，无未来函数）
# ---------------------------------------------------------------------------


def pit_record_to_financials(rec: Optional[dict[str, Any]]) -> dict[str, Any]:
    """PIT 台账记录 → fundamental_quality 的输入形态（dixi_proxy 口径逐键对齐 live）。

    live `_dixi_metrics` 语义：op_cashflow 缺失 ⇒ op_cashflow_positive=None 且
    real_earnings_cashflow=False（不冒充成立）；net_profit/roe 缺失按非正。
    符号口径：PIT 的 ocf_ps 是每股经营现金流，符号与总额一致（判 >0 够用）。
    """
    if rec is None:
        return {"available": False}
    np_ = rec.get("net_profit")
    ocf = rec.get("ocf_ps")
    roe = rec.get("roe_waa")
    np_pos = bool(np_ is not None and np_ > 0)
    ocf_pos = bool(ocf is not None and ocf > 0) if ocf is not None else None
    proxy = {
        "net_profit_positive": np_pos,
        "op_cashflow_positive": ocf_pos,
        "real_earnings_cashflow": bool(np_pos and ocf_pos),
        "roe_positive": bool(roe is not None and roe > 0),
    }
    return {"available": True, "dixi_proxy": proxy}


def build_pit_map(
    records: list[dict[str, Any]],
) -> dict[str, list[tuple[str, str, dict]]]:
    """code → 按 notice_date 排序的 [(notice_date, report_date, record)]（bisect 用）。"""
    by_code: dict[str, list[tuple[str, str, dict]]] = {}
    for r in records:
        nd = str(r.get("notice_date") or "")
        rd = str(r.get("report_date") or "")
        if len(nd) != 10 or len(rd) != 10:
            continue
        by_code.setdefault(str(r.get("code") or ""), []).append((nd, rd, r))
    for lst in by_code.values():
        lst.sort(key=lambda x: (x[0], x[1]))
    return by_code


def pit_tier_at(
    by_code: dict[str, list[tuple[str, str, dict]]], code: str, day: str
) -> str:
    """信号日可见的最新一期（notice_date < day，公告次日才可见）→ 品质档。

    同报告期多版本（更正/补披露）取 notice_date 最大且已可见者——
    与 fetch_pit_financials.as_of 同语义。无可见记录 ⇒ "未知"。
    """
    lst = by_code.get(code)
    if not lst:
        return "未知"
    nds = [x[0] for x in lst]
    k = bisect.bisect_left(nds, day)  # notice_date < day（次日可见口径）
    if k == 0:
        return "未知"
    best = max(lst[:k], key=lambda x: (x[1], x[0]))  # (report_date, notice_date) 最大
    return fundamental_quality(pit_record_to_financials(best[2])).get("tier", "未知")


# ---------------------------------------------------------------------------
# 三面共振 gate（per-code 工厂；检查按成本从低到高排序）
# ---------------------------------------------------------------------------


def make_resonance_gate(
    code: str,
    long_dates: frozenset[str],
    pit_map: dict[str, list],
    index_df,
):
    """j_low ∧ 基本面优(PIT as-of) ∧ 技术强(as-of 技术分≥60)。

    成本排序：regime 集合查询 → J<13 预计算点 → PIT 查表 → as-of 技术分
    （compute_metrics ~50ms，只在前三腿全过的 bar 上算）。绝不 raise。
    """

    def gate(df_slice, precomputed=None) -> bool:
        try:
            d = str(df_slice["date"].iloc[-1])[:10]
            if d not in long_dates:  # ① 0AMV 做多
                return False
            if not bf.j_low_gate(df_slice, precomputed):  # ② J<13
                return False
            if pit_tier_at(pit_map, code, d) != "优":  # ③ 基本面优（PIT）
                return False
            # ④ 技术强：as-of live 技术分（截断 df_slice，无未来函数——钉测钉住）
            score, _level, _contrib = srs.asof_technical_score(
                df_slice, index_df, len(df_slice) - 1, code
            )
            return score >= TECH_STRONG
        except Exception:  # noqa: BLE001
            return False

    return gate


# ---------------------------------------------------------------------------
# 两臂统计
# ---------------------------------------------------------------------------


def arm_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """单臂全景：基础指标 + 期望R + margin + Wilson + 前后半窗 + 出场分布。"""
    st = srs.ret_stats(trades)
    n_win = sum(1 for t in trades if t["ret"] > 0)
    rm = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    dates = sorted(t["entry_date"] for t in trades)
    mid = dates[len(dates) // 2] if dates else None
    halves = {}
    for name, sub in (
        ("first", [t for t in trades if mid and t["entry_date"] <= mid]),
        ("second", [t for t in trades if mid and t["entry_date"] > mid]),
    ):
        s = srs.ret_stats(sub)
        halves[name] = {"n": len(sub), **s, "margin": svs_margin(s)}
    return {
        "n": len(trades),
        **st,
        "n_win": n_win,
        "expectancy_R": round(statistics.mean(rm), 4) if rm else None,
        "margin": svs_margin(st),
        "wr_wilson95": svs_wilson(n_win, len(trades)),
        "half_window": {"split_date": mid, **halves},
        "exit_reasons": srs.exit_reason_dist(trades),
    }


def svs_margin(st: dict[str, Any]) -> Optional[float]:
    """margin = 胜率 − 盈亏平衡胜率（m2 口径复用）。"""
    return _margin({"win": st.get("win_rate"), "payoff": st.get("payoff_ratio")})


def svs_wilson(n_win: int, n: int):
    return list(svs.wilson_wr_interval(n_win, n))


def compare_arms(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """B vs A：Δmargin（全体 + 前后半窗）+ Wilson 重叠 + 预注册判定。"""
    d_margin = (
        round(b["margin"] - a["margin"], 4)
        if a["margin"] is not None and b["margin"] is not None
        else None
    )
    lo_a, hi_a = a["wr_wilson95"]
    lo_b, hi_b = b["wr_wilson95"]
    wilson_overlap = (
        None if None in (lo_a, hi_a, lo_b, hi_b) else not (hi_b < lo_a or hi_a < lo_b)
    )
    half_dirs = []
    for h in ("first", "second"):
        ma, mb = a["half_window"][h].get("margin"), b["half_window"][h].get("margin")
        half_dirs.append(None if ma is None or mb is None else (mb - ma) > 0)
    half_consistent = None if len(half_dirs) < 2 else (half_dirs[0] == half_dirs[1])
    # 预注册判读线：Δmargin>0 且 Wilson 不重叠 且 半窗同正 ⇒ 加值
    adds_value = bool(
        d_margin is not None
        and d_margin > 0
        and wilson_overlap is False
        and half_consistent
        and all(half_dirs)
    )
    return {
        "preregistered_criterion": PREREG_CRITERION,
        "delta_margin": d_margin,
        "delta_win_rate": round(b["win_rate"] - a["win_rate"], 4),
        "delta_payoff": (
            round(b["payoff_ratio"] - a["payoff_ratio"], 3)
            if a["payoff_ratio"] and b["payoff_ratio"]
            else None
        ),
        "delta_avg_ret": round(b["avg_ret"] - a["avg_ret"], 4),
        "wr_wilson_overlap": wilson_overlap,
        "half_window_delta_margin_positive": half_dirs,
        "half_window_consistent": half_consistent,
        "adds_value": adds_value,
    }


def rsi_deep_share(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """臂内 rsi_deep_oversold 命中占比（R21 重叠度；None=unavailable 不进分母）。"""
    vals = [(t.get("panel") or {}).get("rsi_deep_oversold") for t in trades]
    ev = [v for v in vals if v is not None]
    n_hit = sum(1 for v in ev if v)
    return {
        "n_eval": len(ev),
        "n_hit": n_hit,
        "share": round(n_hit / len(ev), 4) if ev else None,
    }


def build_report(
    trades_a: list[dict[str, Any]], trades_b: list[dict[str, Any]], meta: dict[str, Any]
) -> dict[str, Any]:
    return {
        "preregistered_criterion": PREREG_CRITERION,
        "r11_r21_warning": (
            "⚠️ R11：量级不作数。⚠️ R21：画像富集≠可交易——本研究是交易层验证。"
        ),
        "arm_a": arm_stats(trades_a),
        "arm_b": arm_stats(trades_b),
        "compare": compare_arms(arm_stats(trades_a), arm_stats(trades_b)),
        "rsi_deep_overlap": {
            "arm_a": rsi_deep_share(trades_a),
            "arm_b": rsi_deep_share(trades_b),
        },
        "meta": meta,
    }


def print_report(rep: dict[str, Any]) -> None:
    print("\n" + "=" * 76)
    print("三面共振研究：j_low 基底（A） vs j_low∧基本面优∧技术强（B）")
    print("=" * 76)
    print(rep["r11_r21_warning"])
    print(rep["preregistered_criterion"])
    print(f"\nPIT 覆盖：{rep['meta'].get('pit_coverage')}")
    for label, key in (("A=j_low 基底", "arm_a"), ("B=三面共振", "arm_b")):
        s = rep[key]
        print(
            f"\n[{label}] n={s['n']} 胜率 {s['win_rate'] * 100:.1f}% "
            f"Wilson95={s['wr_wilson95']} | 盈亏比 {s['payoff_ratio']} | "
            f"均收 {s['avg_ret'] * 100:.2f}% | 期望R {s['expectancy_R']} | "
            f"margin {s['margin'] * 100:+.1f}pp"
        )
        hw = s["half_window"]
        print(
            f"  半窗（切于 {hw['split_date']}）：前半 n={hw['first']['n']} "
            f"margin {hw['first'].get('margin') and hw['first']['margin'] * 100:+.1f}pp / "
            f"后半 n={hw['second']['n']} "
            f"margin {hw['second'].get('margin') and hw['second']['margin'] * 100:+.1f}pp"
        )
        print(
            "  出场："
            + "，".join(
                f"{k} {v['n']}({v['frac'] * 100:.0f}%,{v['avg_ret'] * 100:+.1f}%)"
                for k, v in s["exit_reasons"].items()
            )
        )
    c = rep["compare"]
    print(
        f"\n── B−A：Δmargin {c['delta_margin'] and c['delta_margin'] * 100:+.1f}pp | "
        f"Δ胜率 {c['delta_win_rate'] * 100:+.1f}pp | Δ盈亏比 {c['delta_payoff']} | "
        f"Δ均收 {c['delta_avg_ret'] * 100:+.2f}pp | Wilson重叠={c['wr_wilson_overlap']} | "
        f"半窗Δmargin同正={c['half_window_delta_margin_positive']}"
    )
    print(
        f"── 判定（预注册线）：三面共振过滤"
        f"{'✅ 加值' if c['adds_value'] else '❌ 不加值/证据不足'}"
    )
    ro = rep["rsi_deep_overlap"]
    print(
        f"── R21 重叠：rsi_deep 命中占比 A={ro['arm_a']['share']} "
        f"B={ro['arm_b']['share']}（命中/可评：A {ro['arm_a']['n_hit']}/"
        f"{ro['arm_a']['n_eval']}，B {ro['arm_b']['n_hit']}/{ro['arm_b']['n_eval']}）"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--max-stocks", type=int, default=400, help="宇宙抽样只数")
    ap.add_argument("--seed", type=int, default=0, help="抽样种子")
    ap.add_argument("--start", default="2010-01-01", help="0AMV regime 起点")
    ap.add_argument("--cost-bps", type=float, default=srs.COST_BPS)
    ap.add_argument("--out", default="")
    return ap


def main(argv: Optional[list] = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)

    regime = bf.load_amv_regime(since=args.start)
    if not regime:
        ap.error("读不到 0AMV 台账/regime 数据")
    long_dates = frozenset(d for d, s in regime.items() if s == "做多")
    print(f"[INFO] 0AMV 做多日 {len(long_dates)} 天", file=sys.stderr)

    from custos.datasource.local_tdx import fetch_pit_financials as pit  # noqa: PLC0415
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    records = pit.load_ledger()
    if not records:
        ap.error(
            "PIT 财务台账为空（data/fundamentals/pit_financials.jsonl）——"
            "基本面腿不可用，如实报卡点开跑无意义"
        )
    pit_map = build_pit_map(records)
    first_notice = min(x[0] for lst in pit_map.values() for x in lst)

    base = local_tdx_data.list_local_vipdoc_codes()
    codes = bf.sample_codes(base, args.max_stocks, args.seed)
    covered = sum(1 for c in codes if c in pit_map)
    print(
        f"[INFO] PIT 台账 {len(records)} 条（{len(pit_map)} 只，首期可见 "
        f"{first_notice}）；宇宙 {len(codes)} 只中 {covered} 只有 PIT 覆盖",
        file=sys.stderr,
    )
    index_df = (
        local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
        .sort_values("date")
        .reset_index(drop=True)
    )

    common = dict(
        cost_bps=args.cost_bps,
        stop_pct=ARM_STOP_PCT,
        breakeven_trigger=ARM_BREAKEVEN,
        scale_out_frac=ARM_SCALE_OUT,
        trade_hook=wfs.panel_hook,
    )
    print("[INFO] 臂 A（j_low 基底）开跑", file=sys.stderr)
    trades_a = srs.run_study(codes, regime, index_df, **common)
    print(f"[INFO] 臂 A 完成 {len(trades_a)} 笔；臂 B（三面共振）开跑", file=sys.stderr)
    trades_b = srs.run_study(
        codes,
        regime,
        index_df,
        entry_gate_factory=lambda code: make_resonance_gate(
            code, long_dates, pit_map, index_df
        ),
        **common,
    )
    print(f"[INFO] 臂 B 完成 {len(trades_b)} 笔", file=sys.stderr)
    if not trades_a:
        print("⛔ 臂 A 0 笔交易", file=sys.stderr)
        return 1

    rep = build_report(
        trades_a,
        trades_b,
        meta={
            "signal": "日KDJ J<13 + 0AMV 做多（固定基底）",
            "exit": f"stop{ARM_STOP_PCT} + breakeven {ARM_BREAKEVEN} + scale_out "
            f"{ARM_SCALE_OUT} + BBI连破2根",
            "arm_b_extra": f"基本面优（PIT as-of，notice_date<信号日）∧ 技术分≥{TECH_STRONG}",
            "cost_bps": args.cost_bps,
            "max_stocks": args.max_stocks,
            "seed": args.seed,
            "start": args.start,
            "n_codes": len(codes),
            "pit_records": len(records),
            "pit_first_notice": first_notice,
            "pit_coverage": f"{covered}/{len(codes)} 只有 PIT 覆盖；2014-04 前无数据"
            "（此前信号按『不可证优⇒不进场』处理，live 同语义）",
        },
    )
    rep["trades_a"] = trades_a
    rep["trades_b"] = trades_b

    out = (
        Path(args.out)
        if args.out
        else (
            Path("artifacts/logs/resonance3_study")
            / f"resonance3_study_s{args.seed}_n{len(codes)}.json"
        )
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    bf.write_json_stream(out, rep, big=len(trades_a) + len(trades_b) > 20000)
    print(f"[OK] 写出 {out}")
    print_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
