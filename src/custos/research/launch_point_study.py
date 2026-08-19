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
用法(用户机):uv run python src/custos/research/launch_point_study.py --data-source qlib --universe-sdata \
  --start 2024-09-01 --end 2025-06-30 --entry-filter reversal_k --top-pct 10 --buffer-days 60
"""

from __future__ import annotations

import argparse
import bisect
import math
import os
import statistics
import sys
from datetime import date as _date, timedelta as _td
from pathlib import Path
from typing import Any, Optional, cast

import pandas as pd


from custos.research import backtest_factors as bt  # noqa: E402  复用 ENTRY_GATES / load_amv_regime

# 股本事件索引构建的唯一所有者（包限定导入：该模块持可变缓存，见 _shares 模块头）。
from custos.core.factors._shares import events_to_idx as _shares_events_to_idx  # noqa: E402
from custos.core.paths import MARKET_DIR  # noqa: E402


def window_return(
    dates: list, closes: list, start: str, end: str, exclude_new_listing: bool = False
) -> Optional[float]:
    """区间收益 close(最后<=end)/close(第一>=start)-1。数据不足返回 None。

    exclude_new_listing=True 时，**窗内新上市**的票返回 None：它的"区间收益"其实是
    从上市首日/次新首个可得价起算，掺进了新股上市定价的一次性跳幅，而这类票在
    赢家阈值（top_pct 分位）里照样参与统计，却因 len(df)<min_bars 几乎不可能出信号
    —— 结果是抬高赢家门槛、压低 recall（审计）。判定：窗内第一根 K 线就是这只票
    **全部数据的第一根**且晚于 start ⇒ 窗前无价格 ⇒ 视为窗内新上市（数据源历史被
    截断时同样成立，两者都不该进区间收益统计）。
    默认 False：这是研究口径变更，会改动赢家集合，开关交给调用方/CLI。
    """
    idx = [i for i, d in enumerate(dates) if start <= str(d)[:10] <= end]
    if len(idx) < 2:
        return None
    a, b = idx[0], idx[-1]
    if exclude_new_listing and a == 0 and str(dates[0])[:10] > start:
        return None
    return (closes[b] / closes[a] - 1) if closes[a] else None


def find_launch(
    dates: list, closes: list, signal_idxs: list[int], end: str
) -> Optional[dict]:
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
        r = kdj(df.iloc[: idx + 1])
        return (
            round(float(r["j"]), 2)
            if r.get("available") and r.get("j") is not None
            else None
        )
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
                lead = k - bisect.bisect_left(
                    rdates, launch_date
                )  # 交易日数(regime日历)
                break
    return {"regime": cur, "lead_days_to_long": lead}


def _analyze_stats(launches: list, winners: list, winner_rets: dict) -> dict[str, Any]:
    """analyze 的统计块:regime 计数 / lead-days 分布 / 起涨点 J 值分布。"""
    n = len(launches)
    by_regime: dict = {}
    for L in launches:
        by_regime[L["regime"]] = by_regime.get(L["regime"], 0) + 1
    leads = [
        L["lead_days_to_long"]
        for L in launches
        if L["regime"] in ("空头", "中性") and L["lead_days_to_long"] is not None
    ]  # "未知"不计入分布
    out: dict[str, Any] = {
        "n_winners": len(winners),
        "n_launches": n,
        "by_regime": by_regime,
        "winners": winners,
        "winner_rets": winner_rets,
        "launches": launches,
    }
    if leads:
        leads.sort()
        out["lead_days"] = {
            "n": len(leads),
            "median": statistics.median(leads),
            "p25": leads[len(leads) // 4],
            "p75": leads[3 * len(leads) // 4],
            "min": leads[0],
            "max": leads[-1],
            "mean": round(statistics.mean(leads), 1),
        }
    js = sorted(L["j_at_launch"] for L in launches if L.get("j_at_launch") is not None)
    if js:
        out["j_at_launch_stats"] = {
            "n": len(js),
            "min": js[0],
            "p25": js[len(js) // 4],
            "median": statistics.median(js),
            "p75": js[3 * len(js) // 4],
            "max": js[-1],
            "share_neg": round(sum(1 for v in js if v < 0) / len(js), 3),
            "share_lt5": round(sum(1 for v in js if v < 5) / len(js), 3),
        }
    return out


def _render_analyze(out: dict) -> str:
    """analyze 的文本渲染块(输入为 _analyze_stats 的产物)。"""
    n = out["n_launches"]
    by_regime = out["by_regime"]
    long_share = round(by_regime.get("做多", 0) / n, 3) if n else None
    ld = out.get("lead_days")
    lead_txt = (
        f"空头起涨→领先做多 中位 {ld['median']} / p25 {ld['p25']} / "
        f"p75 {ld['p75']} / max {ld['max']} 交易日 (n={ld['n']})"
        if ld
        else "无空头起涨样本"
    )
    st = out.get("j_at_launch_stats")
    j_txt = ""
    if st:
        j_txt = (
            f"\n  起涨点 J 值: 中位 {st['median']} (p25 {st['p25']} / p75 {st['p75']}), "
            f"范围 {st['min']}~{st['max']}; J<0 占 {st['share_neg'] * 100:.0f}%、J<5 占 {st['share_lt5'] * 100:.0f}%"
            f" (n={st['n']}; 门槛 J<13 已限定上界,看池内深度分布)"
        )
    return (
        f"赢家 {out['n_winners']} 只 / 起涨点 {n} 个; 落做多 {by_regime.get('做多', 0)}"
        f"({(long_share or 0) * 100:.0f}%)、空头 {by_regime.get('空头', 0)}、中性 {by_regime.get('中性', 0)}。\n  {lead_txt}{j_txt}"
    )


def analyze(
    bars_by_code: dict,
    regime: dict[str, str],
    start: str,
    end: str,
    entry_gate,
    top_pct: float = 10.0,
    buffer_days: int = 60,
    min_bars: int = 40,
) -> dict[str, Any]:
    """主分析。bars_by_code:{code:df[date,open,high,low,close,volume]};regime:date→做多/空头/中性。"""
    rets = []
    for code, raw in bars_by_code.items():
        if raw is None or len(raw) == 0:
            continue
        df = raw.sort_values("date").reset_index(drop=True)
        r = window_return(
            df["date"].tolist(), df["close"].astype(float).tolist(), start, end
        )
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
        sig = [
            i
            for i in range(min_bars, len(df))
            if lo <= ds[i] <= end and entry_gate(df.iloc[: i + 1])
        ]
        if not sig:
            continue
        lp = find_launch(
            df["date"].tolist(), df["close"].astype(float).tolist(), sig, end
        )
        if lp is None:
            continue
        rl = regime_at_and_lead(regime, lp["date"])
        rec = {"code": code, **lp, **rl}
        jv = _kdj_j_at(df, lp["idx"])  # 起涨点当日 J 值(超卖深度)
        if jv is not None:
            rec["j_at_launch"] = jv
        launches.append(rec)

    out = _analyze_stats(launches, winners, {c: round(r, 4) for c, r in rets[:n_top]})
    out["text"] = _render_analyze(out)
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


def _pick_winners(
    rets: list,
    top_pct: float,
    min_winner_ret: Optional[float] = None,
    basis: str = "universe",
) -> tuple[set, dict]:
    """按口径挑赢家。rets 需已按收益降序。返回 (winners, meta)。
    basis="universe"  :全域(含下跌股)按收益排序取前 top_pct% —— top50% ≈ "中位数以上"。
    basis="profitable":**先筛盈利股(ret>0),再取其中前 top_pct%** —— 更贴近"真赢家"语义。
    min_winner_ret:再叠绝对收益门槛(如 0.5=+50%)。
    ⚠️ meta["n_universe_all"] 的口径局限:它只是**传入 rets 的条数**(有信号或被 delisted_ret
    救回的票),不含无信号退市股 → 以其为分母的上涨占比偏高,普涨窗判定偏保守。"""
    pool = [(c, v) for c, v in rets if v > 0] if basis == "profitable" else list(rets)
    n_top = max(1, int(len(pool) * top_pct / 100)) if pool else 0
    sel = pool[:n_top]
    winners = {c for c, v in sel if min_winner_ret is None or v >= min_winner_ret}
    meta = {
        "winner_basis": basis,
        "n_universe_all": len(rets),
        "n_profitable": sum(1 for _, v in rets if v > 0),
        "n_basis_pool": len(pool),
        "min_winner_ret": min_winner_ret,
        "winner_ret_cutoff": (round(sel[-1][1], 4) if sel else None),
    }
    return winners, meta


def _capture_accumulate(
    winners: set,
    win_fire: dict,
    rank_of: dict,
    surface_top_n: int,
    day_winners: Optional[dict],
) -> dict[str, Any]:
    """_summarize_capture 的逐赢家累加块:best_rank / 每日池 / oracle / 随机基线。"""
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
        if day_winners:  # oracle:完美排序下,只要某触发日"池中赢家数"≤top_n 即可浮出
            wc = [day_winners.get(d, 0) for d in days]
            if wc:
                wpools.append(min(wc))
                if min(wc) <= surface_top_n:
                    oracle += 1
        p_miss = 1.0
        for _, pl in cand:  # 随机排名下"至少一天进 top_n"的概率
            p_miss *= (1 - min(1.0, surface_top_n / pl)) if pl else 1.0
        rand_p.append(1 - p_miss)
    return {
        "captured": captured,
        "surfaced": surfaced,
        "buried": buried,
        "oracle": oracle,
        "best_ranks": best_ranks,
        "best_pcts": best_pcts,
        "pools": pools,
        "rand_p": rand_p,
        "wpools": wpools,
    }


def _render_capture(out: dict) -> str:
    """_summarize_capture 的文本渲染块(输入为已填好统计字段的 out)。"""
    nw = out["n_winners"]
    captured = out["captured"]
    buried = out["buried_selected_not_found"]
    top_pct = out["top_pct"]
    surface_top_n = out["surface_top_n"]
    miss = round(buried / captured, 3) if captured else None
    edge = (out["surfaced_rate_of_captured"] or 0) - (
        out["random_surfaced_rate_of_captured"] or 0
    )
    text = (
        (
            f"全域 {out['n_universe']} 只(盈利 {out.get('n_profitable', '-')} 只), "
            f"赢家口径={out.get('winner_basis', 'universe')}"
            + (
                f"→盈利股内前{top_pct:.0f}%"
                if out.get("winner_basis") == "profitable"
                else f"→全域前{top_pct:.0f}%"
            )
            + (
                f", 且≥{(out['min_winner_ret'] or 0) * 100:.0f}%"
                if out.get("min_winner_ret")
                else ""
            )
            + f", 收益切点 {out.get('winner_ret_cutoff')}"
        )
        + f"; 赢家 {nw} 只; **捕捉率(recall) {(out['recall'] or 0) * 100:.0f}%**"
        f"({captured}/{nw})。捕捉到者中: 进 top{surface_top_n} = **surfaced {(out['surfaced_rate_of_captured'] or 0) * 100:.0f}%**, "
        f"'选出来但没发现'(埋没) {(miss or 0) * 100:.0f}%。\n  "
        f"当日信号池 中位 {out.get('daily_pool', {}).get('median', '-')} / max {out.get('daily_pool', {}).get('max', '-')}; "
        f"赢家最佳排名 中位 {out.get('best_rank', {}).get('median', '-')} (百分位中位 {out.get('best_rank_pct_median', '-')}, 0.5≈随机)。\n  "
        f"排序增益: 我们 surfaced {(out['surfaced_rate_of_captured'] or 0) * 100:.0f}% vs 随机 "
        f"{(out['random_surfaced_rate_of_captured'] or 0) * 100:.0f}% → {'排序有效(+%.0fpp)' % (edge * 100) if edge > 0.02 else '排序≈随机(无surfacing增益)' if abs(edge) <= 0.02 else '排序反而更差(%.0fpp)' % (edge * 100)}。"
    )
    if out.get("oracle_surfaced_rate_of_captured") is not None:
        orc = out["oracle_surfaced_rate_of_captured"]
        text += (
            f"\n  **完美排序上限(oracle) {orc * 100:.0f}%**(池中赢家数中位 {out['winners_in_pool_median']:.0f} "
            f"vs 展示位 top{surface_top_n})——"
            + (
                "上限已高 → 瓶颈在**排序能力**(有提升空间)"
                if orc >= 0.9
                else "上限本身就低 → 瓶颈是**展示位不够/赢家口径过宽**(结构),排序再好也救不回"
            )
            + "。"
        )
    return text


def _summarize_capture(
    rets: list,
    winners: set,
    win_fire: dict,
    rank_of: dict,
    top_pct: float,
    surface_top_n: int,
    day_winners: Optional[dict] = None,
    wmeta: Optional[dict] = None,
) -> dict[str, Any]:
    """捕捉率/排名质量的共享汇总(流式与两趟分片模式同口径)。
    day_winners:{date: 当日池中赢家数} → 据此算 **oracle(完美排序上限)**:
    完美排序把赢家排在最前,则当日赢家数 ≤ top_n 时该赢家必浮出。区分"排序不行"与"展示位不够"。"""
    acc = _capture_accumulate(winners, win_fire, rank_of, surface_top_n, day_winners)
    captured, surfaced, buried = acc["captured"], acc["surfaced"], acc["buried"]
    best_ranks, best_pcts, pools = acc["best_ranks"], acc["best_pcts"], acc["pools"]
    rand_p, wpools, oracle = acc["rand_p"], acc["wpools"], acc["oracle"]

    nw = len(winners)
    out: dict[str, Any] = {
        "n_universe": len(rets),
        "n_winners": nw,
        "top_pct": top_pct,
        "surface_top_n": surface_top_n,
        "captured": captured,
        "recall": round(captured / nw, 3) if nw else None,
        "surfaced": surfaced,
        "buried_selected_not_found": buried,
        "surfaced_rate_of_captured": round(surfaced / captured, 3)
        if captured
        else None,
        "random_surfaced_rate_of_captured": round(statistics.mean(rand_p), 3)
        if rand_p
        else None,
    }
    if day_winners and wpools:
        out["oracle_surfaced"] = oracle
        out["oracle_surfaced_rate_of_captured"] = (
            round(oracle / captured, 3) if captured else None
        )
        out["winners_in_pool_median"] = statistics.median(sorted(wpools))
    if best_ranks:
        br_s, po_s, pc_s = sorted(best_ranks), sorted(pools), sorted(best_pcts)
        out["best_rank"] = {
            "median": statistics.median(br_s),
            "p25": br_s[len(br_s) // 4],
            "p75": br_s[3 * len(br_s) // 4],
            "min": br_s[0],
            "max": br_s[-1],
        }
        out["daily_pool"] = {"median": statistics.median(po_s), "max": po_s[-1]}
        out["best_rank_pct_median"] = statistics.median(pc_s)  # 0=最强,0.5≈随机,1=最弱
    if wmeta:
        out.update(wmeta)
    out["text"] = _render_capture(out)
    return out


def build_sector_features(index_dir, members, mom_days: int = 20):
    """构建 **as-of 板块特征**查询 fn(code6, date)->dict(供 extract_firings 记录到每个信号):
    - f_sector_favorable(0/1):所属板块任一当日处有利相位(DIF>0 且无近期顶背离/三打,
      favorable_series 因果序列,摆动高点需 i+fractal 确认,无未来函数);
    - f_sector_momentum(float):所属板块指数 trailing mom_days 最强收益——当期强势/主流的
      **因果代理**(密度榜需逐日全市场横截面,流式 Pass1 算不起;板块动量是单板块可算的近似)。
    未分类/数据缺失 → 返回 {}(特征缺省,不误标 0)。绝不 raise。

    返回的 fn 带 **.stats**:sectors_requested/sectors_loaded/csv_missing/csv_error/
    queries/emitted/unclassified/no_asof_close[/build_error]。
    ⚠️ 这是审计 E8 的修法:原实现在"一个板块 CSV 都没有"时照样返回一个恒空的 fn,
    整轮研究 f_sector_* 全缺省,结论被写成"板块相位无判别力"——而真相是特征从未生成。
    调用方(main)据 stats 硬失败/告警,与 --sector-filter 的 ap.error 校验对齐。
    另:板块有 CSV 但信号日早于板块数据起点(as-of 取不到收盘)时,原实现仍吐
    f_sector_favorable=0,把"没数据"写成"不利相位";现按文档意图记 no_asof_close 并返回 {}。"""
    import bisect as _b
    from custos.core.factors import sector_phase as sp  # noqa: PLC0415
    from custos.core.factors import sector_mainstream as sm  # noqa: PLC0415

    stats: dict[str, Any] = {
        "sectors_requested": 0,
        "sectors_loaded": 0,
        "csv_missing": 0,
        "csv_error": 0,
        "queries": 0,
        "emitted": 0,
        "unclassified": 0,
        "no_asof_close": 0,
    }
    code2secs: dict = {}
    sec_data: dict[str, tuple] = {}
    try:
        code2secs = sm.invert_members(members)
        wanted = {s for secs in code2secs.values() for s in secs}
        stats["sectors_requested"] = len(wanted)
        for sec in sorted(wanted):
            p = Path(index_dir) / f"{sec}.csv"
            if not p.is_file():
                stats["csv_missing"] += 1
                continue
            try:
                df = pd.read_csv(p)
                dates = [str(d)[:10] for d in df["date"]]
                closes = df["close"].astype(float).tolist()
                fav = sp.favorable_series(dates, closes)
                sec_data[sec] = (dates, closes, fav)
            except Exception as exc:  # noqa: BLE001
                stats["csv_error"] += 1
                print(f"[WARN] 板块指数 CSV 不可用 {p.name}: {exc}", file=sys.stderr)
                continue
        stats["sectors_loaded"] = len(sec_data)
    except Exception as exc:  # noqa: BLE001
        stats["build_error"] = f"{exc.__class__.__name__}: {exc}"
        print(
            f"[WARN] 板块特征构建失败(特征将全程缺省): {stats['build_error']}",
            file=sys.stderr,
        )

    def fn(code6: str, date: str) -> dict:
        stats["queries"] += 1
        secs = [s for s in code2secs.get(str(code6)[:6], []) if s in sec_data]
        if not secs:
            stats["unclassified"] += 1
            return {}
        fav_any = 0
        best_mom: Optional[float] = None
        n_asof = 0
        for s in secs:
            dates, closes, fav = sec_data[s]
            j = _b.bisect_right(dates, date) - 1  # as-of:最近 ≤ date 的板块收盘
            if j < 0:
                continue
            n_asof += 1
            if fav.get(dates[j]):
                fav_any = 1
            k = j - mom_days
            if k >= 0 and closes[k]:
                m = closes[j] / closes[k] - 1
                best_mom = m if best_mom is None else max(best_mom, m)
        if not n_asof:  # 信号日早于板块数据起点 → 缺省,不写 0
            stats["no_asof_close"] += 1
            return {}
        out: dict[str, Any] = {"f_sector_favorable": fav_any}
        if best_mom is not None:
            out["f_sector_momentum"] = round(best_mom, 4)
        stats["emitted"] += 1
        return out

    fn.stats = stats  # type: ignore[attr-defined]  # 函数挂元数据是刻意的（同 sector_phase.gate）
    return fn


def _scorer_value(scorer, sub, code):
    """Run a scorer and distinguish "no data" from "scored zero".

    Returns (value, ok). ``ok=False`` means the scorer could not produce a
    score — the caller must **skip** that firing rather than substitute 0.0.

    Substituting 0.0 was actively misleading for scorers whose domain includes
    negatives (mcap / low_vol / momentum): a stock with no market-cap data
    scored 0.0 and therefore outranked every genuinely scored stock, so
    surfaced/best_rank conclusions were driven by missing data.
    """
    if scorer is None:
        return 0.0, True
    sr = scorer(sub, code)
    if isinstance(sr, dict):
        val = sr.get("score") if sr else None
    else:
        val = sr
    if val is None:
        return None, False
    return float(val), True


def _horizon_extras(closes: list, i: int, horizons: tuple) -> dict:
    """因果前向收益块:只用信号日之后的数据(fwd{h}=期末收益, mfe{h}=区间最大涨幅)。"""
    extra: dict = {}
    for h in horizons:
        j = i + int(h)
        if j < len(closes) and closes[i]:
            extra[f"fwd{h}"] = round(closes[j] / closes[i] - 1, 4)
            seg = closes[i + 1 : j + 1]
            if seg:
                extra[f"mfe{h}"] = round(max(seg) / closes[i] - 1, 4)
    return extra


def _feature_scorer_extras(sub, code, feature_scorers: Optional[dict], stats) -> dict:
    """特征打分器块:逐 scorer 记 f_{name};异常计数防静默(恒失败的特征不得无声消失)。"""
    extra: dict = {}
    for fname, fsc in (feature_scorers or {}).items():
        try:
            fr = fsc(sub, code)
            v = (fr or {}).get("score") if isinstance(fr, dict) else fr
            if v is not None:
                extra[f"f_{fname}"] = round(float(v), 6)
        except Exception:  # noqa: BLE001
            if stats is not None:
                ff = stats.setdefault("feature_failures", {})
                ff[fname] = ff.get(fname, 0) + 1
    return extra


def _extra_fn_values(code, date: str, extra_feature_fn, stats) -> dict:
    """as-of 附加特征块(板块相位/动量等);异常计数防静默。"""
    try:
        return dict(extra_feature_fn(code, date))
    except Exception:  # noqa: BLE001
        if stats is not None:
            ff = stats.setdefault("feature_failures", {})
            ff["_extra"] = ff.get("_extra", 0) + 1
        return {}


def _style_extras(df, i: int, closes: list, code) -> dict:
    """风格特征块:上市板序数(免数据) + 20日均成交额(log10,市值代理)。"""
    extra: dict = {
        "f_board_code": float(
            next(
                (k for k, (nm, _) in enumerate(BOARDS) if nm == board_of(code)),
                len(BOARDS),
            )
        )
    }
    lo20 = max(0, i - 19)
    amt = [closes[j] * float(df["volume"].iloc[j]) for j in range(lo20, i + 1)]
    amt = [a for a in amt if a > 0]
    if amt:
        extra["f_amount20"] = round(math.log10(sum(amt) / len(amt)), 4)
    return extra


def _trade_sim_extra(df, i: int, bbi, bbi_consec: int, stop_pct: float, stats) -> dict:
    """本策略买卖规则下的实际一笔(simulate_b1_trade);异常计数防静默。"""
    try:
        sub_full = df.iloc[:]  # 规则需向后走,故用全量 df
        sim = bt.simulate_b1_trade(
            sub_full,
            i,
            bbi,
            bbi_exit_consec=bbi_consec,
            stop_mode="pct",
            stop_pct=stop_pct,
        )
        return {
            "sim_ret": round(float(sim["ret"]), 4),
            "sim_reason": sim["reason"],
            "sim_holding": sim["holding"],
        }
    except Exception:  # noqa: BLE001
        if stats is not None:
            ff = stats.setdefault("feature_failures", {})
            ff["_trade_sim"] = ff.get("_trade_sim", 0) + 1
        return {}


def _signal_extras(
    df,
    i: int,
    ds: list,
    closes: list,
    sub,
    code,
    *,
    horizons: tuple,
    feature_scorers: Optional[dict],
    extra_feature_fn,
    style_features: bool,
    shares_idx: Optional[dict],
    trade_sim: bool,
    bbi,
    stop_pct: float,
    bbi_consec: int,
    stats: Optional[dict],
) -> dict:
    """extract_firings 的单信号特征块:fwd/mfe、f_ 特征、as-of 附加特征、风格、市值、trade_sim。

    bbi 由调用方**逐股预计算一次全序列**传入:BBI 是因果滑动均值(rolling mean),
    前缀第 i 根 == 全序列第 i 根,与"每个信号对 sub_full 重算"逐位等价(原实现 O(n²))。
    """
    extra: dict = {}
    extra.update(_horizon_extras(closes, i, horizons))
    extra.update(_feature_scorer_extras(sub, code, feature_scorers, stats))
    if extra_feature_fn is not None:  # as-of 附加特征(板块相位/动量)
        extra.update(_extra_fn_values(code, ds[i], extra_feature_fn, stats))
    if style_features:  # 风格:上市板 + 成交额(市值代理)
        extra.update(_style_extras(df, i, closes, code))
    if shares_idx is not None:  # 真市值:as-of 股本×信号日收盘
        evs = shares_idx.get(code)
        if evs:
            k2 = bisect.bisect_right(evs, (ds[i], float("inf"))) - 1
            if k2 >= 0 and evs[k2][1] and closes[i]:
                extra["f_mcap"] = round(
                    math.log10(evs[k2][1] * closes[i] / 1e8), 4
                )  # 亿元
    if trade_sim:  # 本策略买卖规则下的实际一笔
        extra.update(_trade_sim_extra(df, i, bbi, bbi_consec, stop_pct, stats))
    return extra


def _extract_signals(
    df,
    ds: list,
    closes: list,
    code,
    start: str,
    end: str,
    entry_gate,
    scorer,
    min_bars: int,
    gate_window: int,
    horizons: tuple,
    feature_scorers: Optional[dict],
    stats: Optional[dict],
    extra_feature_fn,
    trade_sim: bool,
    bbi,
    stop_pct: float,
    bbi_consec: int,
    style_features: bool,
    shares_idx: Optional[dict],
) -> tuple:
    """单股信号扫描块 → (days=[[date,score(,extra)]...], 无打分跳过数)。"""
    days: list = []
    skipped_no_score = 0
    for i in range(min_bars, len(df)):
        if not (start <= ds[i] <= end):
            continue
        lo = max(0, i + 1 - gate_window) if gate_window else 0
        sub = df.iloc[lo : i + 1]
        if not entry_gate(sub):
            continue
        sc, sc_ok = _scorer_value(scorer, sub, code)
        if not sc_ok:
            skipped_no_score += 1  # 打分不出来的信号不参与排名
            continue
        rec: list = [ds[i], float(sc)]
        if (
            horizons
            or feature_scorers
            or extra_feature_fn
            or style_features
            or trade_sim
            or shares_idx is not None
        ):
            rec.append(
                _signal_extras(
                    df,
                    i,
                    ds,
                    closes,
                    sub,
                    code,
                    horizons=horizons,
                    feature_scorers=feature_scorers,
                    extra_feature_fn=extra_feature_fn,
                    style_features=style_features,
                    shares_idx=shares_idx,
                    trade_sim=trade_sim,
                    bbi=bbi,
                    stop_pct=stop_pct,
                    bbi_consec=bbi_consec,
                    stats=stats,
                )
            )
        days.append(rec)
    return days, skipped_no_score


def _extract_stock(
    code,
    raw,
    start: str,
    end: str,
    entry_gate,
    scorer,
    min_bars: int,
    gate_window: int,
    horizons: tuple,
    feature_scorers: Optional[dict],
    stats: Optional[dict],
    extra_feature_fn,
    ret_start: Optional[str],
    ret_end: Optional[str],
    delisted_ret: Optional[float],
    trade_sim: bool,
    stop_pct: float,
    bbi_consec: int,
    style_features: bool,
    shares_idx: Optional[dict],
) -> tuple:
    """extract_firings 的单股处理块 → (rec_out 或 None, 本股因无打分被跳过的信号数)。"""
    if raw is None or not len(raw):
        return None, 0
    skipped_no_score = 0
    df = raw.sort_values("date").reset_index(drop=True)
    ds = [str(d)[:10] for d in df["date"]]
    closes = df["close"].astype(float).tolist()
    # BBI 逐股预计算一次全序列:bbi_series 是因果 rolling 均值,前缀第 i 根 == 全序列
    # 第 i 根,与"每个信号对 sub_full 重算"(原实现,每信号 O(n))逐位等价 → 每股 O(n)。
    bbi = (
        bt._bbi_series(df["close"].astype(float))
        if trade_sim and len(df) >= min_bars
        else None
    )
    r = window_return(ds, closes, ret_start or start, ret_end or end)
    days: list = []
    if len(df) >= min_bars:
        days, skipped_no_score = _extract_signals(
            df,
            ds,
            closes,
            code,
            start,
            end,
            entry_gate,
            scorer,
            min_bars,
            gate_window,
            horizons,
            feature_scorers,
            stats,
            extra_feature_fn,
            trade_sim,
            bbi,
            stop_pct,
            bbi_consec,
            style_features,
            shares_idx,
        )
    delisted = False
    if r is None and days and delisted_ret is not None:
        # 有信号但 label 窗口无价格 = 空头段内退市/长停 → 按大亏计入非赢家(去幸存者偏差)
        r, delisted = float(delisted_ret), True
    rec_out: Optional[dict] = None
    if r is not None or days:
        rec_out = {"code": code, "ret": r, "days": days}
        if delisted:
            rec_out["delisted"] = True
    del df, ds, closes
    return rec_out, skipped_no_score


def extract_firings(
    bars,
    start: str,
    end: str,
    entry_gate,
    scorer=None,
    min_bars: int = 40,
    gate_window: int = 120,
    progress: int = 0,
    horizons: tuple = (),
    feature_scorers: Optional[dict] = None,
    stats: Optional[dict] = None,
    extra_feature_fn=None,
    ret_start: Optional[str] = None,
    ret_end: Optional[str] = None,
    delisted_ret: Optional[float] = None,
    trade_sim: bool = False,
    stop_pct: float = 8.0,
    bbi_consec: int = 2,
    style_features: bool = False,
    shares_events: Optional[list] = None,
) -> list[dict]:
    """**Pass1(可分片)**:逐股抽取 {code, ret, days:[[date,score],..]}——极小的中间产物。
    可按 --shard 拆多个独立进程跑(每片内存全新),彻底规避 loader 内存问题;Pass2 再合并算排名。
    horizons:如 (20,60) → 每个信号日额外记 **因果前向收益**(fwd{h}=close[i+h]/close[i]-1,
    mfe{h}=区间内最大涨幅),用于"信号当时能否判别未来会跑"的研究(不看窗口末尾,无未来函数)。
    feature_scorers:{名称: scorer} → 每个信号日记下**当时可得**的特征值,作为判别子候选。
    extra_feature_fn(code6, date)->dict → 每个信号日追加 as-of 特征(如 build_sector_features
    的板块相位/板块动量);键需以 f_ 开头才会被判别研究收集。
    stats:传入 dict 时收集运行统计(feature_failures=各特征 scorer 异常次数——防异常被静默吞掉)。
    ret_start/ret_end:**赢家口径窗口**,与信号窗口 [start,end] 解耦。默认与信号窗口相同;
    研究"空头段就识别未来赢家"时须设成随后的**做多段**——信号在空头采集(建仓点多在空头,
    结论#11),但"涨得好"发生在随后的多头段,两窗混用会把赢家定义成"空头里跌得少"。
    delisted_ret:两窗解耦引入的**幸存者偏差补丁**。空头段就退市/长期停牌的票在 label 窗口
    没有价格 → ret=None → 记录被丢 → 飞刀被自动剔除,判别力被系统性高估(§3 首条)。
    传入如 -1.0 时:这类票按"清零/大亏"计入**非赢家**并标 delisted=True,不再消失。
    ⚠️口径局限:该补丁只能救回**有信号**的退市股;无信号退市股仍完全缺失(连记录都没有),
    下游 n_universe_all / 上涨占比的分母因此偏小 → 上涨占比偏高、普涨窗可能漏标(见
    discriminate_at_signal 输出中的口径警示)。
    trade_sim:每个信号额外按 **本策略买卖规则**(simulate_b1_trade:信号日收盘进场、pct 止损、
    BBI 连破止盈)算一笔实际收益 → sim_ret/sim_reason/sim_holding。与区间涨幅口径对比即可回答
    "赢家我们到底吃到了几成"(coverage_report)。⚠️ 收益受加载窗口右端截断(reason=open_end)。
    style_features:追加风格特征 f_board_code(上市板序数,免数据)与 f_amount20
    (log10 20日均 close×volume ≈ 成交额,**市值代理**——qlib bundle 无总股本)。
    shares_events:fetch_market_cap 的股本变动事件(load_events 产物)——提供时追加
    f_mcap = log10(信号日总股本×信号日收盘 / 1e8)(**真市值,亿元**;股本按 observed_on≤信号日
    取最近事件,只可能 stale 不会 look-ahead;早于最早事件的信号日 → 特征缺省)。
    ⚠️ 2026-07-31 起真市值已可得:`local_tdx/fetch_market_cap.py` 提供总股本/总市值;
    台账此后已**东财 F10 全史回填**(2018 前亦有,最早事件 1979)——新研究应改用真市值,
    成交额只作流动性因子。索引构建走 `factors/_shares.events_to_idx`(唯一所有者,
    2026-08-09 收敛;load_events 的 LEDGER 与 `_shares.shares_idx()` 读的是同一个
    share_changes.jsonl)。"""
    import gc  # noqa: PLC0415

    # 真市值索引:code → [(observed_on, total_shares)] 升序,信号日 bisect 取 as-of 股本(O(1)/信号)
    # 2026-08-09:不再本地构建,复用 factors/_shares.events_to_idx(同一数据源,见 docstring)。
    shares_idx: Optional[dict] = (
        _shares_events_to_idx(shares_events) if shares_events else None
    )
    items = bars.items() if isinstance(bars, dict) else bars
    out: list[dict] = []
    skipped_no_score = 0  # 打分器无数据 → 跳过的信号数(不得当 0 分参与排名)
    n = 0
    for code, raw in items:
        n += 1
        rec_out, skipped = _extract_stock(
            code,
            raw,
            start,
            end,
            entry_gate,
            scorer,
            min_bars,
            gate_window,
            horizons,
            feature_scorers,
            stats,
            extra_feature_fn,
            ret_start,
            ret_end,
            delisted_ret,
            trade_sim,
            stop_pct,
            bbi_consec,
            style_features,
            shares_idx,
        )
        skipped_no_score += skipped
        if rec_out is not None:
            out.append(rec_out)
        if progress and n % progress == 0:
            print(
                f"[pass1] {n} 股 | RSS={_rss_mb():.0f}MB", file=sys.stderr, flush=True
            )
            gc.collect()
    if stats is not None and skipped_no_score:
        stats["skipped_no_score"] = skipped_no_score
    if skipped_no_score:
        print(
            f"[extract_firings] 跳过 {skipped_no_score} 个无法打分的信号"
            f"(缺数据不得按 0 分参与排名)",
            file=sys.stderr,
        )
    return out


def _auc(pos: list, neg: list) -> Optional[float]:
    """Mann-Whitney AUC(0.5=无判别力,>0.5=分数越高越可能"跑出来")。并列取平均秩。"""
    if not pos or not neg:
        return None
    vals = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg], key=lambda x: x[0])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(vals):  # 并列取平均秩
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
    见 research/R15_meta_bias_and_limits.md;要求两半程 AUC 与全样本同号才敢称'弱可用')。"""
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


def long_regime_windows(
    regime: dict[str, str], min_days: int = 20, state: str = "做多"
) -> list[tuple[str, str, int]]:
    """从 0AMV regime 日历里枚举**连续同态区间**(默认做多段) → [(start, end, 交易日数)]。

    研究"每次多头区间涨得好的标的"必须**按区间分别做**:跨区间混池会把日期效应/风格轮动
    混进判别力(结论#13 的 Simpson 悖论同源),且"共同点"只有在**多数区间都成立**才算共同点
    (结论#8/#11 的教训:单窗成立的东西换窗就翻)。
    """
    dates = sorted(regime)
    idx = {d: i for i, d in enumerate(dates)}
    segs: list[tuple[str, str, int]] = []
    start = prev = None
    for d in dates:
        if str(regime[d]) == state:
            if start is None:
                start = d
            prev = d
        elif start is not None:
            segs.append((start, cast(str, prev), idx[cast(str, prev)] - idx[start] + 1))
            start = prev = None
    if start is not None:
        segs.append((start, cast(str, prev), idx[cast(str, prev)] - idx[start] + 1))
    return [s for s in segs if s[2] >= min_days]


def bear_to_long_pairs(
    regime: dict[str, str],
    min_bear_days: int = 10,
    min_long_days: int = 20,
    include_long_head_days: int = 0,
    signal_span: str = "adjacent",
) -> list[dict[str, Any]]:
    """把每个**空头段**与其紧随的**做多段**配对 → 研究"在空头就识别出未来赢家"。

    依据结论#11:赢家的系统可选起涨点 **73% 落在空头**(中位领先做多 12 个交易日),
    所以"能否在买点当时认出赢家"这个问题的**信号窗口必须是空头段**,而赢家口径要用
    **随后那段多头的收益**来定(涨得好是在多头段发生的)。两窗解耦后:
      signal_window = 空头段(可选再纳入做多段头部 include_long_head_days 天)
      label_window  = **时间上紧邻其后的**做多段(赢家=该段盈利前 top%)

    两个必须防的口径错(2026-07-30 首轮枚举实际踩到):
    1. **跨年配对**:后继做多段必须取 regime 时间轴上**紧邻的下一段做多**;若它短于
       min_long_days 则该空头段**直接丢弃**,绝不能跳过它去接更晚的长段——否则会出现
       "2015-04 的 17 日空头 → 2016-06 的做多段"这种隔一年、中间夹了好几轮 regime 的无效配对。
    2. **伪独立窗口**:多个空头段(被中性段隔开)会指向同一个做多段。跨窗一致性判定按窗计票,
       同一段行情被计多次会虚高一致性(§3 窗口敏感)。故**每个 label 窗只保留一对**:
       signal_span="adjacent"(默认)取紧邻该做多段的那个空头段;
       signal_span="since-prev-long" 则把信号窗前伸到**上一个做多段结束之后**(覆盖整段下跌+筑底的
       建仓期,含中间的中性段),更贴近"建仓期"语义但信号数更多、噪声也更多。
    ⚠️ include_long_head_days>0 时,落在做多段头部的信号其 label(整段做多收益)**包含信号之前
       已经发生的那几天涨幅** → 对动量类特征造成顺向污染。主结论应以 0 为准,带头部的当敏感性对照。
    """
    dates = sorted(regime)
    idx = {d: i for i, d in enumerate(dates)}
    bears = long_regime_windows(regime, min_days=min_bear_days, state="空头")
    longs_all = long_regime_windows(
        regime, min_days=1, state="做多"
    )  # 不过滤:用于找"紧邻的下一段"
    out: dict[str, dict[str, Any]] = {}  # key=label 窗 → 每窗只留一对
    for b_start, b_end, b_days in bears:
        nxt = next(((a, z, n) for a, z, n in longs_all if idx[a] > idx[b_end]), None)
        if nxt is None:
            continue  # 右删失:此后再无做多段
        l_start, l_end, l_days = nxt
        if l_days < min_long_days:
            continue  # 紧邻做多段太短 → 丢弃,不跨接
        sig_start = b_start
        if signal_span == "since-prev-long":
            prior = [z for a, z, _ in longs_all if idx[z] < idx[b_start]]
            if prior:
                sig_start = dates[min(idx[max(prior)] + 1, idx[b_start])]
        sig_end = b_end
        if include_long_head_days > 0:
            j = min(idx[l_start] + include_long_head_days - 1, idx[l_end])
            sig_end = dates[j]
        key = f"{l_start}~{l_end}"
        cand: dict[str, Any] = {
            "signal_start": sig_start,
            "signal_end": sig_end,
            "label_start": l_start,
            "label_end": l_end,
            "bear_days": b_days,
            "long_days": l_days,
            "signal_days": idx[sig_end] - idx[sig_start] + 1,
        }
        old = out.get(key)
        # 同一 label 窗多个候选 → 留**最贴近该做多段**的那个空头段(起点最晚),避免伪独立重复计票
        if old is None or idx[cand["signal_start"]] > idx[old["signal_start"]]:
            out[key] = cand
    return sorted(out.values(), key=lambda p: p["signal_start"])


def _median(vals: list) -> Optional[float]:
    v = [x for x in vals if x is not None]
    return round(statistics.median(v), 4) if v else None


def _build_rows(
    records: list, key: str, label_basis: str, exclude_zero_ret: bool
) -> tuple[list, int, int, dict]:
    """discriminate 的行构建 + 右删失块 → (rows, n_censored, n_zero_excluded, rets)。"""
    rows = []
    n_zero_excluded = 0
    if exclude_zero_ret:  # 僵尸样本(赢家窗收益恰好为0)不进分母/不出信号
        records, n_zero_excluded = drop_zero_ret(records)
    n_censored = 0  # 无标签被剔除(前向不足 h 根 / 无窗口收益)
    rets: dict[str, float] = {}
    for r in records:
        code = r["code"]
        if r.get("ret") is not None:
            rets[code] = float(r["ret"])
        for d in r.get("days") or []:
            ex = d[2] if len(d) >= 3 and isinstance(d[2], dict) else {}
            if label_basis == "winner":
                if code not in rets:  # 无窗口收益 → 无法判定赢家
                    n_censored += 1
                    continue
                y = rets[code]
            else:
                if key not in ex:
                    n_censored += 1
                    continue
                y = float(ex[key])
            rows.append(
                {
                    "date": d[0],
                    "code": code,
                    "y": float(y),
                    "base_score": float(d[1]),
                    "feats": {k[2:]: v for k, v in ex.items() if k.startswith("f_")},
                }
            )
    return rows, n_censored, n_zero_excluded, rets


def _assign_labels(
    rows: list,
    rets: dict,
    *,
    label_basis: str,
    winner_top_pct: float,
    min_winner_ret: Optional[float],
    winner_basis: str,
    win_thresh: Optional[float],
    win_top_q: float,
    use_mfe: bool,
    horizon: int,
    n_zero_excluded: int,
) -> tuple[float, dict, str]:
    """赢家标注块:**就地**给 rows 写 "win",返回 (thr, wmeta, label_txt)。"""
    wmeta: dict = {}
    if label_basis == "winner":
        pairs = sorted(rets.items(), key=lambda kv: kv[1], reverse=True)
        winners, wmeta = _pick_winners(
            pairs, winner_top_pct, min_winner_ret, winner_basis
        )
        for x in rows:
            x["win"] = x["code"] in winners
        thr = wmeta.get("winner_ret_cutoff") or 0.0
        # 赢家窗的**上涨股占比**必须显式报出:普涨窗(如 2015 春 98% 上涨)里"盈利前50%"退化成
        # "中位数以上"(结论#12),基准率≈50%,挑中的多是 beta 而非识别力;且各窗占比差异极大 ⇒
        # 同一个 top_pct 在不同窗含义不同,跨窗一致性判定的可比前提被破坏。
        up_ratio = (
            wmeta["n_profitable"] / wmeta["n_universe_all"]
            if wmeta.get("n_universe_all")
            else None
        )
        wmeta["up_ratio"] = None if up_ratio is None else round(up_ratio, 4)
        wmeta["degenerate_label"] = bool(up_ratio is not None and up_ratio >= 0.8)
        wmeta["n_zero_excluded"] = n_zero_excluded
        label_txt = (
            f"该股是否为本区间赢家({wmeta.get('winner_basis')}口径前{winner_top_pct:.0f}%"
            + (f"且≥{(min_winner_ret or 0) * 100:.0f}%" if min_winner_ret else "")
            + f",收益切点 {thr}); 赢家 {len(winners)}/{len(pairs)} 只"
            + (f"; 已剔除零收益僵尸 {n_zero_excluded} 只" if n_zero_excluded else "")
            + (f"; 该窗上涨股占比 {up_ratio:.0%}" if up_ratio is not None else "")
            + (
                " ⚠️普涨窗:'前%.0f%%'退化为中位数以上,增益多为beta" % winner_top_pct
                if wmeta["degenerate_label"]
                else ""
            )
        )
    else:
        ys = sorted((x["y"] for x in rows), reverse=True)
        thr = (
            win_thresh
            if win_thresh is not None
            else ys[max(0, int(len(ys) * win_top_q) - 1)]
        )
        for x in rows:
            x["win"] = x["y"] >= thr
        label_txt = (
            f"{'MFE' if use_mfe else '前向收益'}{horizon}日 >= {thr:+.2%}"
            f" ({'绝对阈值' if win_thresh is not None else '全体前%.0f%%分位' % (win_top_q * 100)})"
        )
    return thr, wmeta, label_txt


def _daily_topk(by_day: dict, vf, picks_per_day: int, constant: bool) -> dict:
    """每日按特征选 top-k 的精确率 vs 每日随机选同样只数的公平期望(两个方向都算)。"""
    hit_hi = hit_lo = tot = 0
    fair_num = 0.0  # 每日随机选k的期望命中(公平基线,与特征方向无关)
    for _d, lst in by_day.items():
        cand = [x for x in lst if vf(x) is not None]
        if not cand:
            continue
        k = min(picks_per_day, len(cand))
        wr = sum(1 for x in cand if x["win"]) / len(cand)
        fair_num += k * wr  # 随机选k的期望命中数(与特征无关)
        cand.sort(key=vf, reverse=True)
        tot += k
        hit_hi += sum(int(x["win"]) for x in cand[:k])  # 取特征最大的 k 只
        hit_lo += sum(int(x["win"]) for x in cand[-k:])  # 取特征最小的 k 只
    fair = round(fair_num / tot, 4) if tot else None  # ← 正确的对照(非全局基准率)
    prec = prec_lo = lift = lift_lo = None
    if tot and not constant:  # 恒定特征的"精确率/增益"纯为并列排序假象,不出数
        prec, prec_lo = round(hit_hi / tot, 4), round(hit_lo / tot, 4)
        if fair is not None:
            lift = round((prec - fair) * 100, 2)
            lift_lo = round((prec_lo - fair) * 100, 2)
    return {
        "fair": fair,
        "prec": prec,
        "prec_lo": prec_lo,
        "lift": lift,
        "lift_lo": lift_lo,
        "tot": tot,
    }


def _feature_halves_auc(constant: bool, auc, halves: tuple, vf) -> tuple:
    """前/后半程日内AUC + 同号一致性(样本内挑特征极易过拟合,须半程同号才计)。"""
    h1 = None if constant else _auc_within_day(halves[0], vf)
    h2 = None if constant else _auc_within_day(halves[1], vf)
    consistent = bool(
        auc is not None
        and h1 is not None
        and h2 is not None
        and (h1 - 0.5) * (auc - 0.5) > 0
        and (h2 - 0.5) * (auc - 0.5) > 0
    )
    return h1, h2, consistent


def _eval_feature(
    f: str, rows: list, by_day: dict, halves: tuple, picks_per_day: int
) -> dict:
    """单特征度量块(判别循环的主体):日内AUC + 每日top-k精确率 + 半程一致性。"""

    def vf(x):
        return x["base_score"] if f == "base_score" else x["feats"].get(f)

    pos = [v for v in (vf(x) for x in rows if x["win"]) if v is not None]
    neg = [v for v in (vf(x) for x in rows if not x["win"]) if v is not None]
    allv = [v for v in (vf(x) for x in rows) if v is not None]
    constant = len(set(allv)) <= 1  # 零方差(如 reversal_k 内的 reversal_quality 恒=4)
    auc = None if constant else _auc_within_day(by_day, vf)
    # 方向:AUC<0.5 表示"特征越小越会跑"= 反向预测子(取反即可用,如 reversal_quality_inv 的由来),
    # 故两个方向的 top-k 精确率都算,判定按 |AUC-0.5| 与**有效方向**的净增益,避免误杀反向信号。
    tk = _daily_topk(by_day, vf, picks_per_day, constant)
    direction = None if auc is None else ("high" if auc >= 0.5 else "low")
    edge = None if auc is None else round(abs(auc - 0.5), 4)
    lift_eff = tk["lift"] if direction != "low" else tk["lift_lo"]
    h1, h2, consistent = _feature_halves_auc(constant, auc, halves, vf)
    return {
        "feature": f,
        "auc_pooled": _auc(pos, neg),
        "auc": auc,
        "auc_edge": edge,
        "direction": direction,
        "constant": constant,
        "n_pos": len(pos),
        "n_neg": len(neg),
        "median_win": _median(pos),
        "median_lose": _median(neg),
        "median_diff": (
            None
            if (mp := _median(pos)) is None or (mn := _median(neg)) is None
            else round(mp - mn, 4)
        ),
        "precision_at_daily_top": tk["prec"],
        "precision_at_daily_bottom": tk["prec_lo"],
        "fair_random_precision": tk["fair"],
        "picks": tk["tot"],
        "lift_pp": tk["lift"],
        "lift_pp_inverted": tk["lift_lo"],
        "lift_pp_effective": lift_eff,
        "auc_first_half": h1,
        "auc_second_half": h2,
        "split_consistent": consistent,
    }


def _split_usable(out_feats: list) -> tuple[list, list]:
    """特征表 → (弱可用候选, 前后半程不同号的疑过拟合) 两个子集。"""
    usable = [
        r
        for r in out_feats
        if not r["constant"]
        and r["auc_edge"] is not None
        and r["auc_edge"] >= 0.03
        and (r["lift_pp_effective"] or 0) >= 2
        and r["split_consistent"]
    ]
    # 仅通过 |AUC|/增益 但前后半程不同号 → 单独列出,避免当成结论(项目 §3 偏差警示:样本内挑特征极易过拟合)
    unstable = [
        r
        for r in out_feats
        if not r["constant"]
        and r["auc_edge"] is not None
        and r["auc_edge"] >= 0.03
        and (r["lift_pp_effective"] or 0) >= 2
        and not r["split_consistent"]
    ]
    return usable, unstable


def _render_discrimination(
    n_rows: int,
    label_basis: str,
    label_txt: str,
    base: float,
    n_censored: int,
    picks_per_day: int,
    out_feats: list,
    usable: list,
    unstable: list,
) -> str:
    """判别力研究的文本渲染块(特征表 + verdict)。"""

    def _dtxt(r):
        return "取反(越小越会跑)" if r["direction"] == "low" else "同向(越大越会跑)"

    verdict = (
        "**无判别力**:无任一特征同时满足 |日内AUC-0.5|≥0.03、有效方向净增益≥+2pp、前后半程同号 "
        "⇒ 信号当时无法把会跑的票挑出来"
        if not usable
        else "弱可用候选(**仅样本内,未 OOS**): "
        + ", ".join(
            f"{r['feature']}[{_dtxt(r)}](日内AUC {r['auc']}, "
            f"{r['lift_pp_effective']:+.1f}pp, 半程 {r['auc_first_half']}/{r['auc_second_half']})"
            for r in usable
        )
    )
    if unstable:
        verdict += "; ⚠️前后半程不同号(疑过拟合,不作结论): " + ", ".join(
            f"{r['feature']}({r['auc_first_half']}/{r['auc_second_half']})"
            for r in unstable
        )
    lines = [
        f"信号 {n_rows} 个 | 标签口径[{label_basis}]:{label_txt}"
        f" (信号级基准率 {base:.1%})",
        f"  右删失(无标签被剔除) {n_censored} 个"
        + (
            " —— ⚠️占比高时样本偏早期信号,结论可能失真"
            if n_censored > n_rows * 0.2
            else ""
        ),
    ]
    if label_basis == "winner":
        # 分母口径警示:普涨判定(up_ratio)的分母并不真是"全宇宙",读普涨窗结论前必须知道这一点
        lines.append(
            "  ⚠️口径局限:上涨占比分母 n_universe_all 只含**有信号或被 delisted_ret 救回**的票,"
            "无信号退市股缺失 → 上涨占比偏高、普涨窗可能漏标(判定偏保守)。"
        )
    lines += [
        f"  全局基准率 {base:.1%}(仅参考);对照用**每日随机选top{picks_per_day}的公平期望**:",
        f"    {'特征':<20} {'日内AUC':>8} {'全局AUC':>8} {'方向':>6} {'精确率':>8} {'公平随机':>8}"
        f" {'净增益':>8} {'半程AUC':>13} {'赢家中位/非赢家':>18}",
    ]
    for r in out_feats:
        tag = "  ⚠️恒定(门槛内零方差,无判别信息)" if r["constant"] else ""
        if (
            not r["constant"]
            and r["auc_edge"] is not None
            and r["auc_edge"] >= 0.03
            and not r["split_consistent"]
        ):
            tag = "  ⚠️半程不同号"
        pr = (
            r["precision_at_daily_bottom"]
            if r["direction"] == "low"
            else r["precision_at_daily_top"]
        )
        lines.append(
            f"    {r['feature']:<20} {_fmt_num(r['auc']):>8} {_fmt_num(r['auc_pooled']):>8} "
            f"{('取反' if r['direction'] == 'low' else '同向' if r['direction'] else '-'):>6} "
            f"{_fmt_pct(pr):>8} {_fmt_pct(r['fair_random_precision']):>8} "
            f"{_fmt_pp(r['lift_pp_effective']):>8} "
            f"{_fmt_num(r['auc_first_half'])}/{_fmt_num(r['auc_second_half'])} "
            f"{_fmt_num(r['median_win'])}/{_fmt_num(r['median_lose'])}{tag}"
        )
    lines.append(f"  -> {verdict}")
    return "\n".join(lines)


def discriminate_at_signal(
    records: list,
    horizon: int = 20,
    win_thresh: Optional[float] = None,
    win_top_q: float = 0.2,
    use_mfe: bool = False,
    picks_per_day: int = 3,
    label_basis: str = "forward",
    winner_top_pct: float = 50.0,
    winner_basis: str = "profitable",
    min_winner_ret: Optional[float] = None,
    exclude_zero_ret: bool = False,
) -> dict:
    """**信号当时能否明确选出会跑的票?** 对每个信号(特征全部 as-of、无未来函数)。

    label_basis:
      - "forward"(默认):label = 信号后 fwd{h}/mfe{h} 是否跑出来(绝对阈值 win_thresh 或前 win_top_q 分位)
        —— 问"这个买点后面 h 天涨不涨";
      - "winner":label = **该股在本区间是否属于赢家**(窗口收益口径,默认 profitable 内前 winner_top_pct%)
        —— 问"这个买点属不属于本区间最终跑出来的那批票",即"反向分析赢家在买点当时长什么样"。
        用 winner 口径时 Pass1 不必带 --horizons(records 里的 ret 即窗口收益)。
    度量:每特征算 日内AUC(同日内比较,去日期效应) + 每日按该特征选 picks_per_day 只的精确率
    vs **每日随机选同样只数的公平期望**;并给出赢家/非赢家的特征中位数对比(共同点画像)。
    方向:AUC<0.5 = 反向预测子(取反即可用),判定用 |AUC-0.5| 与有效方向净增益。
    稳健性:另算前/后半程日内AUC,要求与全样本同号(split_consistent)才计入"弱可用",否则标为疑过拟合。
    """
    key = f"{'mfe' if use_mfe else 'fwd'}{horizon}"
    rows, n_censored, n_zero_excluded, rets = _build_rows(
        records, key, label_basis, exclude_zero_ret
    )
    if not rows:
        return {
            "n": 0,
            "n_censored": n_censored,
            "text": (
                "无窗口收益(Pass1 未记 ret)"
                if label_basis == "winner"
                else f"无前向数据(Pass1 需带 --horizons 含 {horizon})"
            ),
        }

    thr, wmeta, label_txt = _assign_labels(
        rows,
        rets,
        label_basis=label_basis,
        winner_top_pct=winner_top_pct,
        min_winner_ret=min_winner_ret,
        winner_basis=winner_basis,
        win_thresh=win_thresh,
        win_top_q=win_top_q,
        use_mfe=use_mfe,
        horizon=horizon,
        n_zero_excluded=n_zero_excluded,
    )
    base = sum(1 for x in rows if x["win"]) / len(rows)
    by_day: dict = {}
    for x in rows:
        by_day.setdefault(x["date"], []).append(x)

    halves = _split_days_in_half(by_day)  # 分段一致性(前/后半程各自重算 AUC)
    feat_names = sorted({k for x in rows for k in x["feats"]}) + ["base_score"]
    out_feats = [
        _eval_feature(f, rows, by_day, halves, picks_per_day) for f in feat_names
    ]
    out_feats.sort(
        key=lambda r: r["auc_edge"] if r["auc_edge"] is not None else -1, reverse=True
    )
    usable, unstable = _split_usable(out_feats)
    return {
        "n": len(rows),
        "n_censored": n_censored,
        "horizon": horizon,
        "use_mfe": use_mfe,
        "label_basis": label_basis,
        "winner_meta": wmeta,
        "threshold": round(thr, 4),
        "base_rate": round(base, 4),
        "features": out_feats,
        "usable": [r["feature"] for r in usable],
        "text": _render_discrimination(
            len(rows),
            label_basis,
            label_txt,
            base,
            n_censored,
            picks_per_day,
            out_feats,
            usable,
            unstable,
        ),
    }


def _degenerate_windows(results: dict) -> tuple[list, set]:
    """普涨窗识别:先从计票池剔除(文本中仍列环境摘要并点名)。"""
    degen = sorted(
        label
        for label, res in results.items()
        if (res.get("winner_meta") or {}).get("degenerate_label")
    )
    return degen, set(degen)


def _collect_votes(results: dict, degen_set: set) -> tuple[dict, dict]:
    """跨窗计票收集 → (per={特征:[窗度量]}, overfit={特征:[被剔窗]})。

    两道剔除:单窗被判疑过拟合(split_consistent=False)的特征在该窗不计票;
    普涨窗(degenerate_label)分子分母都不含。"""
    per: dict[str, list] = {}
    overfit: dict[str, list] = {}  # 特征 -> [被判疑过拟合而不计票的窗]
    for label, res in results.items():
        if label in degen_set:
            continue  # 普涨窗:分子分母都不含
        for r in res.get("features") or []:
            if r.get("constant") or r.get("auc") is None:
                continue
            if r.get("split_consistent") is False:  # 该窗已判疑过拟合 → 不参与跨窗计票
                overfit.setdefault(r["feature"], []).append(label)
                continue
            per.setdefault(r["feature"], []).append(
                {
                    "window": label,
                    "auc": r["auc"],
                    "direction": r["direction"],
                    "lift_pp_effective": r.get("lift_pp_effective"),
                    "median_diff": r.get("median_diff"),
                    "n_pos": r.get("n_pos"),
                }
            )
    return per, overfit


def _windows_meta(results: dict) -> list:
    """各窗环境摘要:上涨股占比差异大 ⇒ 同一 top_pct 在各窗含义不同,跨窗计票的可比前提被削弱。"""
    wins_meta = []
    for label, res in sorted(results.items()):
        wm = res.get("winner_meta") or {}
        wins_meta.append(
            {
                "window": label,
                "n_signals": res.get("n"),
                "base_rate": res.get("base_rate"),
                "up_ratio": wm.get("up_ratio"),
                "winner_ret_cutoff": wm.get("winner_ret_cutoff"),
                "degenerate_label": bool(wm.get("degenerate_label")),
            }
        )
    return wins_meta


def _aggregate_feature(
    f: str,
    lst: list,
    overfit: dict,
    n_eligible: int,
    min_hit_ratio: float,
    min_edge: float,
    min_lift_pp: float,
) -> dict[str, Any]:
    """单特征跨窗汇总:同号率/中位AUC/中位增益 → 是否"跨窗共同点"。"""
    aucs = [x["auc"] for x in lst]
    med_auc = _median(aucs)
    dirs = [1 if x["auc"] >= 0.5 else -1 for x in lst]
    major = 1 if sum(dirs) >= 0 else -1
    same = sum(1 for d in dirs if d == major)
    med_lift = _median([x["lift_pp_effective"] for x in lst])
    med_edge = _median([abs(x["auc"] - 0.5) for x in lst])
    hit_ratio = round(same / len(lst), 3) if lst else 0.0
    is_common = bool(
        len(lst) >= max(2, int(n_eligible * min_hit_ratio))
        and hit_ratio >= min_hit_ratio
        and (med_edge or 0) >= min_edge
        and (med_lift or 0) >= min_lift_pp
    )
    return {
        "feature": f,
        "n_windows": len(lst),
        "same_direction_windows": same,
        "hit_ratio": hit_ratio,
        "median_auc": med_auc,
        "median_edge": med_edge,
        "median_lift_pp": med_lift,
        "direction": "high" if major > 0 else "low",
        "median_of_median_diff": _median([x["median_diff"] for x in lst]),
        "overfit_excluded_windows": overfit.get(f, []),
        "cross_window_common": is_common,
        "per_window": sorted(lst, key=lambda x: x["window"]),
    }


def _agg_verdict(
    n_eligible: int, n_win: int, degen: list, out: list, common: list
) -> str:
    """三种"没有共同点"必须分开说:①一个窗都没参与计票 ②有窗但没有任何特征拿到有效计票
    ③真的算过了但没特征过线。①②是**未能检验**(不构成结论),只有③才是"判别不出来"。"""
    if n_eligible <= 0:
        return (
            f"**未能检验**:{n_win} 个窗全部被剔除(普涨窗 {len(degen)} 个)⇒ 无有效计票窗,"
            "本次不构成任何结论;需换赢家口径(如 --min-winner-ret 收紧)或补非普涨窗后重跑"
        )
    if not out:
        return (
            f"**未能检验**:{n_eligible} 个计票窗里没有任何特征拿到有效计票"
            "(全被判恒定/疑过拟合)⇒ 不构成'判别不出来'的结论"
        )
    if not common:
        return (
            "**无跨窗共同点**:没有任何 as-of 特征在多数多头区间里稳定把赢家分出来 "
            "⇒ 买点当时无法精确识别(与结论#8/#13 一致)"
        )
    return (
        "跨窗共同点候选(**仍为样本内,须 walk-forward 复现才可进 live**): "
        + ", ".join(
            f"{r['feature']}[{'取反' if r['direction'] == 'low' else '同向'}]"
            f"(中位AUC {r['median_auc']}, {r['median_lift_pp']:+.1f}pp, "
            f"{r['same_direction_windows']}/{r['n_windows']} 窗同号)"
            for r in common
        )
    )


def _render_aggregate(
    wins_meta: list,
    degen: list,
    n_win: int,
    n_eligible: int,
    overfit: dict,
    out: list,
    min_hit_ratio: float,
    min_edge: float,
    min_lift_pp: float,
    verdict: str,
) -> str:
    """跨窗汇总的文本渲染块(各窗环境表 + 特征表 + verdict)。"""
    lines = []
    if wins_meta and any(w["up_ratio"] is not None for w in wins_meta):
        lines.append(
            "各窗环境(上涨股占比差异大 ⇒ 同一 top% 在各窗含义不同,读表时须一并看):"
        )
        lines.append(
            f"    {'窗口':<44} {'信号数':>6} {'上涨占比':>8} {'赢家切点':>9} {'基准率':>7}"
        )
        for w in wins_meta:
            flag = "  ⚠️普涨窗(增益多为beta)" if w["degenerate_label"] else ""
            lines.append(
                f"    {w['window']:<44} {str(w['n_signals'] or '-'):>6} "
                f"{_fmt_pct(w['up_ratio']):>8} {_fmt_num(w['winner_ret_cutoff']):>9} "
                f"{_fmt_pct(w['base_rate']):>7}{flag}"
            )
        if degen:
            lines.append(
                f"  ⚠️ {len(degen)}/{n_win} 个窗为普涨窗(上涨占比≥80%),其"
                f"'前 top%'退化为中位数以上;共同点若主要靠这些窗撑起,应视为 beta 而非识别力。"
            )
            lines.append(
                f"     已排除普涨窗 beta(不计入 hit_ratio 分子分母): {', '.join(degen)}"
            )
        lines.append("")
    if overfit:
        lines.append(
            "⚠️ 疑过拟合特征(单窗前后半程不同号)不参与跨窗计票: "
            + ", ".join(f"{f}({', '.join(ws)})" for f, ws in sorted(overfit.items()))
        )
    lines += [
        f"跨 {n_win} 个多头区间汇总(计票窗 {n_eligible} 个,已排除普涨窗 {len(degen)} 个;共同点判定:"
        f"≥{min_hit_ratio:.0%} 窗同号 且 中位|AUC-0.5|≥{min_edge} "
        f"且 中位净增益≥{min_lift_pp}pp):",
        f"    {'特征':<20} {'窗数':>4} {'同号':>4} {'同号率':>6} {'中位AUC':>8} {'中位增益':>9} {'方向':>6} {'共同点':>6}",
    ]
    for r in out:
        lines.append(
            f"    {r['feature']:<20} {r['n_windows']:>4} {r['same_direction_windows']:>4} "
            f"{r['hit_ratio']:>6.0%} {_fmt_num(r['median_auc']):>8} "
            f"{_fmt_pp(r['median_lift_pp']):>9} "
            f"{('取反' if r['direction'] == 'low' else '同向'):>6} "
            f"{'✅' if r['cross_window_common'] else '—':>6}"
        )
    lines.append(f"  -> {verdict}")
    return "\n".join(lines)


def aggregate_discriminate(
    results: dict[str, dict],
    min_edge: float = 0.03,
    min_lift_pp: float = 2.0,
    min_hit_ratio: float = 0.75,
) -> dict:
    """跨**多个多头区间**汇总判别力 → 回答"赢家在买点当时的共同点是什么"。

    单窗成立不算共同点(结论#8:reversal_quality_inv 单窗大胜、换窗翻转)。判定要求:
      ①在 ≥min_hit_ratio 的窗里方向同号;②各窗 |日内AUC-0.5| 中位 ≥min_edge;
      ③有效方向净增益中位 ≥min_lift_pp。三者齐备才算"跨窗共同点"。
    计票口径(两道剔除,防把噪声/beta 当共同点):
      - 单窗被判**疑过拟合**(split_consistent=False,前后半程不同号)的特征在该窗不计票
        (单窗都不作结论,跨窗汇总自然也不能收),被剔除的 (特征,窗) 在文本中列名;
      - **普涨窗**(degenerate_label,上涨占比≥80%)不计入 hit_ratio 分子分母——其"前 top%"
        已退化为中位数以上,增益多为 beta;普涨窗在文本中列名说明已排除。
    results: {窗口标签: discriminate_at_signal 输出}
    """
    degen, degen_set = _degenerate_windows(results)
    per, overfit = _collect_votes(results, degen_set)
    n_win = len(results)
    n_eligible = n_win - len(degen)  # 计票分母:剔除普涨窗
    wins_meta = _windows_meta(results)
    out: list[dict[str, Any]] = [
        _aggregate_feature(
            f, lst, overfit, n_eligible, min_hit_ratio, min_edge, min_lift_pp
        )
        for f, lst in per.items()
    ]
    out.sort(
        key=lambda r: (
            (r["median_edge"] or 0) * (1 if r["cross_window_common"] else 0.5)
        ),
        reverse=True,
    )
    common = [r for r in out if r["cross_window_common"]]
    verdict = _agg_verdict(n_eligible, n_win, degen, out, common)
    return {
        "n_windows": n_win,
        "features": out,
        "common": [r["feature"] for r in common],
        "n_eligible_windows": n_eligible,
        "verdict_kind": (
            "not_tested"
            if (n_eligible <= 0 or not out)
            else ("no_common" if not common else "candidates")
        ),
        "windows": wins_meta,
        "degenerate_windows": degen,
        "overfit_excluded": {f: ws for f, ws in sorted(overfit.items())},
        "text": _render_aggregate(
            wins_meta,
            degen,
            n_win,
            n_eligible,
            overfit,
            out,
            min_hit_ratio,
            min_edge,
            min_lift_pp,
            verdict,
        ),
    }


def rank_from_firings(
    records: list[dict],
    top_pct: float = 50.0,
    surface_top_n: int = 20,
    min_winner_ret: Optional[float] = None,
    winner_basis: str = "universe",
) -> dict[str, Any]:
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
        for rec in r.get("days") or []:
            d, sc = rec[0], rec[1]  # 带 horizons/特征时 rec 为 3 元素,只取前两个
            day_fire.setdefault(d, []).append((r["code"], sc))
            if r["code"] in winners:
                win_fire.setdefault(r["code"], []).append(d)
                day_winners[d] = day_winners.get(d, 0) + 1
    rank_of: dict[tuple, tuple] = {}
    for d, lst in day_fire.items():
        pool = len(lst)
        for rk, (code, _) in enumerate(
            sorted(lst, key=lambda x: x[1], reverse=True), 1
        ):
            if code in winners:
                rank_of[(d, code)] = (rk, pool)
    day_fire.clear()
    return _summarize_capture(
        rets,
        winners,
        win_fire,
        rank_of,
        top_pct,
        surface_top_n,
        day_winners=day_winners,
        wmeta=wmeta,
    )


def _capture_scan_stock(
    code,
    raw,
    start: str,
    end: str,
    entry_gate,
    scorer,
    min_bars: int,
    gate_window: int,
    day_fire: dict,
) -> tuple[Optional[float], list[str]]:
    """capture_rank_study 的单股扫描块 → (窗口收益, 信号日列表);信号池就地追加进 day_fire。
    raw 用完即释放(del);打分不出来的信号不参与排名(直接跳过,不得当 0 分)。"""
    df = raw.sort_values("date").reset_index(drop=True)
    ds = [str(d)[:10] for d in df["date"]]
    closes = df["close"].astype(float).tolist()
    r = window_return(ds, closes, start, end)
    fired: list[str] = []
    if len(df) >= min_bars:
        for i in range(min_bars, len(df)):
            if not (start <= ds[i] <= end):
                continue
            lo = max(0, i + 1 - gate_window) if gate_window else 0  # 尾窗口:省时省内存
            sub = df.iloc[lo : i + 1]
            if not entry_gate(sub):
                continue
            sc, sc_ok = _scorer_value(scorer, sub, code)
            if not sc_ok:
                continue  # 打分不出来的信号不参与排名
            day_fire.setdefault(ds[i], []).append((code, float(sc)))
            fired.append(ds[i])
    del df, ds, closes
    return r, fired


def _capture_scan(
    items,
    start: str,
    end: str,
    entry_gate,
    scorer,
    min_bars: int,
    gate_window: int,
    progress: int,
) -> tuple[list, dict, dict]:
    """capture_rank_study 的流式扫描块 → (rets, day_fire, stock_days)。
    每股只加载一次,raw 用完即释放;progress>0 时每 progress 只股打印进度 + RSS 探针。"""
    import gc  # noqa: PLC0415

    rets: list[tuple] = []
    day_fire: dict[str, list] = {}  # date -> [(code, score)]  当日全域信号池(轻量)
    stock_days: dict[str, list] = {}  # code -> [dates] 该股信号日(用于赢家过滤)
    n_seen = 0
    for code, raw in items:  # 流式:raw 用完即释放
        n_seen += 1
        if raw is not None and len(raw):
            r, fired = _capture_scan_stock(
                code,
                raw,
                start,
                end,
                entry_gate,
                scorer,
                min_bars,
                gate_window,
                day_fire,
            )
            if r is not None:
                rets.append((code, r))
            if fired:
                stock_days[code] = fired
        if progress and n_seen % progress == 0:
            print(
                f"[capture] {n_seen} 股 | firings={sum(len(v) for v in day_fire.values())} "
                f"| RSS={_rss_mb():.0f}MB",
                file=sys.stderr,
                flush=True,
            )
            gc.collect()
    return rets, day_fire, stock_days


def _count_day_winners(win_fire: dict) -> dict[str, int]:
    """{date: 当日池中赢家信号数}(oracle 完美排序上限的口径)。"""
    day_winners: dict[str, int] = {}
    for days in win_fire.values():
        for d in days:
            day_winners[d] = day_winners.get(d, 0) + 1
    return day_winners


def _rank_of_winners(day_fire: dict, winners: set) -> dict[tuple, tuple]:
    """(date,code) -> (rank, pool);只需赢家的排名(省内存)。"""
    rank_of: dict[tuple, tuple] = {}
    for d, lst in day_fire.items():
        pool = len(lst)
        for rk, (code, _) in enumerate(
            sorted(lst, key=lambda x: x[1], reverse=True), 1
        ):
            if code in winners:  # 只需赢家的排名,省内存
                rank_of[(d, code)] = (rk, pool)
    return rank_of


def capture_rank_study(
    bars,
    start: str,
    end: str,
    entry_gate,
    scorer=None,
    top_pct: float = 50.0,
    surface_top_n: int = 20,
    min_bars: int = 40,
    gate_window: int = 0,
    progress: int = 0,
    min_winner_ret: Optional[float] = None,
    winner_basis: str = "universe",
) -> dict[str, Any]:
    """赢家捕捉率 + 排名质量。回答:多头区间收益前 top_pct% 赢家,
      ①被我们信号捕捉到的比例(recall);②捕捉当日在"同类信号池"里的排名/是否进 top_n(surfaced);
      ③量化"选出来但没发现"(捕捉到却排名埋没=captured 但 best_rank>N)。
    scorer(sub_df, code)->{'score':..} 用于当日池内排序(高=靠前);None 则按 0 分(等同随机),仍给随机基线对照。
    **内存**:bars 可为 dict{code:df} 或 **(code, df) 迭代器**(流式:逐股加载→抽取→释放,避免全量载入 OOM)。
    单趟扫描,每股只加载一次;累加器只存轻量元组并即时释放 K 线(del + 周期 gc)。
    gate_window>0:只把**最近 gate_window 根**传给 gate/scorer(而非整段前缀)——避免每根K线重算全历史
    (O(n²) 时间 + 大量临时对象,是 OOM/慢的主因)。KDJ 等递归指标需足够预热,建议 ≥120。
    progress>0:每处理 progress 只股打印进度 + RSS(MB) 探针,便于定位内存增长。"""
    items = bars.items() if isinstance(bars, dict) else bars
    rets, day_fire, stock_days = _capture_scan(
        items, start, end, entry_gate, scorer, min_bars, gate_window, progress
    )
    if not rets:
        return {"n_winners": 0, "text": "无数据"}
    rets.sort(key=lambda x: x[1], reverse=True)
    winners, wmeta = _pick_winners(rets, top_pct, min_winner_ret, winner_basis)
    win_fire = {c: stock_days[c] for c in winners if c in stock_days}  # 赢家的信号日
    stock_days.clear()
    day_winners = _count_day_winners(win_fire)
    rank_of = _rank_of_winners(day_fire, winners)
    day_fire.clear()

    return _summarize_capture(
        rets,
        winners,
        win_fire,
        rank_of,
        top_pct,
        surface_top_n,
        day_winners=day_winners,
        wmeta=wmeta,
    )


def _sector_index_returns(
    members: dict, index_dir, start: str, end: str
) -> dict[str, float]:
    """全成员板块(含零赢家板块)的同窗口指数收益;读盘失败/无数据的板块缺省。"""
    idx = Path(index_dir)
    sec_ret: dict[str, float] = {}
    for sec in members:  # 全成员板块都算收益(含零赢家板块,供相关性用全样本)
        p = idx / f"{sec}.csv"
        if p.is_file():
            try:
                df = pd.read_csv(p)
                r = window_return(
                    df["date"].tolist(), df["close"].astype(float).tolist(), start, end
                )
                if r is not None:
                    sec_ret[sec] = r
            except Exception:  # noqa: BLE001
                pass
    return sec_ret


def _winner_count_corr(n_by_sec: dict, sec_ret: dict) -> tuple[Optional[float], int]:
    """赢家数 vs 板块指数收益相关性 → (corr, n);零赢家板块计入(不左截断),n<3 → None。"""
    pairs = [
        (n_by_sec.get(s, 0), sec_ret[s]) for s in sec_ret
    ]  # 零赢家板块计入(不左截断)
    corr = None
    if len(pairs) >= 3:
        try:
            corr = round(
                statistics.correlation([a for a, _ in pairs], [b for _, b in pairs]), 3
            )
        except Exception:  # noqa: BLE001
            corr = None
    return corr, len(pairs)


def _sector_top_rows(agg: dict, members: dict, sec_ret: dict) -> list:
    """主流板块行:赢家数/赢家密度(纠大板块偏差)/板块名/同窗口指数收益。"""
    from custos.core.factors import sector_mainstream as sm  # noqa: PLC0415

    top_rows = []
    for r in agg["top_sectors"]:
        sec = r["sector"]
        top_rows.append(
            {
                **r,
                "n_winners": r["n"],
                "density": round(r["n"] / max(len(members.get(sec) or [1]), 1), 3),
                "name": sm.sector_name(sec),
                "sector_return": (round(sec_ret[sec], 4) if sec in sec_ret else None),
            }
        )
    return top_rows


def _render_concentration(
    winners: list,
    agg: dict,
    top_k: int,
    corr: Optional[float],
    n_pairs: int,
    top_rows: list,
    by_density: list,
) -> str:
    """sector_concentration 的文本渲染块。"""
    conc = (
        "集中"
        if (agg["top5_share"] or 0) >= 0.5
        else ("偏分散" if (agg["top5_share"] or 0) < 0.3 else "中等")
    )
    im, om = agg["in_mainstream"], agg["off_mainstream"]
    return (
        f"赢家 {len(winners)} 只(有板块归属 {agg['n_classified']}), 覆盖 {agg['distinct_sectors']} 个板块; "
        f"前5板块占归属次数 {(agg['top5_share'] or 0) * 100:.0f}%({conc};分母=归属次数,一股多板块重复计), "
        f"HHI {agg['hhi']};\n"
        f"  主流(top{top_k})内赢家: n={im.get('n')} 均收 {(im.get('expectancy') or 0) * 100:+.1f}% vs "
        f"分散: n={om.get('n')} 均收 {(om.get('expectancy') or 0) * 100:+.1f}% "
        f"(差 {((agg['mainstream_lift'] or 0) * 100):+.1f}pp);\n"
        f"  赢家数 vs 板块指数收益 相关 {corr} (n={n_pairs},含零赢家板块;⚠️含机械成分,仅描述)。\n"
        "  归属数 Top: "
        + "; ".join(
            f"{r['name']}({r['n']},{(r['expectancy'] or 0) * 100:+.0f}%)"
            for r in top_rows[:6]
        )
        + "\n"
        "  密度 Top(纠大板块偏差): "
        + "; ".join(
            f"{r['name']}({r['density'] * 100:.0f}%×{r['n']})" for r in by_density[:6]
        )
    )


def sector_concentration(
    winners: list[str],
    members: dict[str, list],
    index_dir,
    start: str,
    end: str,
    top_k: int = 12,
    winner_rets: Optional[dict] = None,
) -> dict[str, Any]:
    """赢家的板块族分布(聚集效应):集中于少数主流板块还是分散?

    聚合口径复用 sector_mainstream:每票带**整个板块族**(多重归属,地区/风格剔除),
    按赢家窗口收益聚合到板块(胜率/期望),并报:
    - **赢家密度**(归属数/板块成分数)——纠大板块偏差(大板块天然归属多,密度才是真聚集);
    - 主流(归属数 top_k) vs 分散 的赢家收益对照;
    - 板块指数同窗口收益 + 赢家数相关性(含零赢家板块;⚠️含机械成分,仅描述)。
    口径:一股多板块重复计(归属次数≠赢家数)。"""
    from custos.core.factors import sector_mainstream as sm  # noqa: PLC0415

    code2secs = sm.invert_members(members)
    rets_map = winner_rets or {}
    trades = [{"code": w, "ret": float(rets_map.get(w, 0.0))} for w in winners]
    agg = sm.aggregate(trades, code2secs, top_k=top_k)
    if not agg["rows"]:
        return {"n_winners": len(winners), "n_classified": 0, "text": "无板块成员映射"}
    sec_ret = _sector_index_returns(members, index_dir, start, end)
    n_by_sec = {r["sector"]: r["n"] for r in agg["rows"]}
    corr, n_pairs = _winner_count_corr(n_by_sec, sec_ret)
    top_rows = _sector_top_rows(agg, members, sec_ret)
    by_density = sorted(top_rows, key=lambda r: r["density"], reverse=True)
    out = {
        "n_winners": len(winners),
        "n_classified": agg["n_classified"],
        "distinct_sectors": agg["distinct_sectors"],
        "top5_winner_share": agg["top5_share"],
        "herfindahl": agg["hhi"],
        "corr_wincount_vs_sectorret": corr,
        "corr_n": n_pairs,
        "top_sectors": top_rows,
        "top_by_density": by_density[:top_k],
        "mainstream": {
            "sectors": agg["mainstream_sectors"],
            "in": agg["in_mainstream"],
            "off": agg["off_mainstream"],
            "lift": agg["mainstream_lift"],
        },
    }
    out["text"] = _render_concentration(
        winners, agg, top_k, corr, n_pairs, top_rows, by_density
    )
    return out


def _explain_inputs(
    agg: dict, feature: str, min_hit_ratio: float
) -> tuple[int, int, set, list]:
    """explain_aggregate 的前置口径 → (计票窗数, 覆盖门槛, 全部非普涨窗, 待解读特征列表)。"""
    n_elig = agg.get("n_eligible_windows") or agg.get("n_windows") or 0
    need = max(2, int(n_elig * min_hit_ratio))
    all_w = {
        w["window"] for w in (agg.get("windows") or []) if not w.get("degenerate_label")
    }
    feats = [
        f for f in (agg.get("features") or []) if not feature or f["feature"] == feature
    ]
    return n_elig, need, all_w, feats


def _explain_window_rows(per: dict, overfit: list, absent: list) -> list:
    """单特征的逐窗行:计票(证据)/疑过拟合剔除(反面证据)/未出现(既非支持也非反对)。"""
    lines = []
    for w in sorted(per):
        x = per[w]
        lines.append(
            f"    计票  {w:<44} AUC {_fmt_num(x.get('auc'))} "
            f"{_fmt_pp(x.get('lift_pp_effective'))} "
            f"{'取反' if x.get('direction') == 'low' else '同向'}"
        )
    for w in sorted(overfit):
        lines.append(f"    剔除  {w:<44} 疑过拟合(前后半程不同号)= **反面证据**")
    for w in absent:
        lines.append(f"    未测  {w:<44} 恒定/特征缺失(既非支持也非反对)")
    return lines


def _explain_feature_lines(f: dict, all_w: set, need: int) -> tuple[float, list]:
    """explain_aggregate 的单特征块 → (纯噪声下 100% 同号概率, 该特征的行列表)。"""
    per = {x["window"]: x for x in (f.get("per_window") or [])}
    overfit = list(f.get("overfit_excluded_windows") or [])
    absent = sorted(all_w - set(per) - set(overfit))
    n = len(per)
    p_noise = 0.5 ** (n - 1) if n >= 1 else 1.0
    lines = [
        "",
        (
            f"[{f['feature']}] 计票 {n} 窗 / 同号 {f.get('same_direction_windows')} "
            f"({(f.get('hit_ratio') or 0):.0%}) / 中位AUC {_fmt_num(f.get('median_auc'))} "
            f"/ 中位增益 {_fmt_pp(f.get('median_lift_pp'))} / "
            f"{'覆盖达标' if n >= need else f'⚠️覆盖不足({n}<{need})'} / "
            f"纯噪声下 100% 同号概率 {p_noise:.3f}"
        ),
    ]
    lines.extend(_explain_window_rows(per, overfit, absent))
    return p_noise, lines


def explain_aggregate(agg: dict, feature: str = "", min_hit_ratio: float = 0.75) -> str:
    """解读跨窗汇总:逐特征列出**各窗 AUC/增益**、被剔除的窗及原因、覆盖是否达标,
    并给出**纯噪声下 100% 同号的概率**(0.5^(n-1))——防止把噪声里必然冒出的"完美一致"当信号。

    三类窗必须分开看:计票窗(per_window)、疑过拟合被剔(overfit_excluded_windows)、
    未出现(恒定/特征缺失)。前者是证据,第二类是反面证据,第三类只是没测到。
    """
    n_elig, need, all_w, feats = _explain_inputs(agg, feature, min_hit_ratio)
    lines = [
        f"计票窗 {n_elig} 个(普涨窗已排除 {len(agg.get('degenerate_windows') or [])} 个);"
        f"覆盖门槛 = {need} 窗"
    ]
    exp_total = 0.0
    for f in sorted(feats, key=lambda r: -(r.get("median_edge") or 0)):
        p_noise, flines = _explain_feature_lines(f, all_w, need)
        exp_total += p_noise
        lines.extend(flines)
    if len(feats) > 1:
        lines.append("")
        lines.append(
            f"→ {len(feats)} 个特征在纯噪声下期望出现 {exp_total:.2f} 个 100% 同号;"
            "观测数不显著高于它就不能当信号(结论#13 多重比较警示)"
        )
    return "\n".join(lines)


BOARDS = (
    ("科创板", ("688", "689")),
    ("创业板", ("300", "301")),
    ("北交所", ("43", "83", "87", "88", "920")),
    ("沪主板", ("600", "601", "603", "605")),
    ("深主板", ("000", "001", "002", "003")),
)


def board_of(code6: str) -> str:
    """6 位代码 → 上市板(免数据、无未来函数)。
    注:真市值改由 local_tdx/fetch_market_cap.py 提供(qlib bundle 本身无总股本)。"""
    c = str(code6 or "").strip()
    if not c.isdigit():  # 空/非数字不得 zfill 成 "000000" 误判为深主板
        return "其他"
    c = c.zfill(6)
    for name, prefixes in BOARDS:
        if c.startswith(prefixes):
            return name
    return "其他"


def _pct(vals: list[float], q: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    return round(s[min(len(s) - 1, max(0, int(len(s) * q)))], 4)


def drop_zero_ret(records: list[dict]) -> tuple[list[dict], int]:
    """剔除**赢家窗收益恰好为 0** 的记录。⚠️ **本函数的前提已被实测证伪,不应再使用。**

    加它时的假设是:这类样本系赢家窗内长期停牌/退市整理期,前复权数据被 forward-fill 成
    一条直线,既非赢家也非飞刀。**2026-07-31 用 `--zero-ret-report` 抽样 177 只实测:
    100% 是 `traded_flat`(窗内正常成交、只是首末收盘恰好同价),全程停牌 0 只、断续停牌 0 只。**
    上市板分布也不集中在北交所(创业板 758 / 北交所 542 / 科创板 428 / 沪主板 398 / 深主板 279)。

    ⇒ 这些是**正常成交、真实区间收益恰好 0%** 的合法样本,本来就该算作"非上涨"。
    删它们会让 up_ratio 单向上升(实测某窗 78.2% → 约 96%),越过 80% 阈值把该窗打成**普涨窗**,
    而普涨窗会被 `aggregate_discriminate` **整窗剔除计票**(分子分母都不含)——
    等于凭空削掉投票窗、降低本就稀缺的统计功效。原始 up_ratio 才是正确口径。

    保留本函数与 `--exclude-zero-ret` 仅为复现历史稳健性检验;新研究一律不要带该开关。
    """
    keep = [r for r in records if r.get("ret") is None or float(r["ret"]) != 0.0]
    return keep, len(records) - len(keep)


def _dist_subset_stats(sub: list[dict], bands: tuple) -> dict:
    """distribution_report 的子集统计块:分位/上涨率/涨幅带。"""
    rets = [x["ret"] for x in sub]
    pos = [x for x in sub if x["ret"] > 0]
    zero = [x for x in sub if x["ret"] == 0]
    n = len(rets)
    return {
        "n": len(sub),
        "n_up": len(pos),
        "n_zero": len(zero),
        "up_ratio": round(len(pos) / n, 4) if n else None,
        "zero_ratio": round(len(zero) / n, 4) if n else None,
        "median": _median(rets),
        "p10": _pct(rets, 0.10),
        "p25": _pct(rets, 0.25),
        "p75": _pct(rets, 0.75),
        "p90": _pct(rets, 0.90),
        "p99": _pct(rets, 0.99),
        # n=0 时(如全无信号)不得除零 —— 空子集是合法输入
        "bands": {
            f">={b:.0%}": {
                "n": sum(1 for x in rets if x >= b),
                "share": (round(sum(1 for x in rets if x >= b) / n, 4) if n else None),
            }
            for b in bands
        },
    }


def _recall_by_band(all_stats: dict, sig_stats: dict, bands: tuple) -> tuple:
    """**按涨幅带的召回率**:各带里"我们曾触发过信号"的比例。这是比"信号池收益分布"更直接的问题——
    若召回率随涨幅单调下降,说明入场门槛对大牛股是**负选择**(压根没进池子),
    瓶颈在召回而非排序(与结论#2"买弱指纹排除做多区间突破赢家"同源)。"""
    base_recall = (sig_stats["n"] / all_stats["n"]) if all_stats["n"] else None
    recall_by_band = {}
    for b in bands:
        k = f">={b:.0%}"
        tot, sig = all_stats["bands"][k]["n"], sig_stats["bands"][k]["n"]
        recall_by_band[k] = {
            "n_universe": tot,
            "n_with_signal": sig,
            "recall": round(sig / tot, 4) if tot else None,
            "vs_base_pct": (
                round((sig / tot) / base_recall - 1, 4) if tot and base_recall else None
            ),
        }
    return base_recall, recall_by_band


def _dist_by_board(rows: list, bands: tuple) -> dict:
    """按上市板分组的统计块。"""
    by_board = {}
    for name, _ in BOARDS + (("其他", ()),):
        sub = [x for x in rows if x["board"] == name]
        if sub:
            s = _dist_subset_stats(sub, bands)
            s["share_of_universe"] = round(len(sub) / len(rows), 4)
            s["n_with_signal"] = sum(1 for x in sub if x["has_signal"])
            by_board[name] = s
    return by_board


def _render_distribution(out: dict, bands: tuple) -> str:
    """distribution_report 的文本渲染块。"""
    all_stats, sig_stats = out["all"], out["with_signal"]
    by_board = out["by_board"]
    base_recall = out["base_recall"]
    recall_by_band = out["recall_by_band"]
    n_zero_excluded = out["n_zero_excluded"]
    lines = [
        f"赢家窗收益分布(口径=区间买入持有,非本策略买卖规则):全域 {all_stats['n']} 只 / "
        f"有信号 {sig_stats['n']} 只"
        + (f" ｜ 已剔除零收益僵尸样本 {n_zero_excluded} 只" if n_zero_excluded else ""),
        f"    {'子集':<10} {'只数':>6} {'上涨率':>7} {'中位':>8} {'p75':>8} {'p90':>8} {'p99':>8}",
    ]
    for label, s in (("全域", all_stats), ("有信号", sig_stats)):
        lines.append(
            f"    {label:<10} {s['n']:>6} {(s['up_ratio'] or 0):>6.1%} "
            f"{_fmt_pct(s['median']):>8} {_fmt_pct(s['p75']):>8} "
            f"{_fmt_pct(s['p90']):>8} {_fmt_pct(s['p99']):>8}"
        )
    if (all_stats["zero_ratio"] or 0) >= 0.02:
        lines.append(
            f"  ⚠️ 收益**恰好为 0** 的样本 {all_stats['n_zero']} 只"
            f"({all_stats['zero_ratio']:.1%}):已实测确认(2026-07-31 --zero-ret-report"
            " 抽样 177 只)100% 是正常成交的'直线回位'合法样本(停牌 0 只)——"
            "**勿剔除**,它们本该算作'非上涨';单列观察即可"
            "(误剔会让 up_ratio 单向上升、把窗错打成普涨窗并整窗剔除计票)"
        )
    lines.append(
        f"  **按涨幅带的召回率**(该带里我们曾触发信号的比例;基准 {(base_recall or 0):.1%}):"
    )
    lines.append(
        f"    {'涨幅带':>8} {'全域只数':>9} {'有信号':>7} {'召回率':>7} {'相对基准':>9}"
    )
    for b in bands:
        k = f">={b:.0%}"
        rb = recall_by_band[k]
        lines.append(
            f"    {k:>8} {rb['n_universe']:>9} {rb['n_with_signal']:>7} "
            f"{(rb['recall'] or 0):>6.1%} {((rb['vs_base_pct'] or 0) * 100):>+8.1f}%"
        )
    hi = recall_by_band.get(">=50%", {}).get("vs_base_pct")
    if hi is not None and hi <= -0.10:
        lines.append(
            f"  → **大涨幅段召回不足**(≥50% 带相对基准 {hi * 100:+.0f}%):入场门槛对大牛股是"
            "**负选择**——它们压根没进候选池 ⇒ 瓶颈在**召回**而非排序"
            "(与结论#2『买弱指纹排除做多区间突破赢家』同源)"
        )
    lines.append("  涨幅带占比(全域 / 有信号):")
    for b in bands:
        k = f">={b:.0%}"
        a, g = all_stats["bands"][k], sig_stats["bands"][k]
        lines.append(
            f"    {k:>7}  {a['n']:>5} 只({_fmt_pct(a['share'])})  |  "
            f"有信号 {g['n']:>5} 只({_fmt_pct(g['share'])})"
        )
    lines.append("  按上市板:")
    lines.append(
        f"    {'板':<8} {'只数':>6} {'占宇宙':>7} {'有信号':>7} {'上涨率':>7} {'中位':>8} {'≥30%占比':>9}"
    )
    for name, s in sorted(by_board.items(), key=lambda kv: -kv[1]["n"]):
        lines.append(
            f"    {name:<8} {s['n']:>6} {s['share_of_universe']:>6.1%} "
            f"{s['n_with_signal']:>7} {(s['up_ratio'] or 0):>6.1%} "
            f"{_fmt_pct(s['median']):>8} {_fmt_pct(s['bands']['>=30%']['share']):>8}"
        )
    lines.append(
        "  注:板间差异要与'占宇宙比例'一起看——创业板/科创板波动天然更大,"
        "中位更高不等于'可选出来',判别力仍看 --discriminate。"
    )
    return "\n".join(lines)


def distribution_report(
    records: list[dict],
    bands=(0.0, 0.1, 0.2, 0.3, 0.5, 1.0),
    exclude_zero_ret: bool = False,
) -> dict:
    """赢家窗收益的**分布画像** + 按上市板分组(纯 Pass2,不重跑 Pass1)。

    回答"涨幅怎么分布、赢家集中在哪个板":
      - 全域/有信号子集各自的分位(p10..p99)与中位;
      - 各涨幅带(>0/≥10%/≥20%/≥30%/≥50%/≥100%)的只数与占比 —— 看"真牛股"有多稀;
      - 按上市板(主板/创业/科创/北证)分组的只数、上涨率、中位收益、≥30% 占比 ——
        看"赢家有无板块规律";同时给**有信号子集**的同口径,以便对比"我们能看到的池子"是否有偏。
    ⚠️ 收益口径是 window_return(赢家窗首根→末根收盘,买入持有),**不是**我们的买卖规则;
       规则口径需 Pass1 带 --trade-sim,再看 coverage_report。
    """
    rows = [
        {
            "code": r["code"],
            "ret": float(r["ret"]),
            "board": board_of(r["code"]),
            "has_signal": bool(r.get("days")),
        }
        for r in records
        if r.get("ret") is not None
    ]
    n_zero_excluded = 0
    if exclude_zero_ret:
        before = len(rows)
        rows = [x for x in rows if x["ret"] != 0.0]
        n_zero_excluded = before - len(rows)
    if not rows:
        return {"n": 0, "text": "无收益数据"}

    all_stats = _dist_subset_stats(rows, bands)
    sig_stats = _dist_subset_stats([x for x in rows if x["has_signal"]], bands)
    base_recall, recall_by_band = _recall_by_band(all_stats, sig_stats, bands)
    by_board = _dist_by_board(rows, bands)
    out = {
        "n": len(rows),
        "all": all_stats,
        "with_signal": sig_stats,
        "by_board": by_board,
        "base_recall": (round(base_recall, 4) if base_recall else None),
        "recall_by_band": recall_by_band,
        "n_zero_excluded": n_zero_excluded,
    }
    out["text"] = _render_distribution(out, bands)
    return out


def _coverage_stats(best: dict, winners: set, rets: dict) -> tuple:
    """coverage_report 的赢家侧统计块:有信号赢家/规则盈利赢家/退出原因/中位对照/捕获率。"""
    w_sig = [c for c in winners if c in best]
    w_pos = [c for c in w_sig if best[c]["ret"] > 0]
    reasons: dict[str, int] = {}
    for c in w_sig:
        k = best[c]["reason"] or "unknown"
        reasons[k] = reasons.get(k, 0) + 1
    med_sim = _median([best[c]["ret"] for c in w_sig])
    med_win = _median([rets[c] for c in w_sig])
    capture = round(med_sim / med_win, 3) if med_sim is not None and med_win else None
    return w_sig, w_pos, reasons, med_sim, med_win, capture


def _collect_sim_trades(records: list[dict]) -> dict[str, list[dict]]:
    """coverage_report 的累加块:从 firings 收集每股每笔规则模拟交易 → {code: [trade]}。"""
    per_code: dict[str, list[dict]] = {}
    for r in records:
        for d in r.get("days") or []:
            ex = d[2] if len(d) >= 3 and isinstance(d[2], dict) else {}
            if "sim_ret" in ex:
                per_code.setdefault(r["code"], []).append(
                    {
                        "date": d[0],
                        "ret": float(ex["sim_ret"]),
                        "reason": ex.get("sim_reason", ""),
                        "holding": ex.get("sim_holding"),
                    }
                )
    return per_code


def _render_coverage(
    out: dict,
    n_w_sig: int,
    n_w_pos: int,
    reasons: dict,
    med_sim: Optional[float],
    med_win: Optional[float],
    capture: Optional[float],
) -> str:
    """coverage_report 的文本渲染块。"""
    lines = [
        f"赢家 {out['n_winners']} 只(区间涨幅口径),其中有信号 {n_w_sig} 只;",
        f"  **覆盖度(规则下赚钱的赢家占比) {(out['coverage'] or 0):.0%}** "
        f"({n_w_pos}/{n_w_sig});每只取其最好一笔(最乐观口径)",
        f"  中位:规则收益 {_fmt_pct(med_sim)} vs 区间涨幅 {_fmt_pct(med_win)} → "
        f"**捕获率 {capture if capture is not None else '-'}**",
        "  赢家的退出原因分布: "
        + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())),
    ]
    stop_share = reasons.get("stop", 0) / n_w_sig if n_w_sig else 0
    if stop_share >= 0.5:
        lines.append(
            f"  → **{stop_share:.0%} 的赢家在规则下以止损离场**:选对了也拿不住 ⇒ "
            "瓶颈在交易管理(止损空间/退出规则),不在选股(与结论#3/#5 一致)"
        )
    elif (out["coverage"] or 0) >= 0.7:
        lines.append(
            "  → 规则能吃到大部分赢家 ⇒ 瓶颈不在交易管理,而在**事前选不出**(与判别力结论一致)"
        )
    return "\n".join(lines)


def coverage_report(
    records: list[dict],
    winner_top_pct: float = 50.0,
    winner_basis: str = "profitable",
    min_winner_ret: Optional[float] = None,
    exclude_zero_ret: bool = False,
) -> dict:
    """**双口径对比**:赢家(区间涨幅口径)在**我们的买卖规则**下实际赚到了多少。

    需 Pass1 带 --trade-sim(每个信号记 sim_ret/sim_reason/sim_holding,由 simulate_b1_trade 产出:
    信号日收盘进场、pct 止损、BBI 连破止盈)。回答的是结论#3/#5 那条老问题的量化版:
      - **覆盖度**=赢家中"我们规则下 sim_ret>0"的占比;取每只赢家的**最好一笔**(最乐观口径);
      - 捕获率=median(sim_ret)/median(区间涨幅) —— 规则吃到了区间涨幅的几成;
      - 退出原因分布 —— 赢家里若大量 reason=stop,说明"选对了但被止损扫出",
        问题在**交易管理**而非选股(与结论#5"买入K最低止损 83.7% 被扫"同源)。
    ⚠️ sim 收益受**加载窗口右端**截断:reason=open_end 表示到数据末仍持有,收益按末根收盘计。
    """
    rets = {r["code"]: float(r["ret"]) for r in records if r.get("ret") is not None}
    if exclude_zero_ret:
        records, _nz = drop_zero_ret(records)
        rets = {c: v for c, v in rets.items() if v != 0.0}
    if not rets:
        return {"n_winners": 0, "text": "无收益数据"}
    pairs = sorted(rets.items(), key=lambda kv: kv[1], reverse=True)
    winners, wmeta = _pick_winners(pairs, winner_top_pct, min_winner_ret, winner_basis)
    per_code = _collect_sim_trades(records)
    if not per_code:
        return {
            "n_winners": len(winners),
            "text": "无规则模拟收益(Pass1 需带 --trade-sim)",
        }
    best: dict[str, dict] = {
        c: max(v, key=lambda x: x["ret"]) for c, v in per_code.items()
    }
    w_sig, w_pos, reasons, med_sim, med_win, capture = _coverage_stats(
        best, winners, rets
    )
    out: dict[str, Any] = {
        "n_winners": len(winners),
        "n_winner_with_signal": len(w_sig),
        "n_winner_rule_profitable": len(w_pos),
        "coverage": round(len(w_pos) / len(w_sig), 3) if w_sig else None,
        "median_sim_ret": med_sim,
        "median_window_ret": med_win,
        "capture_ratio": capture,
        "exit_reasons": reasons,
        "winner_meta": wmeta,
        "all_signals_median_sim_ret": _median([b["ret"] for b in best.values()]),
    }
    out["text"] = _render_coverage(
        out, len(w_sig), len(w_pos), reasons, med_sim, med_win, capture
    )
    return out


def _buffered_start(sorted_dates: list[str], start: str, buffer_days: int) -> str:
    j = bisect.bisect_left(sorted_dates, start)
    return sorted_dates[max(0, j - buffer_days)] if sorted_dates else start


def _write_json_out(path: str, obj: Any, indent: int = 2) -> Path:
    """--out 落盘:父目录不存在时自动创建。

    此前深路径直接 FileNotFoundError——Pass1/Pass2 跑几十分钟后在最后一行落盘时炸掉,
    结果全丢(审计 O12)。"""
    import json as _jo  # noqa: PLC0415

    p = Path(path)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_jo.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")
    return p


def _empty_firings_guard(n_records: int, n_signal_days: int, allow_empty: bool) -> int:
    """Pass1 空产物护栏:一只票都没加载 / 一个信号都没抽到 → **非零退出且不落盘**。

    审计 E9:原实现照样把空 firings 原子落盘,`run_bear_to_long_study.firings_reusable`
    只看"JSON 完整 + 参数一致",于是这份"0 信号"的文件被当成**已完成**永久跳过——
    数据源没挂上的一整轮 12 窗研究会静默地全是空窗,结论写成"分不出赢家"。"""
    msg = None
    if n_records <= 0:
        msg = "未加载到任何 K 线(数据源/宇宙/日期区间有问题?)"
    elif n_signal_days <= 0:
        msg = f"加载了 {n_records} 只票却抽到 0 个信号日(门槛过严/依赖失败/窗口无数据?)"
    if msg is None:
        return 0
    if allow_empty:
        print(
            f"[WARN] Pass1 {msg};--allow-empty 已开,仍落盘(产物带 empty_ok 标记,"
            "续跑校验不会复用)",
            file=sys.stderr,
        )
        return 0
    print(
        f"[ERR] Pass1 {msg};不落盘 firings——空产物会被断点续跑当成'已完成'永久复用。"
        "确需空产物请显式加 --allow-empty",
        file=sys.stderr,
    )
    return 2


def _read_codes_file(path_str: str, err=None) -> list:
    """--codes-file 解析（2026-08-16 review 加固，对齐 backtest_factors 约定）：

    存在性检查（不再裸 traceback）、utf-8-sig（Windows 记事本默认 BOM，否则首个
    token 变 BOM 前缀代码静默漏票）、跳过 # 注释、只收 6 位数字（表头/
    600519.SH 之类脏内容剔除并出声）。err 传 argparse 的 ap.error（exit 2）。
    """
    import re as _re

    p = Path(path_str)
    if not p.is_file():
        if err:
            err(f"--codes-file 不存在: {p}")
        raise FileNotFoundError(p)
    codes: list = []
    rejected: list = []
    for tok in _re.split(r"[\s,]+", p.read_text(encoding="utf-8-sig")):
        tok = tok.strip()
        if not tok or tok.startswith("#"):
            continue
        (codes if tok.isdigit() and len(tok) == 6 else rejected).append(tok)
    if rejected:
        print(
            f"[WARN] --codes-file 剔除 {len(rejected)} 个非 6 位代码 token"
            f"（如 {rejected[:3]}）",
            file=sys.stderr,
        )
    return codes


def _build_parser() -> argparse.ArgumentParser:
    """CLI 参数定义。

    ⚠️ add_argument 调用必须留在**本文件**内:research/__main__._modes() 用 AST
    解析本文件收集 store_true 模式开关,挪出文件会让模式清单静默消失。
    """
    ap = argparse.ArgumentParser(description="起涨点 vs 0AMV regime 研究")
    ap.add_argument("--data-source", choices=["tdx", "qlib", "csv"], default="qlib")
    ap.add_argument(
        "--s-data-root",
        default=os.environ.get("S_DATA_ROOT") or r"E:\S_DATA",
        help=r"s_data 根目录;可用环境变量 S_DATA_ROOT 覆盖,默认 E:\S_DATA",
    )
    ap.add_argument("--universe-sdata", action="store_true")
    ap.add_argument("--codes", default="")
    ap.add_argument(
        "--codes-file",
        default="",
        help="从文件读宇宙（每行/逗号/空白分隔的 6 位代码）。全市场 tdx 宇宙"
        "（~5500 只）超 Windows 命令行长度上限，--codes 塞不下（2026-08-13，TODO #8"
        " R3 受影响窗 tdx 重跑引入：vipdoc 全宇宙 + 前复权，替代已弃用的加法 bundle）",
    )
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument(
        "--entry-filter",
        choices=["j_low", "reversal_k", "j_macd_turn"],
        default="reversal_k",
    )
    ap.add_argument("--top-pct", type=float, default=10.0)
    ap.add_argument("--buffer-days", type=int, default=60)
    ap.add_argument(
        "--sector-members",
        default=str(MARKET_DIR / "sector_members.json"),
        help="板块成员 JSON(算赢家板块集中度/共振;缺失则跳过)",
    )
    ap.add_argument("--sector-index-dir", default=str(MARKET_DIR / "sector_index"))
    ap.add_argument(
        "--capture-rank",
        action="store_true",
        help="额外跑赢家捕捉率+排名质量研究(recall/surfaced/埋没),量化'选出来但没发现'",
    )
    ap.add_argument(
        "--capture-only",
        action="store_true",
        help="只跑捕捉率+排名研究(流式分块加载,省内存);跳过起涨点分析与全量载入",
    )
    ap.add_argument(
        "--chunk-size",
        type=int,
        default=400,
        help="捕捉研究流式加载的每块股票数(内存/IO 权衡)",
    )
    ap.add_argument(
        "--gate-window",
        type=int,
        default=120,
        help="传给 gate/scorer 的尾窗口根数(0=整段前缀)。默认120:省时省内存,KDJ 预热足够",
    )
    ap.add_argument(
        "--progress",
        type=int,
        default=500,
        help="每 N 股打印进度+RSS(MB)探针,0=关闭(用于定位内存增长)",
    )
    ap.add_argument(
        "--emit-firings",
        default="",
        help="Pass1:只抽取信号→写 JSON(极小),可配 --shard 分多进程跑,彻底避开 loader 内存",
    )
    ap.add_argument(
        "--shard", default="", help="Pass1 分片 i/N(如 1/6):只处理第 i 片股票"
    )
    ap.add_argument(
        "--horizons",
        default="",
        help="Pass1:逗号分隔前向天数(如 20,60)→ 每信号记因果 fwd/mfe,供判别力研究",
    )
    ap.add_argument(
        "--feature-scores",
        default="",
        help="Pass1:逗号分隔特征打分器(SCORERS 名,如 reversal_quality,momentum,low_vol,alpha101)",
    )
    ap.add_argument(
        "--sector-features",
        action="store_true",
        help="Pass1:每个信号日记 as-of 板块特征(f_sector_favorable 相位有利 / f_sector_momentum 板块动量;"
        "需 sector_members.json + 板块指数CSV)",
    )
    ap.add_argument(
        "--discriminate",
        action="store_true",
        help="Pass2:跑'信号当时能否选出会跑的票'判别力研究(AUC + 每日选top-k精确率)",
    )
    ap.add_argument(
        "--label-basis",
        choices=["forward", "winner"],
        default="forward",
        help="判别研究标签口径:forward=信号后前向收益跑不跑;"
        "winner=**该股是否为本区间赢家**(用 --capture-top-pct/--winner-basis/--min-winner-ret)",
    )
    ap.add_argument(
        "--per-window",
        action="store_true",
        help="Pass2:把 --from-firings 的每个文件当作**一个多头区间**分别跑判别力,再跨窗汇总共同点",
    )
    ap.add_argument(
        "--list-long-windows",
        action="store_true",
        help="只列出 0AMV 做多区间(供按区间分别跑 Pass1),配 --min-window-days",
    )
    ap.add_argument(
        "--list-window-pairs",
        action="store_true",
        help="只列出**空头段→随后做多段**配对(信号窗/赢家窗):研究'在空头就识别未来赢家'用",
    )
    ap.add_argument(
        "--min-window-days",
        type=int,
        default=20,
        help="做多区间最短交易日数(短于此的碎片段跳过)",
    )
    ap.add_argument(
        "--min-bear-days", type=int, default=10, help="空头(信号)区间最短交易日数"
    )
    ap.add_argument(
        "--include-long-head-days",
        type=int,
        default=0,
        help="信号窗额外纳入做多段头部 N 个交易日(覆盖那~27%% 落在做多的起涨点);"
        "⚠️会让这些信号的 label 含信号前已发生涨幅,主结论请用 0",
    )
    ap.add_argument(
        "--signal-span",
        choices=["adjacent", "since-prev-long"],
        default="adjacent",
        help="信号窗口径:adjacent=紧邻赢家窗的那个空头段(默认);"
        "since-prev-long=上一段做多结束后至空头段末(整段建仓期,含中性段)",
    )
    ap.add_argument(
        "--ret-start",
        default="",
        help="Pass1:赢家口径窗口起点(与信号窗解耦;空头段采信号+做多段定赢家时用)",
    )
    ap.add_argument("--ret-end", default="", help="Pass1:赢家口径窗口终点")
    ap.add_argument(
        "--delisted-ret",
        type=float,
        default=None,
        help="Pass1:有信号但赢家窗无价格(空头内退市/长停)时按此收益计入非赢家(如 -1.0);"
        "缺省=丢弃该股(⚠️会重新引入幸存者偏差)",
    )
    ap.add_argument("--horizon", type=int, default=20, help="判别研究用的前向天数")
    ap.add_argument(
        "--win-thresh",
        type=float,
        default=None,
        help="判别研究:'跑出来'的绝对收益阈值(如 0.3=+30%%);缺省用分位数",
    )
    ap.add_argument(
        "--win-top-q",
        type=float,
        default=0.2,
        help="判别研究:分位口径(默认前20%%算跑出来)",
    )
    ap.add_argument(
        "--use-mfe",
        action="store_true",
        help="判别研究:用区间最大涨幅(MFE)而非期末收益",
    )
    ap.add_argument(
        "--picks-per-day", type=int, default=3, help="判别研究:每日选几只算精确率"
    )
    ap.add_argument(
        "--from-firings",
        default="",
        help="Pass2:读一个或多个(逗号分隔) Pass1 JSON,合并算捕捉率+排名(内存极小)",
    )
    ap.add_argument(
        "--capture-top-pct",
        type=float,
        default=50.0,
        help="捕捉研究的赢家口径(默认收益前50%%)",
    )
    ap.add_argument(
        "--surface-top-n", type=int, default=20, help="每日展示阈值(top-N 算 surfaced)"
    )
    ap.add_argument(
        "--winner-basis",
        choices=["universe", "profitable"],
        default="universe",
        help="赢家口径:universe=全域收益前top_pct%%(含下跌股);profitable=**盈利股内**前top_pct%%",
    )
    ap.add_argument(
        "--min-winner-ret",
        type=float,
        default=None,
        help="赢家另加绝对收益门槛(如 0.5=至少+50%%);默认仅按 top_pct 排名(不看正负)",
    )
    ap.add_argument(
        "--rank-score",
        choices=sorted(bt.SCORERS) + ["none"],
        default="reversal_quality",
        help="当日信号池内排序分(none=随机;全部 SCORERS 可选)",
    )
    ap.add_argument(
        "--trade-sim",
        action="store_true",
        help="Pass1:每个信号按**本策略买卖规则**(收盘进场/pct止损/BBI连破止盈)另算一笔实际收益"
        "(sim_ret),供 --coverage 对比'赢家我们吃到几成'",
    )
    ap.add_argument(
        "--stop-pct",
        type=float,
        default=8.0,
        help="--trade-sim 的固定止损百分比(默认8)",
    )
    ap.add_argument(
        "--bbi-consec", type=int, default=2, help="--trade-sim 的BBI连破日数(默认2)"
    )
    ap.add_argument(
        "--pit-features",
        action="store_true",
        help="Pass1:追加 PIT 基本面特征(A 组,纯财务比率不需市值,2015 起可用):"
        "f_roe/f_gross_margin/f_ocf_ps/f_deduct_ratio/f_rev_yoy/f_np_yoy/"
        "f_pit_lag_days。全部按信号日**可见**的财报计算,同比取同口径上年同期的"
        "**当时可见版本**(不是今天的最终版)",
    )
    ap.add_argument(
        "--pit-ledger",
        default=None,
        help="PIT 财务台账路径(默认 data/fundamentals/pit_financials.jsonl)",
    )
    ap.add_argument(
        "--pit-visible-same-day",
        action="store_true",
        help="把公告当日算作可见(默认次日;公告多在盘后发布)",
    )
    ap.add_argument(
        "--style-features",
        action="store_true",
        help="Pass1:追加风格特征 f_board_code(上市板)与 f_amount20(20日均成交额,市值代理)",
    )
    ap.add_argument(
        "--exclude-zero-ret",
        action="store_true",
        help="⚠️【前提已证伪,勿用】Pass2:剔除赢家窗收益恰好为 0 的记录。"
        "加它时以为是停牌 forward-fill 的僵尸样本,实测 100%% 是正常成交的"
        "'直线回位'合法样本;剔除会让 up_ratio 单向上升、把窗错打成普涨窗"
        "并整窗剔除计票。仅为复现历史稳健性检验保留",
    )
    ap.add_argument(
        "--distribution",
        action="store_true",
        help="Pass2:赢家窗收益分布画像 + 按上市板分组(纯读 firings)",
    )
    ap.add_argument(
        "--coverage",
        action="store_true",
        help="Pass2:双口径对比——赢家在本策略买卖规则下的覆盖度/捕获率/退出原因(需 --trade-sim 的 firings)",
    )
    ap.add_argument(
        "--explain-agg",
        default="",
        help="读 Pass2 输出的汇总 JSON,逐特征解读:各窗 AUC/增益、被剔窗及原因、"
        "覆盖是否达标、纯噪声下 100%% 同号概率(不跑任何计算)",
    )
    ap.add_argument(
        "--explain-feature", default="", help="只解读指定特征(配 --explain-agg)"
    )
    ap.add_argument(
        "--allow-empty",
        action="store_true",
        help="允许空结果(0 K线/0 信号/0AMV regime 全空)仍 exit 0 并落盘;"
        "默认拒绝——空产物会被当成'已完成'复用,并被误读为'无判别力'",
    )
    ap.add_argument("--out", default="")
    return ap


def _validate_universe_args(args, ap) -> None:
    """宇宙来源互斥校验(2026-08-16 review 修复:此前 --codes 与 --codes-file 同给时
    后者静默赢(--universe-sdata 同给更乱),静默选边违背「不静默」惯例)。"""
    _uni_given = [
        flag
        for flag, v in (
            ("--universe-sdata", args.universe_sdata),
            ("--codes-file", args.codes_file),
            ("--codes", args.codes),
        )
        if v
    ]
    if len(_uni_given) > 1:
        ap.error(f"宇宙来源互斥，只给一个：{'、'.join(_uni_given)}")


def _mode_explain_agg(args) -> int:
    import json as _je

    raw = _je.loads(Path(args.explain_agg).read_text(encoding="utf-8"))
    agg = raw.get("aggregate") or raw
    print(explain_aggregate(agg, feature=args.explain_feature))
    return 0


def _mode_list_windows(args) -> int:
    # ⚠️ since 不能是 None：--start 缺省为 ""，None 会一路传进
    # compass_amv.parse_amv_daily 的 `date >= since` 比较炸 TypeError，
    # 被 load_amv_regime 的 except 吞成 {} ⇒ regime 静默为空、窗口枚举恒无结果。
    # "1900-01-01" = 不过滤（同 compass_amv 里取全序列的惯例）。
    regime = bt.load_amv_regime(since=args.start or "1900-01-01")
    if args.list_window_pairs:
        pairs = bear_to_long_pairs(
            regime,
            min_bear_days=args.min_bear_days,
            min_long_days=args.min_window_days,
            include_long_head_days=args.include_long_head_days,
            signal_span=args.signal_span,
        )
        print(
            f"\n=== 空头(信号窗) → 紧邻做多段(赢家窗) 配对，共 {len(pairs)} 对（每个赢家窗只留一对）==="
        )
        print(
            "   建仓点多在空头(结论#11:73%),故信号在空头段采;'涨得好'发生在随后多头段,故赢家按多头段收益定。"
        )
        print(
            f"   信号窗口径 signal_span={args.signal_span}"
            + (
                "(空头段本身)"
                if args.signal_span == "adjacent"
                else "(上一段做多结束后至空头段末)"
            )
        )
        if args.include_long_head_days:
            print(
                f"   ⚠️ 已纳入做多段头部 {args.include_long_head_days} 日:这些信号的 label 含信号之前"
                "已发生的涨幅,对动量类特征顺向污染 → 主结论请用 0,本次仅作敏感性对照"
            )
        for p in pairs:
            print(
                f"  信号 {p['signal_start']} ~ {p['signal_end']} ({p['signal_days']}日"
                f"; 空头段 {p['bear_days']}日)  →  赢家窗 {p['label_start']} ~ {p['label_end']}"
                f" ({p['long_days']}日)"
            )
        if args.out:
            _write_json_out(args.out, {"window_pairs": pairs})
        return 0
    segs = long_regime_windows(regime, min_days=args.min_window_days)
    print(f"\n=== 0AMV 做多区间(≥{args.min_window_days} 交易日, 共 {len(segs)} 段) ===")
    for a, b, n in segs:
        print(f"  {a} ~ {b}  ({n} 交易日)")
    if args.out:
        _write_json_out(
            args.out,
            {"long_windows": [{"start": a, "end": b, "days": n} for a, b, n in segs]},
        )
    return 0


def _warn_zero_ret_and_require_dates(args, ap) -> None:
    """zero-ret 前提证伪警告 + start/end 必填校验。"""
    if args.exclude_zero_ret:
        print(
            "[WARN] --exclude-zero-ret 的前提已被实测证伪(2026-07-31,--zero-ret-report 抽样 177 只"
            "全部为 traded_flat=正常成交的直线回位,停牌 0 只)。这些是合法的 0% 收益样本,"
            "剔除会让 up_ratio 单向上升、把窗错打成普涨窗并整窗剔除计票。"
            "除复现历史稳健性检验外请勿使用。",
            file=sys.stderr,
        )

    if not args.from_firings and (not args.start or not args.end):
        ap.error("需提供 --start 和 --end(--from-firings 模式除外)")


def _load_firings(fp: str) -> list[dict]:
    import json as _j

    d = _j.loads(Path(fp).read_text(encoding="utf-8"))
    return d if isinstance(d, list) else (d.get("records") or [])


def _run_discriminate(args, recs: list[dict]) -> dict:
    return discriminate_at_signal(
        recs,
        horizon=args.horizon,
        win_thresh=args.win_thresh,
        win_top_q=args.win_top_q,
        use_mfe=args.use_mfe,
        picks_per_day=args.picks_per_day,
        label_basis=args.label_basis,
        winner_top_pct=args.capture_top_pct,
        winner_basis=args.winner_basis,
        min_winner_ret=args.min_winner_ret,
        exclude_zero_ret=args.exclude_zero_ret,
    )


def _firings_label(raw, fp: str) -> str:
    """per-window 模式的窗口标签:两窗解耦 > 单窗 > 文件名。"""
    if (
        isinstance(raw, dict)
        and raw.get("ret_start")
        and raw.get("ret_start") != raw.get("start")
    ):
        return (
            f"信号{raw['start']}~{raw['end']}→赢家{raw['ret_start']}~{raw['ret_end']}"
        )
    if isinstance(raw, dict) and raw.get("start"):
        return f"{raw.get('start')}~{raw.get('end')}"
    return Path(fp).stem


def _mode_per_window(args, files: list) -> int:
    """每文件=一个多头区间,分窗跑判别 + 跨窗汇总。"""
    import json as _j

    results: dict[str, dict] = {}
    n_all_recs = 0
    for fp in files:
        raw = _j.loads(Path(fp).read_text(encoding="utf-8"))
        label = _firings_label(raw, fp)
        wrecs = raw if isinstance(raw, list) else (raw.get("records") or [])
        n_all_recs += len(wrecs)
        wres = _run_discriminate(args, wrecs)
        results[label] = wres
        print(f"\n=== [窗口 {label}] 信号当时判别力({args.label_basis} 口径) ===")
        print(wres["text"])
    if not n_all_recs and not args.allow_empty:  # 全空 firings → 不得产出"无共同点"结论
        print(
            f"[ERR] Pass2 读到 {len(files)} 份 firings 但记录总数为 0"
            "(Pass1 全空/文件对不上?);拒绝输出——'分不出赢家'与'根本没数据'不是一回事。"
            "确需空结果请显式加 --allow-empty",
            file=sys.stderr,
        )
        return 2
    agg = aggregate_discriminate(results)
    print("\n=== 跨多头区间:赢家在买点当时的共同点 ===")
    print(agg["text"])
    if args.out:
        _write_json_out(args.out, {"per_window": results, "aggregate": agg})
    return 0


def _mode_dist_coverage(args, recs: list) -> int:
    res_out: dict = {}
    if args.distribution:
        dist = distribution_report(recs, exclude_zero_ret=args.exclude_zero_ret)
        print("\n=== 赢家窗收益分布 + 上市板分组(口径=区间买入持有) ===")
        print(dist["text"])
        res_out["distribution"] = dist
    if args.coverage:
        cov = coverage_report(
            recs,
            winner_top_pct=args.capture_top_pct,
            winner_basis=args.winner_basis,
            min_winner_ret=args.min_winner_ret,
            exclude_zero_ret=args.exclude_zero_ret,
        )
        print("\n=== 双口径对比:赢家在本策略买卖规则下的覆盖度 ===")
        print(cov["text"])
        res_out["coverage"] = cov
    if args.out:
        _write_json_out(args.out, res_out)
    return 0


def _mode_single_discriminate(args, recs: list) -> int:
    dis = _run_discriminate(args, recs)
    print(f"\n=== 信号当时判别力:能否明确选出会跑的票({args.label_basis} 口径) ===")
    print(dis["text"])
    if args.out:
        _write_json_out(args.out, {"discriminate": dis})
    return 0


def _mode_rank(args, recs: list) -> int:
    cap = rank_from_firings(
        recs,
        top_pct=args.capture_top_pct,
        surface_top_n=args.surface_top_n,
        min_winner_ret=args.min_winner_ret,
        winner_basis=args.winner_basis,
    )
    print(
        f"\n=== 赢家捕捉率 + 排名质量（合并 {len(recs)} 股记录, top{args.capture_top_pct:.0f}%赢家, "
        f"展示top{args.surface_top_n}）==="
    )
    print(cap["text"])
    if args.out:
        _write_json_out(args.out, {"capture_rank": cap})
    return 0


def _mode_from_firings(args) -> int:
    files = [x.strip() for x in args.from_firings.split(",") if x.strip()]
    if args.discriminate and args.per_window:  # 每文件=一个多头区间,分窗跑 + 跨窗汇总
        return _mode_per_window(args, files)
    recs: list[dict] = []
    for fp in files:
        recs.extend(_load_firings(fp))
    if not recs and not args.allow_empty:
        print(
            f"[ERR] Pass2 读到 {len(files)} 份 firings 但记录总数为 0(Pass1 全空/文件对不上?);"
            "拒绝输出——空结果会被误读成'无判别力'。确需空结果请显式加 --allow-empty",
            file=sys.stderr,
        )
        return 2
    if args.distribution or args.coverage:
        return _mode_dist_coverage(args, recs)
    if args.discriminate:
        return _mode_single_discriminate(args, recs)
    return _mode_rank(args, recs)


def _resolve_codes(args, ap) -> list:
    if args.universe_sdata:
        from custos.datasource import s_data  # noqa: PLC0415

        sub = "CSV_DATA" if args.data_source == "csv" else "Q_DATA"
        codes = s_data.list_universe(
            str(Path(args.s_data_root) / sub), source=args.data_source
        )
    elif args.codes_file:
        codes = _read_codes_file(args.codes_file, err=ap.error)
    else:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if not codes:
        ap.error("需 --universe-sdata 或 --codes 或 --codes-file")
    return codes


def _load_start(args) -> str:
    """数据/regime 起点必须比 --start 再早 buffer 段(起涨点要在 [start-buffer, end] 内回溯),
    否则 buffer 被加载窗口截为 0(此前真实运行从未真正回溯,结论有偏)。捕捉研究也靠它提供 min_bars 回溯。"""
    load_start = args.start
    # 裕量必须同时盖住 buffer_days 与 gate_window:gate_window(默认120)是 gate/scorer 的尾窗长度,
    # 只按 buffer_days(默认60)留裕量会让信号窗开头 ~gate_window-buffer 根 K 线预热不足,
    # 且截断程度随信号在窗内位置变化(同一特征在不同位置口径不一)。
    margin = max(args.buffer_days or 0, args.gate_window or 0)
    if margin:
        load_start = (
            _date.fromisoformat(args.start) - _td(days=int(margin * 1.6) + 10)
        ).isoformat()  # 交易日→日历日留裕量
    return load_start


def _make_chunk_iter(codes: list, args, load_start: str, loader):
    """返回流式 (code, df) 生成器函数(捕获 codes/args/load_start/loader 四个外层变量)。"""

    def _chunked_items(chunk: int):
        """流式产出 (code, df):逐块加载→逐股产出→释放该块,capture 研究据此避免全量载入 OOM。
        每股裁到必需列 + 窗口范围并 copy()(切断对 loader 大对象的引用,否则 clear() 也释放不掉)。"""
        import gc  # noqa: PLC0415

        need = ["date", "open", "high", "low", "close", "volume"]
        if loader is not None:
            for c, df in loader(codes, 0).items():
                yield c, df
            return
        from custos.datasource import s_data  # noqa: PLC0415

        sub2 = "CSV_DATA" if args.data_source == "csv" else "Q_DATA"
        fn2 = (
            s_data.load_bars_csv if args.data_source == "csv" else s_data.load_bars_qlib
        )
        root2 = str(Path(args.s_data_root) / sub2)
        if (
            args.data_source == "tdx"
        ):  # 本地通达信 vipdoc(近端数据;qlib 止于 2026-02-06)
            from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

            def fn2(codes, count, start=None, end=None, root=None):  # noqa: F811
                out: dict = {}
                for c in codes:
                    try:
                        df = local_tdx_data.get_ohlcv_table(c, count=2000)
                        if df is None or not len(df):
                            continue
                        df = df.copy()
                        df["date"] = df["date"].astype(str).str[:10]
                        if start:
                            df = df[df["date"] >= start]
                        if end:
                            df = df[df["date"] <= end]
                        if count:
                            df = df.tail(count)
                        if len(df):
                            out[c] = df.reset_index(drop=True)
                    except Exception:  # noqa: BLE001
                        continue
                return out

        # 加载终点要比 --end 多带 max(horizons) 根,否则区间尾部的信号拿不到前向标签被静默右删失
        load_end = args.end
        hz_max = max((int(x) for x in args.horizons.split(",") if x.strip()), default=0)
        if hz_max:
            load_end = (
                _date.fromisoformat(args.end) + _td(days=int(hz_max * 1.6) + 5)
            ).isoformat()
        if (
            args.ret_end and args.ret_end > load_end
        ):  # 赢家窗晚于信号窗 → 必须load到赢家窗末
            load_end = args.ret_end
        for k in range(0, len(codes), chunk):
            d = fn2(codes[k : k + chunk], 0, start=load_start, end=load_end, root=root2)
            for c in list(d):
                df = d.pop(c)  # 取出即从 dict 移除
                try:
                    cols = [x for x in need if x in df.columns]
                    yield c, df[cols].copy()  # copy:切断父 block 引用,确保可回收
                finally:
                    del df
            d.clear()
            gc.collect()  # 每块显式回收,内存只留一块 + 轻量累加器

    return _chunked_items


def _apply_shard(args, ap, codes: list) -> list:
    """--shard i/N 分片:非法分片必须 ap.error(exit 2),不能崩栈。"""
    try:
        parts = [int(x) for x in args.shard.split("/")]
    except ValueError:
        parts = []
    if len(parts) != 2 or parts[1] < 1 or not (1 <= parts[0] <= parts[1]):
        ap.error(f"--shard 格式须为 i/n 且 1<=i<=n(如 1/3..3/3),收到 {args.shard!r}")
    i, n = parts
    codes = [c for k, c in enumerate(codes) if k % n == (i - 1) % n]
    if not codes:
        ap.error(
            f"--shard {args.shard} 分到 0 只代码(宇宙不足 {n} 只?);"
            "空分片会写出空 firings 并被续跑校验当成已完成"
        )
    print(f"[pass1] 分片 {args.shard}: {len(codes)} 只", file=sys.stderr)
    return codes


def _sector_xfn(args, ap):
    """--sector-features:板块 as-of 特征 fn(构建失败/无数据 → ap.error 硬失败,审计 E8)。"""
    import json as _jm

    mpath = Path(args.sector_members)
    members = _jm.loads(mpath.read_text(encoding="utf-8")) if mpath.is_file() else {}
    if not members:
        ap.error(
            "--sector-features 需 sector_members.json(先跑 fetch_sector_index_history.py --members)"
        )
    xf = build_sector_features(args.sector_index_dir, members)
    _st = getattr(xf, "stats", {})
    if _st.get("build_error"):
        ap.error(
            f"--sector-features 构建失败({_st['build_error']});板块特征会全程缺省,"
            "结论会被误读为'板块相位无判别力'"
        )
    if not _st.get("sectors_loaded"):
        ap.error(
            f"--sector-features 无任何板块指数数据(dir={args.sector_index_dir};"
            f"需 {_st.get('sectors_requested', 0)} 个板块,缺 CSV "
            f"{_st.get('csv_missing', 0)},解析失败 {_st.get('csv_error', 0)});"
            "先跑 fetch_sector_index_history.py,否则 f_sector_* 全程缺省,"
            "'板块无判别力'的结论不成立"
        )
    print(
        f"[pass1] 板块特征已启用: {_st['sectors_loaded']}/{_st['sectors_requested']} "
        f"板块有数据(缺 CSV {_st['csv_missing']}, 解析失败 {_st['csv_error']}; "
        f"dir={args.sector_index_dir})",
        file=sys.stderr,
    )
    return xf


def _pit_xfn(args, ap) -> tuple:
    """--pit-features:PIT 基本面特征 fn → (fn, 台账条数);无台账 → ap.error。"""
    from custos.datasource.local_tdx import fetch_pit_financials as _pit  # noqa: PLC0415

    pit_recs = (
        _pit.load_ledger(args.pit_ledger) if args.pit_ledger else _pit.load_ledger()
    )
    if not pit_recs:
        ap.error(
            "--pit-features 需 PIT 财务台账(先跑 local_tdx/fetch_pit_financials.py "
            "--since 2015,并用 --verify 确认无缺期)"
        )
    fn = _pit.build_pit_feature_fn(
        pit_recs, visible_next_day=not args.pit_visible_same_day
    )
    print(
        f"[pass1] PIT 基本面特征已启用 ({len(pit_recs)} 条台账;"
        f"{'公告当日即可见' if args.pit_visible_same_day else '公告次日起可见'})",
        file=sys.stderr,
    )
    return fn, len(pit_recs)


def _load_shares_events(args) -> Optional[list]:
    """--style-features:真市值股本事件(fetch_market_cap 台账,已 F10 全史回填);
    台账空/加载失败 → f_mcap 特征缺省并告警(不硬失败)。"""
    try:
        from custos.datasource.local_tdx.fetch_market_cap import (
            load_events,
            LEDGER,
        )  # noqa: PLC0415

        shares_ev = load_events(LEDGER) or None
        if not shares_ev:
            print(
                "[WARN] 市值台账为空/缺失,f_mcap 特征缺省(先跑 fetch_market_cap.py)",
                file=sys.stderr,
            )
        return shares_ev
    except Exception as _exc:  # noqa: BLE001
        print(f"[WARN] 市值台账加载失败,f_mcap 特征缺省: {_exc}", file=sys.stderr)
        return None


def _build_pass1_features(args, ap) -> tuple:
    """Pass1 特征栈装配(独立纯块):rank scorer / horizons / 特征打分器 /
    板块 as-of / PIT 基本面 / 风格(市值事件)。返回 (scorer, hz, fsc, fstats, xfn,
    shares_ev, pit_ledger_n, xf_sector)——xf_sector 供事后统计汇报(stats 挂在 fn 上)。"""
    scorer = None if args.rank_score == "none" else bt.SCORERS.get(args.rank_score)
    hz = (
        tuple(int(x) for x in args.horizons.split(",") if x.strip())
        if args.horizons
        else ()
    )
    fsc = {}
    for nm in [x.strip() for x in args.feature_scores.split(",") if x.strip()]:
        if nm in bt.SCORERS:
            fsc[nm] = bt.SCORERS[nm]
        else:
            print(
                f"[WARN] 未知特征打分器 {nm}(可选: {','.join(sorted(bt.SCORERS))})",
                file=sys.stderr,
            )
    fstats: dict = {}
    xfns = []
    pit_ledger_n = 0
    xf_sector = None
    if args.sector_features:
        xf_sector = _sector_xfn(args, ap)
        xfns.append(xf_sector)
    if args.pit_features:
        _pf, pit_ledger_n = _pit_xfn(args, ap)
        xfns.append(_pf)
    if not xfns:
        xfn = None
    elif len(xfns) == 1:
        xfn = xfns[0]
    else:

        def xfn(code, day, _fns=tuple(xfns)):  # 多组 as-of 特征合并
            merged: dict = {}
            for f in _fns:
                try:
                    merged.update(f(code, day) or {})
                except Exception:  # noqa: BLE001 单组失败不拖垮其余
                    continue
            return merged

    shares_ev = _load_shares_events(args) if args.style_features else None
    return scorer, hz, fsc, fstats, xfn, shares_ev, pit_ledger_n, xf_sector


def _report_pass1_stats(args, fstats: dict, xf_sector) -> None:
    """Pass1 事后统计汇报:特征打分器异常 + 板块特征产出可见性(审计 E8)。"""
    if fstats.get("feature_failures"):
        print(
            f"[WARN] 特征打分器异常(特征可能缺失): {fstats['feature_failures']}",
            file=sys.stderr,
        )
    if args.sector_features:  # 板块特征"生成了几个"必须可见(审计 E8)
        _st = getattr(xf_sector, "stats", {})
        print(
            f"[pass1] 板块特征产出: {_st.get('emitted', 0)}/{_st.get('queries', 0)} 个信号带值"
            f"(未分类 {_st.get('unclassified', 0)}, 信号日早于板块数据 "
            f"{_st.get('no_asof_close', 0)})",
            file=sys.stderr,
        )
        if not _st.get("emitted"):
            print(
                "[WARN] 一个信号都没拿到板块特征:f_sector_* 全缺省,"
                "**不得**据本轮结果判定板块相位无判别力(先补板块指数/成员映射)",
                file=sys.stderr,
            )


def _write_firings(
    args, recs: list, n_signal_days: int, n_delisted: int, pit_ledger_n: int
) -> None:
    """firings 原子落盘。⚠️ JSON 头字段(start/end/ret_start/ret_end/entry_filter/
    delisted_ret/feature_scores/各开关/rank_score/shard/records)是
    run_bear_to_long_study 的 firings_reusable 契约——逐字不动。"""
    import json as _j

    firings_path = Path(args.emit_firings)
    if firings_path.parent and not firings_path.parent.exists():
        firings_path.parent.mkdir(
            parents=True, exist_ok=True
        )  # 深路径不再 FileNotFoundError
    tmp_firings = firings_path.with_name(firings_path.name + ".tmp")
    tmp_firings.write_text(
        _j.dumps(
            {
                "start": args.start,
                "end": args.end,
                "ret_start": args.ret_start or args.start,
                "ret_end": args.ret_end or args.end,
                "entry_filter": args.entry_filter,
                "delisted_ret": args.delisted_ret,
                "n_delisted": n_delisted,
                # 空产物必须自带标记:续跑校验据此拒绝复用(审计 E9)
                "n_signal_days": n_signal_days,
                **({"empty_ok": True} if not n_signal_days else {}),
                "feature_scores": args.feature_scores,
                "universe": (
                    "sdata"
                    if args.universe_sdata
                    else ("codes_file" if args.codes_file else "codes")
                ),
                # 特征开关必须落盘:否则驱动脚本的续跑校验看不出"这份 firings 是否带
                # 基本面/风格/板块特征",会把旧参数的结果当新参数复用,结论静默失真。
                "sector_features": bool(args.sector_features),
                "style_features": bool(args.style_features),
                "trade_sim": bool(args.trade_sim),
                "pit_features": bool(args.pit_features),
                "pit_visible_same_day": bool(args.pit_visible_same_day),
                # 复用指纹字段:台账路径(换台账必须重跑)与 trade-sim 出场参数
                "pit_ledger": args.pit_ledger or "",
                "stop_pct": args.stop_pct,
                "bbi_consec": args.bbi_consec,
                # 仅供追溯,**不进复用指纹**:台账每季都会增长,若进指纹会导致每次补数后
                # 全部窗口强制重跑;需要按新台账重算时显式 --force。
                "pit_ledger_n": pit_ledger_n,
                "rank_score": args.rank_score,
                "shard": args.shard,
                "records": recs,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tmp_firings.replace(
        firings_path
    )  # 原子落盘:中断不留半截 JSON(断点续跑会把半截文件当已完成)
    print(
        f"[pass1] 写出 {len(recs)} 股记录(信号日合计 {n_signal_days} 个;"
        f"其中赢家窗无价格按大亏计入 {n_delisted} 只) "
        f"→ {args.emit_firings} (RSS={_rss_mb():.0f}MB)",
        file=sys.stderr,
    )


def _mode_emit_firings(args, ap, codes: list, load_start: str, loader) -> int:
    """Pass1 模式:抽信号→小 JSON(可分片,多进程各自内存全新)。"""
    if args.shard:
        codes = _apply_shard(args, ap, codes)
    scorer, hz, fsc, fstats, xfn, shares_ev, pit_ledger_n, xf_sector = (
        _build_pass1_features(args, ap)
    )
    recs = extract_firings(
        _make_chunk_iter(codes, args, load_start, loader)(args.chunk_size),
        args.start,
        args.end,
        bt.ENTRY_GATES[args.entry_filter],
        scorer=scorer,
        gate_window=args.gate_window,
        progress=args.progress,
        horizons=hz,
        feature_scorers=fsc,
        stats=fstats,
        extra_feature_fn=xfn,
        ret_start=args.ret_start or None,
        ret_end=args.ret_end or None,
        delisted_ret=args.delisted_ret,
        trade_sim=args.trade_sim,
        stop_pct=args.stop_pct,
        bbi_consec=args.bbi_consec,
        style_features=args.style_features,
        shares_events=shares_ev,
    )
    _report_pass1_stats(args, fstats, xf_sector)
    n_signal_days = sum(len(r.get("days") or []) for r in recs)
    rc_empty = _empty_firings_guard(len(recs), n_signal_days, args.allow_empty)
    if rc_empty:
        return rc_empty
    n_delisted = sum(1 for r in recs if r.get("delisted"))
    if args.ret_end and args.delisted_ret is None:
        print(
            "[WARN] 两窗解耦但未设 --delisted-ret:赢家窗无价格的票(空头内退市/长停)被丢弃,"
            "会重新引入幸存者偏差(§3 首条)。建议 --delisted-ret -1.0",
            file=sys.stderr,
        )
    _write_firings(args, recs, n_signal_days, n_delisted, pit_ledger_n)
    return 0


def _load_bars(args, codes: list, load_start: str, loader) -> dict:
    if loader is not None:
        return loader(codes, 0)
    from custos.datasource import s_data  # noqa: PLC0415

    sub = "CSV_DATA" if args.data_source == "csv" else "Q_DATA"
    fn = s_data.load_bars_csv if args.data_source == "csv" else s_data.load_bars_qlib
    return fn(
        codes,
        0,
        start=load_start,
        end=None,
        root=str(Path(args.s_data_root) / sub),
    )


def _run_analyze(args, codes: list, load_start: str, loader) -> int:
    """默认路径:起涨点分析 + 捕捉率 + 板块集中度。"""
    res: dict[str, Any] = {}
    bars = None
    if not args.capture_only:  # 起涨点分析:需全量在内存(与既有行为一致)
        bars = _load_bars(args, codes, load_start, loader)
        regime = bt.load_amv_regime(
            since=load_start
        )  # regime 起点跟随数据起点(早前窗口)
        if not args.allow_empty:  # 审计 E9:全空输入不得产出"结论"
            if not bars:
                print(
                    "[ERR] 未加载到任何 K 线(数据源/宇宙/日期区间有问题?);"
                    "拒绝输出起涨点分析——空结果会被误读成'起涨点无规律'。"
                    "确需空结果请显式加 --allow-empty",
                    file=sys.stderr,
                )
                return 2
            if not regime:
                print(
                    "[ERR] 0AMV regime 为空(指南针数据不可用):起涨点的 regime 归属会全是"
                    "'未知'、lead-days 分布整段消失,本分析的**唯一自变量**就没了。"
                    "先补 compass_amv;确需空 regime 请显式加 --allow-empty",
                    file=sys.stderr,
                )
                return 2
        res = analyze(
            bars,
            regime,
            args.start,
            args.end,
            bt.ENTRY_GATES[args.entry_filter],
            top_pct=args.top_pct,
            buffer_days=args.buffer_days,
        )
        print(
            f"\n=== 起涨点 vs 0AMV（{args.start}~{args.end}, {args.entry_filter}, top{args.top_pct}%）==="
        )
        print(res["text"])

    # 赢家捕捉率 + 排名质量(recall/surfaced/'选出来但没发现')。流式:capture-only 用分块加载,省内存
    if args.capture_rank or args.capture_only:
        scorer = None if args.rank_score == "none" else bt.SCORERS.get(args.rank_score)
        src = (
            _make_chunk_iter(codes, args, load_start, loader)(args.chunk_size)
            if args.capture_only
            else bars
        )
        cap = capture_rank_study(
            src,
            args.start,
            args.end,
            bt.ENTRY_GATES[args.entry_filter],
            scorer=scorer,
            top_pct=args.capture_top_pct,
            surface_top_n=args.surface_top_n,
            gate_window=args.gate_window,
            progress=args.progress,
            min_winner_ret=args.min_winner_ret,
            winner_basis=args.winner_basis,
        )
        res["capture_rank"] = cap
        print(
            f"\n=== 赢家捕捉率 + 排名质量（top{args.capture_top_pct:.0f}%赢家, 展示top{args.surface_top_n}, "
            f"排序={args.rank_score}）==="
        )
        print(cap["text"])
    # 赢家板块集中度 / 板块共振(仅起涨点分析产出 winners 时)
    import json as _json

    mpath = Path(args.sector_members)
    if mpath.is_file() and res.get("winners"):
        members = _json.loads(mpath.read_text(encoding="utf-8"))
        conc = sector_concentration(
            res["winners"],
            members,
            args.sector_index_dir,
            args.start,
            args.end,
            winner_rets=res.get("winner_rets"),
        )
        res["sector_concentration"] = conc
        print("\n=== 赢家板块集中度 / 板块共振 ===")
        print(conc["text"])
    if args.out:
        _write_json_out(args.out, res)
    return 0


def main(argv=None, loader=None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    _validate_universe_args(args, ap)

    if args.explain_agg:  # 纯读 JSON,不加载数据、不算指标
        return _mode_explain_agg(args)

    if args.list_long_windows or args.list_window_pairs:  # 只枚举区间,不加载任何 K 线
        return _mode_list_windows(args)

    # ⚠️ 顺序是行为契约:zero-ret 警告与 start/end 必填**不能**提前到 explain/list 模式之前
    _warn_zero_ret_and_require_dates(args, ap)

    # Pass2:仅合并 Pass1 产物算排名(不加载任何K线,内存极小)
    if args.from_firings:
        return _mode_from_firings(args)

    codes = _resolve_codes(args, ap)
    load_start = _load_start(args)

    # Pass1:只抽信号→小 JSON(可分片,多进程各自内存全新)
    if args.emit_firings:
        return _mode_emit_firings(args, ap, codes, load_start, loader)
    return _run_analyze(args, codes, load_start, loader)


if __name__ == "__main__":
    raise SystemExit(main())
