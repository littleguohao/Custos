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
⚠️ 右删失:起涨点靠近 end 时此后可能再无做多日,lead=None 的样本被丢弃,lead 分布偏"较快转多"。
⚠️ 板块共振相关含机械成分:赢家本身贡献板块指数收益,corr>0 部分是恒真的,仅作描述。
用法(用户机):uv run python 07_tools/screening/launch_point_study.py --data-source qlib --universe-sdata \
  --start 2024-09-01 --end 2025-06-30 --entry-filter reversal_k --top-pct 10 --buffer-days 60
"""
from __future__ import annotations

import argparse
import bisect
import statistics
import sys
from datetime import date as _date, timedelta as _td
from pathlib import Path
from typing import Any, Optional

import pandas as pd

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
             if L["regime"] in ("空头", "中性") and L["lead_days_to_long"] is not None]  # "未知"不计入分布
    out = {"n_winners": len(winners), "n_launches": n, "by_regime": by_regime,
           "winners": winners, "winner_rets": {c: round(r, 4) for c, r in rets[:n_top]},
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


def sector_concentration(winners: list[str], members: dict[str, list], index_dir,
                         start: str, end: str, top_k: int = 12,
                         winner_rets: Optional[dict] = None) -> dict[str, Any]:
    """赢家的板块族分布(聚集效应):集中于少数主流板块还是分散?

    聚合口径复用 sector_mainstream:每票带**整个板块族**(多重归属,地区/风格剔除),
    按赢家窗口收益聚合到板块(胜率/期望),并报:
    - **赢家密度**(归属数/板块成分数)——纠大板块偏差(大板块天然归属多,密度才是真聚集);
    - 主流(归属数 top_k) vs 分散 的赢家收益对照;
    - 板块指数同窗口收益 + 赢家数相关性(含零赢家板块;⚠️含机械成分,仅描述)。
    口径:一股多板块重复计(归属次数≠赢家数)。"""
    import sector_mainstream as sm  # noqa: PLC0415
    code2secs = sm.invert_members(members)
    rets_map = winner_rets or {}
    trades = [{"code": w, "ret": float(rets_map.get(w, 0.0))} for w in winners]
    agg = sm.aggregate(trades, code2secs, top_k=top_k)
    if not agg["rows"]:
        return {"n_winners": len(winners), "n_classified": 0, "text": "无板块成员映射"}
    idx = Path(index_dir)
    sec_ret: dict[str, float] = {}
    for sec in members:                                   # 全成员板块都算收益(含零赢家板块,供相关性用全样本)
        p = idx / f"{sec}.csv"
        if p.is_file():
            try:
                df = pd.read_csv(p)
                r = window_return(df["date"].tolist(), df["close"].astype(float).tolist(), start, end)
                if r is not None:
                    sec_ret[sec] = r
            except Exception:  # noqa: BLE001
                pass
    n_by_sec = {r["sector"]: r["n"] for r in agg["rows"]}
    pairs = [(n_by_sec.get(s, 0), sec_ret[s]) for s in sec_ret]          # 零赢家板块计入(不左截断)
    corr = None
    if len(pairs) >= 3:
        try:
            corr = round(statistics.correlation([a for a, _ in pairs], [b for _, b in pairs]), 3)
        except Exception:  # noqa: BLE001
            corr = None
    top_rows = []
    for r in agg["top_sectors"]:
        sec = r["sector"]
        top_rows.append({**r, "n_winners": r["n"],
                         "density": round(r["n"] / max(len(members.get(sec) or [1]), 1), 3),
                         "name": sm.sector_name(sec),
                         "sector_return": (round(sec_ret[sec], 4) if sec in sec_ret else None)})
    by_density = sorted(top_rows, key=lambda r: r["density"], reverse=True)
    out = {"n_winners": len(winners), "n_classified": agg["n_classified"],
           "distinct_sectors": agg["distinct_sectors"],
           "top5_winner_share": agg["top5_share"], "herfindahl": agg["hhi"],
           "corr_wincount_vs_sectorret": corr, "corr_n": len(pairs),
           "top_sectors": top_rows, "top_by_density": by_density[:top_k],
           "mainstream": {"sectors": agg["mainstream_sectors"],
                          "in": agg["in_mainstream"], "off": agg["off_mainstream"],
                          "lift": agg["mainstream_lift"]}}
    conc = "集中" if (agg["top5_share"] or 0) >= 0.5 else ("偏分散" if (agg["top5_share"] or 0) < 0.3 else "中等")
    im, om = agg["in_mainstream"], agg["off_mainstream"]
    out["text"] = (
        f"赢家 {len(winners)} 只(有板块归属 {agg['n_classified']}), 覆盖 {agg['distinct_sectors']} 个板块; "
        f"前5板块占归属次数 {(agg['top5_share'] or 0)*100:.0f}%({conc};分母=归属次数,一股多板块重复计), "
        f"HHI {agg['hhi']};\n"
        f"  主流(top{top_k})内赢家: n={im.get('n')} 均收 {(im.get('expectancy') or 0)*100:+.1f}% vs "
        f"分散: n={om.get('n')} 均收 {(om.get('expectancy') or 0)*100:+.1f}% "
        f"(差 {((agg['mainstream_lift'] or 0)*100):+.1f}pp);\n"
        f"  赢家数 vs 板块指数收益 相关 {corr} (n={len(pairs)},含零赢家板块;⚠️含机械成分,仅描述)。\n"
        f"  归属数 Top: " + "; ".join(f"{r['name']}({r['n']},{(r['expectancy'] or 0)*100:+.0f}%)"
                                     for r in top_rows[:6]) + "\n"
        f"  密度 Top(纠大板块偏差): " + "; ".join(f"{r['name']}({r['density']*100:.0f}%×{r['n']})"
                                                 for r in by_density[:6]))
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
    ap.add_argument("--sector-members",
                    default=str(TOOLS.parent / "01_data" / "market" / "sector_members.json"),
                    help="板块成员 JSON(算赢家板块集中度/共振;缺失则跳过)")
    ap.add_argument("--sector-index-dir",
                    default=str(TOOLS.parent / "01_data" / "market" / "sector_index"))
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

    # 数据/regime 起点必须比 --start 再早 buffer 段(起涨点要在 [start-buffer, end] 内回溯),
    # 否则 buffer 被加载窗口截为 0(此前真实运行从未真正回溯,结论有偏)。
    load_start = args.start
    if args.buffer_days:
        load_start = (_date.fromisoformat(args.start)
                      - _td(days=int(args.buffer_days * 1.6) + 10)).isoformat()   # 交易日→日历日留裕量
    if loader is not None:
        bars = loader(codes, 0)
    else:
        import s_data  # noqa: PLC0415
        sub = "CSV_DATA" if args.data_source == "csv" else "Q_DATA"
        fn = s_data.load_bars_csv if args.data_source == "csv" else s_data.load_bars_qlib
        bars = fn(codes, 0, start=load_start, end=None, root=str(Path(args.s_data_root) / sub))

    regime = bt.load_amv_regime(since=load_start)          # regime 起点跟随数据起点(早前窗口)
    res = analyze(bars, regime, args.start, args.end, bt.ENTRY_GATES[args.entry_filter],
                  top_pct=args.top_pct, buffer_days=args.buffer_days)
    print(f"\n=== 起涨点 vs 0AMV（{args.start}~{args.end}, {args.entry_filter}, top{args.top_pct}%）===")
    print(res["text"])
    # 赢家板块集中度 / 板块共振
    import json as _json
    mpath = Path(args.sector_members)
    if mpath.is_file() and res.get("winners"):
        members = _json.loads(mpath.read_text(encoding="utf-8"))
        conc = sector_concentration(res["winners"], members, args.sector_index_dir, args.start, args.end,
                                    winner_rets=res.get("winner_rets"))
        res["sector_concentration"] = conc
        print(f"\n=== 赢家板块集中度 / 板块共振 ===")
        print(conc["text"])
    if args.out:
        import json
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
