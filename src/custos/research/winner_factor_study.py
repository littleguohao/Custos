# -*- coding: utf-8 -*-
"""研究：0AMV 做多区间收益 top-50% 的票，在 J<13 信号日还命中哪些因子？

> ⚠️ **R11 警示（先读）**：B1 基准已实现口径为负期望且带幸存者偏差
> （governance/research/R11_baseline_margin_collapse.md）——本研究的收益读数
> **仅供相对排序**，不得引用为策略量级预期。
>
> ⚠️ **R3 报告纪律**：判别力研究曾证伪「无跨窗共同点」——任何 top-50% 富集发现
> **必须过前后半窗一致性**才许写进结论；小样本因子如实标不足。

研究问题（owner 2026-08-22）：每次 0AMV 做多区间收益最好的 50% 股票，在出现
基础信号（J<13）的时候，是否还满足其他信号？是否有其他规律？

方法：把 score_return_study 的技术分总量拆成**单因子命中面板**——信号日 as-of
截断（复用 ``score_return_study.asof_frames``，与 live 1800 链逐位对齐、已对拍）
喂 ``enrich_candidates.compute_metrics`` 得到完整 cand，再从 cand 提取 ~29 个
布尔命中（True/False/**None=unavailable**，算不出不当 False）。按区间切分、
区间内按收益切 top50/bottom50（复用 ``score_return_study.split_top_half``），
逐因子报：两侧命中率、lift=top/bottom、命中数支撑、前后半窗方向一致性、
区间级 lift>1 占比。

主臂 = 无止损基线（--stop-pct 50，样本最大）；稳健臂 = pct5（--stop-pct 5），
top 因子复算方向是否保持。``--top-frac``（默认 0.5）把赢家组收紧到前 N%
（0.10=TOP10% 赢家 vs 90% 对照，v0.105 owner 要求；分母/lift/支撑标注同步，
MIN_HIT_SUPPORT 不随 frac 放水——top 组变薄后小样本如实标不足）。

CLI::

    uv run python src/custos/research/winner_factor_study.py            # 主臂（基线）
    uv run python src/custos/research/winner_factor_study.py --stop-pct 5  # 稳健臂
    uv run python src/custos/research/winner_factor_study.py --top-frac 0.10 --stop-pct 12
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd  # noqa: E402

from custos.pipeline.screening import enrich_candidates as ec  # noqa: E402
from custos.pipeline.screening import score_candidates as sc  # noqa: E402
from custos.research import backtest_factors as bf  # noqa: E402
from custos.research import score_return_study as srs  # noqa: E402

# 小样本判定常量（如实标注，不静默）
MIN_HIT_SUPPORT = 30  # 任一侧命中数低于此 ⇒ 标「样本不足」，不进结论
MIN_INTERVAL_TRADES = 50  # 区间级一致性统计的最小笔数（同 R19 报告的口径）
MIN_INTERVAL_SIDE_HIT = 5  # 区间内单侧命中数低于此 ⇒ 该区间该因子不参与方向统计

R11_R3_WARNING = (
    "⚠️ R11：基准已实现口径为负、带幸存者偏差，读数仅供相对排序。"
    "⚠️ R3 纪律：任何富集发现必须过前后半窗一致性才进结论；小样本如实标注。"
)

# ── 因子面板键（稳定顺序；分组即 core/factors 的 live/证据层因子单项）──────────
# j_low 是基底（全体恒真），不在面板里。
PANEL_GROUPS: dict[str, list[str]] = {
    "entry_patterns": [
        "bbi_above",
        "reversal_k_candidate",
        "volume_contraction",
        "relative_strength_strong",
    ],
    "macd_technics": [
        "macd_zone1",
        "macd_zone1_restart",
        "macd_bottom_divergence",
        "macd_above_water",
        "macd_bar_grow",
        "macd_wm_bar_grow",
        "macd_top_divergence",
        "macd_three_peaks",
    ],
    "volume_detectors": [
        "bottom_volume",
        "leader_volume",
        "volume_sustain_mainline",
    ],
    "ignition": ["ignition", "pullback_shrink", "b1_ignition"],
    "b1_structure": [
        "five_day_entry",
        "repair_signals",
        "non_one_wave_confirmed",
        "non_one_wave_revoked",
    ],
    "weekly_j": ["weekly_j_low"],
    "platform_pullback": ["platform_pullback_b1"],
    "rsi_state": ["rsi_strong", "rsi_deep_oversold", "rsi_bull_div"],
    "distribution": ["distribution_watch", "distribution_high"],
}
PANEL_KEYS: list[str] = [k for keys in PANEL_GROUPS.values() for k in keys]

# signals 三态标注 → 面板键（state: hit/miss/unavailable → True/False/None）
_SIGNAL_KEY_MAP = {
    "platform_pullback_b1": "breakout_pullback_b1",
    "rsi_strong": "rsi_strong",
    "rsi_deep_oversold": "rsi_deep_oversold",
    "rsi_bull_div": "rsi_bull_div",
}


def _tri(d: Any, key: str = "hit") -> Optional[bool]:
    """{available, hit/status...} 检测器输出 → True/False/None（unavailable）。"""
    if not isinstance(d, dict) or not d.get("available"):
        return None
    return bool(d.get(key))


def _signal_tri(signals: dict, skey: str) -> Optional[bool]:
    """signal_labels 三态（hit/miss/unavailable）→ True/False/None。"""
    state = (signals.get(skey) or {}).get("state")
    if state == "hit":
        return True
    if state == "miss":
        return False
    return None


def build_factor_panel(cand: dict[str, Any]) -> dict[str, Optional[bool]]:
    """从 compute_metrics 的 cand 提取单因子命中面板（True/False/None）。

    None = unavailable（检测器数据不足/未评估），**绝不当 False**——
    命中率统计的分母只含可评估样本（同 signal_labels.summarize_signals 的口径）。
    """
    panel: dict[str, Optional[bool]] = {}
    patterns = cand.get("patterns") or {}
    for k in PANEL_GROUPS["entry_patterns"]:
        panel[k] = bool(patterns.get(k))  # patterns 五单项恒可评估（布尔）

    mt = cand.get("macd_technics") or {}
    mt_avail = bool(mt.get("available"))

    def _m(key: str, val: Any, extra_avail: bool = True) -> None:
        panel[key] = (bool(val) if extra_avail else None) if mt_avail else None

    _m("macd_zone1", mt.get("zone") == 1)
    _m("macd_zone1_restart", mt.get("zone1_restart"))
    _m("macd_bottom_divergence", (mt.get("bottom_divergence") or {}).get("hit"))
    _m("macd_above_water", mt.get("above_water"))
    _m("macd_bar_grow", mt.get("bar_grow"))
    # 周/月红柱腿单独有可用性标记（df_long 不足时 wm_available=False 如实标注）
    _m("macd_wm_bar_grow", mt.get("wm_bar_grow"), bool(mt.get("wm_available")))
    _m("macd_top_divergence", (mt.get("top_divergence") or {}).get("hit"))
    _m("macd_three_peaks", (mt.get("three_peaks") or {}).get("hit"))

    panel["bottom_volume"] = _tri(cand.get("bottom_volume"))
    panel["leader_volume"] = _tri(cand.get("leader_volume"))
    vs = cand.get("volume_sustain") or {}
    panel["volume_sustain_mainline"] = (
        (vs.get("status") == "mainline_confirmed") if vs.get("available") else None
    )

    panel["ignition"] = _tri(cand.get("ignition"))
    panel["pullback_shrink"] = _tri(cand.get("pullback_shrink"))
    # b1_ignition 是纯函数复合判定（输入全是已算中间态），无独立 available
    panel["b1_ignition"] = bool((cand.get("b1_ignition") or {}).get("hit"))

    panel["five_day_entry"] = _tri(cand.get("five_day_entry"))
    # repair_signals 无 available 概念（子信号数据不足时天然不命中）：恒可评估
    panel["repair_signals"] = bool(
        (cand.get("repair_signals") or {}).get("signals") or []
    )
    now = cand.get("non_one_wave") or {}
    now_avail = bool(now.get("available"))
    panel["non_one_wave_confirmed"] = (
        (now.get("status") == "confirmed") if now_avail else None
    )
    panel["non_one_wave_revoked"] = (
        (now.get("status") == "revoked") if now_avail else None
    )

    panel["weekly_j_low"] = (
        bool(cand.get("weekly_j_low")) if cand.get("weekly_j_available") else None
    )

    signals = cand.get("signals") or {}
    for pkey, skey in _SIGNAL_KEY_MAP.items():
        panel[pkey] = _signal_tri(signals, skey)

    dist = cand.get("distribution") or {}
    dist_avail = bool(dist.get("available"))
    panel["distribution_watch"] = (
        (dist.get("risk_level") == "watch") if dist_avail else None
    )
    panel["distribution_high"] = (
        (dist.get("risk_level") == "high") if dist_avail else None
    )

    # 键集合钉死：多键/缺键都是 bug（钉测断言）
    assert list(panel) == PANEL_KEYS, "面板键与 PANEL_KEYS 不一致"
    return panel


def panel_hook(
    df_full: pd.DataFrame, index_full: pd.DataFrame, i: int, code: str
) -> dict[str, Any]:
    """score_return_study.run_study 的 trade_hook：as-of 截断 → cand → 技术分 + 因子面板。"""
    df, df_long, index_asof = srs.asof_frames(df_full, index_full, i)
    cand = ec.compute_metrics(df, index_asof, code=code, df_long=df_long)
    score, level, contrib = sc.technical_score(cand, None)
    return {
        "tech_score": score,
        "tech_level": level,
        "factor_contrib": contrib,
        "panel": build_factor_panel(cand),
    }


# ---------------------------------------------------------------------------
# 富集统计
# ---------------------------------------------------------------------------


def split_top_frac(
    trades: list[dict[str, Any]], frac: float = 0.5, key: str = "ret"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按 key 降序切「前 frac 赢家组 / 其余对照组」（n_top=ceil(n×frac)，至少 1）。

    frac=0.5 时与 ``score_return_study.split_top_half`` 逐位一致
    （ceil(n/2)==(n+1)//2），旧行为不变；0.10 = 每区间收益前 10% 为赢家组。
    """
    ordered = sorted(trades, key=lambda t: t[key], reverse=True)
    n_top = max(1, math.ceil(len(ordered) * frac)) if ordered else 0
    return ordered[:n_top], ordered[n_top:]


def hit_stats(trades: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """单侧命中率：分母只含可评估（panel 值非 None）的样本。"""
    vals = [(t.get("panel") or {}).get(key) for t in trades]
    evaluable = [v for v in vals if v is not None]
    n_hit = sum(1 for v in evaluable if v)
    return {
        "n_eval": len(evaluable),
        "n_hit": n_hit,
        "rate": round(n_hit / len(evaluable), 4) if evaluable else None,
    }


def _lift(top: dict[str, Any], bottom: dict[str, Any]) -> Optional[float]:
    """lift = top50 命中率 / bottom50 命中率；分母 0 或缺失 ⇒ None。"""
    tr, br = top.get("rate"), bottom.get("rate")
    if tr is None or not br:
        return None
    return round(tr / br, 3)


def factor_enrichment(
    trades: list[dict[str, Any]],
    intervals: list[tuple[str, str]],
    key: str,
    top_frac: float = 0.5,
) -> dict[str, Any]:
    """单因子富集全景：overall lift + 命中支撑 + 前后半窗一致性 + 区间级方向一致性。

    ``top_frac``：赢家组占比（0.5=旧行为 top50%；0.10=TOP10% 赢家 vs 90% 对照）。
    口径同步：命中率分母始终是各组**可评估**样本数（与组大小无关）；top 组收紧后
    命中数支撑自然变薄，support 标注如实反映（MIN_HIT_SUPPORT 不随 frac 放水）。
    """
    for t in trades:
        if "interval_idx" not in t:
            t["interval_idx"] = srs.interval_of(t["entry_date"], intervals)
    top, bottom = split_top_frac(trades, top_frac)
    ts, bs = hit_stats(top, key), hit_stats(bottom, key)
    support_ok = ts["n_hit"] >= MIN_HIT_SUPPORT and bs["n_hit"] >= MIN_HIT_SUPPORT

    # 前后半窗（切于 entry_date 中位，同 srs.half_window_check 口径）
    dates = sorted(t["entry_date"] for t in trades)
    mid = dates[len(dates) // 2]
    halves = {}
    for name, subset in (
        ("first", [t for t in trades if t["entry_date"] <= mid]),
        ("second", [t for t in trades if t["entry_date"] > mid]),
    ):
        htop, hbot = split_top_frac(subset, top_frac)
        ht, hb = hit_stats(htop, key), hit_stats(hbot, key)
        halves[name] = {
            "top_rate": ht["rate"],
            "bottom_rate": hb["rate"],
            "lift": _lift(ht, hb),
            "n_hit_top": ht["n_hit"],
            "n_hit_bottom": hb["n_hit"],
        }
    dirs = [
        (h["top_rate"] > h["bottom_rate"])
        for h in halves.values()
        if h["top_rate"] is not None and h["bottom_rate"] is not None
    ]
    half_consistent = None if len(dirs) < 2 else (dirs[0] == dirs[1])

    # 区间级方向一致性（lift>1 的区间占比；小样本区间不参与）
    iv_dirs = 0
    iv_total = 0
    for idx, (_s, _e) in enumerate(intervals):
        sub = [t for t in trades if t["interval_idx"] == idx]
        if len(sub) < MIN_INTERVAL_TRADES:
            continue
        itop, ibot = split_top_frac(sub, top_frac)
        it, ib = hit_stats(itop, key), hit_stats(ibot, key)
        if it["n_hit"] < MIN_INTERVAL_SIDE_HIT or ib["n_hit"] < MIN_INTERVAL_SIDE_HIT:
            continue
        if it["rate"] is None or ib["rate"] is None:
            continue
        iv_total += 1
        iv_dirs += 1 if it["rate"] > ib["rate"] else 0

    return {
        "factor": key,
        "top_frac": top_frac,
        "top50": ts,  # 键名沿用（昨晚 JSON 的消费脚本在读）；top_frac 字段标真实口径
        "bottom50": bs,
        "lift": _lift(ts, bs),
        "support": "ok"
        if support_ok
        else f"不足(任一侧命中<{MIN_HIT_SUPPORT}:top={ts['n_hit']},bottom={bs['n_hit']})",
        "half_window": {
            "split_date": mid,
            **halves,
            "consistent": half_consistent,
        },
        "interval_consistency": {
            "n_intervals": iv_total,
            "n_lift_gt1": iv_dirs,
            "frac_lift_gt1": round(iv_dirs / iv_total, 3) if iv_total else None,
        },
    }


def verdict_of(en: dict[str, Any]) -> str:
    """结论标签（机械规则，防拍脑袋）：

    ✅ 富集且稳定：lift>1.1 且半窗一致（同向）且区间 lift>1 占比≥0.6 且样本足
    ⛔ 反向（输家富集）：lift<0.9 且半窗一致（同向）且区间 lift>1 占比≤0.4 且样本足
    ⚠️ 其余：噪声/不稳/样本不足——不写进结论
    """
    if en["support"] != "ok":
        return "⚠️ 样本不足"
    lift = en["lift"]
    half = en["half_window"]
    iv = en["interval_consistency"]
    frac = iv.get("frac_lift_gt1")
    if lift is None or half.get("consistent") is not True or frac is None:
        return "⚠️ 噪声/不稳"
    if (
        lift > 1.1
        and frac >= 0.6
        and half["first"]["top_rate"] > half["first"]["bottom_rate"]
    ):
        return "✅ 富集且稳定"
    if (
        lift < 0.9
        and frac <= 0.4
        and half["first"]["top_rate"] < half["first"]["bottom_rate"]
    ):
        return "⛔ 反向（输家侧富集）"
    return "⚠️ 噪声/不稳"


def build_report(
    trades: list[dict[str, Any]],
    intervals: list[tuple[str, str]],
    top_frac: float = 0.5,
) -> dict[str, Any]:
    """逐笔（带 panel）→ 全因子富集报告。"""
    for t in trades:
        t["interval_idx"] = srs.interval_of(t["entry_date"], intervals)
    factors = [factor_enrichment(trades, intervals, k, top_frac) for k in PANEL_KEYS]
    # 展示序：lift 降序（None 排尾），组内键序见 PANEL_KEYS
    factors.sort(key=lambda e: -(e["lift"] or 0))
    return {
        "r11_r3_warning": R11_R3_WARNING,
        "n_trades": len(trades),
        "top_frac": top_frac,
        "intervals": [[s, e] for s, e in intervals],
        "overall_stats": srs.ret_stats(trades),
        "panel_keys": PANEL_KEYS,
        "panel_groups": PANEL_GROUPS,
        "min_hit_support": MIN_HIT_SUPPORT,
        "min_interval_trades": MIN_INTERVAL_TRADES,
        "factors": factors,
    }


def print_report(rep: dict[str, Any]) -> None:
    """stdout 中文摘要。"""
    top_pct = round(rep.get("top_frac", 0.5) * 100)
    bot_pct = 100 - top_pct
    print("\n" + "=" * 76)
    print(f"0AMV 做多区间 J<13 信号：赢家组（top-{top_pct}%）因子富集分析")
    print("=" * 76)
    print(rep["r11_r3_warning"])
    os_ = rep["overall_stats"]
    print(
        f"\n样本：{rep['n_trades']} 笔，{len(rep['intervals'])} 个做多区间；"
        f"全体均收 {os_['avg_ret'] * 100:.2f}% / 胜率 {os_['win_rate'] * 100:.1f}%"
    )
    print(
        f"\n── 因子富集（lift=top{top_pct}%命中率/bottom{bot_pct}%命中率；样本阈值 "
        f"{rep['min_hit_support']}；区间级最小 {rep['min_interval_trades']} 笔）"
    )
    print(
        f"因子 | top{top_pct}% | bottom{bot_pct}% | lift | 半窗一致 | 区间lift>1占比 | 判定"
    )
    for en in rep["factors"]:
        t, b = en["top50"], en["bottom50"]
        hw = en["half_window"]
        cons = (
            "一致"
            if hw.get("consistent")
            else ("⚠️翻转" if hw.get("consistent") is False else "缺")
        )
        iv = en["interval_consistency"]
        print(
            f"  {en['factor']:<28} {t['rate'] if t['rate'] is not None else '—':>6} "
            f"({t['n_hit']:>4}/{t['n_eval']:<5}) | "
            f"{b['rate'] if b['rate'] is not None else '—':>6} "
            f"({b['n_hit']:>4}/{b['n_eval']:<5}) | "
            f"{en['lift'] if en['lift'] is not None else '—':>5} | {cons:<4} | "
            f"{iv.get('frac_lift_gt1') if iv.get('frac_lift_gt1') is not None else '—':>5}"
            f"({iv['n_lift_gt1']}/{iv['n_intervals']}) | {verdict_of(en)}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--max-stocks", type=int, default=400, help="宇宙抽样只数（0=全市场）"
    )
    ap.add_argument("--seed", type=int, default=0, help="抽样种子（可复现）")
    ap.add_argument("--start", default="2010-01-01", help="0AMV regime 起点")
    ap.add_argument("--cost-bps", type=float, default=srs.COST_BPS, help="往返成本基点")
    ap.add_argument(
        "--stop-pct",
        type=float,
        default=srs.STOP_PCT_WIDE,
        help="初始止损 %%（**默认 50 = 无止损基线主臂**；稳健臂用 5）",
    )
    ap.add_argument(
        "--cost-zone-bars",
        type=int,
        default=0,
        help="「不涨就拍」（默认 0=关；两臂都不用，cz3 出场侧见 R10）",
    )
    ap.add_argument(
        "--top-frac",
        type=float,
        default=0.5,
        help="赢家组占比（默认 0.5=旧行为 top50%%；0.10=每区间收益前 10%% 为赢家组）",
    )
    ap.add_argument("--out", default="", help="结果 JSON 路径")
    return ap


def main(argv: Optional[list] = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    if not 0 < args.top_frac < 1:
        ap.error("--top-frac 必须在 (0, 1) 开区间（0.5=top50%，0.10=top10%）")

    regime = bf.load_amv_regime(since=args.start)
    if not regime:
        ap.error("读不到指南针 0AMV 数据（compass_amv）；请在有指南针的机器运行")
    intervals = srs.long_intervals(regime)
    print(
        f"[INFO] 0AMV regime {len(regime)} 个交易日，做多区间 {len(intervals)} 段",
        file=sys.stderr,
    )

    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    base = local_tdx_data.list_local_vipdoc_codes()
    codes = bf.sample_codes(base, args.max_stocks, args.seed)
    print(
        f"[INFO] universe=local_vipdoc 共 {len(base)} 只，取 {len(codes)} 只（seed={args.seed}）",
        file=sys.stderr,
    )
    index_df = (
        local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
        .sort_values("date")
        .reset_index(drop=True)
    )

    # 与 score_return_study 同引擎同截断，hook 注入因子面板（口径零重复）
    trades = srs.run_study(
        codes,
        regime,
        index_df,
        cost_bps=args.cost_bps,
        stop_pct=args.stop_pct,
        cost_zone_bars=args.cost_zone_bars,
        trade_hook=panel_hook,
    )
    if not trades:
        print("⛔ 0 笔交易——检查 regime 数据与宇宙", file=sys.stderr)
        return 1

    rep = build_report(trades, intervals, args.top_frac)
    rep["config"] = {
        "signal": "日KDJ J<13（j_low_gate）",
        "regime": "仅 0AMV 做多区间",
        "exit": f"BBI 止盈（连破2根）+ pct {args.stop_pct}% 初始止损"
        + (f" + cost_zone {args.cost_zone_bars}" if args.cost_zone_bars else ""),
        "cost_bps": args.cost_bps,
        "top_frac": args.top_frac,
        "panel": f"{len(PANEL_KEYS)} 个单因子命中（as-of 截断，unavailable=None）",
        "max_stocks": args.max_stocks,
        "seed": args.seed,
        "start": args.start,
        "n_codes": len(codes),
    }
    rep["trades"] = trades

    top_tag = (
        f"_top{round(args.top_frac * 100):g}" if args.top_frac != 0.5 else ""
    )  # 旧默认不加标签（文件名与 v0.104 两臂兼容）
    stop_tag = f"_stop{args.stop_pct:g}"
    cz_tag = f"_cz{args.cost_zone_bars}" if args.cost_zone_bars else ""
    out = (
        Path(args.out)
        if args.out
        else (
            Path("artifacts/logs/winner_factor_study")
            / f"winner_factor_study_s{args.seed}_n{len(codes)}{top_tag}{stop_tag}{cz_tag}.json"
        )
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    bf.write_json_stream(out, rep, big=len(trades) > 20000)
    print(f"[OK] 写出 {out}（{len(trades)} 笔）")
    print_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
