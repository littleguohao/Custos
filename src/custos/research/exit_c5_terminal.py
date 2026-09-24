# -*- coding: utf-8 -*-
"""R37-C5 pre2019 终审终端（判据 v0.273 定稿的代码化身）。

只接受 pre2019 untouched 段（2010-01-01..2016-12-31）内的窗口——与
``score_calibration_study`` Phase 3「只接受 pre2019 输入」互为同族镜像
（挖掘/判定侧工具对 pre2019 硬拒绝，本工具对非 pre2019 硬拒绝）。

做的事（一次一单）：
  冻结候选基因组 vs 基准档 pct5_trail08 在 pre2019 段上 V0 重放（与
  exit_campaign 生产评估器同引擎同公式：信号缓存 + as-of V0 分 +
  summarize_trades/simulate_portfolio_topn + sg._margin），报
  Δmargin + 配对 bootstrap SE（交易按 (code, entry_date) 1:1 配对——
  两变体重放同一信号集，相关性不用猜 ρ），按 v0.273 否决条件判决：

  a. pre2019 n_taken < 100 ⇒ 杀（样本不足按杀计，保守）；
  b. pre2019 Δmargin ≤ 0 ⇒ 杀（符号条款）；
  c. n_taken ≥ 200 且 Δmargin < γ×两窗合并标尺 ⇒ 杀（量级条款；γ 分档
     v0.276：候选窗间保留率 <0.5 标 degraded ⇒ γ=0.75，否则 γ=0.5；
     n<200 时 SE≈0.025 任何 γ 失去意义 ⇒ 量级条款停用，只留
     样本量+符号——owner v0.273/v0.276 拍板在案）。

**全过也只记「C5 未否决」**——本工具只能杀不能确认；判决表达 Δ/SE
（k·SE），不压二值。两窗合并标尺从战役报告自含读取（--campaign-report），
禁止手工转录。
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

from custos.core.paths import LOGS

#: pre2019 untouched 终审段（写死；窗外交集即拒）
PRE2019_START, PRE2019_END = "2010-01-01", "2016-12-31"

#: v0.273 定稿 γ（非 degraded）+ v0.276 C2 量级条款采 B 的执行端：
#: 窗间保留率（判定 Δm ÷ 挖掘 Δm）< 0.5 ⇒ candidate_degraded ⇒ C5 γ=0.75
GAMMA = 0.5
GAMMA_DEGRADED = 0.75
RETENTION_FLOOR = 0.5

#: C1 同门槛样本量；v0.273 前置：n<200 量级条款停用
MIN_N_TAKEN = 100
MIN_N_FOR_MAGNITUDE = 200

#: 判决四态（只能杀不能确认）
VERDICT_KILLED = "killed"
VERDICT_NOT_VETOED = "not_vetoed"


def parse_genome_key(key: str) -> dict[str, Any]:
    """基因组键 → 参数字典（fail-closed：形态/家族/档位任一非法即 ValueError）。

    键形态 = ``sp{N}|{family}=off|{family}=v1[xv2]``（exit_genome.genome_key
    的逆；家族序任意、缺省家族按关处理，normalize 补全键）。
    """
    from custos.research.evolution import exit_genome as eg  # noqa: PLC0415

    parts = [p.strip() for p in str(key).split("|") if p.strip()]
    if not parts or not parts[0].startswith("sp"):
        raise ValueError(f"基因组键须以 sp<N> 起手: {key!r}")
    g: dict[str, Any] = {"stop_pct": float(parts[0][2:])}
    for part in parts[1:]:
        if "=" not in part:
            raise ValueError(f"基因组键段形态非法: {part!r}")
        fam, _, vals = part.partition("=")
        fam = fam.strip()
        if fam not in eg.FAMILIES:
            raise ValueError(f"未知家族 {fam!r}（合法：{sorted(eg.FAMILIES)}）")
        params = eg.FAMILIES[fam]
        if vals.strip() == "off":
            continue  # 关闭家族：normalize 归零/占位
        vs = vals.split("x")
        if len(vs) != len(params):
            raise ValueError(f"家族 {fam} 参数数不符：{vals!r}（须 {len(params)} 个）")
        for p, v in zip(params, vs):
            g[p] = float(v)
    bad = eg.validate(g)
    if bad:
        raise ValueError(f"基因组非法（fail-closed）：{'；'.join(bad)}")
    return eg.normalize(g)


def combined_yardstick(report: dict[str, Any]) -> dict[str, float]:
    """两窗按 n 加权合并 Δmargin（v0.273 标尺）——从战役报告自含读取。

    挖掘窗是数百基因组搜索的最大值，结构性偏高（实测 1.51×）；合并后字面
    「γ=0.5 保留一半效应」与实际严格度一致。返回 combined/bar(=γ×combined)
    及组成件（回填审计用）。
    """
    bc = report.get("best_candidate") or {}
    dm_m = bc.get("d_margin_mining")
    dm_j = bc.get("d_margin_judgment")
    n_m = (bc.get("mining") or {}).get("n_taken")
    n_j = (bc.get("judgment") or {}).get("n_taken")
    if None in (dm_m, dm_j) or not n_m or not n_j:
        raise ValueError("战役报告缺 best_candidate 双窗 Δmargin/n_taken")
    if dm_m <= 0:
        raise ValueError(f"挖掘窗 Δmargin={dm_m} ≤0——不该是候选（对账失败）")
    combined = (dm_m * n_m + dm_j * n_j) / (n_m + n_j)
    # v0.276 γ 分档：窗间保留率 < 0.5 ⇒ degraded ⇒ γ=0.75，否则 γ=0.5
    retention = dm_j / dm_m
    degraded = retention < RETENTION_FLOOR
    gamma = GAMMA_DEGRADED if degraded else GAMMA
    return {
        "d_margin_mining": float(dm_m),
        "d_margin_judgment": float(dm_j),
        "n_mining": float(n_m),
        "n_judgment": float(n_j),
        "combined": combined,
        "retention": retention,
        "candidate_degraded": degraded,
        "gamma": gamma,
        "bar": gamma * combined,
    }


def _margin_of(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """读数块（与 exit_campaign 生产评估器同公式同形状）。"""
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import strategy_grid as sg  # noqa: PLC0415

    tsum = bt.summarize_trades(trades)
    return {
        "margin": sg._margin(
            {"win": tsum.get("win_rate"), "payoff": tsum.get("payoff_ratio")}
        ),
        "win_rate": tsum.get("win_rate"),
        "payoff_ratio": tsum.get("payoff_ratio"),
        "expectancy_R": tsum.get("expectancy_R"),
        "n": tsum.get("n"),
    }


def pair_trades(
    cand: list[dict[str, Any]], base: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(code, entry_date) 1:1 配对——两变体重放同一信号集，配对天然成立。

    仅交集入对；丢对数由调用方如实上报（重放确定性 ⇒ 正常应为 0）。
    """
    bmap = {(t["code"], t["entry_date"]): t for t in base}
    pairs = [
        (t, bmap[(t["code"], t["entry_date"])])
        for t in cand
        if (t["code"], t["entry_date"]) in bmap
    ]
    return pairs


def paired_bootstrap(
    pairs: list[tuple[dict[str, Any]]], *, seed: int, n_boot: int
) -> dict[str, Any]:
    """配对 bootstrap：成对重抽样 ⇒ Δmargin 分布的 SE/CI（相关性不用猜 ρ）。

    每次重抽样保留配对结构（同一对进/同一只出），margin 双边重算后取差。
    """
    rng = random.Random(seed)
    n = len(pairs)
    deltas: list[float] = []
    for _ in range(n_boot):
        draw = [pairs[rng.randrange(n)] for _ in range(n)]
        mc = _margin_of([c for c, _ in draw])["margin"]
        mb = _margin_of([b for _, b in draw])["margin"]
        if mc is None or mb is None:
            continue
        deltas.append(mc - mb)
    if len(deltas) < 2:
        return {"se": None, "ci95": None, "n_boot_ok": len(deltas)}
    deltas.sort()
    lo = deltas[int(0.025 * (len(deltas) - 1))]
    hi = deltas[int(0.975 * (len(deltas) - 1))]
    return {
        "se": statistics.pstdev(deltas),
        "ci95": [lo, hi],
        "n_boot_ok": len(deltas),
    }


def apply_c5(
    n_taken: Optional[int],
    d_margin: Optional[float],
    yardstick: dict[str, float],
) -> dict[str, Any]:
    """v0.273 否决条件（任一即杀，一票否决；全过 = not_vetoed 非确认）。"""
    fired: list[dict[str, Any]] = []

    def _fire(clause: str, value: Any, threshold: Any) -> None:
        fired.append({"clause": clause, "value": value, "threshold": threshold})

    if n_taken is None or n_taken < MIN_N_TAKEN:
        _fire("a_sample", n_taken, f"n_taken≥{MIN_N_TAKEN}")
    elif d_margin is None:
        _fire("b_sign", None, "Δmargin>0（读数缺失按杀计，保守）")
    else:
        if d_margin <= 0:
            _fire("b_sign", d_margin, "Δmargin>0")
        # 量级条款：n<200 停用（SE≈0.025 任何 γ 失去意义——v0.273 前置）
        if n_taken >= MIN_N_FOR_MAGNITUDE and d_margin < yardstick["bar"]:
            _fire(
                "c_magnitude",
                d_margin,
                f"≥{yardstick['gamma']}×合并标尺={yardstick['bar']:.6f}",
            )
    return {
        "verdict": VERDICT_KILLED if fired else VERDICT_NOT_VETOED,
        "fired": fired,
        "magnitude_clause_active": (n_taken or 0) >= MIN_N_FOR_MAGNITUDE,
    }


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R37-C5 pre2019 终审终端（判据 v0.273 定稿；只能杀不能确认）"
    )
    ap.add_argument("--genome", required=True, help="冻结候选基因组键（sp8|...）")
    ap.add_argument("--codes-file", required=True, help="钉死宇宙 codes 表")
    ap.add_argument(
        "--campaign-report",
        required=True,
        help="战役报告 JSON（两窗合并标尺自含读取，禁手工转录；候选键对账）",
    )
    ap.add_argument("--start", default=PRE2019_START)
    ap.add_argument("--end", default=PRE2019_END)
    ap.add_argument("--count", type=int, default=2000)
    ap.add_argument("--cost-bps", type=float, default=25.0)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--n-bootstrap", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260924)
    ap.add_argument("--tag", default="r37_c5")
    return ap


def _check_pre2019(args: Any, ap: argparse.ArgumentParser) -> None:
    """只接受 pre2019 段内窗口（硬拒绝镜像：非 pre2019 一律 exit 2）。"""
    if args.start < PRE2019_START or args.end > PRE2019_END or args.start > args.end:
        ap.error(
            f"C5 只接受 pre2019 段内窗口（{PRE2019_START}~{PRE2019_END}），"
            f"实际 {args.start}~{args.end}"
        )


def _replay_variant(
    per_code: dict[str, dict], params: dict[str, Any], cost_bps: float
) -> list[dict[str, Any]]:
    """单变体信号重放（出场参数 = 基因组）——与 exit_campaign 评估器同调用序。"""
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    trades: list[dict[str, Any]] = []
    for code, pack in per_code.items():
        trs = bt.evaluate_trades(
            {code: pack["df"]},
            signals_in={code: pack["signals"]},
            amv_regime=pack["regime"],
            cost_bps=cost_bps,
            collect_all=True,
            **params,
        )
        for tr in trs:
            score = pack["scores"].get(tr["entry_date"])
            if score is None:
                continue
            trades.append({**tr, "score": score, "code": code})
    return trades


def _warm_pre2019(args: Any) -> dict[str, dict]:
    """pre2019 窗信号缓存+V0 as-of 分（与 exit_campaign._warm 同调用序）。"""
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import score_return_study as srs  # noqa: PLC0415

    codes = [
        l.strip() for l in Path(args.codes_file).read_text().splitlines() if l.strip()
    ]
    regime = bt.load_amv_regime(since=args.start)
    if not regime:
        raise RuntimeError("0AMV regime 读不到（compass_amv）——本机无数据？")
    index_df = (
        local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
        .sort_values("date")
        .reset_index(drop=True)
    )
    per_code: dict[str, dict] = {}
    for i, code in enumerate(codes, 1):
        if i % 200 == 0:
            print(f"[warmup] {args.start}~{args.end} {i}/{len(codes)}", file=sys.stderr)
        df = bt._load_one_bars(code, args.count, args.start, args.end)
        if df is None or not len(df):
            continue
        sigs: list[dict] = []
        bt.evaluate_trades(
            {code: df},
            scorer=bt.SCORERS["baseline"],
            entry_gate=bt.j_low_gate,
            amv_regime=regime,
            cost_bps=args.cost_bps,
            collect_all=True,
            signals_out=sigs,
        )
        if not sigs:
            continue
        scores: dict[str, float] = {}
        for s in sigs:
            try:
                score, _level, _contrib = srs.asof_technical_score(
                    df, index_df, s["i"], code
                )
            except Exception:  # noqa: BLE001 — 单信号评分失败丢该信号
                continue
            scores[s["date"]] = score
        per_code[code] = {"df": df, "signals": sigs, "scores": scores, "regime": regime}
    return per_code


def run_c5(args: Any, per_code: Optional[dict[str, dict]] = None) -> dict[str, Any]:
    """C5 驱动：重放两变体 → 读数 → 配对 bootstrap → 判决 → 报告 dict。

    ``per_code`` 可注入（测试合成数据）；None = 生产预热。空结果护栏：
    任一变体 0 交易 → RuntimeError（不落盘——防误读为「候选被杀」）。
    """
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import exit_campaign as ec  # noqa: PLC0415
    from custos.research.evolution import exit_genome as eg  # noqa: PLC0415

    genome = parse_genome_key(args.genome)
    report = json.loads(Path(args.campaign_report).read_text(encoding="utf-8"))
    camp_key = (report.get("best_candidate") or {}).get("key")
    if camp_key != args.genome:
        raise RuntimeError(
            f"候选键对账不符：CLI {args.genome!r} vs 战役报告 {camp_key!r}（防跑错候选）"
        )
    yard = combined_yardstick(report)

    if per_code is None:
        per_code = _warm_pre2019(args)
    params = {**eg.FIXED_PARAMS, **genome}
    base = {**eg.FIXED_PARAMS, **eg.baseline_genome()}
    cand_trades = _replay_variant(per_code, params, args.cost_bps)
    base_trades = _replay_variant(per_code, base, args.cost_bps)
    if not cand_trades or not base_trades:
        raise RuntimeError(
            f"C5 空结果护栏：候选 {len(cand_trades)} / 基准 {len(base_trades)} 笔"
            "——0 交易不许落盘（防误读为「候选被杀」）"
        )

    cands_c = [t for t in cand_trades]
    cands_b = [t for t in base_trades]
    rd_c = _margin_of(cands_c)
    rd_b = _margin_of(cands_b)
    pf_c = bt.simulate_portfolio_topn(cands_c, top_n=args.top_n, **ec.V0_PORTFOLIO)
    n_taken = pf_c.get("n_taken")
    d_margin = (
        rd_c["margin"] - rd_b["margin"]
        if rd_c["margin"] is not None and rd_b["margin"] is not None
        else None
    )

    pairs = pair_trades(cand_trades, base_trades)
    boot = paired_bootstrap(pairs, seed=args.seed, n_boot=args.n_bootstrap)
    se = boot.get("se")
    verdict = apply_c5(n_taken, d_margin, yard)

    return {
        "version": 1,
        "tag": args.tag,
        "config": {
            "genome_key": args.genome,
            "window": {"start": args.start, "end": args.end},
            "codes_file": str(args.codes_file),
            "count": args.count,
            "cost_bps": args.cost_bps,
            "top_n": args.top_n,
            "n_bootstrap": args.n_bootstrap,
            "seed": args.seed,
            "criteria": "R37-C5 v0.273 定稿 + v0.276 γ 分档（degraded 0.75/否则 0.5；合并标尺+bootstrap SE+n 前置）",
        },
        "yardstick": yard,
        "candidate": {**rd_c, "n_taken": n_taken},
        "baseline": rd_b,
        "d_margin": d_margin,
        "delta_over_se": (d_margin / se if d_margin is not None and se else None),
        "bootstrap": {**boot, "n_pairs": len(pairs)},
        "n_unpaired": len(cand_trades) - len(pairs),
        "kill": verdict,
        "note": "全过也只记「C5 未否决」——本工具只能杀不能确认；判决表达 Δ/SE，不压二值",
    }


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = _build_parser()
    args = ap.parse_args(argv)
    _check_pre2019(args, ap)
    try:
        rep = run_c5(args)
    except (RuntimeError, ValueError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 2
    out_dir = LOGS / "exit_campaign" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"_exit_c5__{args.tag}.json"
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
    print(f"[C5] 报告 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
