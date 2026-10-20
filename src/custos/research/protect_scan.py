# -*- coding: utf-8 -*-
"""R47 浮盈保护扫描（v0.434，TODO #114；预注册 governance/research/R47_profit_protection_breakeven_trail.md）。

入口：``plan_rules_replay --scan-protect``（只读挖掘 A∪B，判定段不读、批次门不触发）。
保本触发 × 移动止损回撤 4×4 网格叠加现行出场 ``LIVE_PARAMS``；参照格 (0,0) = 现行版本体（自参照门恒真）。

判据（跑数前写死，R47 判据节）：
- C1：两窗参照格组合 n_taken ≥ 100；
- C2：g* = 两窗合并 margin 最高且合并过 rdd 门的非参照格；g* 两窗各自 Δmargin > 0 且过门；
- C3：①相邻格（两轴各 ±1 档，越界不计；参照格 Δ≡0 不计入，定稿修订 v0.434）≥ 2/3 两窗 Δmargin > 0；
  ②右尾护栏：g* 两窗「单笔 ≥ +20%」笔数 ≥ 参照格同窗 × 0.8；
- C4：随机参数臂（每臂 15 组固定参数 be~U[3%,15%]、trail~U[5%,20%]，取合并 Δ 的 max）N=过门臂数，``kit.q95``。
"""

from __future__ import annotations

import random
from typing import Any, Callable, Optional

BE_GRID = (0.0, 0.05, 0.08, 0.12)
TRAIL_GRID = (0.0, 0.08, 0.12, 0.15)
REF_CELL = (0.0, 0.0)
BIG_WIN = 0.20
TAIL_KEEP = 0.8
C3_FRAC = 2 / 3
GIVE_BACK_MFE = 0.05
C4_ARM_DRAWS = 15  # = 非参照格数（同预算）
C4_BE_RANGE = (0.03, 0.15)
C4_TRAIL_RANGE = (0.05, 0.20)


def cell_key(be: float, tr: float) -> str:
    return f"be{be * 100:g}_tr{tr * 100:g}"


CELLS: list[tuple[float, float]] = [(b, t) for b in BE_GRID for t in TRAIL_GRID]


def cell_params(be: float, tr: float) -> dict[str, Any]:
    from custos.research.plan_rules_replay import LIVE_PARAMS  # noqa: PLC0415

    return {**LIVE_PARAMS, "breakeven_trigger": float(be), "trail_pct": float(tr)}


def _realized(trades: list[dict]) -> list[dict]:
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    return [t for t in trades if not bt.is_unrealized_reason(t.get("reason"))]


def _mfe_of(t: dict, groups: list[dict]) -> Optional[float]:
    """持有期最大浮盈（入场收盘 → 出场日最高价；无 high 列退 close）。df 缺失 ⇒ None。"""
    for g in groups:
        pack = (g.get("per_code") or {}).get(t.get("code"))
        if not pack or "df" not in pack:
            continue
        df = pack["df"]
        ds = df["date"].astype(str).str[:10]
        m = (ds >= str(t["entry_date"])[:10]) & (ds <= str(t["exit_date"])[:10])
        seg = df[m]
        if not len(seg):
            continue
        entry = float(seg["close"].iloc[0])
        hi = seg["high"] if "high" in seg else seg["close"]
        return float(hi.iloc[1:].max()) / entry - 1 if len(seg) > 1 and entry else 0.0
    return None


def tail_reading(
    trades: list[dict], groups: list[dict], with_give_back: bool
) -> dict[str, Any]:
    """右尾 + 浮盈回吐（R47 新增读数）：≥+20% 笔数；浮盈 ≥5% 仍亏损离场占比（只在需要时算，较慢）。"""
    tr = _realized(trades)
    out: dict[str, Any] = {
        "n_big_win": sum(1 for t in tr if float(t["ret"]) >= BIG_WIN)
    }
    if with_give_back and tr:
        gb = known = 0
        for t in tr:
            mfe = _mfe_of(t, groups)
            if mfe is None:
                continue
            known += 1
            gb += int(mfe >= GIVE_BACK_MFE and float(t["ret"]) <= 0)
        out["give_back_share"] = gb / known if known else None
        out["n_give_back"] = gb
    return out


def run_protect_scan(
    args: Any,
    *,
    warm_fn: Optional[Callable[[str, str], dict[str, dict]]] = None,
    replay_fn: Optional[Callable[[list[dict], dict[str, Any]], list[dict]]] = None,
) -> dict[str, Any]:
    from custos.research import criteria_kit as kit  # noqa: PLC0415
    from custos.research import factor_exit_study as fes  # noqa: PLC0415
    from custos.research import plan_rules_replay as prr  # noqa: PLC0415
    from custos.research import trade_dist as td  # noqa: PLC0415

    mining_segs, segments, groups, regime = prr._scan_prepare(args, warm_fn)
    win_names = [name for name, _s, _e in mining_segs]
    dist_on = bool(getattr(args, "trade_dist", False))

    def replay(gs: list[dict], params: dict[str, Any]) -> list[dict]:
        return prr._replay_width(
            gs, prr._no_stop, params, regime, args.cost_bps, replay_fn
        )[0]

    readings: dict[str, dict[str, Any]] = {}
    all_trades: dict[str, list[dict]] = {cell_key(*c): [] for c in CELLS}
    refs: dict[str, Any] = {}
    for name, g in zip(win_names, groups):
        blk: dict[str, Any] = {}
        ref_tr = replay([g], cell_params(*REF_CELL))
        refs[name] = fes.combine_readings(ref_tr, args.top_n, ref="self")
        for c in CELLS:
            k = cell_key(*c)
            tr = ref_tr if c == REF_CELL else replay([g], cell_params(*c))
            rd = prr._width_reading(
                tr, args.top_n, "self" if c == REF_CELL else refs[name]
            )
            if rd is not None:
                rd.update(tail_reading(tr, [g], with_give_back=True))
                if dist_on:
                    rd["dist"] = td.describe(tr, None if c == REF_CELL else ref_tr)
            blk[k] = rd
            all_trades[k].extend(tr)
        readings[name] = blk
    ref_k = cell_key(*REF_CELL)
    ref_m = fes.combine_readings(all_trades[ref_k], args.top_n, ref="self")
    merged: dict[str, Any] = {}
    for c in CELLS:
        k = cell_key(*c)
        rd = prr._width_reading(
            all_trades[k], args.top_n, "self" if c == REF_CELL else ref_m
        )
        if rd is not None:
            rd.update(tail_reading(all_trades[k], groups, with_give_back=False))
        merged[k] = rd

    def margin(blk: Any) -> Optional[float]:
        return (blk or {}).get("margin")

    def delta(w: str, k: str) -> Optional[float]:
        a, b = margin(readings[w].get(k)), margin(readings[w].get(ref_k))
        return a - b if a is not None and b is not None else None

    # C1
    n_ref = {w: (readings[w].get(ref_k) or {}).get("n_taken") for w in win_names}
    c1: dict[str, Any] = {
        "ok": all(v is not None and v >= prr.MIN_N_TAKEN for v in n_ref.values()),
        "n_taken": n_ref,
        "rule": "两窗参照格组合 n_taken ≥ 100（不过=untested）",
    }
    # C2
    cand = {cell_key(*c): c for c in CELLS if c != REF_CELL}
    passing = {
        k: margin(merged[k])
        for k in cand
        if (merged[k] or {}).get("rdd_gate") and margin(merged[k]) is not None
    }
    g_star = max(passing, key=lambda k: passing[k]) if passing else None  # type: ignore[arg-type,return-value]
    if g_star is None:
        c2: dict[str, Any] = {
            "ok": False,
            "g_star": None,
            "reason": "无格过 rdd 门（合并窗）——现行出场不劣于全部保护方案",
        }
    else:
        per = {
            w: {
                "delta_margin": delta(w, g_star),
                "rdd_gate": bool((readings[w].get(g_star) or {}).get("rdd_gate")),
            }
            for w in win_names
        }
        for v in per.values():
            v["ok"] = (
                v["delta_margin"] is not None
                and v["delta_margin"] > 0
                and v["rdd_gate"]
            )
        md = margin(merged[g_star])
        mr = margin(merged[ref_k])
        c2 = {
            "ok": all(v["ok"] for v in per.values()),
            "g_star": g_star,
            "cell": list(cand[g_star]),
            "windows": per,
            "merged_delta_margin": md - mr
            if md is not None and mr is not None
            else None,
            "passing_rdd_merged": sorted(passing),
            "rule": "g*=合并 margin 最高且过 rdd 门的非参照格；两窗各自 Δmargin>0 且过门",
        }
    # C3
    if g_star is None:
        c3: dict[str, Any] = {"ok": None, "note": "无 g*——C3 不可评"}
    else:
        bi, ti = BE_GRID.index(cand[g_star][0]), TRAIL_GRID.index(cand[g_star][1])
        nb = [
            (BE_GRID[bi + db], TRAIL_GRID[ti + dt])
            for db, dt in ((-1, 0), (1, 0), (0, -1), (0, 1))
            if 0 <= bi + db < len(BE_GRID) and 0 <= ti + dt < len(TRAIL_GRID)
        ]
        nb = [c for c in nb if c != REF_CELL]
        pos = [
            cell_key(*c)
            for c in nb
            if all(
                (d := delta(w, cell_key(*c))) is not None and d > 0 for w in win_names
            )
        ]
        smooth_ok = bool(nb) and len(pos) / len(nb) >= C3_FRAC
        tail = {
            w: {
                "g_star": (readings[w].get(g_star) or {}).get("n_big_win"),
                "ref": (readings[w].get(ref_k) or {}).get("n_big_win"),
            }
            for w in win_names
        }
        tail_ok = all(
            v["g_star"] is not None
            and v["ref"] is not None
            and v["g_star"] >= TAIL_KEEP * v["ref"]
            for v in tail.values()
        )
        c3 = {
            "ok": bool(smooth_ok and tail_ok),
            "smooth_ok": smooth_ok,
            "neighbors": [cell_key(*c) for c in nb],
            "neighbors_positive": pos,
            "tail_ok": tail_ok,
            "tail": tail,
            "rule": "①相邻格（参照格不计）≥2/3 两窗 Δmargin>0；②两窗 ≥+20% 笔数 ≥ 参照 ×0.8",
        }

    # C4
    def _arm(i: int) -> Optional[float]:
        rng = random.Random(f"{args.seed}-p{i}")
        best: Optional[float] = None
        for _d in range(C4_ARM_DRAWS):
            p = cell_params(rng.uniform(*C4_BE_RANGE), rng.uniform(*C4_TRAIL_RANGE))
            rd = fes.combine_readings(replay(groups, p), args.top_n, ref=ref_m)
            if (
                not rd
                or not rd.get("rdd_gate")
                or rd.get("margin") is None
                or (ref_m or {}).get("margin") is None
            ):
                continue
            d = rd["margin"] - ref_m["margin"]  # type: ignore[index]
            best = d if best is None or d > best else best
        return best

    pool_blk = kit.assemble_c4_pool(args.scan_random, _arm)
    pool = pool_blk["pool"]
    plan_delta = c2.get("merged_delta_margin")
    c4_state = kit.c4_state_of(
        pool, min_pool=args.scan_c4_min_pool, plan_delta=plan_delta
    )
    c4 = {
        "state": c4_state,
        "q95": kit.q95(pool),
        "pool_size": len(pool),
        "plan_delta_merged": plan_delta,
        **{
            k: pool_blk[k]
            for k in (
                "evaluated",
                "gate_pass",
                "gate_pass_rate",
                "max_arms",
                "target_pool",
            )
        },
        "min_pool": args.scan_c4_min_pool,
        "arm_draws": C4_ARM_DRAWS,
        "note": "随机参数臂：每组一个固定 (be~U[3%,15%], trail~U[5%,20%]) 作用于全部信号，每臂 15 组取 max",
    }
    verdict = kit.verdict_four_state(
        c1_ok=c1["ok"], c2_ok=c2["ok"], c3_ok=c3["ok"], c4_state=c4_state
    )

    from custos.research import provenance as pv  # noqa: PLC0415
    from custos.core.paths import RESEARCH_DIR  # noqa: PLC0415

    return {
        "schema": "plan_protect_scan/v1",
        "mode": "protect_scan",
        "unit": "R47",
        "tag": args.tag,
        "verdict": verdict,
        "grid": {
            "breakeven_trigger": list(BE_GRID),
            "trail_pct": list(TRAIL_GRID),
            "ref": ref_k,
        },
        "windows": {name: list(seg["span"]) for name, seg in segments.items()},
        "accounting": {
            name: {**seg["accounting"], "n_clipped": seg["n_clipped"]}
            for name, seg in segments.items()
        },
        "readings": readings,
        "merged": merged,
        "criteria": {"C1": c1, "C2": c2, "C3": c3, "C4": c4},
        "window_usage": {
            "window": "not_read",
            "k": None,
            "note": "扫描模式只读挖掘 A∪B——判定段不读、批次门不触发",
        },
        "provenance": pv.build(
            args,
            unit="R47",
            criteria_version="v0.434",
            pre_reg_doc=RESEARCH_DIR / "R47_profit_protection_breakeven_trail.md",
        ),
    }
