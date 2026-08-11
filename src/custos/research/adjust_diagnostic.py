#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""复权口径诊断：量化未复权数据对回测与选股的实际影响。

背景（90_research_summary.md:31/40 记录、一直未解决）：

    链路                              数据源          复权
    live 选股（18:00）                vipdoc .day     未复权
    回测 --data-source tdx（默认）     vipdoc .day     未复权
    回测 --data-source qlib/csv       E:\\S_DATA       前复权

`local_tdx_data.get_adjusted_daily()` 有复权能力，但**只被 CLI --mode adjust 调用**，
生产链与回测链都没用过；仓库里也没有任何除权检测。

未复权的三类危害（合成数据实测：同一段真实上涨走势，未复权 −42.50% vs 前复权 +25.00%）：

    ① 假止损   —— 除权日跳空穿过止损位，移动止损尤其致命（它跟随除权前的高价）
    ② 假信号   —— 除权日价格骤降 ⇒ J 骤降 ⇒ 误判 J<13（生产链唯一 enabled 的公式）
    ③ 假跌停   —— 除权跌幅 >10% 被 tradable_flags 判为跌停不可卖，止损被顺延

且偏差**有方向性**：除权源于分红送转，高分红股（白马/价值股）除权更频繁，
所以未复权会系统性惩罚高分红股——不是能靠大样本抵消的随机噪声。

用法：
    # ① 检测 tdx 数据里的疑似除权跳空（不需要 qlib）
    uv run python src/custos/research/adjust_diagnostic.py --scan --sample 300

    # ② 与前复权源逐日对比，精确识别除权（需要 S_DATA）
    uv run python src/custos/research/adjust_diagnostic.py --compare --sample 100

    # ③ 同一批票同参数，两个口径各跑一遍回测，看结论差多少（最有说服力）
    uv run python src/custos/research/adjust_diagnostic.py --backtest-diff --sample 300
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import subprocess
import sys
from typing import Any, Optional

BASE = pathlib.Path(__file__).resolve().parents[3]


import numpy as np                                            # noqa: E402
from custos.core.paths import LOGS                                        # noqa: E402
import pandas as pd                                           # noqa: E402

# 送转除权的典型跳空幅度（10送N ⇒ 价格 ×10/(10+N)）
SPLIT_RATIOS = {
    "10送1": 1 - 10 / 11, "10送2": 1 - 10 / 12, "10送3": 1 - 10 / 13,
    "10送5": 1 - 10 / 15, "10送8": 1 - 10 / 18, "10送10": 1 - 10 / 20,
}
GAP_THRESHOLD = 0.11      # 跳空超过 11% 才算可疑（A 股主板跌停 10%，留 1pp 余量）


def detect_gaps(df: pd.DataFrame, thr: float = GAP_THRESHOLD) -> list[dict[str, Any]]:
    """检测疑似除权跳空。

    判据：``open / prev_close - 1 <= -thr``。真跌停不会**跳空**这么多（跌停是
    盘中封板，开盘价通常不会直接低于前收 11% 以上，除非一字跌停开盘）。
    所以这里会混入"一字跌停"，需靠 ``--compare`` 与前复权源比对才能精确区分。
    """
    if df is None or df.empty or len(df) < 2:
        return []
    o = df["open"].astype(float).to_numpy()
    c = df["close"].astype(float).to_numpy()
    h = df["high"].astype(float).to_numpy()
    lo = df["low"].astype(float).to_numpy()
    d = df["date"].astype(str).to_numpy()
    out = []
    prev = c[:-1]
    gap = np.divide(o[1:], prev, out=np.zeros_like(prev), where=prev > 0) - 1
    for i in np.where(gap <= -thr)[0]:
        j = i + 1
        rng = (h[j] - lo[j]) / o[j] if o[j] else 0.0
        g = float(gap[i])
        # 与典型送转比例的最近匹配（±1.5pp 内认为吻合）
        near = min(SPLIT_RATIOS.items(), key=lambda kv: abs(kv[1] + g))
        matched = near[0] if abs(near[1] + g) <= 0.015 else None
        out.append({"date": str(d[j])[:10], "gap": round(g, 4),
                    "intraday_range": round(float(rng), 4),
                    "prev_close": round(float(prev[i]), 3),
                    "open": round(float(o[j]), 3),
                    "split_match": matched})
    return out


def _load_tdx(codes: list[str], count: int) -> dict[str, pd.DataFrame]:
    from custos.datasource.local_tdx import local_tdx_data
    out = {}
    for c in codes:
        try:
            df = local_tdx_data.get_ohlcv_table(c, count=count)
            if df is not None and not df.empty:
                out[c] = df.reset_index(drop=True)
        except Exception as e:                                 # noqa: BLE001
            print(f"[WARN] tdx {c}: {e}", file=sys.stderr)
    return out


def _universe(sample: int, seed: int = 0) -> list[str]:
    from custos.datasource.local_tdx import local_tdx_data
    try:
        codes = sorted(local_tdx_data.list_local_vipdoc_codes())
    except Exception as e:                                     # noqa: BLE001
        print(f"[ERR] 读不到本地代码表: {e}", file=sys.stderr)
        return []
    rng = np.random.default_rng(seed)
    if sample and sample < len(codes):
        idx = rng.choice(len(codes), size=sample, replace=False)
        codes = [codes[i] for i in sorted(idx)]
    return codes


def _risk_frac_stats(bars: dict[str, pd.DataFrame]) -> tuple[float, float]:
    """B1 实际止损空间：J<13 入场时 stop_mode='low' 给出的 (entry-low)/entry。

    为什么要算这个：送转除权动辄 −20%~−50%，一眼就知道有害；但**现金分红除息只有
    1%~5%**，容易被当成小事。而 B1 是超卖贴低进场，止损位就是当日最低价——
    risk_frac 常只有 1~3%，**连 2% 的除息都能触发假止损**。
    分红股数量远多于送转股，所以这条路径影响的样本更多。
    """
    fr = []
    for df in bars.values():
        if df is None or len(df) < 2:
            continue
        c = df["close"].astype(float).to_numpy()
        lo = df["low"].astype(float).to_numpy()
        m = c > 0
        fr.extend(((c[m] - lo[m]) / c[m]).tolist())
    if not fr:
        return 0.0, 0.0
    fr = [x for x in fr if 0 <= x < 0.3]
    return (statistics.median(fr) if fr else 0.0,
            float(np.percentile(fr, 25)) if fr else 0.0)


def cmd_scan(sample: int, count: int, seed: int, thr: float = GAP_THRESHOLD) -> int:
    codes = _universe(sample, seed)
    if not codes:
        return 2
    print(f"扫描 {len(codes)} 只 × 最近 {count} 根 K 线…\n")
    bars = _load_tdx(codes, count)
    if not bars:
        print("[ERR] 一只都没读到——检查 TDX_ROOT")
        return 2
    n = len(bars)

    # 分档统计：小幅除息同样有害，不能只看 ≥11% 的送转
    bands = [(0.02, 0.05, "2%~5%   现金分红除息为主"),
             (0.05, 0.11, "5%~11%  大额分红/小比例送转"),
             (0.11, 0.25, "11%~25% 送转(10送2~10送3)"),
             (0.25, 1.00, "≥25%    送转(10送5 及以上)")]
    counts = {b[2]: 0 for b in bands}
    codes_hit = {b[2]: set() for b in bands}
    all_gaps = []
    for c, df in bars.items():
        for g in detect_gaps(df, thr=0.02):        # 低阈值全扫，再分档
            g["code"] = c
            all_gaps.append(g)
            a = abs(g["gap"])
            for lo_, hi_, label in bands:
                if lo_ <= a < hi_:
                    counts[label] += 1
                    codes_hit[label].add(c)
                    break

    med_rf, q25_rf = _risk_frac_stats(bars)
    print(f"读到 {n} 只，共检出 {len(all_gaps)} 次向下跳空（阈值 2%）\n")
    print(f"{'幅度档':<30}{'次数':>7}{'涉及股票':>10}{'占样本':>9}")
    print("-" * 58)
    for _, _, label in bands:
        k, s = counts[label], len(codes_hit[label])
        print(f"{label:<30}{k:>7}{s:>10}{s / n:>8.1%}")

    print("\nB1 实际止损空间（stop_mode=low ⇒ (close−low)/close）：")
    print(f"  中位 {med_rf:.2%}   下四分位 {q25_rf:.2%}")
    danger = sum(counts[label] for lo_, _, label in bands if lo_ >= 0.02)
    print(f"\n⚠️ **止损空间中位仅 {med_rf:.2%}，而检出的 {danger} 次跳空全部 ≥2%**")
    print("   ⇒ 每一次都足以在未复权回测里触发假止损。送转跳空(-20%~-50%)一眼可见，")
    print("     但**现金分红除息(2%~5%)数量更多、更隐蔽**——分红股远多于送转股。")

    split_like = [g for g in all_gaps if g["split_match"]]
    if split_like:
        print(f"\n其中 {len(split_like)} 次幅度吻合典型送转比例 ⇒ 几乎确定是除权：")
        for x in sorted(split_like, key=lambda v: v["gap"])[:10]:
            print(f"  {x['code']} {x['date']} {x['gap']:+.1%} "
                  f"({x['prev_close']}→{x['open']})  ← 吻合 {x['split_match']}")

    big = [g for g in all_gaps if abs(g["gap"]) >= thr]
    if big:
        gs = sorted(x["gap"] for x in big)
        print(f"\n≥{thr:.0%} 的深跳空：{len(big)} 次，中位 {statistics.median(gs):.1%}，"
              f"最深 {gs[0]:.1%}")
    print("\n⚠️ 局限：本扫描按跳空幅度判定，无法区分「除权」与「一字跌停/科创板深跌」。")
    print("   要精确区分，跑 --compare 与前复权源逐日比对。")
    return 0


def cmd_compare(sample: int, count: int, seed: int, s_data_root: str) -> int:
    """与前复权源逐日对比 —— 精确区分除权与真跌停。"""
    try:
        from custos.datasource import s_data
    except Exception as e:                                     # noqa: BLE001
        print(f"[ERR] 导不了 s_data: {e}")
        return 2
    root = pathlib.Path(s_data_root) / "Q_DATA"
    if not root.exists():
        print(f"[ERR] 找不到 {root}（用 --s-data-root 指定，或跳过本项只跑 --scan）")
        return 2
    codes = _universe(sample, seed)
    tdx = _load_tdx(codes, count)
    try:
        adj = s_data.load_bars_qlib(list(tdx.keys()), count=count, root=root)
    except Exception as e:                                     # noqa: BLE001
        print(f"[ERR] 读前复权失败: {e}")
        return 2
    print(f"逐日对比 {len(tdx)} 只未复权 vs {len(adj)} 只前复权\n")
    confirmed, false_pos, no_ref = 0, 0, 0
    rows = []
    for c, df in tdx.items():
        a = adj.get(c)
        gaps = detect_gaps(df)
        if not gaps:
            continue
        if a is None or a.empty:
            no_ref += len(gaps)
            continue
        am = {str(d)[:10]: (o, cl) for d, o, cl in
              zip(a["date"].astype(str), a["open"].astype(float),
                  a["close"].astype(float))}
        ac = a["close"].astype(float).to_numpy()
        ad = [str(x)[:10] for x in a["date"].astype(str)]
        pos = {d: i for i, d in enumerate(ad)}
        for g in gaps:
            i = pos.get(g["date"])
            if i is None or i == 0:
                no_ref += 1
                continue
            adj_gap = am[g["date"]][0] / ac[i - 1] - 1 if ac[i - 1] > 0 else 0.0
            # 未复权大跳空、前复权几乎没跌 ⇒ 除权确认
            if adj_gap > g["gap"] + 0.05:
                confirmed += 1
                rows.append((c, g["date"], g["gap"], adj_gap, g["split_match"]))
            else:
                false_pos += 1
    tot = confirmed + false_pos + no_ref
    print(f"疑似跳空 {tot} 次：")
    print(f"  ✓ 除权确认   {confirmed} 次（未复权大跌、前复权没跌）")
    print(f"  · 真实下跌   {false_pos} 次（两个口径都跌 ⇒ 一字跌停等）")
    print(f"  ? 无参照     {no_ref} 次（前复权源缺该票/该日）")
    if confirmed:
        print(f"\n⇒ **{confirmed / max(confirmed + false_pos, 1):.0%} 的大跳空是除权**，"
              f"未复权回测把它们全当成了真实暴跌\n")
        print("样例（代码 日期 未复权跳空 → 前复权实际）：")
        for c, d, ug, ag, sm in sorted(rows, key=lambda r: r[2])[:12]:
            tag = f"  ← {sm}" if sm else ""
            print(f"  {c} {d}  {ug:+.1%} → {ag:+.1%}{tag}")
    return 0


def cmd_backtest_diff(sample: int, seed: int, s_data_root: str,
                      extra: Optional[list[str]] = None) -> int:
    """同一批票同参数，两个口径各跑一遍回测——最有说服力的证据。"""
    script = BASE / "src" / "custos" / "research" / "backtest_factors.py"
    outdir = LOGS / "adjust_diag"
    outdir.mkdir(parents=True, exist_ok=True)
    common = ["--trade-sim", "--entry-filter", "j_low", "--scorer", "b1_dual",
              "--cost-bps", "25", "--scale-out", "0.5",
              "--universe-sample", str(sample), "--seed", str(seed)] + (extra or [])
    runs = {
        "unadjusted_tdx": ["--universe-local", "--data-source", "tdx"],
        "adjusted_qlib": ["--data-source", "qlib", "--s-data-root", s_data_root],
    }
    res = {}
    for name, args in runs.items():
        out = outdir / f"{name}.json"
        print(f"\n[RUN ] {name}")
        r = subprocess.run([sys.executable, str(script)] + common + args
                           + ["--out", str(out)], cwd=str(BASE))
        if r.returncode != 0 or not out.exists():
            print(f"[FAIL] {name} exit={r.returncode}")
            continue
        try:
            d = json.loads(out.read_text(encoding="utf-8"))
        except Exception as e:                                 # noqa: BLE001
            print(f"[WARN] {name} 结果读不了: {e}")
            continue
        s = d.get("trade_sim") or d.get("summary") or d
        res[name] = s

    if len(res) < 2:
        print("\n两个口径没都跑成，无法对比")
        return 1
    u, a = res["unadjusted_tdx"], res["adjusted_qlib"]
    print("\n" + "=" * 70)
    print("同参数、两个复权口径的回测结论差异")
    print("=" * 70)
    print(f"{'指标':<16}{'未复权(tdx,现状)':>20}{'前复权(qlib)':>18}")
    print("-" * 70)
    for k, label, pct in [("n", "笔数", False), ("win_rate", "胜率", True),
                          ("expectancy", "期望/笔", True),
                          ("expectancy_R", "期望R", False),
                          ("total_R", "累计R", False),
                          ("payoff_ratio", "盈亏比", False),
                          ("avg_win", "均盈", True), ("avg_loss", "均亏", True),
                          ("avg_holding", "均持(根)", False)]:
        uv_, av = u.get(k), a.get(k)
        f = (lambda v: f"{v * 100:+.2f}%" if isinstance(v, (int, float)) else "—") if pct \
            else (lambda v: f"{v}" if v is not None else "—")
        print(f"{label:<16}{f(uv_):>20}{f(av):>18}")
    print("\n出场原因（未复权侧 stop 占比若明显更高，就是假止损在起作用）")
    for name, s in (("未复权", u), ("前复权", a)):
        rs = s.get("exit_reasons") or {}
        tot = sum(v.get("n", 0) for v in rs.values()) or 1
        parts = [f"{k} {v['n']}({v['n'] / tot:.0%})" for k, v in
                 sorted(rs.items(), key=lambda kv: -kv[1].get("n", 0))[:4]]
        print(f"  {name}: {'  '.join(parts)}")
    print("\n⚠️ 两个口径的宇宙不完全相同（qlib 含退市股），所以差异里既有复权因素、")
    print("   也有幸存者偏差因素。要单独隔离复权，用 --compare 的逐日比对。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="复权口径诊断")
    ap.add_argument("--scan", action="store_true", help="检测 tdx 数据里的疑似除权跳空")
    ap.add_argument("--compare", action="store_true", help="与前复权源逐日对比（精确识别）")
    ap.add_argument("--backtest-diff", action="store_true", help="两个口径各跑一遍回测")
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--count", type=int, default=500)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--s-data-root", default="")
    ap.add_argument("--gap-threshold", type=float, default=GAP_THRESHOLD,
                    # ⚠️ argparse 会对 help 串做 `% params` 格式化，所以**里面的每个 % 都要写成 %%**。
                    # 2026-08-07 修：原写法用 `{GAP_THRESHOLD:.0%}` 注入了一个**未转义的** %，
                    # 于是 `--help` 直接 `ValueError: unsupported format character ')'` ——
                    # 后面那个 `2%%` 转义是对的，f-string 注进来的那个漏了。
                    # `--help` 崩掉等于这个脚本的参数无法被发现。
                    help=f"深跳空阈值(默认 {GAP_THRESHOLD * 100:.0f}%%);分档统计一律从 2%% 起扫")
    a = ap.parse_args()

    import os
    s_root = a.s_data_root or os.environ.get("S_DATA_ROOT", r"E:\S_DATA")
    if not (a.scan or a.compare or a.backtest_diff):
        ap.print_help()
        return 0
    rc = 0
    if a.scan:
        rc |= cmd_scan(a.sample, a.count, a.seed, a.gap_threshold)
    if a.compare:
        rc |= cmd_compare(a.sample, a.count, a.seed, s_root)
    if a.backtest_diff:
        rc |= cmd_backtest_diff(a.sample, a.seed, s_root)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
