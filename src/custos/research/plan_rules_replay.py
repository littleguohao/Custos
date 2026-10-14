# -*- coding: utf-8 -*-
"""R41 持仓计划规则离线回放（预注册
``governance/research/R41_position_plan_rules_replay.md``，判据 v0.315 定稿）。

问法：同信号集、同执行语义下，**计划版**（止损=stop_loss_ref as-of 重算
+止盈=scale_out_two_bull）vs **现行版**（EXIT_RULES 启用三条）配对比较——
唯一变量是出场规则。计划止损触发=全额清仓（与影子判定 P0 一致）。

口径（v0.315 定稿写死，LLM 不碰数值）：
- **现行版主读数** = stop_mode="pct", stop_pct=10（只忠实对应 hard_loss；
  loss_reduction −7% 减仓 10~25% 引擎表达不了，注明）；**副读数**
  stop_pct=7（loss_reduction 当全额出场——只报告不作判据）；
- **两版共用写死**：scale_out_frac=0.5 / bbi_exit_consec=2 /
  stop_trigger="close" / cost_bps=25（止盈同一条规则同一个 frac，
  Δ 只来自止损）；
- **配对**：stop_loss_ref 缺失（n_missing）或 ≥ 入场收盘（n_stop_ge_entry
  ——引擎 `_initial_stop` 会静默回退 stop_mode，必须工具层先剔）的信号
  **两版同剔**；margin 在全候选交易（collect_all）上算 ⇒ 精确配对；
- **C2 含 rdd 相对门**：参照=同窗同信号现行版主读数，任一窗不过 ⇒
  C2 不过标 rdd_gate_fail（拿更差回撤收益比换的 margin 不算真提升）；
- **C4 预算对等=两边都不挑选**：随机臂 N=50，每臂给每个信号独立抽
  entry×U[0.85,0.99] 作 stop_override（臂级种子写死），池元素=臂挖掘窗
  Δmargin；臂不过 rdd 门不进池记过门率；**N 指过门臂数**（v0.317）——
  重抽至池满或评估达上限 max_arms=10×N，上限未满按当时池大小判
  provisional/indeterminate（统计含义不变：候选也须过门，两边条件
  对称）；池空=indeterminate 不放行；
- **C3 灵敏度**：lookback=round(10×U(0.5,1.5))×4（种子写死），每次扰动
  重算 stop→重新剔除→**两版都在新子集上重跑**（配对重新对齐）；
- 四态结局（次序同 R39/R40）：C1 不过=untested（优先）；C2/C3 False
  或 C4 confirmed_fail=falsified；全过+confirmed_pass=candidate；
  其余 provisional。

窗口布局（owner 2026-10-13 定案，TODO #90）：窗口四参全缺省=**v2 布局**——
挖掘=段并集 A（2000~2016）∪B（2022~2026-09-04），两段各 warm/段末 90 天
隔离带 clip（load_window 单源）后**合并一池**算挖掘读数（C1~C4 判据形态
不变）；判定=受保护段 2017~2021（唯此入台账 judgment_2017_2021）。四参全
显式=**legacy 单段**（R40 兼容通道：不 clip、判定窗已在挖掘 B 内 ⇒ 不登记；
段检查 + 双窗不相交取代旧次序规则）。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from custos.core.factors.b1_structure import STOP_LOOKBACK, _stop_ref  # noqa: E402
from custos.core.paths import LOGS, RESEARCH_DIR, cn_now  # noqa: E402
from custos.research import criteria_kit as kit  # noqa: E402
from custos.research import factor_exit_study as fes  # noqa: E402
from custos.research import load_window as lw  # noqa: E402
from custos.research import strategy_grid as sg  # noqa: E402
from custos.research import window_usage as wu  # noqa: E402

#: 口径常量（v0.315 定稿写死）
LIVE_PARAMS: dict[str, Any] = {
    "stop_mode": "pct",
    "stop_pct": 10,
    "scale_out_frac": 0.5,
    "bbi_exit_consec": 2,
    "stop_trigger": "close",
}
LIVE_ALT_PARAMS: dict[str, Any] = {**LIVE_PARAMS, "stop_pct": 7}  # 副读数不作判据
PLAN_PARAMS: dict[str, Any] = dict(LIVE_PARAMS)  # 计划版：止盈同现行版
MIN_N_TAKEN = 100  # C1：计划版每窗最小选中笔数
C3_DRAWS = 4
DEFAULT_N_RANDOM = 50
C4_MIN_POOL = 50
C4_STOP_RANGE = (0.85, 0.99)
COST_BPS_DEFAULT = 25.0


# ---------------------------------------------------------------------------
# stop_loss_ref as-of 附着与配对剔除
# ---------------------------------------------------------------------------


def attach_plan_stops(
    per_code: dict[str, dict], lookback: int = STOP_LOOKBACK
) -> tuple[list[dict], dict[str, Any]]:
    """逐信号 as-of 重算 stop_loss_ref → 子集（两版同剔保配对）+ 剔除记账。

    剔除：stop_ref 缺失（历史根数不足 ⇒ n_missing）/ stop_ref ≥ 入场收盘
    （n_stop_ge_entry——引擎会静默回退，必须工具层剔）；缺 V0 分的信号
    同剔（replay 下游本来丢，配对口径在这里一次对齐）。返回的子集 rec
    带 plan_stop 与 entry_close（止损距离分位数与随机臂抽样用）。
    """
    subset: list[dict] = []
    n_signals = 0
    n_missing = 0
    n_stop_ge_entry = 0
    n_score_missing = 0
    for code, pack in per_code.items():
        df = pack["df"]
        closes = df["close"].astype(float).tolist()
        for sig in pack["signals"]:
            n_signals += 1
            i = sig["i"]
            stop = _stop_ref(df.iloc[: i + 1], lookback)
            if stop is None:
                n_missing += 1
                continue
            entry_close = float(closes[i])
            if stop >= entry_close:
                n_stop_ge_entry += 1
                continue
            score = pack["scores"].get(sig["date"])
            if score is None:
                n_score_missing += 1
                continue
            subset.append(
                {
                    "code": code,
                    "date": sig["date"],
                    "i": i,
                    "sig": sig,
                    "score": score,
                    "plan_stop": stop,
                    "entry_close": entry_close,
                }
            )
    dists = sorted(r["entry_close"] / r["plan_stop"] - 1.0 for r in subset)
    accounting: dict[str, Any] = {
        "n_signals": n_signals,
        "n_missing": n_missing,
        "n_stop_ge_entry": n_stop_ge_entry,
        "n_score_missing": n_score_missing,
        "n_subset": len(subset),
        "stop_distance_quantiles": {
            q: (dists[min(len(dists) - 1, int(len(dists) * p))] if dists else None)
            for q, p in (
                ("p5", 0.05),
                ("p25", 0.25),
                ("p50", 0.50),
                ("p75", 0.75),
                ("p95", 0.95),
            )
        },
    }
    return subset, accounting


def _sig_with_stop(rec: dict, stop: float) -> dict:
    """replay 子集 rec：sig 显式携带 stop_override（引擎钩子 R41）。"""
    return {**rec, "sig": {**rec["sig"], "stop_override": stop}}


# ---------------------------------------------------------------------------
# 读数与判据
# ---------------------------------------------------------------------------


def _replay_pair(
    groups: list[dict[str, Any]],
    regime: dict[str, str],
    cost_bps: float,
    top_n: int,
    replay_fn: Optional[Callable[[list[dict], dict[str, Any]], list[dict]]] = None,
) -> dict[str, Any]:
    """两版重放+读数：现行版主/副读数（自参照）+ 计划版（参照=现行主读数）。

    ``groups`` = 信号来源段清单（各带 per_code/subset）：v2 挖掘=A∪B 两段
    **合并一池**——逐段重放后按段序拼接成同一交易集再算读数（各段 per_code
    的 df 索引口径不同，不能合并 per_code 单次重放）；legacy 恒为单段。
    base/plan 子集只差 sig 是否携带 stop_override（两版同剔保配对）。"""

    def _trades(
        build: Callable[[dict[str, Any]], list[dict]], params: dict
    ) -> list[dict]:
        out: list[dict] = []
        for g in groups:
            recs = build(g)
            if not recs:
                continue
            if replay_fn is not None:
                out.extend(replay_fn(recs, params))
            else:
                out.extend(
                    fes.replay_signals(g["per_code"], recs, params, regime, cost_bps)
                )
        return out

    live_trades = _trades(lambda g: [dict(r) for r in g["subset"]], LIVE_PARAMS)
    live_rd = fes.combine_readings(live_trades, top_n, ref="self")
    plan_trades = _trades(
        lambda g: [_sig_with_stop(r, r["plan_stop"]) for r in g["subset"]],
        PLAN_PARAMS,
    )
    plan_rd = fes.combine_readings(plan_trades, top_n, ref=live_rd)
    alt_trades = _trades(lambda g: [dict(r) for r in g["subset"]], LIVE_ALT_PARAMS)
    alt_rd = fes.combine_readings(alt_trades, top_n, ref="self")
    return {
        "live": live_rd,
        "plan": plan_rd,
        "live_alt_pct7": alt_rd,
        "live_trades": live_trades,
        "plan_trades": plan_trades,
    }


def judge_c1(plan_rds: dict[str, Any]) -> dict[str, Any]:
    """C1：计划版双窗 n_taken 各 ≥100——不过 ⇒ untested（优先于其他判决）。"""
    per = {w: (rd.get("n_taken") if rd else None) for w, rd in plan_rds.items()}
    ok = all(v is not None and v >= MIN_N_TAKEN for v in per.values())
    return {
        "ok": ok,
        "n_taken": per,
        "rule": f"计划版双窗 n_taken ≥ {MIN_N_TAKEN}（不过=untested）",
    }


def judge_c2(pair_rds: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """C2：Δmargin（计划−现行主读数）双窗 > 0 且双窗过 rdd 相对门
    （任一窗门不过 ⇒ C2 False 标 rdd_gate_fail，v0.315 ③）。"""
    per: dict[str, Any] = {}
    ok = True
    gate_fail: list[str] = []
    for w, pair in pair_rds.items():
        live, plan = pair["live"], pair["plan"]
        delta = (
            plan["margin"] - live["margin"]
            if live
            and plan
            and live.get("margin") is not None
            and plan.get("margin") is not None
            else None
        )
        gate = bool(plan and plan.get("rdd_gate"))
        if not gate:
            gate_fail.append(w)
        win_ok = delta is not None and delta > 0 and gate
        ok = ok and win_ok
        per[w] = {"delta_margin": delta, "rdd_gate": gate, "ok": win_ok}
    return {
        "ok": ok,
        "windows": per,
        "rdd_gate_fail": gate_fail or None,
        "rule": "Δmargin 双窗同向为正 且 双窗过 rdd 相对门（参照=同窗现行版主读数）",
    }


def _paired_delta_median(pair_rds: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """副读数：逐笔 Δret（计划−现行，按 (code, entry_date) 配对）中位数与符号。"""
    out: dict[str, Any] = {}
    for w, pair in pair_rds.items():
        live_map = {
            (t.get("code"), t.get("entry_date")): t.get("ret")
            for t in pair["live_trades"]
        }
        deltas = [
            t.get("ret") - live_map[(t.get("code"), t.get("entry_date"))]
            for t in pair["plan_trades"]
            if (t.get("code"), t.get("entry_date")) in live_map
            and t.get("ret") is not None
            and live_map[(t.get("code"), t.get("entry_date"))] is not None
        ]
        if not deltas:
            out[w] = {"n": 0}
            continue
        srt = sorted(deltas)
        mid = len(srt) // 2
        median = srt[mid] if len(srt) % 2 else (srt[mid - 1] + srt[mid]) / 2
        out[w] = {
            "n": len(deltas),
            "median": median,
            "n_pos": sum(1 for d in deltas if d > 0),
            "n_neg": sum(1 for d in deltas if d < 0),
        }
    return out


# ---------------------------------------------------------------------------
# 主研究
# ---------------------------------------------------------------------------


def _layout_of(args: Any) -> tuple[str, list[tuple[str, str, str]], tuple[str, str]]:
    """窗口四参 ⇒ (layout, [(挖掘段名, start, end)…], (判定 start, end))。

    全 None ⇒ **v2 布局**（挖掘=段并集 A∪B，判定=受保护段 2017~2021——
    load_window 段常量单源）；四个都显式 ⇒ **legacy 单段**（R40 兼容通道，
    段内子窗）；只给一部分 ⇒ ValueError（不许半截布局，TODO #90）。
    """
    quad = (args.mining_start, args.mining_end, args.judgment_start, args.judgment_end)
    n_given = sum(v is not None for v in quad)
    if n_given == 0:
        return (
            "v2",
            [("mining_a", *lw.SEG_MINING_A), ("mining_b", *lw.SEG_MINING_B)],
            lw.SEG_JUDGMENT,
        )
    if n_given == 4:
        return (
            "legacy",
            [("mining", str(args.mining_start), str(args.mining_end))],
            (str(args.judgment_start), str(args.judgment_end)),
        )
    raise ValueError(
        "窗口四参（--mining-start/end 与 --judgment-start/end）须全给或全不给"
        "（全不给=v2 布局：挖掘 A∪B / 判定 2017~2021）——不许半截布局（TODO #90）"
    )


def _check_windows(args: Any, ap: argparse.ArgumentParser) -> None:
    """legacy 显式四窗校验：逐窗段检查（load_window.segment_violation 单源——
    越段/触 holdout 硬拒绝）+ 判定窗∩挖掘窗**不相交**（取代旧
    mining_end<judgment_start 次序规则：v2 判定段天然夹在挖掘 A/B 之间，
    旧规则作废，owner 2026-10-13 定案 TODO #90）。v2 缺省布局=段常量，天然合法。"""
    try:
        layout, mining_segs, judgment = _layout_of(args)
    except ValueError as exc:
        ap.error(str(exc))
    if layout == "v2":
        return
    _, ms, me = mining_segs[0]
    js, je = judgment
    for wname, s, e in (("mining", ms, me), ("judgment", js, je)):
        msg = (
            lw.mining_violation(s, e)
            if wname == "mining"
            else lw.segment_violation(s, e)
        )
        if msg:
            ap.error(f"{wname} {msg}")
    if not (me < js or je < ms):
        ap.error(
            f"挖掘窗 {ms}~{me} 与判定窗 {js}~{je} 相交——双窗硬隔离"
            "（v2 口径：不相交即可，不再要求 mining_end < judgment_start 次序）"
        )


def run_study(
    args: Any,
    *,
    warm_fn: Optional[Callable[[str, str], dict[str, dict]]] = None,
    replay_fn: Optional[Callable[[list[dict], dict[str, Any]], list[dict]]] = None,
) -> dict[str, Any]:
    """双窗配对研究：attach → 两版读数 → C1~C4 → 四态结局。

    v2 布局：挖掘 A/B 两段各 warm、段末隔离带 clip（replay/判据之前，剔除数
    入报告 segments 块）后 A∪B **合并一池**算挖掘读数，判定段单独读数；
    legacy：单段挖掘窗不 clip（R40 兼容通道，行为与切换前逐位一致）。
    """
    # 合成守卫在入口快照：下方生产分支会把 warm_fn 重绑成真实闭包
    # （noqa: F811），事后再判恒 True ⇒ 生产跑被误判合成、判定窗读取
    # 不入台账（r42_probe 事故根因，v0.338 排查的实锤答案）。
    injected = warm_fn is not None or replay_fn is not None
    layout, mining_segs, judgment_span = _layout_of(args)
    # 判定段批次锁（v0.374，TODO #95）：数据加载之前硬门——合成运行（注入件
    # 非 None）/legacy 窗不查；v2 缺省窗生产方向必须带合法批次
    from custos.research import judgment_batch as jb  # noqa: PLC0415

    jb.gate_judgment_read(
        args,
        _build_parser(),
        synthetic=injected,
        judgment_span=judgment_span,
        tool="plan_rules_replay",
    )
    earliest_start = min(s for _name, s, _e in mining_segs)
    regime: dict[str, str] = {}
    if warm_fn is None:
        from custos.research import backtest_factors as bt  # noqa: PLC0415
        from custos.research import exit_campaign as ec  # noqa: PLC0415
        from custos.research import score_return_study as srs  # noqa: PLC0415

        ns = argparse.Namespace(
            codes_file=args.codes_file,
            universe_local=args.universe_local,
            universe_sample=args.universe_sample,
            seed=args.universe_seed,
            codes=args.codes,
        )
        codes = bt._resolve_universe(ns, _build_parser())
        # 宇宙三件套回塞 CLI args：provenance.build 优先读它（v0.356 口径）
        args.universe_meta = getattr(ns, "universe_meta", None)
        regime = bt.load_amv_regime(since=earliest_start)
        if not regime:
            raise RuntimeError("0AMV regime 读不到（compass_amv）——本机无数据？")
        from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

        index_df = (
            local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
            .sort_values("date")
            .reset_index(drop=True)
        )

        def warm_fn(start: str, end: str) -> dict[str, dict]:  # noqa: F811
            return ec.warm_v0_signals(
                codes,
                regime,
                index_df,
                count=args.count,
                cost_bps=args.cost_bps,
                start=start,
                end=end,
                # v2 段前预热 300 根（v0.359，owner review #3）；legacy 0 根
                warmup_bars=300 if layout == "v2" else 0,
            )

    # ── 逐段 warm →（v2）段末隔离带 clip → attach 配对剔除 ──
    segments: dict[str, dict[str, Any]] = {}
    for name, s, e in [*mining_segs, ("judgment", *judgment_span)]:
        pc = warm_fn(s, e)
        n_signals = sum(len(pack["signals"]) for pack in pc.values())
        n_clipped = 0
        if layout == "v2":
            # 段末 90 天禁开仓隔离带（load_window.clip_entries 单源；段首不滤——
            # 指标预热只读价格，定案豁免）。只属 v2 段边界：legacy 兼容通道
            # 不 clip（R40 已跑口径，逐位保真）。clip 出副本不动注入夹具。
            clipped: dict[str, dict] = {}
            for code, pack in pc.items():
                sigs = lw.clip_entries(pack["signals"], e, key="date")
                n_clipped += len(pack["signals"]) - len(sigs)
                clipped[code] = {**pack, "signals": sigs}
            pc = clipped
        subset, acc = attach_plan_stops(pc, STOP_LOOKBACK)
        segments[name] = {
            "span": (s, e),
            "per_code": pc,
            "subset": subset,
            "accounting": acc,
            "n_signals": n_signals,
            "n_clipped": n_clipped,
        }

    mining_groups = [segments[name] for name, _s, _e in mining_segs]
    logical_groups = {"mining": mining_groups, "judgment": [segments["judgment"]]}
    mining_subset = [r for g in mining_groups for r in g["subset"]]
    if not mining_subset or not segments["judgment"]["subset"]:
        raise RuntimeError(
            f"空结果护栏：剔除后子集 挖掘 {len(mining_subset)} / 判定 "
            f"{len(segments['judgment']['subset'])} 信号——不落盘（宇宙/窗口/数据有问题？）"
        )

    pair_rds = {
        w: _replay_pair(groups, regime, args.cost_bps, args.top_n, replay_fn)
        for w, groups in logical_groups.items()
    }
    c1 = judge_c1({w: p["plan"] for w, p in pair_rds.items()})
    c2 = judge_c2(pair_rds)
    c2_detail = _paired_delta_median(pair_rds)

    # ── C3：lookback ±50% ×4 零翻转（每次新子集两版重跑，配对重新对齐）──
    rng = random.Random(args.seed)
    c3_draws: list[dict] = []
    if c1["ok"]:
        for draw_i in range(C3_DRAWS):
            lookback_p = max(1, round(STOP_LOOKBACK * rng.uniform(0.5, 1.5)))
            pair_p: dict[str, Any] = {}
            for w, groups in logical_groups.items():
                groups_p: list[dict[str, Any]] = []
                for g in groups:
                    sub_p, _acc = attach_plan_stops(g["per_code"], lookback_p)
                    groups_p.append({**g, "subset": sub_p})
                pair_p[w] = (
                    _replay_pair(groups_p, regime, args.cost_bps, args.top_n, replay_fn)
                    if any(gp["subset"] for gp in groups_p)
                    else None
                )
            c2_p = judge_c2(pair_p) if all(pair_p.values()) else {"ok": False}
            c3_draws.append(
                {"draw": draw_i, "lookback": lookback_p, "c2_holds": c2_p["ok"]}
            )
    c3: dict[str, Any] = {
        "ok": bool(c3_draws) and all(d["c2_holds"] for d in c3_draws),
        "draws": c3_draws,
        "n_flips": sum(1 for d in c3_draws if not d["c2_holds"]),
        "rule": f"lookback=round(10×U(0.5,1.5)) ×{C3_DRAWS} 扰动 C2 零翻转"
        "（每次新子集两版重跑，配对重新对齐）",
    }

    # ── C4：随机止损价臂（预算对等=两边都不挑选；池元素=臂挖掘窗 Δmargin）──
    # v0.317：**N 指过门臂数**（重抽至池满或评估上限——过不了 rdd 门的臂
    # 不进池；统计含义不变：候选同须过门，两边条件对称）；v0.322：池构造
    # 与状态机 = **criteria_kit 单一来源**（owner 方法论 review #7）。
    lo, hi = C4_STOP_RANGE
    live_m = pair_rds["mining"]["live"]
    plan_m = pair_rds["mining"]["plan"]

    def _arm(i: int) -> Optional[float]:
        arm_rng = random.Random(f"{args.seed}-arm{i}")  # 臂级种子写死可复现
        arm_trades: list[dict] = []
        for g in mining_groups:
            arm_recs = [
                _sig_with_stop(r, r["entry_close"] * arm_rng.uniform(lo, hi))
                for r in g["subset"]
            ]
            if not arm_recs:
                continue
            arm_trades.extend(
                replay_fn(arm_recs, PLAN_PARAMS)
                if replay_fn is not None
                else fes.replay_signals(
                    g["per_code"], arm_recs, PLAN_PARAMS, regime, args.cost_bps
                )
            )
        arm_rd = fes.combine_readings(arm_trades, args.top_n, ref=live_m)
        if not arm_rd or not arm_rd.get("rdd_gate"):
            return None  # 过不了 rdd 门的臂不进池（记过门率）
        if not (
            live_m
            and live_m.get("margin") is not None
            and arm_rd.get("margin") is not None
        ):
            return None
        return arm_rd["margin"] - live_m["margin"]

    c4_pool = kit.assemble_c4_pool(args.n_random, _arm)
    pool = c4_pool["pool"]
    q95_m = kit.q95(pool)
    plan_delta = (
        plan_m["margin"] - live_m["margin"]
        if plan_m
        and live_m
        and plan_m.get("margin") is not None
        and live_m.get("margin") is not None
        else None
    )
    c4_state = kit.c4_state_of(pool, min_pool=args.c4_min_pool, plan_delta=plan_delta)
    c4 = {
        "state": c4_state,
        "pool": pool,
        "pool_size": len(pool),
        "target_pool": c4_pool["target_pool"],  # N 指过门臂数（v0.317）
        "max_arms": c4_pool["max_arms"],
        "evaluated": c4_pool["evaluated"],
        "gate_pass": c4_pool["gate_pass"],
        "gate_pass_rate": c4_pool["gate_pass_rate"],
        "q95": q95_m,
        "plan_delta_mining": plan_delta,
        "min_pool": args.c4_min_pool,
        "note": "预算对等=两边都不挑选（v0.315 ④）：池元素=臂挖掘窗 Δmargin"
        "（每信号独立抽 entry×U[0.85,0.99]，臂级种子写死）；N 指过门臂数——"
        "重抽至池满或评估达上限（v0.317；池构造=criteria_kit 单源 v0.322）",
    }

    # ── 四态结局（criteria_kit 单源：C1 优先；C4 池未满/池空 ⇒ provisional）──
    verdict = kit.verdict_four_state(
        c1_ok=c1["ok"], c2_ok=c2["ok"], c3_ok=c3["ok"], c4_state=c4_state
    )

    # ── 成本副读数（owner review #6）：两版交易集逐窗 50bps 解析双报 ──
    from custos.research.cost_sensitivity import cost_side_block  # noqa: PLC0415

    cost_sens = cost_side_block(
        {
            name: pair_rds[w][key]
            for w in logical_groups
            for name, key in (
                (f"plan_{w}", "plan_trades"),
                (f"live_{w}", "live_trades"),
            )
        },
        base_bps=args.cost_bps,
        deltas={f"d_margin_{w}": (f"plan_{w}", f"live_{w}") for w in logical_groups},
    )

    # 判定窗台账 v2 口径（TODO #90，同 exit_campaign）：只有判定窗=受保护段
    # （2017~2021）才登记——legacy 判定窗已并入挖掘 B，读挖掘段不入台账
    # （挖掘数据自由可读）；冻结键 judgment/pre2019 永不新写。
    _is_protected_judgment = lw.touches_judgment(*judgment_span)
    _wu_k = (
        wu.record_use(
            "R41",
            "judgment_2017_2021",
            args.tag,
            "C1~C4 判定窗读数",
            synthetic=injected,
        )
        if _is_protected_judgment
        else None
    )
    if getattr(args, "_judgment_gated", False):
        # 批次锁过闸（v0.374）⇒ 登记读完（末位候选读完自动关批）
        jb.mark_read(
            args.batch_id, args.candidate_id, tool="plan_rules_replay", tag=args.tag
        )
    from custos.research import provenance as pv  # noqa: PLC0415

    rep: dict[str, Any] = {
        "schema": "plan_rules_replay/v1",
        "tag": args.tag,
        "verdict": verdict,
        "window_usage": {
            "window": (
                "judgment_2017_2021" if _is_protected_judgment else "mining_segment"
            ),
            "k": _wu_k,
            "note": (
                wu.usage_note("R41", "judgment_2017_2021", _wu_k)
                if _is_protected_judgment
                else "判定窗在挖掘段内——不入台账（v2 口径）"
            ),
        },
        "provenance": pv.build(
            args,
            unit="R41",
            criteria_version="v0.315/v0.317",
            pre_reg_doc=RESEARCH_DIR / "R41_position_plan_rules_replay.md",
            data_last_date=max(
                filter(
                    None,
                    (pv.last_date_of(seg["per_code"]) for seg in segments.values()),
                ),
                default=None,
            ),
        ),
        "windows": {
            "mining": {"start": mining_segs[0][1], "end": mining_segs[-1][2]},
            "judgment": {"start": judgment_span[0], "end": judgment_span[1]},
        },
        "params": {
            "live_main": LIVE_PARAMS,
            "live_alt_pct7": LIVE_ALT_PARAMS,
            "plan": PLAN_PARAMS,
            "lookback": STOP_LOOKBACK,
            "cost_bps": args.cost_bps,
            "top_n": args.top_n,
            "note": "现行版主读数 pct10 忠实 hard_loss（loss_reduction −7% 减仓"
            "引擎表达不了，v0.315 ① 注明）；副读数 pct7 只报告不作判据；"
            "两版 scale_out_frac=0.5 共用——Δ 只来自止损；⚠️ LIVE_PARAMS "
            "未合并 exit_genome.FIXED_PARAMS（两版同用 evaluate_trades 默认值"
            "——配对内部口径一致；与 R37/R39 基准档非逐位相同，横向对比前需"
            "统一，v0.317 注明）",
        },
        "accounting": {name: seg["accounting"] for name, seg in segments.items()},
        "cost_sensitivity": cost_sens,
        "forward_holdout_note": lw.EXIT_BARS_HOLDOUT_NOTE,
        "readings": {
            w: {
                "live": p["live"],
                "plan": p["plan"],
                "live_alt_pct7": p["live_alt_pct7"],
            }
            for w, p in pair_rds.items()
        },
        "criteria": {
            "C1": c1,
            "C2": {**c2, "paired_delta": c2_detail},
            "C3": c3,
            "C4": c4,
            "rule_note": "R41-C1~C4 跑数前写死（v0.315 口径定稿）；C5=pre2019 "
            "单独终步只能杀，C2~C4 全过才启动（owner 拍板发令）",
        },
        "objective_version": sg.OBJECTIVE_VERSION,
        "generated_at": cn_now().isoformat(timespec="seconds"),
    }
    if layout == "v2":
        rep["layout"] = "v2"
        rep["segments"] = {
            **{
                name: {
                    "start": seg["span"][0],
                    "end": seg["span"][1],
                    "n_signals": seg["n_signals"],
                    "n_clipped": seg["n_clipped"],
                }
                for name, seg in segments.items()
            },
            "mining_total": {
                "n_signals": sum(g["n_signals"] for g in mining_groups),
                "n_clipped": sum(g["n_clipped"] for g in mining_groups),
                "note": "挖掘读数=A∪B 合并一池（判定段独立；段末 90 天隔离带禁开仓）",
            },
        }
    return rep


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R41 持仓计划规则离线回放：计划止损 stop_loss_ref+计划止盈 vs 现行 EXIT_RULES 配对双窗"
    )
    ap.add_argument(
        "--mining-start",
        default=None,
        help="挖掘窗起点（四窗参全缺省 ⇒ v2 布局：挖掘 A 2000~2016 ∪ B "
        "2022~2026-09-04 合并池 / 判定 2017~2021；全显式=legacy 单段）",
    )
    ap.add_argument("--mining-end", default=None)
    ap.add_argument("--judgment-start", default=None)
    ap.add_argument("--judgment-end", default=None)
    ap.add_argument("--codes", default="")
    ap.add_argument("--codes-file", default="")
    ap.add_argument("--universe-sample", type=int, default=0)
    ap.add_argument("--universe-local", action="store_true")
    ap.add_argument("--universe-seed", type=int, default=42)
    ap.add_argument(
        "--count",
        type=int,
        default=None,
        help="每股回溯 K 线根数（缺省=按最早挖掘段起点自动推算，显式值覆盖）",
    )
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--cost-bps", type=float, default=COST_BPS_DEFAULT)
    ap.add_argument("--n-random", type=int, default=DEFAULT_N_RANDOM)
    ap.add_argument("--c4-min-pool", type=int, default=C4_MIN_POOL)
    ap.add_argument("--seed", type=int, default=20261009)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out-dir", default=str(LOGS))
    # 判定段批次锁（v0.374，TODO #95）：读判定段 2017~2021 须带批次身份
    ap.add_argument("--batch-id", default="", help="判定段批次号（攒批协议）")
    ap.add_argument("--candidate-id", default="", help="批内候选 id")
    ap.add_argument(
        "--print-config-hash",
        action="store_true",
        help="只打印 <tool>:<配置哈希> 并退出（冻结入批流程；不碰数据）",
    )
    return ap


def main(
    argv: Optional[list[str]] = None,
    *,
    warm_fn: Optional[Callable[[str, str], dict[str, dict]]] = None,
    replay_fn: Optional[Callable[[list[dict], dict[str, Any]], list[dict]]] = None,
) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    ap = _build_parser()
    args = ap.parse_args(argv)
    args.cmdline = " ".join(argv) if argv is not None else " ".join(sys.argv[1:])
    _check_windows(args, ap)  # legacy 显式四窗：段检查 + 双窗不相交（v2 口径）
    if warm_fn is None:  # 生产路径才做加载到达校验（注入路径无真实加载）
        from custos.research.exit_c5_terminal import check_reach  # noqa: PLC0415
        from custos.research.load_window import resolve_count  # noqa: PLC0415

    try:
        if warm_fn is None:
            # --count 缺省按最早挖掘段起点推算（v2=2000-01-01 全历史量级）
            earliest = min(s for _n, s, _e in _layout_of(args)[1])
            args.count = resolve_count(  # v0.328 缺省自动推算
                args.count, earliest
            )
            check_reach(args.count, earliest)
        if args.print_config_hash:
            # 冻结入批流程（TODO #95）：哈希必须与批次门同状态（参数归一化之后）
            from custos.research import judgment_batch as jb  # noqa: PLC0415

            return jb.print_config_hash_and_exit(args, "plan_rules_replay")
        rep = run_study(args, warm_fn=warm_fn, replay_fn=replay_fn)
    except (RuntimeError, ValueError) as exc:
        print(f"[plan_rules_replay] {exc}", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir) / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"_plan_rules_replay__{args.tag}.json"
    out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8"
    )
    c = rep["criteria"]
    print(
        f"[plan_rules_replay] verdict={rep['verdict']} C1={c['C1']['ok']} "
        f"C2={c['C2']['ok']} C3={c['C3']['ok']} C4={c['C4']['state']} ⇒ {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
