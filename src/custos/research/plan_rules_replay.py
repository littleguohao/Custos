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
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from custos.core.factors.b1_structure import STOP_LOOKBACK, _stop_ref  # noqa: E402
from custos.core.paths import LOGS, cn_now  # noqa: E402
from custos.research import factor_exit_study as fes  # noqa: E402
from custos.research import strategy_grid as sg  # noqa: E402
from custos.research import window_usage as wu  # noqa: E402
from custos.research.exit_campaign import _q95  # noqa: E402

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
    per_code: dict[str, dict],
    subset: list[dict],
    regime: dict[str, str],
    cost_bps: float,
    top_n: int,
    replay_fn: Optional[Callable[[list[dict], dict[str, Any]], list[dict]]] = None,
) -> dict[str, Any]:
    """两版重放+读数：现行版主/副读数（自参照）+ 计划版（参照=现行主读数）。

    base/plan 子集只差 sig 是否携带 stop_override（两版同剔保配对）。"""
    base = [dict(r) for r in subset]
    plan = [_sig_with_stop(r, r["plan_stop"]) for r in subset]

    def _trades(recs: list[dict], params: dict) -> list[dict]:
        if replay_fn is not None:
            return replay_fn(recs, params)
        return fes.replay_signals(per_code, recs, params, regime, cost_bps)

    live_trades = _trades(base, LIVE_PARAMS)
    live_rd = fes.combine_readings(live_trades, top_n, ref="self")
    plan_trades = _trades(plan, PLAN_PARAMS)
    plan_rd = fes.combine_readings(plan_trades, top_n, ref=live_rd)
    alt_trades = _trades(base, LIVE_ALT_PARAMS)
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


def run_study(
    args: Any,
    *,
    warm_fn: Optional[Callable[[str, str], dict[str, dict]]] = None,
    replay_fn: Optional[Callable[[list[dict], dict[str, Any]], list[dict]]] = None,
) -> dict[str, Any]:
    """双窗配对研究：attach → 两版读数 → C1~C4 → 四态结局。"""
    windows = {
        "mining": (args.mining_start, args.mining_end),
        "judgment": (args.judgment_start, args.judgment_end),
    }
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
        regime = bt.load_amv_regime(since=args.mining_start)
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
            )

    per_code = {w: warm_fn(*se) for w, se in windows.items()}
    subsets: dict[str, list[dict]] = {}
    accounting: dict[str, Any] = {}
    for w, pc in per_code.items():
        subsets[w], accounting[w] = attach_plan_stops(pc, STOP_LOOKBACK)
    if not subsets["mining"] or not subsets["judgment"]:
        raise RuntimeError(
            f"空结果护栏：剔除后子集 挖掘 {len(subsets['mining'])} / 判定 "
            f"{len(subsets['judgment'])} 信号——不落盘（宇宙/窗口/数据有问题？）"
        )

    pair_rds = {
        w: _replay_pair(pc, subsets[w], regime, args.cost_bps, args.top_n, replay_fn)
        for w, pc in per_code.items()
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
            for w, pc in per_code.items():
                sub_p, _acc = attach_plan_stops(pc, lookback_p)
                pair_p[w] = (
                    _replay_pair(
                        pc, sub_p, regime, args.cost_bps, args.top_n, replay_fn
                    )
                    if sub_p
                    else None
                )
            c2_p = judge_c2(pair_p) if all(pair_p.values()) else {"ok": False}
            c3_draws.append(
                {"draw": draw_i, "lookback": lookback_p, "c2_holds": c2_p["ok"]}
            )
    c3 = {
        "ok": bool(c3_draws) and all(d["c2_holds"] for d in c3_draws),
        "draws": c3_draws,
        "n_flips": sum(1 for d in c3_draws if not d["c2_holds"]),
        "rule": f"lookback=round(10×U(0.5,1.5)) ×{C3_DRAWS} 扰动 C2 零翻转"
        "（每次新子集两版重跑，配对重新对齐）",
    }

    # ── C4：随机止损价臂（预算对等=两边都不挑选；池元素=臂挖掘窗 Δmargin）──
    # v0.317：**N 指过门臂数**——过不了 rdd 门的臂不进池，「抽 N 次」实现下
    # 过门率 <100% 就永远 provisional（owner 零假设实测过门 ~20%）。改
    # **重抽直至过门臂满 N 或评估数达上限 max_arms=10×N**；上限仍未满 ⇒
    # 按当时池大小判 provisional/indeterminate，如实记过门率与评估数。
    # 统计含义不变：候选本身也须过门（C2），零假设=「同样过了门的随机
    # 臂」，两边条件对称——只改凑齐 N 的方式，不改判据。
    lo, hi = C4_STOP_RANGE
    max_arms = 10 * args.n_random
    pool: list[float] = []
    arm_evaluated = 0
    live_m = pair_rds["mining"]["live"]
    plan_m = pair_rds["mining"]["plan"]
    while len(pool) < args.n_random and arm_evaluated < max_arms:
        arm_rng = random.Random(f"{args.seed}-arm{arm_evaluated}")  # 臂级种子写死可复现
        arm_evaluated += 1
        arm_subset = [
            _sig_with_stop(r, r["entry_close"] * arm_rng.uniform(lo, hi))
            for r in subsets["mining"]
        ]
        arm_trades = (
            replay_fn(arm_subset, PLAN_PARAMS)
            if replay_fn is not None
            else fes.replay_signals(
                per_code["mining"], arm_subset, PLAN_PARAMS, regime, args.cost_bps
            )
        )
        arm_rd = fes.combine_readings(arm_trades, args.top_n, ref=live_m)
        if not arm_rd or not arm_rd.get("rdd_gate"):
            continue  # 过不了 rdd 门的臂不进池（记过门率）
        if (
            live_m
            and live_m.get("margin") is not None
            and arm_rd.get("margin") is not None
        ):
            pool.append(arm_rd["margin"] - live_m["margin"])
    arm_gate_pass = len(pool)
    q95_m = _q95(pool)
    plan_delta = (
        plan_m["margin"] - live_m["margin"]
        if plan_m
        and live_m
        and plan_m.get("margin") is not None
        and live_m.get("margin") is not None
        else None
    )
    if not pool:
        c4_state = "indeterminate"  # 池空=不放行（v0.297 族）
    elif len(pool) < args.c4_min_pool:
        c4_state = "provisional"
    elif plan_delta is not None and plan_delta > q95_m:
        c4_state = "confirmed_pass"
    else:
        c4_state = "confirmed_fail"
    c4 = {
        "state": c4_state,
        "pool": pool,
        "pool_size": len(pool),
        "target_pool": args.n_random,  # N 指过门臂数（v0.317）
        "max_arms": max_arms,
        "evaluated": arm_evaluated,
        "gate_pass": arm_gate_pass,
        "gate_pass_rate": (arm_gate_pass / arm_evaluated if arm_evaluated else None),
        "q95": q95_m,
        "plan_delta_mining": plan_delta,
        "min_pool": args.c4_min_pool,
        "note": "预算对等=两边都不挑选（v0.315 ④）：池元素=臂挖掘窗 Δmargin"
        "（每信号独立抽 entry×U[0.85,0.99]，臂级种子写死）；N 指过门臂数——"
        "重抽至池满或评估达上限（v0.317，统计含义不变：候选也须过门，"
        "两边条件对称）",
    }

    # ── 四态结局（C1 优先；C4 indeterminate/池未满 ⇒ provisional 不放行）──
    if not c1["ok"]:
        verdict = "untested"
    elif c2["ok"] is False or c3["ok"] is False or c4_state == "confirmed_fail":
        verdict = "falsified"
    elif c2["ok"] is True and c3["ok"] is True and c4_state == "confirmed_pass":
        verdict = "candidate"
    else:
        verdict = "provisional"  # C4 池未满/池空 indeterminate（不放行不判死）

    # 判定窗使用台账（v0.321，owner 方法论 review #1）：本报告=该窗第 k 次被读
    _wu_k = wu.record_use("R41", "judgment", args.tag, "C1~C4 判定窗读数")

    return {
        "schema": "plan_rules_replay/v1",
        "tag": args.tag,
        "verdict": verdict,
        "window_usage": {
            "window": "judgment",
            "k": _wu_k,
            "note": wu.usage_note("R41", "judgment", _wu_k),
        },
        "windows": {w: {"start": se[0], "end": se[1]} for w, se in windows.items()},
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
        "accounting": accounting,
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R41 持仓计划规则离线回放：计划止损 stop_loss_ref+计划止盈 vs 现行 EXIT_RULES 配对双窗"
    )
    ap.add_argument("--mining-start", required=True)
    ap.add_argument("--mining-end", required=True)
    ap.add_argument("--judgment-start", required=True)
    ap.add_argument("--judgment-end", required=True)
    ap.add_argument("--codes", default="")
    ap.add_argument("--codes-file", default="")
    ap.add_argument("--universe-sample", type=int, default=0)
    ap.add_argument("--universe-local", action="store_true")
    ap.add_argument("--universe-seed", type=int, default=42)
    ap.add_argument("--count", type=int, default=2000)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--cost-bps", type=float, default=COST_BPS_DEFAULT)
    ap.add_argument("--n-random", type=int, default=DEFAULT_N_RANDOM)
    ap.add_argument("--c4-min-pool", type=int, default=C4_MIN_POOL)
    ap.add_argument("--seed", type=int, default=20261009)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out-dir", default=str(LOGS))
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
    fes._check_windows(args, ap)  # pre2019 硬拒绝 + 双窗次序（同族单源）
    if warm_fn is None:  # 生产路径才做加载到达校验（注入路径无真实加载）
        from custos.research.exit_c5_terminal import check_reach  # noqa: PLC0415

        check_reach(args.count, args.mining_start)
    try:
        rep = run_study(args, warm_fn=warm_fn, replay_fn=replay_fn)
    except RuntimeError as exc:
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
