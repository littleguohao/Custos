# -*- coding: utf-8 -*-
"""R36-C5 pre2019 终审终端（score 侧；``exit_c5_terminal`` 的同族镜像）。

只接受 pre2019 untouched 段（2010-01-01..2016-12-31）内的窗口——与
``score_evolution_study`` 对 pre2019 的硬拒绝互为镜像（挖掘/判定侧工具
对 pre2019 硬拒绝，本工具对非 pre2019 硬拒绝）。

做的事（一次一单）：
  冻结候选基因组（v0-lattice 倍率向量，从 score_evolution_study 产物
  ``top_genome.multipliers`` 自含读取，禁手工转录）vs 等倍率基准
  （=live V0 默认权重）在 pre2019 段上重放——与 score_evolution_study
  v0-lattice 同引擎同公式（``_collect_v0_window`` 权重无关收集 +
  ``technical_score`` 倍率重打分 + ``simulate_portfolio_topn`` 选中子集），
  报 Δmargin + **日簇配对 bootstrap** SE/CI95，按 v0.299 判决
  （a_sample 可疑闸：n 低于预期下限 ⇒ 跑数可疑不出判决 + thr 三分；
  复用 ``exit_c5_terminal.apply_c5``——判据/常量单源）：

  thr = γ×合并标尺（量级激活 n≥200 时）否则 0：
  - CI95 hi < thr ⇒ killed（整个置信区间够不到标尺，证据性否决）；
  - CI95 lo > 0 且点估计 ≥ thr ⇒ not_vetoed（显著性+量级双要）；
  - 其余 ⇒ untested（既不进下一步也不按证伪归档）。
  γ 分档 v0.276：窗间保留率 <0.5 ⇒ degraded ⇒ γ=0.75，否则 0.5；
  n<200 时 thr=0。

配对单元 = **entry_date 日簇**（与出场侧的差異：两变体从同一候选池
选股、选中集合不同 ⇒ 交易级 (code, entry_date) 1:1 配对不成立；同一
交易日两臂同进同出，保留日内相关结构，相关性不用猜 ρ）。

**全过也只记「C5 未否决」**——本工具只能杀不能确认；判决表达 Δ/SE
（k·SE），不压二值。两窗合并标尺从 score_evolution 报告自含读取
（criteria_readings R34-C1/C2），禁止手工转录。
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

from custos.core.paths import LOGS, RESEARCH_DIR
from custos.research import window_usage as wu
from custos.research.exit_c5_terminal import (  # 判据/常量单源
    GAMMA,
    GAMMA_DEGRADED,
    MIN_N_TAKEN,
    PRE2019_END,
    PRE2019_START,
    RETENTION_FLOOR,
    _margin_of,
    apply_c5,
    check_reach,
)
from custos.research.load_window import resolve_count
from custos.research.cost_sensitivity import cost_side_block
from custos.research.score_calibration_study import CONTRIB_LEG_KEYS


def load_frozen_genome(report: dict[str, Any]) -> dict[str, float]:
    """冻结候选倍率向量（自含读取，fail-closed）。

    只接**纯 v0-lattice** 基因组：``top_genome.multipliers`` 键集必须恰为
    CONTRIB_LEG_KEYS、值全非负有限、不全零；带 addon（思路二模式）拒收——
    加腿基因组的 pre2019 评估要 TS_RANK 序列，不属本终端口径。
    """
    tg = report.get("top_genome") or {}
    if tg.get("addon"):
        raise ValueError(
            "top_genome 带 addon（思路二模式）——本终端只接纯 v0-lattice 倍率基因组"
        )
    mult = tg.get("multipliers")
    if not isinstance(mult, dict) or not mult:
        raise ValueError("报告缺 top_genome.multipliers（非 v0-lattice 产物？）")
    legs = set(CONTRIB_LEG_KEYS)
    if set(mult) != legs:
        raise ValueError(
            "multipliers 键集与 CONTRIB_LEG_KEYS 不符："
            f"多 {sorted(set(mult) - legs)} 缺 {sorted(legs - set(mult))}"
        )
    out: dict[str, float] = {}
    for k, v in mult.items():
        f = float(v)
        if not math.isfinite(f) or f < 0:
            raise ValueError(f"倍率非法 {k}={v!r}（须非负有限）")
        out[k] = f
    if all(v == 0.0 for v in out.values()):
        raise ValueError("全零倍率基因组非法（空打分无排序语义）")
    return out


def yardstick_from_score_report(report: dict[str, Any]) -> dict[str, float]:
    """两窗按 n 加权合并 Δmargin（v0.273 标尺的 score 侧自含读取版）。

    数据源 = 报告 ``criteria_readings`` 的 R34-C1（双窗 n_taken）与
    R34-C2（双窗 Δmargin）——挖掘窗是格点搜索最大值，结构性偏高，合并后
    字面「γ=0.5 保留一半效应」与实际严格度一致（同 exit 侧口径）。
    """
    cr = report.get("criteria_readings") or {}
    c1 = cr.get("R34-C1") or {}
    c2 = cr.get("R34-C2") or {}
    dm, dj = c2.get("delta_mining"), c2.get("delta_judgment")
    nm, nj = c1.get("top_n_mining"), c1.get("top_n_judgment")
    if dm is None or dj is None or not nm or not nj:
        raise ValueError("报告缺 criteria_readings R34-C1/C2 双窗 Δmargin/n_taken")
    if dm <= 0:
        raise ValueError(f"挖掘窗 Δmargin={dm} ≤0——不该是候选（对账失败）")
    combined = (dm * nm + dj * nj) / (nm + nj)
    retention = dj / dm
    degraded = retention < RETENTION_FLOOR
    gamma = GAMMA_DEGRADED if degraded else GAMMA
    return {
        "d_margin_mining": float(dm),
        "d_margin_judgment": float(dj),
        "n_mining": float(nm),
        "n_judgment": float(nj),
        "combined": combined,
        "retention": retention,
        "candidate_degraded": degraded,
        "gamma": gamma,
        "bar": gamma * combined,
    }


def _select_taken(
    collected: list[dict], overrides: dict[str, float], top_n: int
) -> tuple[list[dict], dict]:
    """单倍率向量：technical_score 重打分 → top_n 组合 → 选中子集交易。

    与 ``_V0LatticeEvaluator.evaluate`` 同调用序（逐位同公式）；返回
    （taken, portfolio 汇总）——taken 是日簇 bootstrap 的原料。
    """
    from custos.pipeline.screening import score_candidates as sc  # noqa: PLC0415
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import score_evolution_study as ses  # noqa: PLC0415

    cands: list[dict[str, Any]] = []
    for it in collected:
        score, _level, _contrib = sc.technical_score(it["cand"], overrides)
        cands.append({**it["trade"], "score": score})
    taken: list[dict] = []
    pf = bt.simulate_portfolio_topn(
        cands, top_n=top_n, taken_out=taken, **ses._V0_PORTFOLIO
    )
    return taken, pf


def day_cluster_bootstrap(
    cand_taken: list[dict[str, Any]],
    base_taken: list[dict[str, Any]],
    *,
    seed: int,
    n_boot: int,
) -> dict[str, Any]:
    """日簇配对 bootstrap：成日重抽样 ⇒ Δmargin 分布的 SE/CI95。

    每次重抽样保留配对结构（抽中某日 ⇒ 两臂该日的选中交易同进），
    margin 双边重算后取差；任一边池空（margin None）该次跳过并计数。
    """
    rng = random.Random(seed)
    by_c: dict[str, list[dict]] = defaultdict(list)
    by_b: dict[str, list[dict]] = defaultdict(list)
    for t in cand_taken:
        by_c[t["entry_date"]].append(t)
    for t in base_taken:
        by_b[t["entry_date"]].append(t)
    days = sorted(set(by_c) | set(by_b))
    if not days:
        return {"se": None, "ci95": None, "n_boot_ok": 0, "n_days": 0}
    deltas: list[float] = []
    for _ in range(n_boot):
        pool_c: list[dict] = []
        pool_b: list[dict] = []
        for _ in range(len(days)):
            d = days[rng.randrange(len(days))]
            pool_c.extend(by_c.get(d, ()))
            pool_b.extend(by_b.get(d, ()))
        mc = _margin_of(pool_c)["margin"]
        mb = _margin_of(pool_b)["margin"]
        if mc is None or mb is None:
            continue
        deltas.append(mc - mb)
    if len(deltas) < 2:
        return {"se": None, "ci95": None, "n_boot_ok": len(deltas), "n_days": len(days)}
    deltas.sort()
    lo = deltas[int(0.025 * (len(deltas) - 1))]
    hi = deltas[int(0.975 * (len(deltas) - 1))]
    return {
        "se": statistics.pstdev(deltas),
        "ci95": [lo, hi],
        "n_boot_ok": len(deltas),
        "n_days": len(days),
    }


def _resolve_exit(args: Any, ap: argparse.ArgumentParser) -> dict:
    """出场档：默认 DEFAULT_EXIT_GRID 中档（pct5_trail08，与 P3 钉死一致）。"""
    from custos.research import strategy_grid as sg  # noqa: PLC0415

    grid = sg.DEFAULT_EXIT_GRID
    if not args.exit_name:
        e = grid[len(grid) // 2]
    else:
        hit = [e for e in grid if e["name"] == args.exit_name]
        if not hit:
            ap.error(
                f"--exit-name 未知: {args.exit_name}"
                f"（DEFAULT_EXIT_GRID: {[e['name'] for e in grid]}）"
            )
        e = hit[0]
    return {"name": e["name"], "params": dict(e.get("params") or {})}


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R36-C5 pre2019 终审终端（score 侧，判据 v0.299 可疑闸+thr 三分同族；只能杀不能确认）"
    )
    ap.add_argument(
        "--from-report",
        required=True,
        help="score_evolution_study 产物 JSON（冻结基因组+两窗标尺自含读取，禁手工转录）",
    )
    ap.add_argument("--codes-file", required=True, help="钉死宇宙 codes 表")
    ap.add_argument("--start", default=PRE2019_START)
    ap.add_argument("--end", default=PRE2019_END)
    ap.add_argument(
        "--count",
        type=int,
        default=None,
        help="每股加载 K 线根数（缺省=按 --start 自动推算：busday 交易日+300 "
        "预热，check_reach 实测兜底；显式值覆盖，如 100000=全历史。count 是"
        "「最新向前 N 根」滚动窗，到达不足会把窗口静默剪空——首跑 "
        "count=2000 只跑到 19 笔碎片宇宙）",
    )
    ap.add_argument("--cost-bps", type=float, default=25.0)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument(
        "--exit-name", default="", help="出场档（默认 DEFAULT_EXIT_GRID 中档）"
    )
    ap.add_argument("--n-bootstrap", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261005)
    ap.add_argument("--tag", default="r36_c5")
    return ap


def _check_pre2019(args: Any, ap: argparse.ArgumentParser) -> None:
    """只接受 pre2019 段内窗口（硬拒绝镜像：非 pre2019 一律 exit 2）。"""
    if args.start < PRE2019_START or args.end > PRE2019_END or args.start > args.end:
        ap.error(
            f"C5 只接受 pre2019 段内窗口（{PRE2019_START}~{PRE2019_END}），"
            f"实际 {args.start}~{args.end}"
        )


def run_c5(
    args: Any,
    collector: Optional[Callable[[Any], Optional[list[dict]]]] = None,
    select_fn: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    """C5 驱动：收集一次 → 两臂重打分 → 读数 → 日簇 bootstrap → 判决。

    ``collector``/``select_fn`` 可注入（测试合成数据）；None = 生产路径。
    空结果护栏：收集 0 候选或任一变体 0 选中 → RuntimeError（不落盘——
    防误读为「候选被杀」）。
    """
    from custos.research import score_evolution_study as ses  # noqa: PLC0415
    from custos.research.evolution.dual_window import Window  # noqa: PLC0415

    report = json.loads(Path(args.from_report).read_text(encoding="utf-8"))
    mult = load_frozen_genome(report)
    yard = yardstick_from_score_report(report)
    legs = list(CONTRIB_LEG_KEYS)
    window = Window(args.start, args.end)
    codes = [
        line.strip()
        for line in Path(args.codes_file).read_text().splitlines()
        if line.strip()
    ]

    if collector is not None:
        collected = collector(window)
    else:
        args.count = resolve_count(args.count, args.start)
        check_reach(args.count, args.start)
        exit_spec = _resolve_exit(args, _build_parser())
        collected = ses._collect_v0_window(args, codes, exit_spec, window)
    if not collected:
        raise RuntimeError(
            "C5 空结果护栏：pre2019 收集 0 候选（宇宙/窗口/数据有问题？）——不落盘"
        )

    sel = select_fn or _select_taken
    cand_ovr = ses._v0_mult_overrides([mult[k] for k in legs], legs)
    base_ovr = ses._v0_mult_overrides([1.0] * len(legs), legs)
    cand_taken, pf_c = sel(collected, cand_ovr, args.top_n)
    base_taken, pf_b = sel(collected, base_ovr, args.top_n)
    if not cand_taken or not base_taken:
        raise RuntimeError(
            f"C5 空结果护栏：候选 {len(cand_taken)} / 基准 {len(base_taken)} 笔选中"
            "——0 交易不许落盘（防误读为「候选被杀」）"
        )

    rd_c = _margin_of(cand_taken)
    rd_b = _margin_of(base_taken)
    d_margin = (
        rd_c["margin"] - rd_b["margin"]
        if rd_c["margin"] is not None and rd_b["margin"] is not None
        else None
    )
    boot = day_cluster_bootstrap(
        cand_taken, base_taken, seed=args.seed, n_boot=args.n_bootstrap
    )
    se = boot.get("se")
    n_taken = pf_c.get("n_taken")
    verdict = apply_c5(n_taken, d_margin, yard, boot.get("ci95"))
    nonzero = {k: v for k, v in mult.items() if v}
    _wu_k = wu.record_use(
        "R36-C5",
        "pre2019",
        args.tag,
        "C5 pre2019 终审",
        synthetic=(collector is not None or select_fn is not None),
    )
    from custos.research import provenance as pv  # noqa: PLC0415

    rep = {
        "version": 1,
        "tag": args.tag,
        "window_usage": {
            "window": "pre2019",
            "k": _wu_k,
            "note": wu.usage_note("R36-C5", "pre2019", _wu_k),
        },
        "provenance": pv.build(
            args,
            unit="R36-C5",
            criteria_version="v0.281/v0.299",
            pre_reg_doc=RESEARCH_DIR / "R36_perfect_b1_supervised_scoring.md",
        ),
        "config": {
            "from_report": str(args.from_report),
            "candidate_multipliers_nonzero": nonzero,
            "baseline": "等倍率（全 1 倍率 = live V0 默认权重）",
            "window": {"start": args.start, "end": args.end},
            "codes_file": str(args.codes_file),
            "count": args.count,
            "cost_bps": args.cost_bps,
            "top_n": args.top_n,
            "n_bootstrap": args.n_bootstrap,
            "seed": args.seed,
            "criteria": "R36-C5 同族 R37-C5 v0.299（a_sample 可疑闸：n<100 ⇒ 跑数可疑不出判决；thr 三分：CI95 hi<thr 杀/lo>0 且点估计≥thr 活/其余 untested；thr=bar（n≥200）否则 0；v0.276 γ 分档；标尺自含读取）；配对=entry_date 日簇",
        },
        "yardstick": yard,
        "candidate": {**rd_c, "n_taken": n_taken},
        "baseline": {**rd_b, "n_taken": pf_b.get("n_taken")},
        "d_margin": d_margin,
        "cost_sensitivity": cost_side_block(
            {"cand": cand_taken, "base": base_taken},
            base_bps=args.cost_bps,
            deltas={"d_margin": ("cand", "base")},
        ),
        "delta_over_se": (d_margin / se if d_margin is not None and se else None),
        "bootstrap": boot,
        "kill": verdict,
        "note": "全过也只记「C5 未否决」——本工具只能杀不能确认；判决表达 Δ/SE，不压二值",
    }
    if verdict["sample_suspicious"]:
        rep["warning"] = (
            f"n_taken={n_taken} 低于预期下限 {MIN_N_TAKEN} ⇒ 本次跑数判为可疑"
            "（数据完整性信号；check_reach 只守加载截断一种成因，宇宙/信号集/"
            "未来数据缺陷不在保护范围），不出判决——排查数据后重跑"
        )
    return rep


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = _build_parser()
    args = ap.parse_args(argv)
    args.cmdline = " ".join(argv) if argv is not None else " ".join(sys.argv[1:])
    _check_pre2019(args, ap)
    try:
        rep = run_c5(args)
    except (RuntimeError, ValueError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 2
    out_dir = LOGS / "score_evolution" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"_score_c5__{args.tag}.json"
    # 研究产物允许 NaN（区别于生产侧 paths.write_json 的 allow_nan=False）
    out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8"
    )
    k = rep["kill"]
    ds = rep["delta_over_se"]
    dm = f"{rep['d_margin']:+.6f}" if rep["d_margin"] is not None else "None"
    ds_txt = f"（{ds:+.2f}·SE）" if ds is not None else ""
    print(
        f"[C5] verdict={k['verdict']} "
        f"fired={[f['clause'] for f in k['fired']]} Δmargin={dm}{ds_txt}"
    )
    if "warning" in rep:
        print(f"[C5] ⚠️ WARNING: {rep['warning']}", file=sys.stderr)
    print(f"[C5] 报告 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
