# -*- coding: utf-8 -*-
"""R52 owner 批语入场规则（v0.536，预注册见 ``governance/research/R52_remark_entry_rules.md``）。

同一批 B1 信号（日 J<13 ∧ 0AMV 做多，j_low gate 同口径 collect_all），比较 8 条批语规则相对「当根收盘进」（B0）：

- 等待臂 W1~W4：信号次根起 20 根内首个满足条件的 bar 收盘进，未出现 ⇒ 放弃（``remark_rules.WAIT_COND``）；
- 过滤臂 F1~F4：信号当根满足风险状态 ⇒ 放弃，否则当根进（``remark_rules.STATE_CHECK``）。

出场 = ``plan_rules_replay.LIVE_PARAMS``（R47 参照格），成本 25bp；只读挖掘 A / B，K 线截到段末，进场日不晚于段末隔离带。
判据（写死，``criteria_kit`` 单源）：C1 两窗簇数 ≥30；C2 两窗 Δmargin 簇 bootstrap 95% 下界 >0；C3 挖掘 B 2022~2025 逐年稳定；
C4 同参与率 / 同等待分布的随机臂（N 默认 200）Δmargin 池，候选 > ``batch_threshold(pool, m=8)``。四态 ``verdict_four_state``。

实现要点：每个信号一次性预算「延后 d 根（0..20）进场」的交易结果，所有臂与随机臂只做查表（逐位确定）。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from custos.core.paths import LOGS, RESEARCH_DIR
from custos.research import criteria_kit as kit
from custos.research import load_window as lw
from custos.research import remark_rules as rr

CRITERIA_VERSION = "v0.545"
COST_BPS = 25.0
MAX_DELAY = rr.WAIT_N
N_RANDOM = 200
C4_MIN_POOL = 50
N_BOOT = 400
SEGS = {"mining_a": lw.SEG_MINING_A, "mining_b": lw.SEG_MINING_B}
ARMS: dict[str, tuple[str, str]] = {
    "W1": ("wait", "w_green_shrink"),
    "W2": ("wait", "w_fractal"),
    "W3": ("wait", "w_zero"),
    "W4": ("wait", "w_stable"),
    "F1": ("filter", "s_vol_down"),
    "F2": ("filter", "s_green_grow"),
    "F3": ("filter", "s_dead_cross"),
    "F4": ("filter", "s_far_zero"),
}
ARM_NAME = {k: rr.TAG_NAME[v[1]] for k, v in ARMS.items()}


# ---------------------------------------------------------------------------
# 逐票：信号 + 各延后档交易结果 + 条件
# ---------------------------------------------------------------------------


def code_records(
    code: str, df: Any, regime: dict[str, str], seg: str
) -> list[dict[str, Any]]:
    """单票单段的信号记录。``out[d]`` = 延后 d 根进场的净收益（%；None = 进场越过隔离带 / 段末未平 / 买不到）。"""
    from custos.core.indicators import bbi_series  # noqa: PLC0415
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import plan_rules_replay as prr  # noqa: PLC0415

    s, e = SEGS[seg]
    ds = df["date"].astype(str).str[:10].tolist()
    inseg = [k for k, d in enumerate(ds) if d <= e]
    if not inseg:
        return []
    limit = inseg[-1]
    deadline = lw.entry_deadline(e)
    d = df.iloc[: limit + 1].reset_index(drop=True)
    sigs: list[dict[str, Any]] = []
    bt.evaluate_trades(
        {code: d},
        scorer=bt.SCORERS["baseline"],
        entry_gate=bt.j_low_gate,
        amv_regime=regime,
        cost_bps=COST_BPS,
        collect_all=True,
        signals_out=sigs,
    )
    idxs = sorted(
        {int(x["i"]) for x in sigs if s <= str(x.get("date"))[:10] <= deadline}
    )
    if not idxs:
        return []
    a = rr.arrays(d)
    bbi = bbi_series(d["close"])
    memo: dict[int, Optional[float]] = {}
    edge_j: set[int] = set()  # 越过段末 / 隔离带 / 段末未平的进场位

    def outcome(j: int) -> Optional[float]:
        if j > limit or ds[j] > deadline:
            edge_j.add(j)
            return None
        if j not in memo:
            tr = bt.simulate_b1_trade(d, j, bbi, **prr.LIVE_PARAMS)
            if bt.is_unrealized_reason(tr.get("reason")):
                edge_j.add(j)
                memo[j] = None
            elif tr.get("skipped"):
                memo[j] = None  # 买不到：该臂自然不成交，不算段末
            else:
                memo[j] = float(tr["ret"]) * 100 - COST_BPS / 100
        return memo[j]

    out: list[dict[str, Any]] = []
    for i in idxs:
        waits = {}
        for _k, (kind, cond) in ARMS.items():
            if kind == "wait":
                jj = rr.first_trigger(a, i, rr.WAIT_COND[cond], limit=limit)
                waits[cond] = None if jj is None else jj - i
        flags = {
            cond: bool(rr.STATE_CHECK[cond](a, i))
            for kind, cond in ARMS.values()
            if kind == "filter"
        }
        out.append(
            {
                "code": code,
                "i": i,
                "date": ds[i],
                "_seg": seg,
                "year": int(ds[i][:4]),
                "out": {
                    dd: outcome(i + dd) for dd in range(MAX_DELAY + 1)
                },  # 随机臂按任意延后档查表
                # v0.545：前向窗（等待 20 根 + 持有）任一档越过段末 ⇒ 整笔剔除，所有臂同剔（保配对）
                "edge": any(i + dd in edge_j for dd in range(MAX_DELAY + 1)),
                "wait": waits,
                "flag": flags,
            }
        )
    return out


# ---------------------------------------------------------------------------
# 读数与判据
# ---------------------------------------------------------------------------


def arm_delay(rec: dict[str, Any], arm: str) -> Optional[int]:
    """该臂对该信号的进场延后根数；None = 放弃。B0 恒 0。"""
    if arm == "B0":
        return 0
    kind, cond = ARMS[arm]
    if kind == "wait":
        return rec["wait"].get(cond)
    return None if rec["flag"].get(cond) else 0


def _margin(rets: list[float]) -> Optional[float]:
    if not rets:
        return None
    w = np.ones((1, len(rets)))
    v = float(kit._weighted_margin(w, np.asarray(rets, float))[0])
    return v if v == v else None


def _trades(
    recs: list[dict[str, Any]], cids: list[int], arm: str
) -> tuple[list[float], list[int]]:
    r, c = [], []
    for rec, cid in zip(recs, cids):
        dl = arm_delay(rec, arm)
        if dl is None:
            continue
        v = rec["out"].get(dl)
        if v is not None:
            r.append(v)
            c.append(cid)
    return r, c


def window_reading(
    recs: list[dict[str, Any]], cids: list[int], arm: str, seed: int
) -> dict[str, Any]:
    ra, ca = _trades(recs, cids, arm)
    rb, cb = _trades(recs, cids, "B0")
    part = (
        sum(arm_delay(x, arm) is not None for x in recs) / len(recs) if recs else None
    )
    bd = (
        kit.margin_delta_bounds(ra, ca, rb, cb, seed=seed, n_boot=N_BOOT)
        if ra and rb
        else {"delta": None, "lo": None, "hi": None, "n_clusters": 0}
    )
    return {
        "n_trades": len(ra),
        "n_clusters": len(set(ca)),
        "participation": None if part is None else round(part, 4),
        "margin": _margin(ra),
        "margin_b0": _margin(rb),
        "delta": bd["delta"],
        "lo": bd["lo"],
        "hi": bd["hi"],
        "mean_ret": round(float(np.mean(ra)), 3) if ra else None,
        "p_big": round(sum(x >= 20 for x in ra) / len(ra), 4) if ra else None,
    }


def _arm_clusters_by_seg(
    recs: list[dict[str, Any]], cids: list[int], delays: list[Optional[int]]
) -> dict[str, set[int]]:
    by: dict[str, set[int]] = {w: set() for w in SEGS}
    for x, c, dl in zip(recs, cids, delays):
        if dl is not None and x["out"].get(dl) is not None:
            by.setdefault(x["_seg"], set()).add(c)
    return by


def random_pool(
    recs: list[dict[str, Any]], cids: list[int], arm: str, n: int, seed: int
) -> dict[str, Any]:
    """C4 随机臂（v0.545 修订）：**整簇抽签**（``criteria_kit.cluster_draw``）——每簇从该臂的
    逐信号延后分布（含放弃 None）里抽一个签，同簇同签，参与率与等待天数分布同该臂；
    过门 = 随机臂两窗各自已实现簇数 ≥ ``MIN_CLUSTERS``（同 C1）；过门臂凑满 n、上限 10×n
    （``criteria_kit.assemble_c4_pool``）。池值 = 两窗合并 Δmargin 点估计。"""
    rng = random.Random(seed)
    choices = tuple(arm_delay(x, arm) for x in recs)
    base_r = [v for v in (x["out"].get(0) for x in recs) if v is not None]
    mb = _margin(base_r)

    def evaluate(_i: int) -> Optional[float]:
        if not choices or mb is None:
            return None
        delays = kit.cluster_draw(cids, choices, rng)
        by = _arm_clusters_by_seg(recs, cids, delays)
        if any(len(by.get(w, ())) < kit.MIN_CLUSTERS for w in SEGS):
            return None
        rs = [
            v
            for x, dl in zip(recs, delays)
            if dl is not None and (v := x["out"].get(dl)) is not None
        ]
        m = _margin(rs)
        return None if m is None else m - mb

    return kit.assemble_c4_pool(n, evaluate, cap_factor=10)


def judge(
    recs_by_seg: dict[str, list[dict[str, Any]]],
    *,
    n_random: int = N_RANDOM,
    seed: int = 0,
) -> dict[str, Any]:
    cids_by = {w: kit.cluster_ids(r) for w, r in recs_by_seg.items()}
    merged = [x for w in SEGS for x in recs_by_seg.get(w, [])]
    merged_c = kit.cluster_ids(merged)
    out: dict[str, Any] = {}
    for k, arm in enumerate(ARMS):
        wins = {
            w: window_reading(recs_by_seg[w], cids_by[w], arm, seed + 17 * k)
            for w in SEGS
            if recs_by_seg.get(w)
        }
        c1 = all(wins.get(w, {}).get("n_clusters", 0) >= kit.MIN_CLUSTERS for w in SEGS)
        c2 = all(
            (wins.get(w) or {}).get("lo") is not None and wins[w]["lo"] > 0
            for w in SEGS
        )
        b = recs_by_seg.get("mining_b", [])
        cb = cids_by.get("mining_b", [])
        per_year = {}
        for y in kit.RECENT_YEARS:
            sel = [(x, c) for x, c in zip(b, cb) if x["year"] == y]
            if not sel:
                continue
            rd = window_reading(
                [x for x, _ in sel], [c for _, c in sel], arm, seed + 31 * y
            )
            per_year[y] = {
                "point": rd["delta"],
                "lo": rd["lo"],
                "hi": rd["hi"],
                "n_clusters": rd["n_clusters"],
            }
        c3 = kit.yearly_stability(per_year, null=0.0)
        ra, ca = _trades(merged, merged_c, arm)
        rb, _cb = _trades(merged, merged_c, "B0")
        ma, mb = _margin(ra), _margin(rb)
        cand_delta = None if ma is None or mb is None else ma - mb
        c4p = random_pool(merged, merged_c, arm, n_random, seed + 101 * k)
        pool = c4p["pool"]
        thr = kit.batch_threshold(pool, len(ARMS)) if pool else None
        if not pool:
            c4 = "indeterminate"
        elif len(pool) < C4_MIN_POOL:
            c4 = "provisional"
        else:
            c4 = (
                "confirmed_pass"
                if cand_delta is not None and thr is not None and cand_delta > thr
                else "confirmed_fail"
            )
        out[arm] = {
            "name": ARM_NAME[arm],
            "windows": wins,
            "merged_delta": cand_delta,
            "c4": {
                "state": c4,
                "threshold": thr,
                "pool_n": len(pool),
                "m": len(ARMS),
                "evaluated": c4p["evaluated"],
                "gate_pass_rate": c4p["gate_pass_rate"],
            },
            "yearly_b": c3,
            "C1": c1,
            "C2": c2,
            "C3": c3["ok"],
            "verdict": kit.verdict_four_state(
                c1_ok=c1, c2_ok=c2, c3_ok=c3["ok"], c4_state=c4
            ),
        }
    return out


# ---------------------------------------------------------------------------
# 生产路径
# ---------------------------------------------------------------------------

_W: dict[str, Any] = {}


def _w_init(regime: dict[str, str], count: int) -> None:
    _W["regime"], _W["count"] = regime, count


def _w_code(code: str) -> dict[str, list[dict[str, Any]]]:
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    res: dict[str, list[dict[str, Any]]] = {}
    for seg, (s, e) in SEGS.items():
        df = bt._load_one_bars(code, _W["count"], s, e, warmup_bars=300)
        res[seg] = (
            code_records(code, df, _W["regime"], seg)
            if df is not None and len(df) >= 150
            else []
        )
    return res


def _production(args: Any) -> dict[str, list[dict[str, Any]]]:
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    ns = argparse.Namespace(
        codes_file=args.codes_file,
        universe_local=args.universe_local,
        universe_sample=args.universe_sample,
        seed=args.universe_seed,
        codes=args.codes,
    )
    codes = bt._resolve_universe(ns, _build_parser())
    args.universe_meta = getattr(ns, "universe_meta", None)
    from custos.research.exit_c5_terminal import check_reach  # noqa: PLC0415

    check_reach(args.count, lw.SEG_MINING_A[0])  # 逐股加载到达校验（fail-closed）
    regime = bt.load_amv_regime(since=lw.SEG_MINING_A[0])
    if not regime:
        raise RuntimeError("0AMV regime 读不到")
    out: dict[str, list[dict[str, Any]]] = {s: [] for s in SEGS}
    procs = int(args.warm_procs or 0)
    if procs > 1:
        from concurrent.futures import ProcessPoolExecutor  # noqa: PLC0415

        with ProcessPoolExecutor(
            procs, initializer=_w_init, initargs=(regime, args.count)
        ) as ex:
            for k, r in enumerate(ex.map(_w_code, codes, chunksize=4), 1):
                for s in SEGS:
                    out[s] += r[s]
                if k % 500 == 0:
                    print(f"[R52] {k}/{len(codes)}", file=sys.stderr)
    else:
        _w_init(regime, args.count)
        for c in codes:
            r = _w_code(c)
            for s in SEGS:
                out[s] += r[s]
    return out


def cost_block(
    recs: dict[str, list[dict[str, Any]]], side_bps: Optional[float] = None
) -> dict[str, Any]:
    """成本副读数：``cost_sensitivity.shift_cost`` 把逐笔收益平移到副成本（默认 50bp），margin 用本单元主读数同一公式；
    报每臂每窗 Δ(臂−B0) 在主 / 副成本下的值与是否翻号。"""
    from custos.research import cost_sensitivity as cs  # noqa: PLC0415

    side = cs.DEFAULT_SIDE_BPS if side_bps is None else side_bps

    def m_at(rs: list[float], bps: float) -> Optional[float]:
        sh = cs.shift_cost([{"ret": v / 100} for v in rs], bps - COST_BPS)
        return _margin([t["ret"] * 100 for t in sh])

    out: dict[str, Any] = {"base_bps": COST_BPS, "side_bps": side, "deltas": {}}
    for w in SEGS:
        rr_ = recs.get(w, [])
        zc = [0] * len(rr_)
        rb, _ = _trades(rr_, zc, "B0")
        for a in ARMS:
            ra, _ = _trades(rr_, zc, a)
            d = {}
            for tag, bps in (("base", COST_BPS), ("side", side)):
                ma, mb = m_at(ra, bps), m_at(rb, bps)
                d[tag] = None if ma is None or mb is None else round(ma - mb, 5)
            d["flip"] = (
                d["base"] is not None
                and d["side"] is not None
                and (d["base"] > 0) != (d["side"] > 0)
            )
            out["deltas"][f"{a}_vs_B0_{w}"] = d
    return out


def power_block(recs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """功效（``power_mde``，k=2.8、配对 ρ=0.75、deff 情景 1/1.5/2）：每窗 B0 交易数与胜率下的 Δmargin MDE（pp）。"""
    from custos.research import power_mde as pm  # noqa: PLC0415

    out: dict[str, Any] = {}
    for w in SEGS:
        r, _c = _trades(recs.get(w, []), [0] * len(recs.get(w, [])), "B0")
        wr = sum(v > 0 for v in r) / len(r) if r else None
        out[w] = {
            "n_b0": len(r),
            "wr_b0": None if wr is None else round(wr, 4),
            "mde_pp": (
                {
                    str(dfx): round(100 * pm.mde(wr, len(r), pm.RHO_PAIRED, dfx), 2)
                    for dfx in pm.DEFF_SCENARIOS
                }
                if r and wr is not None and 0 < wr < 1
                else None
            ),
        }
    return out


def run_study(
    args: Any,
    *,
    recs_fn: Optional[Callable[[Any], dict[str, list[dict[str, Any]]]]] = None,
) -> dict[str, Any]:
    from custos.research import provenance as pv  # noqa: PLC0415

    for nm, (s, e) in SEGS.items():
        bad = lw.mining_violation(s, e)
        if bad:
            raise RuntimeError(f"remark_entry_study 窗口校验失败 {nm}: {bad}")
    raw = (recs_fn or _production)(args)
    n_edge = {s: sum(bool(x.get("edge")) for x in raw.get(s, [])) for s in SEGS}
    recs = {s: [x for x in raw.get(s, []) if not x.get("edge")] for s in SEGS}
    if not any(recs.get(s) for s in SEGS):
        raise RuntimeError("空结果护栏：两挖掘段 0 个 B1 信号——不落盘")
    arms = judge(recs, n_random=args.n_random, seed=args.seed)
    last = max((x["date"] for s in SEGS for x in recs.get(s, [])), default=None)
    return {
        "unit": "R52",
        "kind": "r52",
        "tag": args.tag,
        "criteria_version": CRITERIA_VERSION,
        "n_signals": {s: len(recs.get(s, [])) for s in SEGS},
        "n_edge_dropped": n_edge,
        "arms": arms,
        "cost_sensitivity": cost_block(recs),
        "power": power_block(recs),
        "verdicts": {k: v["verdict"] for k, v in arms.items()},
        "window_usage": {
            "window": "not_read",
            "k": None,
            "note": "只读挖掘 A∪B——判定段不读、window_usage 不登记",
        },
        "provenance": pv.build(
            args,
            unit="R52",
            criteria_version=CRITERIA_VERSION,
            pre_reg_doc=RESEARCH_DIR / "R52_remark_entry_rules.md",
            data_last_date=last,
        ),
    }


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R52 owner 批语入场规则：等条件再进 / 风险过滤 vs 当根进（只读挖掘 A∪B）"
    )
    ap.add_argument("--codes", default="")
    ap.add_argument("--codes-file", default="")
    ap.add_argument("--universe-local", action="store_true")
    ap.add_argument("--universe-sample", type=int, default=0)
    ap.add_argument("--universe-seed", type=int, default=0)
    ap.add_argument("--count", type=int, default=100000)
    ap.add_argument(
        "--warm-procs",
        type=int,
        default=0,
        help="逐票进程并行路数（建议 = 物理核数 − 2）",
    )
    ap.add_argument(
        "--n-random", type=int, default=N_RANDOM, help="C4 随机臂数（预注册 200）"
    )
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--tag", default="r52_v1")
    ap.add_argument("--out-dir", default=str(LOGS / "remark_entry"))
    return ap


def main(argv: Optional[list[str]] = None, **inj: Any) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = _build_parser().parse_args(argv)
    args.cmdline = " ".join(sys.argv if argv is None else ["remark_entry_study", *argv])
    try:
        rep = run_study(args, **inj)
    except (RuntimeError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    out = Path(args.out_dir) / f"_remark_entry__{args.tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, default=str, allow_nan=True),
        encoding="utf-8",
    )
    print("[R52] " + "  ".join(f"{k}={v}" for k, v in rep["verdicts"].items()))
    print(f"[OK] 写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
