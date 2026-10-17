# -*- coding: utf-8 -*-
"""R46 V0 打分家族级配比重测（TODO #110；预注册 governance/research/R46_v0_family_weight_lattice.md）。

30 条 V0 腿分 6 家族，每家族倍率 m_f ∈ {0,1,2}，全枚举 3⁶ = 729 格；基准格 = 全 1（live 默认权重）。

**矩阵化**：technical_score 各段对权重线性（命中即加权重、repair min(次数×each, cap) 同比缩放），
⇒ score(m) = clamp₀¹⁰⁰(Σ_f m_f·S_f)，S_f = 只开家族 f 时的 clamp 前原始分
（``score_candidates.technical_score_raw``）。每笔候选只算 6 次原始分，729 格只是向量乘 + 组合模拟；
``test_family_lattice.py`` 钉住与 technical_score 逐位一致。

判据（跑数前写死，预注册 v0.404 定稿）：
**主读数口径（v0.416 修订，#92 功效复核后、跑数前）**：margin = 每日 top-N **选中集**
（``bt.select_daily_topn``，不过组合并发约束）的已实现交易 margin——组合实际成交（V0 口径并发≤5）
n 仅数百、MDE 3.4~6.4pp 触 untested 线；组合层读数降为 rdd 相对门 + 副读数 ``margin_portfolio``。

- C1：A、B 两段基准格选中集 n_selected ≥ 5400（= 功效线 MDE≤3pp@deff5 反解）**且** 组合 n_taken ≥ 100（rdd 门要有意义）；
- C2：g* = 两段都过 rdd 相对门、满分占比 ≤30% 的格中，A∪B 合并 Δmargin（选中集并集，vs 基准）最高者；
  要求 g* 在 A、B **各自** Δmargin > 0（平分种子 0 选格；另按种子 0~19 读 Δ 中位数作稳健副读数，中位数 ≤0
  的窗 ⇒ C2 不过）；
- C3：g* 的 12 个邻格（每家族 ±1 档）≥ 2/3 在 A、B 两段 Δmargin 均 > 0；
- C4：簇级置换零假设 N=100（过门臂数）——每段内把候选簇（``kit.cluster_ids``）的家族子分向量按簇长分层
  整体置换，再跑同一套 729 格取 g* 同规则的 max 合并 Δ；g* 合并 Δ > ``kit.q95`` ⇒ confirmed_pass；
- 判定段（仅 ``--batch-id``）：候选 g* 判定段 Δ vs 冻结臂（各臂选中格 × 判定段同策略重新置换）池，
  判定线 ``judgment_batch.judgment_line``（m 来自名册）。

不带 ``--batch-id`` = 只读挖掘段（不登记台账、不过批次门）；带 ⇒ 同一次运行挖掘段选格（确定性重复）
后读判定段。副读数：64 个家族开/关子格（m ∈ {0,1}⁶）Δmargin 表（#87 选项 B 证据）。
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from custos.core.paths import LOGS, RESEARCH_DIR
from custos.research import load_window as lw

#: 腿家族划分（预注册写死；改划分 = 新单元）
FAMILIES: dict[str, tuple[str, ...]] = {
    "F1_oversold": (
        "j_low",
        "weekly_j_low",
        "pullback_shrink",
        "b1_healthy_pullback_pack",
        "reversal_k_candidate",
        "five_day_entry",
    ),
    "F2_volume": (
        "volume_contraction",
        "leader_volume",
        "bottom_volume",
        "volume_yy_bull",
    ),
    "F3_trend": (
        "bbi_above",
        "relative_strength_strong",
        "adx_gt_60",
        "non_one_wave_confirmed",
        "zhixing_bull",
        "zhixing_close_above_qsx",
        "zhixing_in_qsx_dks_band",
    ),
    "F4_macd": (
        "macd_zone1",
        "macd_zone1_restart",
        "macd_bottom_divergence",
        "macd_above_water",
        "macd_bar_grow",
        "macd_wm_bar_grow",
    ),
    "F5_ignition": ("ignition", "b1_ignition", "repair_signals"),
    "F6_penalty": (
        "macd_top_divergence",
        "volume_yy_bear",
        "distribution_watch",
        "distribution_high",
    ),
}
FAMILY_NAMES = tuple(FAMILIES)
LEVELS = (0, 1, 2)
GRID: list[tuple[int, ...]] = list(itertools.product(LEVELS, repeat=len(FAMILY_NAMES)))
BASE = tuple(1 for _ in FAMILY_NAMES)
ONOFF = [m for m in GRID if all(x in (0, 1) for x in m)]
MIN_N_TAKEN = 100
#: v0.416：主读数=选中集；门槛 = 功效线反解——power_mde(wr 0.44, ρ 0.75, deff 5) ≤ 3pp ⇔ n ≥ 5400
MIN_N_SELECTED = 5400
FULL_SCORE_MAX = 0.30
C3_FRAC = 2 / 3
N_RANDOM = 100
C4_MIN_POOL = 50
TIE_SEEDS = 20
TOP_N = 20
SEG_NAMES = ("A", "B")


# ---------------------------------------------------------------------------
# 家族子分
# ---------------------------------------------------------------------------


def family_subscores(cand: dict, legs: list[str]) -> np.ndarray:
    """单笔候选的 6 维家族原始分 S（clamp 前）。"""
    from custos.pipeline.screening import score_candidates as sc  # noqa: PLC0415
    from custos.research.score_evolution_study import (  # noqa: PLC0415
        _v0_mult_overrides,
    )

    out = np.zeros(len(FAMILY_NAMES))
    for k, f in enumerate(FAMILY_NAMES):
        mask = [1.0 if leg in FAMILIES[f] else 0.0 for leg in legs]
        out[k] = sc.technical_score_raw(cand, _v0_mult_overrides(mask, legs))
    return out


def scores_of(S: np.ndarray, m: tuple[int, ...]) -> np.ndarray:
    return np.clip(S @ np.asarray(m, dtype=float), 0.0, 100.0)


# ---------------------------------------------------------------------------
# 读数
# ---------------------------------------------------------------------------


def _reading(
    trades: list[dict],
    scores: np.ndarray,
    top_n: int,
    tie_seed: int,
    ref_rdd: Optional[float],
) -> dict[str, Any]:
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import strategy_grid as sg  # noqa: PLC0415
    from custos.research.exit_campaign import V0_PORTFOLIO  # noqa: PLC0415

    for t, s in zip(trades, scores):
        t["score"] = float(s)
    taken: list[dict] = []
    pf = bt.simulate_portfolio_topn(
        trades, top_n=top_n, taken_out=taken, tie_seed=tie_seed, **V0_PORTFOLIO
    )
    tsum = bt.summarize_realized(taken)
    margin_pf = sg._margin(
        {"win": tsum.get("win_rate"), "payoff": tsum.get("payoff_ratio")}
    )
    sel = bt.select_daily_topn(trades, top_n, tie_seed)
    ssum = bt.summarize_realized(sel)
    margin = sg._margin(
        {"win": ssum.get("win_rate"), "payoff": ssum.get("payoff_ratio")}
    )
    rdd = sg._ret_over_dd(pf)
    gate = (
        True
        if ref_rdd is None
        else sg.rdd_gate_ok({"ret_over_dd": rdd}, {"ret_over_dd": ref_rdd})
    )
    return {
        "margin": margin,
        "margin_portfolio": margin_pf,
        "ret_over_dd": rdd,
        "rdd_gate": gate,
        "n_selected": ssum.get("n"),
        "n_taken": pf.get("n_taken"),
        "full_share": float((scores >= 100.0).mean()) if len(scores) else None,
        "_sel": [dict(t) for t in sel],
    }


def _merged_margin(taken: list[dict]) -> Optional[float]:
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import strategy_grid as sg  # noqa: PLC0415

    if not taken:
        return None
    ts = bt.summarize_realized(taken)
    return sg._margin({"win": ts.get("win_rate"), "payoff": ts.get("payoff_ratio")})


def _d(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return a - b if a is not None and b is not None else None


def eval_grid(
    segs: dict[str, dict[str, Any]],
    top_n: int,
    *,
    cells: Optional[list[tuple[int, ...]]] = None,
    tie_seed: int = 0,
) -> dict[tuple[int, ...], dict[str, Any]]:
    """逐格读数：每段 (margin, rdd, gate, Δ) + A∪B 合并 Δmargin。``segs[w] = {trades, S}``。"""
    base: dict[str, dict[str, Any]] = {
        w: _reading(
            segs[w]["trades"], scores_of(segs[w]["S"], BASE), top_n, tie_seed, None
        )
        for w in segs
    }
    base_merged = _merged_margin([t for w in segs for t in base[w]["_sel"]])
    out: dict[tuple[int, ...], dict[str, Any]] = {}
    for m in cells or GRID:
        per: dict[str, Any] = {}
        taken_all: list[dict] = []
        for w in segs:
            rd = (
                base[w]
                if m == BASE
                else _reading(
                    segs[w]["trades"],
                    scores_of(segs[w]["S"], m),
                    top_n,
                    tie_seed,
                    base[w]["ret_over_dd"],
                )
            )
            taken_all += rd["_sel"]
            per[w] = {
                k: rd[k]
                for k in (
                    "margin",
                    "margin_portfolio",
                    "ret_over_dd",
                    "rdd_gate",
                    "n_selected",
                    "n_taken",
                    "full_share",
                )
            }
            per[w]["delta"] = _d(rd["margin"], base[w]["margin"])
        per["merged_delta"] = _d(_merged_margin(taken_all), base_merged)
        out[m] = per
    return out


def pick_gstar(
    grid: dict[tuple[int, ...], dict[str, Any]],
) -> Optional[tuple[int, ...]]:
    ok = [
        m
        for m, r in grid.items()
        if m != BASE
        and r["merged_delta"] is not None
        and all(
            r[w]["rdd_gate"]
            and r[w]["full_share"] is not None
            and r[w]["full_share"] <= FULL_SCORE_MAX
            for w in SEG_NAMES
            if w in r
        )
    ]
    if not ok:
        return None
    return max(ok, key=lambda m: grid[m]["merged_delta"])  # 并列取格序在前


def neighbors(m: tuple[int, ...]) -> list[tuple[int, ...]]:
    out = []
    for k in range(len(m)):
        for dv in (-1, 1):
            v = m[k] + dv
            if v in LEVELS:
                out.append(m[:k] + (v,) + m[k + 1 :])
    return out


# ---------------------------------------------------------------------------
# 簇级置换零假设
# ---------------------------------------------------------------------------


def permute_clusters(
    items: list[dict], S: np.ndarray, rng: random.Random
) -> np.ndarray:
    """段内簇级置换：按簇长分层，把各簇的 S 行块整体打乱指派（同簇内顺序不变）。"""
    from custos.research import criteria_kit as kit  # noqa: PLC0415

    cids = kit.cluster_ids(items)
    members: dict[int, list[int]] = {}
    for idx, c in enumerate(cids):
        members.setdefault(c, []).append(idx)
    for c in members:
        members[c].sort(key=lambda j: items[j].get("i", 0))
    by_len: dict[int, list[int]] = {}
    for c, mem in members.items():
        by_len.setdefault(len(mem), []).append(c)
    out = S.copy()
    for _ln, cs in sorted(by_len.items()):
        perm = cs[:]
        rng.shuffle(perm)
        for src, dst in zip(cs, perm):
            out[members[dst]] = S[members[src]]
    return out


def run_null_pool(
    segs: dict[str, dict[str, Any]],
    top_n: int,
    n_random: int,
    seed: int,
) -> dict[str, Any]:
    from custos.research import criteria_kit as kit  # noqa: PLC0415

    arms: list[dict[str, Any]] = []

    def _arm(i: int) -> Optional[float]:
        rng = random.Random(f"{seed}-p{i}")
        pseg = {
            w: {**segs[w], "S": permute_clusters(segs[w]["items"], segs[w]["S"], rng)}
            for w in segs
        }
        g = eval_grid(pseg, top_n)
        gs = pick_gstar(g)
        arms.append({"arm": i, "cell": list(gs) if gs else None})
        return g[gs]["merged_delta"] if gs is not None else None

    pool = kit.assemble_c4_pool(n_random, _arm)
    return {**pool, "arms": arms}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _segment_items(
    collector: Callable[[Any], Optional[list[dict]]], name: str, span: tuple[str, str]
) -> list[dict]:
    from custos.research.evolution.dual_window import Window  # noqa: PLC0415

    items = collector(Window(*span)) or []
    dl = lw.entry_deadline(span[1])
    kept = [
        {**it, "_seg": name}
        for it in items
        if span[0] <= str(it["trade"]["entry_date"])[:10] <= dl
    ]
    return kept


def _prep_seg(items: list[dict], legs: list[str]) -> dict[str, Any]:
    trades = [dict(it["trade"]) for it in items]
    recs = [
        {"code": it["code"], "i": it.get("i", 0), "_seg": it["_seg"]} for it in items
    ]
    S = (
        np.vstack([family_subscores(it["cand"], legs) for it in items])
        if items
        else np.zeros((0, len(FAMILY_NAMES)))
    )
    return {"trades": trades, "S": S, "items": recs}


def run_study(
    args: Any,
    *,
    collector: Optional[Callable[[Any], Optional[list[dict]]]] = None,
) -> dict[str, Any]:
    from custos.research import criteria_kit as kit  # noqa: PLC0415
    from custos.research import judgment_batch as jb  # noqa: PLC0415
    from custos.research.score_calibration_study import CONTRIB_LEG_KEYS  # noqa: PLC0415

    legs = list(CONTRIB_LEG_KEYS)
    injected = collector is not None
    batch = bool(getattr(args, "batch_id", ""))
    spans = {"A": (args.mining_a_start, lw.SEG_MINING_A[1]), "B": lw.SEG_MINING_B}
    if batch:
        jb.gate_judgment_read(
            args,
            _build_parser(),
            synthetic=injected,
            judgment_span=lw.SEG_JUDGMENT,
            tool="family_lattice_study",
        )
    if collector is None:
        from custos.research import score_evolution_study as ses  # noqa: PLC0415
        from custos.research import universe as _u  # noqa: PLC0415

        ns = argparse.Namespace(
            codes_file=args.codes_file,
            universe_local=False,
            universe_sample=0,
            seed=0,
            codes=args.codes,
        )
        codes = _u.resolve(ns, _build_parser())
        args.universe_meta = ns.universe_meta
        exit_spec = {"params": {"stop_mode": "pct", "stop_pct": 5, "trail_pct": 0.08}}
        cnt = lw.count_for_start(args.mining_a_start)

        def collector(window):  # noqa: F811
            a2 = argparse.Namespace(count=cnt, cost_bps=args.cost_bps, rank_window=250)
            return ses._collect_v0_window(a2, codes, exit_spec, window)

    segs = {
        w: _prep_seg(_segment_items(collector, w, sp), legs) for w, sp in spans.items()
    }
    if not all(len(segs[w]["trades"]) for w in segs):
        return {}
    grid = eval_grid(segs, args.top_n)
    gs = pick_gstar(grid)
    base_n = {w: grid[BASE][w]["n_taken"] or 0 for w in SEG_NAMES}
    base_sel = {w: grid[BASE][w]["n_selected"] or 0 for w in SEG_NAMES}
    c1: dict[str, Any] = {
        "ok": all(v >= MIN_N_TAKEN for v in base_n.values())
        and all(v >= MIN_N_SELECTED for v in base_sel.values()),
        "n_taken_base": base_n,
        "n_selected_base": base_sel,
    }

    # 稳健副读数：g* 与基准按平分种子 0~19 的 Δ 中位数
    seed_med: dict[str, Optional[float]] = {}
    if gs is not None:
        for w in SEG_NAMES:
            ds = []
            for sd in range(args.tie_seeds):
                g1 = eval_grid({w: segs[w]}, args.top_n, cells=[gs], tie_seed=sd)
                if g1[gs][w]["delta"] is not None:
                    ds.append(g1[gs][w]["delta"])
            seed_med[w] = statistics.median(ds) if ds else None
    c2_ok = bool(
        gs is not None
        and all((grid[gs][w]["delta"] or 0) > 0 for w in SEG_NAMES)
        and all((seed_med.get(w) or 0) > 0 for w in SEG_NAMES)
    )
    c2: dict[str, Any] = {
        "ok": c2_ok if gs is not None else False,
        "g_star": list(gs) if gs else None,
        "deltas": {w: grid[gs][w]["delta"] for w in SEG_NAMES} if gs else None,
        "merged_delta": grid[gs]["merged_delta"] if gs else None,
        "tie_seed_median_delta": seed_med,
    }
    c3: dict[str, Any]
    if gs is not None:
        nb = neighbors(gs)
        pos = [m for m in nb if all((grid[m][w]["delta"] or 0) > 0 for w in SEG_NAMES)]
        c3 = {
            "ok": len(pos) / len(nb) >= C3_FRAC,
            "n_neighbors": len(nb),
            "n_positive": len(pos),
        }
    else:
        c3 = {"ok": None}

    null = run_null_pool(segs, args.top_n, args.n_random, args.seed)
    cand = grid[gs]["merged_delta"] if gs else None
    c4_state = kit.c4_state_of(null["pool"], min_pool=args.c4_min_pool, plan_delta=cand)
    c4 = {
        "state": c4_state,
        "q95": kit.q95(null["pool"]),
        "pool": null["pool"],
        "evaluated": null["evaluated"],
        "gate_pass_rate": null["gate_pass_rate"],
    }
    verdict = kit.verdict_four_state(
        c1_ok=c1["ok"], c2_ok=c2["ok"], c3_ok=c3["ok"], c4_state=c4_state
    )

    judgment_blk = None
    if batch:
        judgment_blk = _judgment_read(args, collector, legs, gs, null["arms"])

    onoff = {
        "".join(map(str, m)): {w: grid[m][w]["delta"] for w in SEG_NAMES} for m in ONOFF
    }
    from custos.research import provenance as pv  # noqa: PLC0415

    rep: dict[str, Any] = {
        "schema": "family_lattice_report/v2",  # v2 = 主读数选中集（v0.416）
        "unit": "R46",
        "families": {f: list(v) for f, v in FAMILIES.items()},
        "levels": list(LEVELS),
        "n_cells": len(GRID),
        "segments": {
            w: {"span": list(spans[w]), "n_candidates": len(segs[w]["trades"])}
            for w in segs
        },
        "base": {w: grid[BASE][w] for w in SEG_NAMES},
        "top": [
            {"cell": list(m), **grid[m]}
            for m in sorted(
                (m for m in grid if grid[m]["merged_delta"] is not None),
                key=lambda m: -grid[m]["merged_delta"],
            )[:20]
        ],
        "onoff_subgrid": onoff,
        "criteria": {"C1": c1, "C2": c2, "C3": c3, "C4": c4},
        "verdict": verdict,
        "judgment_batch": judgment_blk,
        "base_equals_live": _base_equals_live(),
        "provenance": pv.build(
            args,
            unit="R46",
            criteria_version="v0.416",
            pre_reg_doc=RESEARCH_DIR / "R46_v0_family_weight_lattice.md",
        ),
    }
    return rep


def _base_equals_live() -> bool:
    """基准格（DEFAULT_TECH_WEIGHTS × 1）是否等于 live 现行权重（registry 覆盖后）——
    False ⇒ 「基准 = live」的表述不成立，判读时须注明（审计 2 T11）。"""
    from custos.pipeline.screening import score_candidates as sc  # noqa: PLC0415

    try:
        live = sc.resolve_tech_weights(None)
    except Exception:  # noqa: BLE001
        return False
    return dict(live) == dict(
        sc.DEFAULT_TECH_WEIGHTS
    )  # 双向（审计 3 C-4：live 多键也算漂移）


def _judgment_read(args, collector, legs, gs, arms) -> dict[str, Any]:
    from custos.research import judgment_batch as jb  # noqa: PLC0415

    items = _segment_items(collector, "J", lw.SEG_JUDGMENT)
    seg = _prep_seg(items, legs)
    if not seg["trades"]:
        return {"verdict": "untested", "reason": "判定段 0 候选"}
    base = eval_grid(
        {"J": seg}, args.top_n, cells=[BASE] + ([gs] if gs and gs != BASE else [])
    )
    cand = base[gs]["J"]["delta"] if gs else None
    gate = bool(gs and base[gs]["J"]["rdd_gate"])
    pool = []
    for a in arms:
        if not a["cell"]:
            continue
        rng = random.Random(f"{args.seed}-pj{a['arm']}")
        ps = {"J": {**seg, "S": permute_clusters(seg["items"], seg["S"], rng)}}
        cell = tuple(a["cell"])
        r = eval_grid(ps, args.top_n, cells=[cell])[cell]["J"]
        if r["rdd_gate"] and r["delta"] is not None:
            pool.append(r["delta"])
    line = jb.judgment_line(args.batch_id, cand, pool, min_pool=args.c4_min_pool)
    n_taken = base[BASE]["J"]["n_taken"] or 0
    n_sel = base[BASE]["J"]["n_selected"] or 0
    if n_taken < MIN_N_TAKEN or n_sel < MIN_N_SELECTED:
        v = "untested"
    elif cand is None or cand <= 0 or not gate:
        v = "falsified"
    else:
        v = "candidate" if line["state"] == "confirmed_pass" else "provisional"
    return {
        "cand_delta": cand,
        "rdd_gate": gate,
        "pool": pool,
        "line": line,
        "verdict": v,
    }


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="R46 V0 家族级配比 729 格（预注册 R46）")
    ap.add_argument("--codes", default="", help="逗号分隔代码（缺省=全量 as-of）")
    ap.add_argument("--codes-file", default="", help="钉死宇宙文件（兼容通道）")
    ap.add_argument(
        "--mining-a-start",
        default=lw.SEG_MINING_A[0],
        help="挖掘 A 起点（= SEG_A_START，#91 回填；缺省 2000-01-01）",
    )
    ap.add_argument("--top-n", type=int, default=TOP_N)
    ap.add_argument("--cost-bps", type=float, default=25.0)
    ap.add_argument(
        "--n-random", type=int, default=N_RANDOM, help="簇级置换臂数（过门臂）"
    )
    ap.add_argument("--c4-min-pool", type=int, default=C4_MIN_POOL)
    ap.add_argument(
        "--tie-seeds", type=int, default=TIE_SEEDS, help="g* 稳健副读数平分种子数"
    )
    ap.add_argument("--seed", type=int, default=20261016)
    ap.add_argument("--out", default=str(LOGS / "family_lattice" / "r46.json"))
    ap.add_argument("--batch-id", default="", help="判定段批次号（攒批协议）")
    ap.add_argument("--candidate-id", default="", help="批内候选 id")
    ap.add_argument(
        "--print-config-hash", action="store_true", help="冻结入批：打印配置哈希后退出"
    )
    return ap


def main(
    argv: Optional[list[str]] = None, *, collector: Optional[Callable] = None
) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    from custos.research import judgment_batch as jb  # noqa: PLC0415

    ap = _build_parser()
    args = ap.parse_args(argv)
    if lw.mining_violation(args.mining_a_start, lw.SEG_MINING_A[1]):
        ap.error(
            f"--mining-a-start 非法: {lw.mining_violation(args.mining_a_start, lw.SEG_MINING_A[1])}"
        )
    if args.print_config_hash:
        return jb.print_config_hash_and_exit(args, "family_lattice_study")
    args.cmdline = " ".join(
        sys.argv if argv is None else ["family_lattice_study", *argv]
    )
    rep = run_study(args, collector=collector)
    if not rep:
        print("[FAIL] 挖掘段 0 候选——空结果不写产物", file=sys.stderr)
        return 2
    if args.batch_id:
        from custos.research import window_usage as wu  # noqa: PLC0415

        tag = Path(args.out).stem
        k = wu.record_use(
            "R46",
            "judgment_2017_2021",
            tag,
            "判定段读数（攒批）",
            synthetic=collector is not None,
        )
        if getattr(args, "_judgment_gated", False):
            jb.mark_read(
                args.batch_id, args.candidate_id, tool="family_lattice_study", tag=tag
            )
        rep["window_usage"] = {"window": "judgment_2017_2021", "k": k}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    c = rep["criteria"]
    print(
        f"[R46] g*={c['C2']['g_star']} 合并Δ={c['C2']['merged_delta']} C1={c['C1']['ok']} "
        f"C2={c['C2']['ok']} C3={c['C3']['ok']} C4={c['C4']['state']} ⇒ {rep['verdict']}"
    )
    print(f"[OK] 写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
