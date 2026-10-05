# -*- coding: utf-8 -*-
"""R36 Phase 4 排序器→过滤器判据研究（判据草案 v0.277 的代码化身）。

问法转换：不问「按该因子排序取头部能不能赚」，改问「**按该因子剔掉尾部
X%、再让现行 V0 选 top20，margin 是否改善**」。口径与 Phase 3 逐位一致
（同宇宙/双窗/j_low gate/pct5_trail08/topn=20），唯一改动 = V0 打分**之前**
插入一道前置剔除（topn 槽位由 V0 次名回填——live 的现实类比）。

写死的纪律（R36 Phase 4 草案）：
- **预注册测试集（只测这 4 个，不许加）**：①``v0_self`` V0 自身=空对照
  （只要 X% < (N−20)/N，top20 理论逐位不变——它若给出显著 Δmargin 说明
  管线有 bug）；②``neg4`` 四条负分腿（distribution_high/distribution_watch/
  macd_top_divergence/volume_yy_bear）软扣分改硬剔除；③``p2_sole``
  Phase 2 独苗（DSL expr，TS_RANK 归一，collect 期 addon 通道预计算）；
  ④``reversal_quality``（SCORERS 键 as-of 值，二次过帧计算）。
- **X 网格 {10,20,30,50}% 跑数前写死，不许事后加档**；逐日按因子值剔尾部
  floor(n_avail×X) 名；**缺值不剔（fail-open）**——「算不出」≠「差」，
  缺值率必报。
- **判据（P4-C1~C5 同族编号）**：C1 单窗 n_taken≥100 且槽位填充率≥90%
  （剔太多填不满 ⇒ 该 X 档作废不计负）；C2 Δmargin（vs 同口径不剔除基准）
  双窗同向为正（v0.276 保留率<0.5 标 candidate_degraded）；C3 相邻 X 档
  不翻转符号（最优档邻档翻负 ⇒ 参数敏感不过）；C4 同 X 档随机剔除 N=50
  次的合并分布 q95（**真零假设**：随机剔一批票，无 DSL 裸终结符污染），
  候选 Δmargin 须超过；C5 全过才启动 pre2019 终审（单独终步）。
- **必报位移数**：被剔掉的原 top20 名额数——不报就无法分辨「改善来自
  剔对了」还是「仅仅来自扰动了组合」。
- pre2019 untouched 段硬拒绝（与挖掘/判定侧同纪律）。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Optional

from custos.core.paths import LOGS
from custos.research.evolution_loop import _overlaps_pre2019, PRE2019_END, PRE2019_START

#: 预注册 4 测试对象（不许加）
FILTER_KEYS = ("v0_self", "neg4", "p2_sole", "reversal_quality")

#: neg4 的四条负分腿（contrib 键；命中才进 contrib，缺席=未触发=0）
NEG4_LEGS = (
    "distribution_high",
    "distribution_watch",
    "macd_top_divergence",
    "volume_yy_bear",
)

#: Phase 2 独苗表达式（R36 回填区写死；TS_RANK 归一在 collect 期 addon 通道算）
P2_SOLE_EXPR = (
    "(1-TS_RANK(STD(close,20)/MA(close,20),120))"
    "*(1-TS_RANK(MAX(high,5)-MIN(low,5),60))*(MA(close,5)/MA(close,10))"
)

#: X 网格（跑数前写死，不许事后加档）
X_GRID = (0.10, 0.20, 0.30, 0.50)

FILL_FLOOR = 0.90  # P4-C1 槽位填充率下限
MIN_N_TAKEN = 100  # P4-C1 样本量下限（同 C1 族）
RETENTION_FLOOR = 0.5  # v0.276 保留率分档


def _day_groups(items: list[dict]) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        d = it["trade"].get("entry_date")
        if d:
            by[d].append(it)
    return by


def _filter_day(items: list[dict], x: float) -> list[dict]:
    """单日前置剔除：剔尾部 floor(n_avail×X) 名（缺值不剔，fail-open）。

    尾部 = 因子值最低端（neg4 值 ≤0 越负越差；v0_self/p2_sole/reversal_quality
    越低越差）。排序键 (value, code) 保证确定性；X=0 原样返回。
    """
    if x <= 0:
        return list(items)
    with_val: list[dict] = []
    without: list[dict] = []  # 缺值 fail-open
    for it in items:
        v = it.get("fval")
        if isinstance(v, (int, float)) and math.isfinite(v):
            with_val.append(it)
        else:
            without.append(it)
    n_drop = int(len(with_val) * x)  # floor（写死口径；池大时取舍方向无感）
    if n_drop <= 0:
        return list(items)
    with_val.sort(key=lambda it: (it["fval"], it["code"]))
    return without + with_val[n_drop:]


def _random_filter_day(items: list[dict], x: float, rng: random.Random) -> list[dict]:
    """单日随机剔除（P4-C4 零假设臂）：同 X 同池随机剔 floor(n×X) 名。"""
    if x <= 0:
        return list(items)
    n_drop = int(len(items) * x)
    if n_drop <= 0:
        return list(items)
    idx = list(range(len(items)))
    rng.shuffle(idx)
    drop = set(idx[:n_drop])
    return [it for j, it in enumerate(items) if j not in drop]


def _eval_pool(
    items: list[dict], top_n: int
) -> tuple[Optional[dict], list[dict], dict[str, int]]:
    """V0 默认权重打分 + topn 组合：→（读数, taken, 日槽位统计）。

    读数键与 v0-lattice 同族（margin/expectancy_R/win_rate/payoff/n/n_taken/
    ret_over_dd）；日槽位统计 = {days, slots}（slots=Σ min(top_n, 当日池)——
    填充率分母，组合容量约束前的需求侧口径）。
    """
    from custos.pipeline.screening import score_candidates as sc  # noqa: PLC0415
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import score_evolution_study as ses  # noqa: PLC0415
    from custos.research import strategy_grid as sg  # noqa: PLC0415

    cands: list[dict[str, Any]] = []
    for it in items:
        # v0_self 分值在 _enrich 期已算（live 默认权重）——直接复用，
        # 不逐评估重算（418 次评估 × 7 万候选的重打分是浪费）
        score = it.get("v0_self")
        if score is None:
            score, _level, _contrib = sc.technical_score(it["cand"], None)
        cands.append({**it["trade"], "score": score})
    by = _day_groups([{"trade": c} for c in cands])
    slots = sum(min(top_n, len(v)) for v in by.values())
    taken: list[dict] = []
    pf = bt.simulate_portfolio_topn(
        cands, top_n=top_n, taken_out=taken, **ses._V0_PORTFOLIO
    )
    tsum = bt.summarize_trades(taken)
    margin = sg._margin(
        {"win": tsum.get("win_rate"), "payoff": tsum.get("payoff_ratio")}
    )
    reading = {
        "margin": margin,
        "expectancy_R": tsum.get("expectancy_R"),
        "win_rate": tsum.get("win_rate"),
        "payoff_ratio": tsum.get("payoff_ratio"),
        "n": tsum.get("n"),
        "n_taken": pf.get("n_taken"),
        "ret_over_dd": sg._ret_over_dd(pf),
    }
    return reading, taken, {"days": len(by), "slots": slots}


def evaluate_filter(items: list[dict], x: float, top_n: int) -> dict[str, Any]:
    """单 (过滤器值已写入 fval) × X 档：剔除 → 重打分 → 读数 + 填充率。"""
    by = _day_groups(items)
    kept: list[dict] = []
    for d in sorted(by):
        kept.extend(_filter_day(by[d], x))
    reading, taken, slots = _eval_pool(kept, top_n)
    reading["fill_rate"] = (
        (reading["n_taken"] or 0) / slots["slots"] if slots["slots"] else None
    )
    return {"reading": reading, "taken": taken, "slots": slots}


def _displacement(base_taken: list[dict], filt_taken: list[dict]) -> int:
    """位移数：被剔掉的原 top20 名额数（必报，区分「剔对」与「扰动」）。"""
    kept = {(t["code"], t["entry_date"]) for t in filt_taken}
    return sum(1 for t in base_taken if (t["code"], t["entry_date"]) not in kept)


def _q95(xs: list[float]) -> Optional[float]:
    if len(xs) < 20:  # 分位数在样本太小时无意义（fail-closed）
        return None
    return statistics.quantiles(sorted(xs), n=100, method="inclusive")[94]


def _enrich(
    collected: list[dict],
    args: Any,
    window: Any,
    rq_fetcher: Optional[Callable[[str, list[int]], dict[str, Optional[float]]]] = None,
) -> list[dict]:
    """候选池富化（每窗一次）：写入四对象的过滤器值（fval 按对象切换）。

    - ``v0_self``/``neg4``：technical_score（live 默认权重）+ contrib 直出；
    - ``p2_sole``：collect 期 addon 通道已预计算（``item["addon"][expr]``）；
    - ``reversal_quality``：二次过帧（逐股加载 → 进场 bar 的 SCORERS 值），
      ``rq_fetcher`` 可注入（测试）；None = 生产二次过帧。
    返回元素 = 原 item + {v0_self, neg4, p2_sole, reversal_quality} 四值
    （None=缺值，fail-open 计数进缺值率）。键名与 FILTER_KEYS 一一对应
    （「过滤器键=富化键」单映射，键漂移则缺值率=1 全 fail-open——钉测守着）。
    """
    from custos.pipeline.screening import score_candidates as sc  # noqa: PLC0415

    rq_map: dict[tuple[str, str], Optional[float]] = {}
    if rq_fetcher is None:
        rq_map = _rq_second_pass(collected, args, window)
    else:
        by_code: dict[str, list[dict]] = defaultdict(list)
        for it in collected:
            by_code[it["code"]].append(it)
        for code, its in by_code.items():
            vals = rq_fetcher(code, its)
            for it, v in zip(its, vals):
                rq_map[(code, it["trade"]["entry_date"])] = v

    out: list[dict] = []
    for it in collected:
        score, _level, contrib = sc.technical_score(it["cand"], None)
        neg4 = 0.0
        for k in NEG4_LEGS:
            v = contrib.get(k)
            if isinstance(v, (int, float)) and math.isfinite(v):
                neg4 += v
        addon = (it.get("addon") or {}).get(P2_SOLE_EXPR)
        out.append(
            {
                **it,
                "v0_self": score,
                "neg4": neg4,
                "p2_sole": addon,
                "reversal_quality": rq_map.get((it["code"], it["trade"]["entry_date"])),
            }
        )
    return out


def _rq_second_pass(collected: list[dict], args: Any, window: Any) -> dict:
    """reversal_quality 二次过帧：逐股加载 → 进场 bar 的 SCORERS as-of 值。

    每股只算该股候选的进场 bar（不全序列）；评估失败 → None（fail-open，
    缺值率如实上报）。
    """
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    by_code: dict[str, list[dict]] = defaultdict(list)
    for it in collected:
        by_code[it["code"]].append(it)
    out: dict[tuple[str, str], Optional[float]] = {}
    scorer = bt.SCORERS["reversal_quality"]
    for j, (code, its) in enumerate(sorted(by_code.items()), 1):
        if j % 200 == 0:
            print(
                f"[rq] {window.start}~{window.end} {j}/{len(by_code)}", file=sys.stderr
            )
        df = bt._load_one_bars(code, args.count, window.start, window.end)
        if df is None or not len(df):
            continue
        dates = df["date"].astype(str).str[:10].tolist()
        date2i = {d: i for i, d in enumerate(dates)}
        for it in its:
            d = it["trade"]["entry_date"]
            i = date2i.get(d)
            if i is None:
                continue
            try:
                res = scorer(df.iloc[: i + 1], code)
                v = res.get("score") if isinstance(res, dict) else None
                out[(code, d)] = (
                    float(v)
                    if isinstance(v, (int, float)) and math.isfinite(v)
                    else None
                )
            except Exception:  # noqa: BLE001 — 单点失败缺值 fail-open
                out[(code, d)] = None
    return out


def run_study(
    args: Any,
    collector: Optional[Callable[[Any], Optional[list[dict]]]] = None,
    rq_fetcher: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    """Phase 4 主驱动：双窗 collect → 富化 → 4 对象 × 4 X 档 + 随机对照。

    ``collector``/``rq_fetcher`` 可注入（测试合成数据）。空结果护栏：
    任一窗 0 候选 → RuntimeError（不落盘）。
    """
    from custos.research import score_evolution_study as ses  # noqa: PLC0415
    from custos.research.evolution.dual_window import Window  # noqa: PLC0415

    windows = {
        "mining": Window(args.mining_start, args.mining_end),
        "judgment": Window(args.judgment_start, args.judgment_end),
    }
    codes = [
        line.strip()
        for line in Path(args.codes_file).read_text().splitlines()
        if line.strip()
    ]
    # p2_sole 走 collect 的 addon 通道（TS_RANK 归一序列每股一次）
    args.addon_legs = [P2_SOLE_EXPR]
    args.rank_window = 250
    exit_spec = {
        "name": "pct5_trail08",
        "params": {"stop_mode": "pct", "stop_pct": 5, "trail_pct": 0.08},
    }

    enriched: dict[str, list[dict]] = {}
    baseline: dict[str, dict] = {}
    for wname, w in windows.items():
        if collector is not None:
            collected = collector(w)
        else:
            collected = ses._collect_v0_window(args, codes, exit_spec, w)
        if not collected:
            raise RuntimeError(
                f"Phase 4 空结果护栏：{wname} 窗 0 候选（宇宙/窗口/数据有问题？）——不落盘"
            )
        items = _enrich(collected, args, w, rq_fetcher)
        enriched[wname] = items
        base_reading, base_taken, base_slots = _eval_pool(items, args.top_n)
        baseline[wname] = {
            "reading": base_reading,
            "taken": base_taken,
            "slots": base_slots,
        }

    # 随机过滤器对照（每 X 档 N 次；两臂共享基准读数）
    rng = random.Random(args.seed)
    random_pool: dict[str, dict[str, list[float]]] = {
        f"{x:.2f}": {"mining": [], "judgment": []} for x in X_GRID
    }
    for x in X_GRID:
        for _ in range(args.n_random):
            for wname in windows:
                by = _day_groups(enriched[wname])
                kept: list[dict] = []
                for d in sorted(by):
                    kept.extend(_random_filter_day(by[d], x, rng))
                reading, _t, _s = _eval_pool(kept, args.top_n)
                b0 = baseline[wname]["reading"]["margin"]
                if reading["margin"] is not None and b0 is not None:
                    random_pool[f"{x:.2f}"][wname].append(reading["margin"] - b0)

    # 4 对象 × 4 X 档
    filters_out: dict[str, Any] = {}
    for fkey in FILTER_KEYS:
        per_x: dict[str, Any] = {}
        for x in X_GRID:
            row: dict[str, Any] = {"x": x}
            for wname in windows:
                items = [{**it, "fval": it.get(fkey)} for it in enriched[wname]]
                n_missing = sum(1 for it in items if it["fval"] is None)
                res = evaluate_filter(items, x, args.top_n)
                r, taken = res["reading"], res["taken"]
                b0 = baseline[wname]["reading"]
                d_margin = (
                    r["margin"] - b0["margin"]
                    if r["margin"] is not None and b0["margin"] is not None
                    else None
                )
                fill = r.get("fill_rate")
                c1_ok = (
                    (r.get("n_taken") or 0) >= MIN_N_TAKEN
                    and fill is not None
                    and fill >= FILL_FLOOR
                )
                row[wname] = {
                    "margin": r["margin"],
                    "d_margin": d_margin,
                    "n_taken": r.get("n_taken"),
                    "fill_rate": fill,
                    "n_missing": n_missing,
                    "missing_rate": n_missing / len(items) if items else None,
                    "displacement": _displacement(baseline[wname]["taken"], taken),
                    "c1_ok": c1_ok,
                }
            rp = random_pool[f"{x:.2f}"]
            merged = rp["mining"] + rp["judgment"]
            row["random_q95"] = _q95(merged)
            row["random_n"] = len(merged)
            # C2/C4 机械读数（双窗同向为正 / 超随机 q95）
            dm, dj = row["mining"]["d_margin"], row["judgment"]["d_margin"]
            row["c2_ok"] = dm is not None and dj is not None and dm > 0 and dj > 0
            row["c4_ok"] = (
                dm is not None
                and row["random_q95"] is not None
                and dm > row["random_q95"]
            )
            row["void"] = not (
                row["mining"]["c1_ok"] and row["judgment"]["c1_ok"]
            )  # 任一窗 C1 不过 ⇒ 该档作废（保守），不计负
            per_x[f"{x:.2f}"] = row

        # C3：最优档（挖掘窗 Δmargin，非作废档）相邻档不得翻负
        valid = [
            (k, v)
            for k, v in per_x.items()
            if not v["void"] and v["mining"]["d_margin"] is not None
        ]
        c3 = {"flips": None, "note": "无有效档"}
        best_x = None
        if valid:
            best_x, best = max(valid, key=lambda kv: kv[1]["mining"]["d_margin"])
            order = [f"{x:.2f}" for x in X_GRID]
            i = order.index(best_x)
            neigh = [order[j] for j in (i - 1, i + 1) if 0 <= j < len(order)]
            flips = sum(
                1
                for nb in neigh
                if not per_x[nb]["void"] and (per_x[nb]["mining"]["d_margin"] or 0) <= 0
            )
            c3 = {"best_x": best_x, "neighbors": neigh, "flips": flips}
        # 保留率（v0.276 降级标注）：最优档 判定Δ/挖掘Δ
        retention = None
        degraded = None
        if best_x is not None:
            dm = per_x[best_x]["mining"]["d_margin"]
            dj = per_x[best_x]["judgment"]["d_margin"]
            if dm and dj is not None:
                retention = dj / dm
                degraded = retention < RETENTION_FLOOR
        filters_out[fkey] = {
            "per_x": per_x,
            "c3": c3,
            "retention": retention,
            "candidate_degraded": degraded,
            "verdict_hint": _verdict_hint(per_x, c3),
        }

    return {
        "version": 1,
        "tag": args.tag,
        "config": {
            "filters": list(FILTER_KEYS),
            "x_grid": list(X_GRID),
            "windows": {
                k: {"start": w.start, "end": w.end} for k, w in windows.items()
            },
            "codes_file": str(args.codes_file),
            "count": args.count,
            "cost_bps": args.cost_bps,
            "top_n": args.top_n,
            "n_random": args.n_random,
            "seed": args.seed,
            "exit": "pct5_trail08",
            "criteria": "R36 Phase 4（v0.277 草案）：P4-C1 n_taken≥100∧填充率≥90%（否则档作废不计负）/C2 双窗同向为正（保留率<0.5 标 degraded）/C3 相邻档不翻负/C4 Δmargin>同 X 档随机剔除合并分布 q95（N=50 真零假设）；缺值 fail-open 必报缺值率；位移数必报",
        },
        "baseline": {
            k: {"reading": v["reading"], "slots": v["slots"]}
            for k, v in baseline.items()
        },
        "filters": filters_out,
        "random_control": {
            k: {
                "n": len(v["mining"] + v["judgment"]),
                "q95": _q95(v["mining"] + v["judgment"]),
            }
            for k, v in random_pool.items()
        },
    }


def _verdict_hint(per_x: dict[str, Any], c3: dict[str, Any]) -> str:
    """机械汇总（非判据本体——判读按 R36 Phase 4 预注册执行）。"""
    best_x = c3.get("best_x")
    if best_x is None:
        return "no_valid_x（全档作废或无读数）"
    best = per_x[best_x]
    ok = best["c2_ok"] and best["c4_ok"] and (c3.get("flips") or 0) == 0
    return f"best_x={best_x} c2={best['c2_ok']} c4={best['c4_ok']} c3_flips={c3.get('flips')} ⇒ {'pass' if ok else 'fail'}"


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R36 Phase 4 排序器→过滤器判据研究（4 对象×X 网格写死；只读双窗，pre2019 硬拒绝）"
    )
    ap.add_argument("--codes-file", required=True, help="钉死宇宙 codes 表（s2999）")
    ap.add_argument("--mining-start", required=True)
    ap.add_argument("--mining-end", required=True)
    ap.add_argument("--judgment-start", required=True)
    ap.add_argument("--judgment-end", required=True)
    ap.add_argument("--count", type=int, default=2000)
    ap.add_argument("--cost-bps", type=float, default=25.0)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--n-random", type=int, default=50, help="每 X 档随机过滤器臂数")
    ap.add_argument("--seed", type=int, default=20261005)
    ap.add_argument("--tag", default="r36_p4_v1")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = _build_parser()
    args = ap.parse_args(argv)
    for name, s, e in (
        ("mining", args.mining_start, args.mining_end),
        ("judgment", args.judgment_start, args.judgment_end),
    ):
        if _overlaps_pre2019(s, e):
            ap.error(
                f"⛔ 反过拟合纪律：Phase 4 挖掘/判定不许碰 pre2019 untouched 终审段"
                f"（{PRE2019_START}..{PRE2019_END}）——{name} {s}..{e} 与之相交"
            )
    try:
        rep = run_study(args)
    except (RuntimeError, ValueError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 2
    out_dir = LOGS / "score_filter" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"_score_filter__{args.tag}.json"
    # 研究产物允许 NaN（区别于生产侧 paths.write_json 的 allow_nan=False）
    out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8"
    )
    print(f"[P4] 报告 → {out}")
    for fkey, blk in rep["filters"].items():
        print(f"[P4] {fkey}: {blk['verdict_hint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
