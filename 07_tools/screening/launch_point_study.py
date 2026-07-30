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
import os
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
    """在信号日集合里取"到区间峰值(<=end)前向收益最大"者 = 起涨点。返回 {date, idx, fwd_gain}。"""
    ds = [str(d)[:10] for d in dates]
    best = None
    for i in signal_idxs:
        fut = [closes[j] for j in range(i, len(closes)) if ds[j] <= end]
        if not fut or not closes[i]:
            continue
        gain = max(fut) / closes[i] - 1
        if best is None or gain > best["fwd_gain"]:
            best = {"date": ds[i], "idx": i, "fwd_gain": round(gain, 4)}
    return best


def _kdj_j_at(df, idx: int) -> Optional[float]:
    """df 第 idx 根(as-of 切片 df.iloc[:idx+1])当日 KDJ J 值;不可用返回 None。"""
    try:
        kdj = getattr(bt, "_kdj", None)
        if kdj is None:
            return None
        r = kdj(df.iloc[:idx + 1])
        return round(float(r["j"]), 2) if r.get("available") and r.get("j") is not None else None
    except Exception:  # noqa: BLE001
        return None


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
        rec = {"code": code, **lp, **rl}
        jv = _kdj_j_at(df, lp["idx"])                  # 起涨点当日 J 值(超卖深度)
        if jv is not None:
            rec["j_at_launch"] = jv
        launches.append(rec)

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
    js = sorted(L["j_at_launch"] for L in launches if L.get("j_at_launch") is not None)
    j_txt = ""
    if js:
        out["j_at_launch_stats"] = {
            "n": len(js), "min": js[0], "p25": js[len(js) // 4], "median": statistics.median(js),
            "p75": js[3 * len(js) // 4], "max": js[-1],
            "share_neg": round(sum(1 for v in js if v < 0) / len(js), 3),
            "share_lt5": round(sum(1 for v in js if v < 5) / len(js), 3)}
        st = out["j_at_launch_stats"]
        j_txt = (f"\n  起涨点 J 值: 中位 {st['median']} (p25 {st['p25']} / p75 {st['p75']}), "
                 f"范围 {st['min']}~{st['max']}; J<0 占 {st['share_neg']*100:.0f}%、J<5 占 {st['share_lt5']*100:.0f}%"
                 f" (n={st['n']}; 门槛 J<13 已限定上界,看池内深度分布)")
    out["text"] = (f"赢家 {len(winners)} 只 / 起涨点 {n} 个; 落做多 {by_regime.get('做多',0)}"
                   f"({(long_share or 0)*100:.0f}%)、空头 {by_regime.get('空头',0)}、中性 {by_regime.get('中性',0)}。\n  {lead_txt}{j_txt}")
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


def build_sector_features(index_dir, members, mom_days: int = 20):
    """构建 **as-of 板块特征**查询 fn(code6, date)->dict(供 extract_firings 记录到每个信号):
    - f_sector_favorable(0/1):所属板块任一当日处有利相位(DIF>0 且无近期顶背离/三打,
      favorable_series 因果序列,摆动高点需 i+fractal 确认,无未来函数);
    - f_sector_momentum(float):所属板块指数 trailing mom_days 最强收益——当期强势/主流的
      **因果代理**(密度榜需逐日全市场横截面,流式 Pass1 算不起;板块动量是单板块可算的近似)。
    未分类/数据缺失 → 返回 {}(特征缺省,不误标 0)。绝不 raise。"""
    import bisect as _b
    import sector_phase as sp  # noqa: PLC0415
    import sector_mainstream as sm  # noqa: PLC0415
    try:
        code2secs = sm.invert_members(members)
        sec_data: dict[str, tuple] = {}
        for sec in {s for secs in code2secs.values() for s in secs}:
            p = Path(index_dir) / f"{sec}.csv"
            if not p.is_file():
                continue
            try:
                df = pd.read_csv(p)
                dates = [str(d)[:10] for d in df["date"]]
                closes = df["close"].astype(float).tolist()
                fav = sp.favorable_series(dates, closes)
                sec_data[sec] = (dates, closes, fav)
            except Exception:  # noqa: BLE001
                continue

        def fn(code6: str, date: str) -> dict:
            secs = [s for s in code2secs.get(str(code6)[:6], []) if s in sec_data]
            if not secs:
                return {}
            fav_any = 0
            best_mom: Optional[float] = None
            for s in secs:
                dates, closes, fav = sec_data[s]
                j = _b.bisect_right(dates, date) - 1         # as-of:最近 ≤ date 的板块收盘
                if j < 0:
                    continue
                if fav.get(dates[j]):
                    fav_any = 1
                k = j - mom_days
                if k >= 0 and closes[k]:
                    m = closes[j] / closes[k] - 1
                    best_mom = m if best_mom is None else max(best_mom, m)
            out: dict[str, Any] = {"f_sector_favorable": fav_any}
            if best_mom is not None:
                out["f_sector_momentum"] = round(best_mom, 4)
            return out

        return fn
    except Exception:  # noqa: BLE001
        return lambda code6, date: {}


def extract_firings(bars, start: str, end: str, entry_gate, scorer=None,
                    min_bars: int = 40, gate_window: int = 120, progress: int = 0,
                    horizons: tuple = (), feature_scorers: Optional[dict] = None,
                    stats: Optional[dict] = None, extra_feature_fn=None) -> list[dict]:
    """**Pass1(可分片)**:逐股抽取 {code, ret, days:[[date,score],..]}——极小的中间产物。
    可按 --shard 拆多个独立进程跑(每片内存全新),彻底规避 loader 内存问题;Pass2 再合并算排名。
    horizons:如 (20,60) → 每个信号日额外记 **因果前向收益**(fwd{h}=close[i+h]/close[i]-1,
    mfe{h}=区间内最大涨幅),用于"信号当时能否判别未来会跑"的研究(不看窗口末尾,无未来函数)。
    feature_scorers:{名称: scorer} → 每个信号日记下**当时可得**的特征值,作为判别子候选。
    extra_feature_fn(code6, date)->dict → 每个信号日追加 as-of 特征(如 build_sector_features
    的板块相位/板块动量);键需以 f_ 开头才会被判别研究收集。
    stats:传入 dict 时收集运行统计(feature_failures=各特征 scorer 异常次数——防异常被静默吞掉)。"""
    import gc  # noqa: PLC0415
    items = bars.items() if isinstance(bars, dict) else bars
    out: list[dict] = []
    n = 0
    for code, raw in items:
        n += 1
        if raw is not None and len(raw):
            df = raw.sort_values("date").reset_index(drop=True)
            ds = [str(d)[:10] for d in df["date"]]
            closes = df["close"].astype(float).tolist()
            r = window_return(ds, closes, start, end)
            days: list = []
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
                    rec: list = [ds[i], float(sc)]
                    if horizons or feature_scorers or extra_feature_fn:
                        extra: dict = {}
                        for h in horizons:                       # 因果前向:只用信号日之后的数据
                            j = i + int(h)
                            if j < len(closes) and closes[i]:
                                extra[f"fwd{h}"] = round(closes[j] / closes[i] - 1, 4)
                                seg = closes[i + 1:j + 1]
                                if seg:
                                    extra[f"mfe{h}"] = round(max(seg) / closes[i] - 1, 4)
                        for fname, fsc in (feature_scorers or {}).items():
                            try:
                                fr = fsc(sub, code)
                                v = (fr or {}).get("score") if isinstance(fr, dict) else fr
                                if v is not None:
                                    extra[f"f_{fname}"] = round(float(v), 6)
                            except Exception:  # noqa: BLE001
                                if stats is not None:      # 计数防静默:恒失败的特征不得无声消失
                                    ff = stats.setdefault("feature_failures", {})
                                    ff[fname] = ff.get(fname, 0) + 1
                        if extra_feature_fn is not None:         # as-of 附加特征(板块相位/动量)
                            try:
                                extra.update(extra_feature_fn(code, ds[i]))
                            except Exception:  # noqa: BLE001
                                if stats is not None:
                                    ff = stats.setdefault("feature_failures", {})
                                    ff["_extra"] = ff.get("_extra", 0) + 1
                        rec.append(extra)
                    days.append(rec)
            if r is not None or days:
                out.append({"code": code, "ret": r, "days": days})
            del df, ds, closes
        if progress and n % progress == 0:
            print(f"[pass1] {n} 股 | RSS={_rss_mb():.0f}MB", file=sys.stderr, flush=True)
            gc.collect()
    return out


def _auc(pos: list, neg: list) -> Optional[float]:
    """Mann-Whitney AUC(0.5=无判别力,>0.5=分数越高越可能"跑出来")。并列取平均秩。"""
    if not pos or not neg:
        return None
    vals = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg], key=lambda x: x[0])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(vals):                       # 并列取平均秩
        j = i
        while j + 1 < len(vals) and vals[j + 1][0] == vals[i][0]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    rsum = sum(r for r, (_, lab) in zip(ranks, vals) if lab == 1)
    n1, n0 = len(pos), len(neg)
    return round((rsum - n1 * (n1 + 1) / 2) / (n1 * n0), 4)


def _auc_within_day(by_day: dict, valfn, picks_key: str = "win") -> Optional[float]:
    """**日内分层 AUC**:只在同一天的信号之间比较(去掉日期效应),按各日可比对数加权汇总。
    这才是决策相关口径——我们每天是在**当日池内**做选择,不是跨日比较。
    (全局池 AUC 会被日期效应污染,可能与'每日选top-k精确率'方向相反 = Simpson 悖论。)"""
    num = den = 0.0
    for _d, lst in by_day.items():
        pos = [valfn(x) for x in lst if x[picks_key] and valfn(x) is not None]
        neg = [valfn(x) for x in lst if not x[picks_key] and valfn(x) is not None]
        if not pos or not neg:
            continue
        a = _auc(pos, neg)
        if a is None:
            continue
        pairs = len(pos) * len(neg)
        num += a * pairs
        den += pairs
    return round(num / den, 4) if den else None


def _split_days_in_half(by_day: dict) -> tuple[dict, dict]:
    """按日期把信号池切成前/后半程,用于**分段一致性**检验(样本内挑特征极易过拟合,
    见 B1_BACKTEST_FINDINGS §3;要求两半程 AUC 与全样本同号才敢称'弱可用')。"""
    ds = sorted(by_day)
    mid = len(ds) // 2
    return ({d: by_day[d] for d in ds[:mid]}, {d: by_day[d] for d in ds[mid:]})


def _fmt_num(v) -> str:
    """None → '-';0.0 必须显示成 '0.0'(不能用 `v or '-'`——AUC=0.0 是完美反向预测,不是缺失)。"""
    return "-" if v is None else f"{v}"


def _fmt_pct(v) -> str:
    return "-" if v is None else f"{v:.1%}"


def _fmt_pp(v) -> str:
    return "-" if v is None else f"{v:+.1f}pp"


def discriminate_at_signal(records: list, horizon: int = 20, win_thresh: Optional[float] = None,
                           win_top_q: float = 0.2, use_mfe: bool = False,
                           picks_per_day: int = 3) -> dict:
    """**信号当时能否明确选出会跑的票?** 对每个信号(因果、无未来函数):
      label = 前向收益 fwd{h}(或 mfe{h}) 是否"跑出来"(绝对阈值 win_thresh 或全体前 win_top_q 分位);
      对每个**当时可得**特征算 日内AUC(0.5=无判别力) + **每日按该特征选 picks_per_day 只的精确率
      vs 每日随机选同样只数的公平期望**。
    方向:AUC<0.5 = 反向预测子(取反即可用),故两方向精确率都算,判定用 |AUC-0.5| 与有效方向净增益。
    稳健性:另算前/后半程日内AUC,要求与全样本同号(split_consistent)才计入"弱可用",否则标为疑过拟合。
    |AUC-0.5|≈0 且 精确率≈公平基线 ⇒ 信号当时无法把会跑的票挑出来(判别失败)。"""
    rows = []
    key = f"{'mfe' if use_mfe else 'fwd'}{horizon}"
    n_censored = 0                                        # 信号日后数据不足 h 根 → 无标签被剔除(右删失,须报数)
    for r in records:
        for d in (r.get("days") or []):
            if len(d) < 3 or not isinstance(d[2], dict) or key not in d[2]:
                n_censored += 1
                continue
            ex = d[2]
            rows.append({"date": d[0], "code": r["code"], "y": float(ex[key]),
                         "base_score": float(d[1]),
                         "feats": {k[2:]: v for k, v in ex.items() if k.startswith("f_")}})
    if not rows:
        return {"n": 0, "n_censored": n_censored,
                "text": f"无前向数据(Pass1 需带 --horizons 含 {horizon})"}
    ys = sorted((x["y"] for x in rows), reverse=True)
    thr = win_thresh if win_thresh is not None else ys[max(0, int(len(ys) * win_top_q) - 1)]
    for x in rows:
        x["win"] = x["y"] >= thr
    base = sum(1 for x in rows if x["win"]) / len(rows)
    by_day = {}
    for x in rows:
        by_day.setdefault(x["date"], []).append(x)

    def _val(x, f):
        return x["base_score"] if f == "base_score" else x["feats"].get(f)

    halves = _split_days_in_half(by_day)                   # 分段一致性(前/后半程各自重算 AUC)
    feat_names = sorted({k for x in rows for k in x["feats"]}) + ["base_score"]
    out_feats = []
    for f in feat_names:
        vf = (lambda x, _f=f: _val(x, _f))
        pos = [v for v in (vf(x) for x in rows if x["win"]) if v is not None]
        neg = [v for v in (vf(x) for x in rows if not x["win"]) if v is not None]
        allv = [v for v in (vf(x) for x in rows) if v is not None]
        constant = len(set(allv)) <= 1                     # 零方差(如 reversal_k 内的 reversal_quality 恒=4)
        auc = None if constant else _auc_within_day(by_day, vf)
        # 方向:AUC<0.5 表示"特征越小越会跑"= 反向预测子(取反即可用,如 reversal_quality_inv 的由来),
        # 故两个方向的 top-k 精确率都算,判定按 |AUC-0.5| 与**有效方向**的净增益,避免误杀反向信号。
        hit_hi = hit_lo = tot = 0
        fair_num = 0.0                                     # 每日随机选k的期望命中(公平基线,与特征方向无关)
        for _d, lst in by_day.items():
            cand = [x for x in lst if vf(x) is not None]
            if not cand:
                continue
            k = min(picks_per_day, len(cand))
            wr = sum(1 for x in cand if x["win"]) / len(cand)
            fair_num += k * wr                             # 随机选k的期望命中数(与特征无关)
            cand.sort(key=vf, reverse=True)
            tot += k
            hit_hi += sum(int(x["win"]) for x in cand[:k])          # 取特征最大的 k 只
            hit_lo += sum(int(x["win"]) for x in cand[-k:])         # 取特征最小的 k 只
        fair = round(fair_num / tot, 4) if tot else None    # ← 正确的对照(非全局基准率)
        prec = prec_lo = lift = lift_lo = None
        if tot and not constant:                           # 恒定特征的"精确率/增益"纯为并列排序假象,不出数
            prec, prec_lo = round(hit_hi / tot, 4), round(hit_lo / tot, 4)
            if fair is not None:
                lift = round((prec - fair) * 100, 2)
                lift_lo = round((prec_lo - fair) * 100, 2)
        direction = None if auc is None else ("high" if auc >= 0.5 else "low")
        edge = None if auc is None else round(abs(auc - 0.5), 4)
        lift_eff = lift if direction != "low" else lift_lo
        h1 = None if constant else _auc_within_day(halves[0], vf)
        h2 = None if constant else _auc_within_day(halves[1], vf)
        consistent = bool(auc is not None and h1 is not None and h2 is not None
                          and (h1 - 0.5) * (auc - 0.5) > 0 and (h2 - 0.5) * (auc - 0.5) > 0)
        out_feats.append({"feature": f, "auc_pooled": _auc(pos, neg), "auc": auc,
                          "auc_edge": edge, "direction": direction,
                          "constant": constant, "n_pos": len(pos), "n_neg": len(neg),
                          "precision_at_daily_top": prec, "precision_at_daily_bottom": prec_lo,
                          "fair_random_precision": fair, "picks": tot,
                          "lift_pp": lift, "lift_pp_inverted": lift_lo, "lift_pp_effective": lift_eff,
                          "auc_first_half": h1, "auc_second_half": h2, "split_consistent": consistent})
    out_feats.sort(key=lambda r: (r["auc_edge"] if r["auc_edge"] is not None else -1), reverse=True)
    usable = [r for r in out_feats if not r["constant"] and r["auc_edge"] is not None
              and r["auc_edge"] >= 0.03 and (r["lift_pp_effective"] or 0) >= 2 and r["split_consistent"]]
    # 仅通过 |AUC|/增益 但前后半程不同号 → 单独列出,避免当成结论(项目 §3 偏差警示:样本内挑特征极易过拟合)
    unstable = [r for r in out_feats if not r["constant"] and r["auc_edge"] is not None
                and r["auc_edge"] >= 0.03 and (r["lift_pp_effective"] or 0) >= 2 and not r["split_consistent"]]

    def _dtxt(r):
        return "取反(越小越会跑)" if r["direction"] == "low" else "同向(越大越会跑)"
    verdict = ("**无判别力**:无任一特征同时满足 |日内AUC-0.5|≥0.03、有效方向净增益≥+2pp、前后半程同号 "
               "⇒ 信号当时无法把会跑的票挑出来"
               if not usable else
               "弱可用候选(**仅样本内,未 OOS**): "
               + ", ".join(f"{r['feature']}[{_dtxt(r)}](日内AUC {r['auc']}, "
                           f"{r['lift_pp_effective']:+.1f}pp, 半程 {r['auc_first_half']}/{r['auc_second_half']})"
                           for r in usable))
    if unstable:
        verdict += ("; ⚠️前后半程不同号(疑过拟合,不作结论): "
                    + ", ".join(f"{r['feature']}({r['auc_first_half']}/{r['auc_second_half']})" for r in unstable))
    lines = [f"信号 {len(rows)} 个 | 标签:{'MFE' if use_mfe else '前向收益'}{horizon}日 >= {thr:+.2%}"
             f" (基准率 {base:.1%}; {'绝对阈值' if win_thresh is not None else '全体前%.0f%%分位' % (win_top_q*100)})",
             f"  右删失(信号日后不足{horizon}根被剔除) {n_censored} 个"
             + (" —— ⚠️占比高时样本偏早期信号,结论可能失真" if n_censored > len(rows) * 0.2 else ""),
             f"  全局基准率 {base:.1%}(仅参考);对照用**每日随机选top{picks_per_day}的公平期望**:",
             f"    {'特征':<20} {'日内AUC':>8} {'全局AUC':>8} {'方向':>6} {'精确率':>8} {'公平随机':>8}"
             f" {'净增益':>8} {'半程AUC':>13}"]
    for r in out_feats:
        tag = "  ⚠️恒定(门槛内零方差,无判别信息)" if r["constant"] else ""
        if not r["constant"] and r["auc_edge"] is not None and r["auc_edge"] >= 0.03 and not r["split_consistent"]:
            tag = "  ⚠️半程不同号"
        pr = r["precision_at_daily_bottom"] if r["direction"] == "low" else r["precision_at_daily_top"]
        lines.append(f"    {r['feature']:<20} {_fmt_num(r['auc']):>8} {_fmt_num(r['auc_pooled']):>8} "
                     f"{('取反' if r['direction'] == 'low' else '同向' if r['direction'] else '-'):>6} "
                     f"{_fmt_pct(pr):>8} {_fmt_pct(r['fair_random_precision']):>8} "
                     f"{_fmt_pp(r['lift_pp_effective']):>8} "
                     f"{_fmt_num(r['auc_first_half'])}/{_fmt_num(r['auc_second_half'])}{tag}")
    lines.append(f"  -> {verdict}")
    return {"n": len(rows), "n_censored": n_censored, "horizon": horizon, "use_mfe": use_mfe,
            "threshold": round(thr, 4), "base_rate": round(base, 4),
            "features": out_feats, "text": "\n".join(lines)}


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
        for rec in (r.get("days") or []):
            d, sc = rec[0], rec[1]                       # 带 horizons/特征时 rec 为 3 元素,只取前两个
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
    ap.add_argument("--s-data-root", default=os.environ.get("S_DATA_ROOT") or r"E:\S_DATA",
                    help=r"s_data 根目录;可用环境变量 S_DATA_ROOT 覆盖,默认 E:\S_DATA")
    ap.add_argument("--universe-sdata", action="store_true")
    ap.add_argument("--codes", default="")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
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
    ap.add_argument("--horizons", default="",
                    help="Pass1:逗号分隔前向天数(如 20,60)→ 每信号记因果 fwd/mfe,供判别力研究")
    ap.add_argument("--feature-scores", default="",
                    help="Pass1:逗号分隔特征打分器(SCORERS 名,如 reversal_quality,momentum,low_vol,alpha101)")
    ap.add_argument("--sector-features", action="store_true",
                    help="Pass1:每个信号日记 as-of 板块特征(f_sector_favorable 相位有利 / f_sector_momentum 板块动量;"
                    "需 sector_members.json + 板块指数CSV)")
    ap.add_argument("--discriminate", action="store_true",
                    help="Pass2:跑'信号当时能否选出会跑的票'判别力研究(AUC + 每日选top-k精确率)")
    ap.add_argument("--horizon", type=int, default=20, help="判别研究用的前向天数")
    ap.add_argument("--win-thresh", type=float, default=None,
                    help="判别研究:'跑出来'的绝对收益阈值(如 0.3=+30%%);缺省用分位数")
    ap.add_argument("--win-top-q", type=float, default=0.2, help="判别研究:分位口径(默认前20%%算跑出来)")
    ap.add_argument("--use-mfe", action="store_true", help="判别研究:用区间最大涨幅(MFE)而非期末收益")
    ap.add_argument("--picks-per-day", type=int, default=3, help="判别研究:每日选几只算精确率")
    ap.add_argument("--from-firings", default="",
                    help="Pass2:读一个或多个(逗号分隔) Pass1 JSON,合并算捕捉率+排名(内存极小)")
    ap.add_argument("--capture-top-pct", type=float, default=50.0, help="捕捉研究的赢家口径(默认收益前50%%)")
    ap.add_argument("--surface-top-n", type=int, default=20, help="每日展示阈值(top-N 算 surfaced)")
    ap.add_argument("--winner-basis", choices=["universe", "profitable"], default="universe",
                    help="赢家口径:universe=全域收益前top_pct%%(含下跌股);profitable=**盈利股内**前top_pct%%")
    ap.add_argument("--min-winner-ret", type=float, default=None,
                    help="赢家另加绝对收益门槛(如 0.5=至少+50%%);默认仅按 top_pct 排名(不看正负)")
    ap.add_argument("--rank-score", choices=sorted(bt.SCORERS) + ["none"],
                    default="reversal_quality", help="当日信号池内排序分(none=随机;全部 SCORERS 可选)")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    if not args.from_firings and (not args.start or not args.end):
        ap.error("需提供 --start 和 --end(--from-firings 模式除外)")

    # Pass2:仅合并 Pass1 产物算排名(不加载任何K线,内存极小)
    if args.from_firings:
        import json as _j
        recs: list[dict] = []
        for fp in [x.strip() for x in args.from_firings.split(",") if x.strip()]:
            d = _j.loads(Path(fp).read_text(encoding="utf-8"))
            recs.extend(d if isinstance(d, list) else (d.get("records") or []))
        if args.discriminate:
            dis = discriminate_at_signal(recs, horizon=args.horizon, win_thresh=args.win_thresh,
                                         win_top_q=args.win_top_q, use_mfe=args.use_mfe,
                                         picks_per_day=args.picks_per_day)
            print(f"\n=== 信号当时判别力:能否明确选出会跑的票 ===")
            print(dis["text"])
            if args.out:
                import json as _j2
                Path(args.out).write_text(_j2.dumps({"discriminate": dis}, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
            return 0
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
        # 加载终点要比 --end 多带 max(horizons) 根,否则区间尾部的信号拿不到前向标签被静默右删失
        load_end = args.end
        hz_max = max((int(x) for x in args.horizons.split(",") if x.strip()), default=0)
        if hz_max:
            load_end = (_date.fromisoformat(args.end) + _td(days=int(hz_max * 1.6) + 5)).isoformat()
        for k in range(0, len(codes), chunk):
            d = fn2(codes[k:k + chunk], 0, start=load_start, end=load_end, root=root2)
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
            if not (1 <= i <= n):
                ap.error(f"--shard 需满足 1<=i<=n(如 1/3..3/3),收到 {args.shard}")
            codes = [c for k, c in enumerate(codes) if k % n == (i - 1) % n]
            print(f"[pass1] 分片 {args.shard}: {len(codes)} 只", file=sys.stderr)
        scorer = None if args.rank_score == "none" else bt.SCORERS.get(args.rank_score)
        hz = tuple(int(x) for x in args.horizons.split(",") if x.strip()) if args.horizons else ()
        fsc = {}
        for nm in [x.strip() for x in args.feature_scores.split(",") if x.strip()]:
            if nm in bt.SCORERS:
                fsc[nm] = bt.SCORERS[nm]
            else:
                print(f"[WARN] 未知特征打分器 {nm}(可选: {','.join(sorted(bt.SCORERS))})", file=sys.stderr)
        fstats: dict = {}
        xfn = None
        if args.sector_features:
            import json as _jm
            mpath = Path(args.sector_members)
            members = _jm.loads(mpath.read_text(encoding="utf-8")) if mpath.is_file() else {}
            if not members:
                ap.error("--sector-features 需 sector_members.json(先跑 fetch_sector_index_history.py --members)")
            xfn = build_sector_features(args.sector_index_dir, members)
            print(f"[pass1] 板块特征已启用 (dir={args.sector_index_dir})", file=sys.stderr)
        recs = extract_firings(_chunked_items(args.chunk_size), args.start, args.end,
                              bt.ENTRY_GATES[args.entry_filter], scorer=scorer,
                              gate_window=args.gate_window, progress=args.progress,
                              horizons=hz, feature_scorers=fsc, stats=fstats,
                              extra_feature_fn=xfn)
        if fstats.get("feature_failures"):
            print(f"[WARN] 特征打分器异常(特征可能缺失): {fstats['feature_failures']}", file=sys.stderr)
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
        print("\n=== 赢家板块集中度 / 板块共振 ===")
        print(conc["text"])
    if args.out:
        import json
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
