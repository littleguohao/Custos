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


def _rss_mb() -> float:
    """当前进程 RSS(MB);拿不到返回 0。用于 OOM 诊断探针。"""
    try:
        with open("/proc/self/statm", encoding="utf-8") as fh:
            return int(fh.read().split()[1]) * 4096 / 1048576
    except Exception:  # noqa: BLE001
        try:
            import resource  # noqa: PLC0415
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except Exception:  # noqa: BLE001
            return 0.0


def _pick_winners(rets: list, top_pct: float, min_winner_ret: Optional[float] = None,
                  basis: str = "universe") -> tuple[set, dict]:
    """按口径挑赢家。rets 需已按收益降序。返回 (winners, meta)。
    basis="universe"  :全域(含下跌股)按收益排序取前 top_pct% —— top50% ≈ "中位数以上"。
    basis="profitable":**先筛盈利股(ret>0),再取其中前 top_pct%** —— 更贴近"真赢家"语义。
    min_winner_ret:再叠绝对收益门槛(如 0.5=+50%)。"""
    pool = [(c, v) for c, v in rets if v > 0] if basis == "profitable" else list(rets)
    n_top = max(1, int(len(pool) * top_pct / 100)) if pool else 0
    sel = pool[:n_top]
    winners = {c for c, v in sel if min_winner_ret is None or v >= min_winner_ret}
    meta = {"winner_basis": basis, "n_universe_all": len(rets), "n_profitable": sum(1 for _, v in rets if v > 0),
            "n_basis_pool": len(pool), "min_winner_ret": min_winner_ret,
            "winner_ret_cutoff": (round(sel[-1][1], 4) if sel else None)}
    return winners, meta


def _summarize_capture(rets: list, winners: set, win_fire: dict, rank_of: dict,
                       top_pct: float, surface_top_n: int,
                       day_winners: Optional[dict] = None,
                       wmeta: Optional[dict] = None) -> dict[str, Any]:
    """捕捉率/排名质量的共享汇总(流式与两趟分片模式同口径)。
    day_winners:{date: 当日池中赢家数} → 据此算 **oracle(完美排序上限)**:
    完美排序把赢家排在最前,则当日赢家数 ≤ top_n 时该赢家必浮出。区分"排序不行"与"展示位不够"。"""
    captured = surfaced = buried = 0
    oracle = 0
    best_ranks: list[int] = []
    best_pcts: list[float] = []
    pools: list[int] = []
    rand_p: list[float] = []
    wpools: list[int] = []
    for code in winners:
        days = win_fire.get(code)
        if not days:
            continue
        captured += 1
        cand = [rank_of[(d, code)] for d in days if (d, code) in rank_of]
        br, bp = min(cand, key=lambda x: x[0])
        best_ranks.append(br)
        pools.append(bp)
        best_pcts.append(round(br / bp, 3) if bp else 1.0)
        if br <= surface_top_n:
            surfaced += 1
        else:
            buried += 1
        if day_winners:                     # oracle:完美排序下,只要某触发日"池中赢家数"≤top_n 即可浮出
            wc = [day_winners.get(d, 0) for d in days]
            if wc:
                wpools.append(min(wc))
                if min(wc) <= surface_top_n:
                    oracle += 1
        p_miss = 1.0
        for _, pl in cand:                                       # 随机排名下"至少一天进 top_n"的概率
            p_miss *= (1 - min(1.0, surface_top_n / pl)) if pl else 1.0
        rand_p.append(1 - p_miss)

    nw = len(winners)
    out: dict[str, Any] = {
        "n_universe": len(rets), "n_winners": nw, "top_pct": top_pct, "surface_top_n": surface_top_n,
        "captured": captured, "recall": round(captured / nw, 3) if nw else None,
        "surfaced": surfaced, "buried_selected_not_found": buried,
        "surfaced_rate_of_captured": round(surfaced / captured, 3) if captured else None,
        "random_surfaced_rate_of_captured": round(statistics.mean(rand_p), 3) if rand_p else None,
    }
    if day_winners and wpools:
        out["oracle_surfaced"] = oracle
        out["oracle_surfaced_rate_of_captured"] = round(oracle / captured, 3) if captured else None
        out["winners_in_pool_median"] = statistics.median(sorted(wpools))
    if best_ranks:
        br_s, po_s, pc_s = sorted(best_ranks), sorted(pools), sorted(best_pcts)
        out["best_rank"] = {"median": statistics.median(br_s), "p25": br_s[len(br_s) // 4],
                            "p75": br_s[3 * len(br_s) // 4], "min": br_s[0], "max": br_s[-1]}
        out["daily_pool"] = {"median": statistics.median(po_s), "max": po_s[-1]}
        out["best_rank_pct_median"] = statistics.median(pc_s)   # 0=最强,0.5≈随机,1=最弱
    if wmeta:
        out.update(wmeta)
    miss = round(buried / captured, 3) if captured else None
    edge = (out["surfaced_rate_of_captured"] or 0) - (out["random_surfaced_rate_of_captured"] or 0)
    out["text"] = (
        (f"全域 {len(rets)} 只(盈利 {out.get('n_profitable','-')} 只), "
         f"赢家口径={out.get('winner_basis','universe')}"
         + (f"→盈利股内前{top_pct:.0f}%" if out.get('winner_basis') == 'profitable' else f"→全域前{top_pct:.0f}%")
         + (f", 且≥{(out['min_winner_ret'] or 0)*100:.0f}%" if out.get('min_winner_ret') else "")
         + f", 收益切点 {out.get('winner_ret_cutoff')}") + f"; 赢家 {nw} 只; **捕捉率(recall) {(out['recall'] or 0)*100:.0f}%**"
        f"({captured}/{nw})。捕捉到者中: 进 top{surface_top_n} = **surfaced {(out['surfaced_rate_of_captured'] or 0)*100:.0f}%**, "
        f"'选出来但没发现'(埋没) {(miss or 0)*100:.0f}%。\n  "
        f"当日信号池 中位 {out.get('daily_pool',{}).get('median','-')} / max {out.get('daily_pool',{}).get('max','-')}; "
        f"赢家最佳排名 中位 {out.get('best_rank',{}).get('median','-')} (百分位中位 {out.get('best_rank_pct_median','-')}, 0.5≈随机)。\n  "
        f"排序增益: 我们 surfaced {(out['surfaced_rate_of_captured'] or 0)*100:.0f}% vs 随机 "
        f"{(out['random_surfaced_rate_of_captured'] or 0)*100:.0f}% → {'排序有效(+%.0fpp)' % (edge*100) if edge>0.02 else '排序≈随机(无surfacing增益)' if abs(edge)<=0.02 else '排序反而更差(%.0fpp)' % (edge*100)}。")
    if out.get("oracle_surfaced_rate_of_captured") is not None:
        orc = out["oracle_surfaced_rate_of_captured"]
        out["text"] += (
            f"\n  **完美排序上限(oracle) {orc*100:.0f}%**(池中赢家数中位 {out['winners_in_pool_median']:.0f} "
            f"vs 展示位 top{surface_top_n})——"
            + ("上限已高 → 瓶颈在**排序能力**(有提升空间)" if orc >= 0.9 else
               "上限本身就低 → 瓶颈是**展示位不够/赢家口径过宽**(结构),排序再好也救不回")
            + "。")
    return out


def extract_firings(bars, start: str, end: str, entry_gate, scorer=None,
                    min_bars: int = 40, gate_window: int = 120, progress: int = 0) -> list[dict]:
    """**Pass1(可分片)**:逐股抽取 {code, ret, days:[[date,score],..]}——极小的中间产物。
    可按 --shard 拆多个独立进程跑(每片内存全新),彻底规避 loader 内存问题;Pass2 再合并算排名。"""
    import gc  # noqa: PLC0415
    items = bars.items() if isinstance(bars, dict) else bars
    out: list[dict] = []
    n = 0
    for code, raw in items:
        n += 1
        if raw is not None and len(raw):
            df = raw.sort_values("date").reset_index(drop=True)
            ds = [str(d)[:10] for d in df["date"]]
            r = window_return(ds, df["close"].astype(float).tolist(), start, end)
            days: list[list] = []
            if len(df) >= min_bars:
                for i in range(min_bars, len(df)):
                    if not (start <= ds[i] <= end):
                        continue
                    lo = max(0, i + 1 - gate_window) if gate_window else 0
                    sub = df.iloc[lo:i + 1]
                    if not entry_gate(sub):
                        continue
                    sc = 0.0
                    if scorer is not None:
                        sr = scorer(sub, code)
                        sc = (sr or {}).get("score", 0.0) if isinstance(sr, dict) else (sr or 0.0)
                    days.append([ds[i], float(sc)])
            if r is not None or days:
                out.append({"code": code, "ret": r, "days": days})
            del df, ds
        if progress and n % progress == 0:
            print(f"[pass1] {n} 股 | RSS={_rss_mb():.0f}MB", file=sys.stderr, flush=True)
            gc.collect()
    return out


def rank_from_firings(records: list[dict], top_pct: float = 50.0,
                      surface_top_n: int = 20, min_winner_ret: Optional[float] = None,
                      winner_basis: str = "universe") -> dict[str, Any]:
    """**Pass2**:合并(多分片)firings → 赢家捕捉率 + 排名质量。与 capture_rank_study 同口径。
    赢家 = **全域按窗口收益排序的前 top_pct%**(不看正负);min_winner_ret 另加绝对收益门槛
    (如 0.5=至少+50%),用于把"赢家"收紧为真牛股。Pass2 极廉价 → 可在同一份 firings 上反复改口径。"""
    rets = [(r["code"], r["ret"]) for r in records if r.get("ret") is not None]
    if not rets:
        return {"n_winners": 0, "text": "无数据"}
    rets.sort(key=lambda x: x[1], reverse=True)
    winners, wmeta = _pick_winners(rets, top_pct, min_winner_ret, winner_basis)
    day_fire: dict[str, list] = {}
    win_fire: dict[str, list] = {}
    day_winners: dict[str, int] = {}
    for r in records:
        for d, sc in (r.get("days") or []):
            day_fire.setdefault(d, []).append((r["code"], sc))
            if r["code"] in winners:
                win_fire.setdefault(r["code"], []).append(d)
                day_winners[d] = day_winners.get(d, 0) + 1
    rank_of: dict[tuple, tuple] = {}
    for d, lst in day_fire.items():
        pool = len(lst)
        for rk, (code, _) in enumerate(sorted(lst, key=lambda x: x[1], reverse=True), 1):
            if code in winners:
                rank_of[(d, code)] = (rk, pool)
    day_fire.clear()
    return _summarize_capture(rets, winners, win_fire, rank_of, top_pct, surface_top_n,
                              day_winners=day_winners, wmeta=wmeta)


def capture_rank_study(bars, start: str, end: str, entry_gate,
                       scorer=None, top_pct: float = 50.0, surface_top_n: int = 20,
                       min_bars: int = 40, gate_window: int = 0, progress: int = 0,
                       min_winner_ret: Optional[float] = None,
                       winner_basis: str = "universe") -> dict[str, Any]:
    """赢家捕捉率 + 排名质量。回答:多头区间收益前 top_pct% 赢家,
      ①被我们信号捕捉到的比例(recall);②捕捉当日在"同类信号池"里的排名/是否进 top_n(surfaced);
      ③量化"选出来但没发现"(捕捉到却排名埋没=captured 但 best_rank>N)。
    scorer(sub_df, code)->{'score':..} 用于当日池内排序(高=靠前);None 则按 0 分(等同随机),仍给随机基线对照。
    **内存**:bars 可为 dict{code:df} 或 **(code, df) 迭代器**(流式:逐股加载→抽取→释放,避免全量载入 OOM)。
    单趟扫描,每股只加载一次;累加器只存轻量元组并即时释放 K 线(del + 周期 gc)。
    gate_window>0:只把**最近 gate_window 根**传给 gate/scorer(而非整段前缀)——避免每根K线重算全历史
    (O(n²) 时间 + 大量临时对象,是 OOM/慢的主因)。KDJ 等递归指标需足够预热,建议 ≥120。
    progress>0:每处理 progress 只股打印进度 + RSS(MB) 探针,便于定位内存增长。"""
    import gc  # noqa: PLC0415
    items = bars.items() if isinstance(bars, dict) else bars
    rets: list[tuple] = []
    day_fire: dict[str, list] = {}          # date -> [(code, score)]  当日全域信号池(轻量)
    stock_days: dict[str, list] = {}        # code -> [dates] 该股信号日(用于赢家过滤)
    n_seen = 0
    for code, raw in items:                 # 流式:raw 用完即释放
        n_seen += 1
        if raw is not None and len(raw):
            df = raw.sort_values("date").reset_index(drop=True)
            ds = [str(d)[:10] for d in df["date"]]
            closes = df["close"].astype(float).tolist()
            r = window_return(ds, closes, start, end)
            if r is not None:
                rets.append((code, r))
            if len(df) >= min_bars:
                fired: list[str] = []
                for i in range(min_bars, len(df)):
                    if not (start <= ds[i] <= end):
                        continue
                    lo = max(0, i + 1 - gate_window) if gate_window else 0   # 尾窗口:省时省内存
                    sub = df.iloc[lo:i + 1]
                    if not entry_gate(sub):
                        continue
                    sc = 0.0
                    if scorer is not None:
                        sr = scorer(sub, code)
                        sc = (sr or {}).get("score", 0.0) if isinstance(sr, dict) else (sr or 0.0)
                    day_fire.setdefault(ds[i], []).append((code, float(sc)))
                    fired.append(ds[i])
                if fired:
                    stock_days[code] = fired
            del df, ds, closes
        if progress and n_seen % progress == 0:
            print(f"[capture] {n_seen} 股 | firings={sum(len(v) for v in day_fire.values())} "
                  f"| RSS={_rss_mb():.0f}MB", file=sys.stderr, flush=True)
            gc.collect()
    if not rets:
        return {"n_winners": 0, "text": "无数据"}
    rets.sort(key=lambda x: x[1], reverse=True)
    winners, wmeta = _pick_winners(rets, top_pct, min_winner_ret, winner_basis)
    win_fire = {c: stock_days[c] for c in winners if c in stock_days}   # 赢家的信号日
    stock_days.clear()
    day_winners: dict[str, int] = {}
    for _c, _ds in win_fire.items():
        for _d in _ds:
            day_winners[_d] = day_winners.get(_d, 0) + 1

    rank_of: dict[tuple, tuple] = {}        # (date,code) -> (rank, pool)
    for d, lst in day_fire.items():
        pool = len(lst)
        for rk, (code, _) in enumerate(sorted(lst, key=lambda x: x[1], reverse=True), 1):
            if code in winners:                       # 只需赢家的排名,省内存
                rank_of[(d, code)] = (rk, pool)
    day_fire.clear()

    return _summarize_capture(rets, winners, win_fire, rank_of, top_pct, surface_top_n,
                              day_winners=day_winners, wmeta=wmeta)


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
    ap.add_argument("--capture-rank", action="store_true",
                    help="额外跑赢家捕捉率+排名质量研究(recall/surfaced/埋没),量化'选出来但没发现'")
    ap.add_argument("--capture-only", action="store_true",
                    help="只跑捕捉率+排名研究(流式分块加载,省内存);跳过起涨点分析与全量载入")
    ap.add_argument("--chunk-size", type=int, default=400, help="捕捉研究流式加载的每块股票数(内存/IO 权衡)")
    ap.add_argument("--gate-window", type=int, default=120,
                    help="传给 gate/scorer 的尾窗口根数(0=整段前缀)。默认120:省时省内存,KDJ 预热足够")
    ap.add_argument("--progress", type=int, default=500,
                    help="每 N 股打印进度+RSS(MB)探针,0=关闭(用于定位内存增长)")
    ap.add_argument("--emit-firings", default="",
                    help="Pass1:只抽取信号→写 JSON(极小),可配 --shard 分多进程跑,彻底避开 loader 内存")
    ap.add_argument("--shard", default="",
                    help="Pass1 分片 i/N(如 1/6):只处理第 i 片股票")
    ap.add_argument("--from-firings", default="",
                    help="Pass2:读一个或多个(逗号分隔) Pass1 JSON,合并算捕捉率+排名(内存极小)")
    ap.add_argument("--capture-top-pct", type=float, default=50.0, help="捕捉研究的赢家口径(默认收益前50%%)")
    ap.add_argument("--surface-top-n", type=int, default=20, help="每日展示阈值(top-N 算 surfaced)")
    ap.add_argument("--winner-basis", choices=["universe", "profitable"], default="universe",
                    help="赢家口径:universe=全域收益前top_pct%%(含下跌股);profitable=**盈利股内**前top_pct%%")
    ap.add_argument("--min-winner-ret", type=float, default=None,
                    help="赢家另加绝对收益门槛(如 0.5=至少+50%%);默认仅按 top_pct 排名(不看正负)")
    ap.add_argument("--rank-score", choices=["reversal_quality", "reversal_quality_inv", "none"],
                    default="reversal_quality", help="当日信号池内排序分(none=随机)")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    # Pass2:仅合并 Pass1 产物算排名(不加载任何K线,内存极小)
    if args.from_firings:
        import json as _j
        recs: list[dict] = []
        for fp in [x.strip() for x in args.from_firings.split(",") if x.strip()]:
            d = _j.loads(Path(fp).read_text(encoding="utf-8"))
            recs.extend(d.get("records") or d if isinstance(d, list) else d.get("records", []))
        cap = rank_from_firings(recs, top_pct=args.capture_top_pct, surface_top_n=args.surface_top_n,
                                min_winner_ret=args.min_winner_ret,
                                winner_basis=args.winner_basis)
        print(f"\n=== 赢家捕捉率 + 排名质量（合并 {len(recs)} 股记录, top{args.capture_top_pct:.0f}%赢家, "
              f"展示top{args.surface_top_n}）===")
        print(cap["text"])
        if args.out:
            Path(args.out).write_text(_j.dumps({"capture_rank": cap}, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
        return 0

    if args.universe_sdata:
        import s_data  # noqa: PLC0415
        sub = "CSV_DATA" if args.data_source == "csv" else "Q_DATA"
        codes = s_data.list_universe(str(Path(args.s_data_root) / sub), source=args.data_source)
    else:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if not codes:
        ap.error("需 --universe-sdata 或 --codes")

    # 数据/regime 起点必须比 --start 再早 buffer 段(起涨点要在 [start-buffer, end] 内回溯),
    # 否则 buffer 被加载窗口截为 0(此前真实运行从未真正回溯,结论有偏)。捕捉研究也靠它提供 min_bars 回溯。
    load_start = args.start
    if args.buffer_days:
        load_start = (_date.fromisoformat(args.start)
                      - _td(days=int(args.buffer_days * 1.6) + 10)).isoformat()   # 交易日→日历日留裕量

    def _chunked_items(chunk: int):
        """流式产出 (code, df):逐块加载→逐股产出→释放该块,capture 研究据此避免全量载入 OOM。
        每股裁到必需列 + 窗口范围并 copy()(切断对 loader 大对象的引用,否则 clear() 也释放不掉)。"""
        import gc  # noqa: PLC0415
        need = ["date", "open", "high", "low", "close", "volume"]
        if loader is not None:
            for c, df in loader(codes, 0).items():
                yield c, df
            return
        import s_data  # noqa: PLC0415
        sub2 = "CSV_DATA" if args.data_source == "csv" else "Q_DATA"
        fn2 = s_data.load_bars_csv if args.data_source == "csv" else s_data.load_bars_qlib
        root2 = str(Path(args.s_data_root) / sub2)
        for k in range(0, len(codes), chunk):
            d = fn2(codes[k:k + chunk], 0, start=load_start, end=args.end, root=root2)
            for c in list(d):
                df = d.pop(c)                            # 取出即从 dict 移除
                try:
                    cols = [x for x in need if x in df.columns]
                    yield c, df[cols].copy()             # copy:切断父 block 引用,确保可回收
                finally:
                    del df
            d.clear()
            gc.collect()                                 # 每块显式回收,内存只留一块 + 轻量累加器

    res: dict[str, Any] = {}
    # Pass1:只抽信号→小 JSON(可分片,多进程各自内存全新)
    if args.emit_firings:
        if args.shard:
            i, n = (int(x) for x in args.shard.split("/"))
            codes = [c for k, c in enumerate(codes) if k % n == (i - 1) % n]
            print(f"[pass1] 分片 {args.shard}: {len(codes)} 只", file=sys.stderr)
        scorer = None if args.rank_score == "none" else bt.SCORERS.get(args.rank_score)
        recs = extract_firings(_chunked_items(args.chunk_size), args.start, args.end,
                              bt.ENTRY_GATES[args.entry_filter], scorer=scorer,
                              gate_window=args.gate_window, progress=args.progress)
        import json as _j
        Path(args.emit_firings).write_text(
            _j.dumps({"start": args.start, "end": args.end, "entry_filter": args.entry_filter,
                      "rank_score": args.rank_score, "shard": args.shard, "records": recs},
                     ensure_ascii=False), encoding="utf-8")
        print(f"[pass1] 写出 {len(recs)} 股记录 → {args.emit_firings} (RSS={_rss_mb():.0f}MB)",
              file=sys.stderr)
        return 0

    if not args.capture_only:                            # 起涨点分析:需全量在内存(与既有行为一致)
        if loader is not None:
            bars = loader(codes, 0)
        else:
            import s_data  # noqa: PLC0415
            sub = "CSV_DATA" if args.data_source == "csv" else "Q_DATA"
            fn = s_data.load_bars_csv if args.data_source == "csv" else s_data.load_bars_qlib
            bars = fn(codes, 0, start=load_start, end=None, root=str(Path(args.s_data_root) / sub))
        regime = bt.load_amv_regime(since=load_start)    # regime 起点跟随数据起点(早前窗口)
        res = analyze(bars, regime, args.start, args.end, bt.ENTRY_GATES[args.entry_filter],
                      top_pct=args.top_pct, buffer_days=args.buffer_days)
        print(f"\n=== 起涨点 vs 0AMV（{args.start}~{args.end}, {args.entry_filter}, top{args.top_pct}%）===")
        print(res["text"])

    # 赢家捕捉率 + 排名质量(recall/surfaced/'选出来但没发现')。流式:capture-only 用分块加载,省内存
    if args.capture_rank or args.capture_only:
        scorer = None if args.rank_score == "none" else bt.SCORERS.get(args.rank_score)
        src = _chunked_items(args.chunk_size) if args.capture_only else bars
        cap = capture_rank_study(src, args.start, args.end, bt.ENTRY_GATES[args.entry_filter],
                                 scorer=scorer, top_pct=args.capture_top_pct,
                                 surface_top_n=args.surface_top_n,
                                 gate_window=args.gate_window, progress=args.progress,
                                 min_winner_ret=args.min_winner_ret,
                                 winner_basis=args.winner_basis)
        res["capture_rank"] = cap
        print(f"\n=== 赢家捕捉率 + 排名质量（top{args.capture_top_pct:.0f}%赢家, 展示top{args.surface_top_n}, "
              f"排序={args.rank_score}）===")
        print(cap["text"])
    # 赢家板块集中度 / 板块共振(仅起涨点分析产出 winners 时)
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
