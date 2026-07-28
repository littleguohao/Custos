# -*- coding: utf-8 -*-
"""起涨点 vs 0AMV regime 研究:多头区间里涨最好的股票,其(系统可选出的)起涨点落在做多还是空头?
若在空头,距 0AMV 转做多还差几天?有无规律?

方法(纯描述性研究,非可交易信号——起涨点用了区间内后视的"涨最好",天然含 look-ahead,仅用于观察规律):
  1. 在 [start,end] 多头区间,按窗口收益(close_end/close_start-1)排序,取 top --top-pct 赢家。
  2. 每个赢家:在 [start-buffer, end] 内所有 B1 信号(--entry-filter,不含0AMV)里,取"到区间峰值前向收益最大"
     的那个信号日 = 起涨点(即系统本可进、且抓住主升的点)。
  3. 该起涨点日的 0AMV regime(做多/空头/中性);若非做多,数到"下一个做多日"的交易日数(lead days)。
  4. 汇总:起涨点落做多 vs 空头/中性占比;空头者的 lead-days 分布(领先 0AMV 几天)。

⚠️ 幸存者偏差(赢家=现存赢家)+ 起涨点后视 → 结论是"规律观察",不是可交易策略。
用法(用户机):uv run python 07_tools/screening/launch_point_study.py --data-source qlib --universe-sdata \
  --start 2024-09-01 --end 2025-06-30 --entry-filter reversal_k --top-pct 10 --buffer-days 60
"""
from __future__ import annotations

import argparse
import bisect
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

TOOLS = Path(__file__).resolve().parents[1]
for _p in (str(TOOLS), str(TOOLS / "screening"), str(TOOLS / "local_tdx")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest_factors as bt  # noqa: E402  复用 ENTRY_GATES / load_amv_regime


def window_return(dates: list, closes: list, start: str, end: str) -> Optional[float]:
    """区间收益 close(最后<=end)/close(第一>=start)-1。数据不足返回 None。"""
    idx = [i for i, d in enumerate(dates) if start <= str(d)[:10] <= end]
    if len(idx) < 2:
        return None
    a, b = idx[0], idx[-1]
    return (closes[b] / closes[a] - 1) if closes[a] else None


def find_launch(dates: list, closes: list, signal_idxs: list[int], end: str) -> Optional[dict]:
    """在信号日集合里取"到区间峰值(<=end)前向收益最大"者 = 起涨点。返回 {date, fwd_gain}。"""
    ds = [str(d)[:10] for d in dates]
    best = None
    for i in signal_idxs:
        fut = [closes[j] for j in range(i, len(closes)) if ds[j] <= end]
        if not fut or not closes[i]:
            continue
        gain = max(fut) / closes[i] - 1
        if best is None or gain > best["fwd_gain"]:
            best = {"date": ds[i], "fwd_gain": round(gain, 4)}
    return best


def regime_at_and_lead(regime: dict[str, str], launch_date: str) -> dict[str, Any]:
    """起涨点当日 regime + 若非做多,距下一个'做多'的交易日数(基于 regime 日历)。"""
    if not regime:
        return {"regime": "未知", "lead_days_to_long": None}
    rdates = sorted(regime)
    j = bisect.bisect_right(rdates, launch_date) - 1
    cur = regime[rdates[j]] if j >= 0 else "未知"
    lead = None
    if cur != "做多":
        for k in range(max(j, 0), len(rdates)):
            if rdates[k] >= launch_date and regime[rdates[k]] == "做多":
                lead = k - bisect.bisect_left(rdates, launch_date)  # 交易日数(regime日历)
                break
    return {"regime": cur, "lead_days_to_long": lead}


def analyze(bars_by_code: dict, regime: dict[str, str], start: str, end: str,
            entry_gate, top_pct: float = 10.0, buffer_days: int = 60,
            min_bars: int = 40) -> dict[str, Any]:
    """主分析。bars_by_code:{code:df[date,open,high,low,close,volume]};regime:date→做多/空头/中性。"""
    rets = []
    for code, raw in bars_by_code.items():
        if raw is None or len(raw) == 0:
            continue
        df = raw.sort_values("date").reset_index(drop=True)
        r = window_return(df["date"].tolist(), df["close"].astype(float).tolist(), start, end)
        if r is not None:
            rets.append((code, r))
    if not rets:
        return {"n_winners": 0, "text": "无数据"}
    rets.sort(key=lambda x: x[1], reverse=True)
    n_top = max(1, int(len(rets) * top_pct / 100))
    winners = [c for c, _ in rets[:n_top]]

    launches = []
    for code in winners:
        df = bars_by_code[code].sort_values("date").reset_index(drop=True)
        if len(df) < min_bars:
            continue
        ds = [str(d)[:10] for d in df["date"]]
        lo = _buffered_start(ds, start, buffer_days)
        sig = [i for i in range(min_bars, len(df))
               if lo <= ds[i] <= end and entry_gate(df.iloc[:i + 1])]
        if not sig:
            continue
        lp = find_launch(df["date"].tolist(), df["close"].astype(float).tolist(), sig, end)
        if lp is None:
            continue
        rl = regime_at_and_lead(regime, lp["date"])
        launches.append({"code": code, **lp, **rl})

    n = len(launches)
    by_regime = {}
    for L in launches:
        by_regime[L["regime"]] = by_regime.get(L["regime"], 0) + 1
    leads = [L["lead_days_to_long"] for L in launches
             if L["regime"] != "做多" and L["lead_days_to_long"] is not None]
    out = {"n_winners": len(winners), "n_launches": n, "by_regime": by_regime,
           "launches": launches}
    if leads:
        leads.sort()
        out["lead_days"] = {"n": len(leads), "median": statistics.median(leads),
                            "p25": leads[len(leads) // 4], "p75": leads[3 * len(leads) // 4],
                            "min": leads[0], "max": leads[-1], "mean": round(statistics.mean(leads), 1)}
    long_share = round(by_regime.get("做多", 0) / n, 3) if n else None
    lead_txt = (f"空头起涨→领先做多 中位 {out['lead_days']['median']} / p25 {out['lead_days']['p25']} / "
                f"p75 {out['lead_days']['p75']} / max {out['lead_days']['max']} 交易日 (n={out['lead_days']['n']})"
                if leads else "无空头起涨样本")
    out["text"] = (f"赢家 {len(winners)} 只 / 起涨点 {n} 个; 落做多 {by_regime.get('做多',0)}"
                   f"({(long_share or 0)*100:.0f}%)、空头 {by_regime.get('空头',0)}、中性 {by_regime.get('中性',0)}。\n  {lead_txt}")
    return out


def _buffered_start(sorted_dates: list[str], start: str, buffer_days: int) -> str:
    j = bisect.bisect_left(sorted_dates, start)
    return sorted_dates[max(0, j - buffer_days)] if sorted_dates else start


def main(argv=None, loader=None) -> int:
    ap = argparse.ArgumentParser(description="起涨点 vs 0AMV regime 研究")
    ap.add_argument("--data-source", choices=["tdx", "qlib", "csv"], default="qlib")
    ap.add_argument("--s-data-root", default=r"E:\S_DATA")
    ap.add_argument("--universe-sdata", action="store_true")
    ap.add_argument("--codes", default="")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--entry-filter", choices=["j_low", "reversal_k", "j_macd_turn"], default="reversal_k")
    ap.add_argument("--top-pct", type=float, default=10.0)
    ap.add_argument("--buffer-days", type=int, default=60)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    if args.universe_sdata:
        import s_data  # noqa: PLC0415
        sub = "CSV_DATA" if args.data_source == "csv" else "Q_DATA"
        codes = s_data.list_universe(str(Path(args.s_data_root) / sub), source=args.data_source)
    else:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if not codes:
        ap.error("需 --universe-sdata 或 --codes")

    if loader is not None:
        bars = loader(codes, 0)
    else:
        import s_data  # noqa: PLC0415
        sub = "CSV_DATA" if args.data_source == "csv" else "Q_DATA"
        fn = s_data.load_bars_csv if args.data_source == "csv" else s_data.load_bars_qlib
        bars = fn(codes, 0, start=args.start, end=None, root=str(Path(args.s_data_root) / sub))

    regime = bt.load_amv_regime()
    res = analyze(bars, regime, args.start, args.end, bt.ENTRY_GATES[args.entry_filter],
                  top_pct=args.top_pct, buffer_days=args.buffer_days)
    if args.out:
        import json
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 起涨点 vs 0AMV（{args.start}~{args.end}, {args.entry_filter}, top{args.top_pct}%）===")
    print(res["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
