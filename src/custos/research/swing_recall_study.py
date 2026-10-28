# -*- coding: utf-8 -*-
"""R50 ≥20% 波段的逆向匹配：B1 / QN 信号的召回、时点与拦截漏斗（v0.456，TODO #119；
预注册 governance/research/R50_swing_winner_recall.md）。

**探索性单元**（不判 candidate / falsified，只产「线索」供后续预注册）：把好行情定义为
**单个涨幅 ≥ +20% 的价格波段**（ZigZag 双阈值，前复权收盘——加载器 ``get_ohlcv_table``
默认 qfq），逐个波段逆向问 B1 主链与 QN 12 门：起涨窗口内有没有信号、提前多少天、
卡在漏斗哪一层、信号后按现行出场实际拿到多少。

口径全部写死在 R50（本文件不复述理由，只落实现）：
- 波段：自 T0 低点 +20% 成立、−10% 回撤确认 T1；剔除 T0 前 <250 根 / T0 在段末隔离带
  （``load_window.entry_deadline``）/ 停牌跨越 >20 日；只用 T0 落在挖掘 A∪B 段内的；
  段尾未确认 T1 按数据结束截断。
- 匹配：起涨窗口 [T0−10, T0+5]（宽窗 [T0−20, T0+10] 只描述）；B1 漏斗 L1 日 J<13 →
  L2 叠 0AMV 做多 → L3 V0 技术分过 1800 观察线 → L4 当日 top-20（``bt.select_daily_topn``
  同排序键）；QN 12 门 = ``bt.ENTRY_GATES["qn_*"]``（R31 同口径，底层即因子 ``detect``
  规范入口，as-of 前缀判定）。对照集 = 同段同宇宙按 年×市值五分位 分层随机 bar，
  样本量 = 该对象信号数。
- 读数：召回 / 精度 / lift / 提前量分位 / 涨幅分带召回 / 漏斗 / 兑现（LIVE_PARAMS 重放：
  已实现收益、捕获率 ret/amp、出场原因分布）/ 年·月分组。
- 线索筛选（v0.458 修订）：16 对象（B1 L1~L4 + QN 12 门）× 对**局部低点**基线的精度 lift_low；A 合格 n≥1000 ∧ lift_low>1.2；
  B 同门槛复现；统计量 min(lift_low_A, lift_low_B)；零假设 = **保簇形状的随机局部低点入场**
  （每对象按自身簇形状，逐簇抽同层随机局部低点锚点、整簇取锚点命中；基线 = 局部低点
  精确命中率，不抽样）N=200 每臂取 16 对象 max；复现且 > ``kit.q95`` ⇒
  ``lead_robust``。原 lift（对分层随机 bar 基线）只作描述性副读数。

as-of 纪律（R50 风险注记 1）：波段标注用未来价格**只允许在标签侧**——信号采集
（warm_v0_signals / QN 门）全部是前缀 as-of 判定，钉测在案（tests/test_swing_recall_study.py）。
本单元只读挖掘段：窗口写死挖掘 A∪B（``load_window.mining_violation`` 校验），
不读判定段 ⇒ 不登记 window_usage、不过批次门。
"""

from __future__ import annotations

import argparse
import bisect
import json
import random
import sys
from pathlib import Path
from typing import Any, Callable, Optional, cast

import numpy as np

from custos.core.paths import LOGS, RESEARCH_DIR
from custos.research import load_window as lw

# ---------------------------------------------------------------------------
# 写死口径（R50 预注册；改动 = 预注册修订）
# ---------------------------------------------------------------------------

MIN_RISE = 0.20  # 自 T0 低点 +20% 波段成立
MAX_DD = 0.10  # 自波段内最高收盘 −10% 回撤确认 T1
MIN_PRIOR_BARS = 250  # T0 前不足 250 根剔除（新股，指标无预热）
MAX_GAP_DAYS = 20  # 停牌跨越 >20 自然日的波段剔除
WIN_LO, WIN_HI = -10, 5  # 起涨窗口 [T0−10, T0+5]（交易日）
WIDE_LO, WIDE_HI = -20, 10  # 宽窗（只描述，不进线索筛选）
AMP_BANDS = ("20_30", "30_50", "50_100", "100p")  # 涨幅分带 20~30/30~50/50~100/≥100%
MIN_N = 1000  # A 合格信号数门槛（写死；--min-n 仅测试注入通道）
LIFT_MIN = 1.2
N_RANDOM = 200
#: 跑数前修订（v0.458，owner 2026-10-27）：局部低点对照——收盘为近 LOCAL_LOW_N 根（含当日）最低的 bar。
#: 波段起点 T0 按定义是局部低点，超卖类信号天然贴近低点 ⇒ 对「任意 bar」的 lift 近乎白送；
#: 线索筛选 / 零假设统计量改用对局部低点基线的 ``lift_low``，原 ``lift`` 只作描述。
LOCAL_LOW_N = 10
C4_MIN_POOL = 50
#: B1 漏斗 L3 的「1800 观察线」= live 技术分层强档阈（钉测对齐
#: ``score_candidates.TECH_STRONG_FALLBACK``，勿单独改）
B1_SCORE_LINE = 60.0

#: QN 12 门（R31 同口径 = backtest_factors.ENTRY_GATES 的 qn_* 转译层，
#: 底层调 ``core/factors/qn_*.detect`` 规范入口）
QN_GATES = (
    "qn_adx_extreme",
    "qn_box_target",
    "qn_bullish_engulf",
    "qn_kdj_neg_day",
    "qn_ma144_launch",
    "qn_ma25_state",
    "qn_ma_converge",
    "qn_macd_bar_shift",
    "qn_shrink_limit_up",
    "qn_three_red",
    "qn_volume_surge_cut",
    "qn_weekly180_setup",
)
B1_OBJECTS = ("b1_l1", "b1_l2", "b1_l3", "b1_l4")
#: 16 个匹配对象（B1 主链 4 层 + QN 12 门）
OBJECTS = (*B1_OBJECTS, *QN_GATES)


# ---------------------------------------------------------------------------
# 波段识别（纯函数；前复权收盘由加载层保证）
# ---------------------------------------------------------------------------


def zigzag(
    closes: list[float], *, min_rise: float = MIN_RISE, max_dd: float = MAX_DD
) -> list[dict[str, Any]]:
    """ZigZag 双阈值波段机（R50 写死）：

    - 找低：自上一个确认高点之后（首波段=数据起点）的最低收盘 = 起涨低点 T0 候选；
      收盘较 T0 涨 ≥ ``min_rise`` ⇒ 波段成立（进入持有态，峰值=当日）；
    - 持有：跟踪波段内最高收盘（首个达到者为峰）；收盘较峰值回撤 ≥ ``max_dd``
      ⇒ 确认 T1=峰值日，之后重新找低点（新低点候选 = T1 之后至当日的最低收盘）；
    - 数据结束时仍在持有 ⇒ T1 按最后 bar 截断（``truncated=True``）。
    """
    n = len(closes)
    out: list[dict[str, Any]] = []
    if n == 0:
        return out
    lo = 0  # 找低态：当前最低收盘的 bar
    t0 = -1
    peak = -1
    riding = False
    for i in range(1, n):
        c = closes[i]
        if not riding:
            if c < closes[lo]:
                lo = i
            elif c >= closes[lo] * (1.0 + min_rise):
                t0, peak, riding = lo, i, True
        else:
            if c > closes[peak]:
                peak = i
            elif c <= closes[peak] * (1.0 - max_dd):
                out.append({"t0": t0, "t1": peak, "truncated": False})
                riding = False
                # 下一个波段从 T1 之后重新找低点（含当前确认 bar）
                lo = peak + 1
                for j in range(peak + 2, i + 1):
                    if closes[j] < closes[lo]:
                        lo = j
    if riding:
        out.append({"t0": t0, "t1": n - 1, "truncated": True})
    return out


def local_low_mask(closes: list[float], n: int = LOCAL_LOW_N) -> list[bool]:
    """as-of 局部低点：closes[i] ≤ min(closes[max(0,i-n+1) .. i])（只看过去，纯函数）。"""
    out: list[bool] = []
    for i, c in enumerate(closes):
        lo = min(closes[max(0, i - n + 1) : i + 1])
        out.append(bool(c <= lo))
    return out


def _amp_band(amp: float) -> str:
    if amp < 0.30:
        return "20_30"
    if amp < 0.50:
        return "30_50"
    if amp < 1.00:
        return "50_100"
    return "100p"


def identify_swings(
    dates: list[Any],
    closes: list[float],
    *,
    seg_start: str,
    seg_end: str,
    min_prior_bars: int = MIN_PRIOR_BARS,
    max_gap_days: int = MAX_GAP_DAYS,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """ZigZag + R50 剔除规则（纯函数）。只保留 T0 落在 [seg_start, seg_end] 内的波段。

    剔除（计数进返回值第二项）：T0 出段 / T0 在段末隔离带（``entry_deadline``）/
    T0 前不足 ``min_prior_bars`` 根 / （T0, T1] 内相邻 bar 日历跨越 > ``max_gap_days``
    （停牌跨越）。``dates`` 接受 datetime/字符串（一律截到 YYYY-MM-DD）。

    ``truncated``（段尾未确认）波段的 ``amp`` 用**数据末根**而非回撤确认峰值计算
    （zigzag 截断时 T1=末根），幅度系统性低估——如实注记，口径不改。
    """
    import datetime as _dt  # noqa: PLC0415

    ds = [str(d)[:10] for d in dates]
    deadline = lw.entry_deadline(seg_end)
    dropped = {
        "out_of_segment": 0,
        "tail_buffer": 0,
        "short_history": 0,
        "suspension": 0,
    }
    kept: list[dict[str, Any]] = []
    cls = [float(c) for c in closes]
    for sw in zigzag(cls):
        t0, t1 = int(sw["t0"]), int(sw["t1"])
        d0 = ds[t0]
        if not (seg_start <= d0 <= seg_end):
            dropped["out_of_segment"] += 1
            continue
        if d0 > deadline:
            dropped["tail_buffer"] += 1
            continue
        if t0 < min_prior_bars:
            dropped["short_history"] += 1
            continue
        gap_bad = False
        for j in range(t0 + 1, t1 + 1):
            gap = (
                _dt.date.fromisoformat(ds[j]) - _dt.date.fromisoformat(ds[j - 1])
            ).days
            if gap > max_gap_days:
                gap_bad = True
                break
        if gap_bad:
            dropped["suspension"] += 1
            continue
        amp = cls[t1] / cls[t0] - 1.0
        kept.append(
            {
                "t0": t0,
                "t1": t1,
                "t0_date": d0,
                "t1_date": ds[t1],
                "amp": amp,
                "n_bars": t1 - t0,
                "year": int(d0[:4]),
                "month": int(d0[5:7]),
                "band": _amp_band(amp),
                "truncated": bool(sw["truncated"]),
            }
        )
    return kept, dropped


# ---------------------------------------------------------------------------
# 信号采集（生产默认件；全部 as-of 前缀判定）
# ---------------------------------------------------------------------------


def collect_b1_layers(
    per_code: dict[str, dict], regime: dict[str, str], top_n: int, *, tie_seed: int = 0
) -> dict[str, list[dict[str, Any]]]:
    """B1 漏斗各层信号集（R50 匹配口径写死，逐层累计）：

    L1 = warm 信号全集（J<13，不叠 0AMV——生产路径 ``warm_v0_signals(regime={})``
    的空 regime 恒真口径）；
    L2 = L1 中 0AMV 做多日的信号（``bt._amv_checker`` 同语义：as-of 最近 ≤ 当日的 regime）；
    L3 = L2 中 V0 技术分 ≥ ``B1_SCORE_LINE``（1800 观察线）；
    L4 = L3 中进当日 top-N（``bt.select_daily_topn`` 同排序键）。
    """
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    amv_ok = bt._amv_checker(regime)
    l1: list[dict[str, Any]] = []
    for code, pack in per_code.items():
        scores = pack.get("scores") or {}
        for s in pack.get("signals") or []:
            d = str(s.get("date"))[:10]
            l1.append(
                {"code": code, "i": int(s["i"]), "date": d, "score": scores.get(d)}
            )
    l2 = [r for r in l1 if amv_ok(r["date"])]
    l3 = [
        r
        for r in l2
        if isinstance(r["score"], (int, float))
        and r["score"] == r["score"]
        and float(r["score"]) >= B1_SCORE_LINE
    ]
    cand = [
        {
            "code": r["code"],
            "i": r["i"],
            "entry_date": r["date"],
            # 占位准入字段（select_daily_topn 的准入=有日期+_has_risk）——本层只用其
            # 排序键做横截面 top-N，不读出场语义
            "exit_date": r["date"],
            "risk_frac": 0.05,
            "score": r["score"],
        }
        for r in l3
    ]
    l4_keys = {
        (t["code"], t["entry_date"])
        for t in bt.select_daily_topn(cand, top_n, tie_seed)
    }
    l4 = [r for r in l3 if (r["code"], r["date"]) in l4_keys]
    return {"b1_l1": l1, "b1_l2": l2, "b1_l3": l3, "b1_l4": l4}


def default_qn_scan_fn() -> Callable[[str, Any, str, str], dict[str, list[int]]]:
    """QN 12 门的 as-of 全 bar 扫描（R31 同口径 ENTRY_GATES × 因子 detect 规范入口）。

    逐股一次 ``_precompute_gate_series``；每 bar 走无切片快速路径（白名单内，
    ``_PrefixLen`` 占位，逐位等价钉测在 test_gate_precompute_equivalence.py），
    必需键缺失回退真切片慢路径。信号资格沿用宇宙 as-of 规则（满
    ``universe.UNIVERSE_MIN_BARS`` 根起）；只收 date ∈ [start, end] 的信号
    （段前预热 bar 只喂指标，不出信号——v2 纪律）。
    """
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research.universe import UNIVERSE_MIN_BARS  # noqa: PLC0415

    gates = {g: cast(Callable[..., bool], bt.ENTRY_GATES[g]) for g in QN_GATES}

    def scan(code: str, df: Any, start: str, end: str) -> dict[str, list[int]]:
        dates = df["date"].astype(str).str[:10].tolist()
        pre = bt._precompute_gate_series(df)
        out: dict[str, list[int]] = {g: [] for g in gates}
        n = len(df)
        for i in range(UNIVERSE_MIN_BARS - 1, n):
            d = dates[i]
            if d < start or d > end:
                continue
            for g, fn in gates.items():
                if bt._slice_free_ok(fn, pre):
                    hit = fn(bt._PrefixLen(i + 1), pre)
                else:
                    hit = fn(df.iloc[: i + 1], pre)
                if hit:
                    out[g].append(i)
        return out

    return scan


def default_mcap_fn() -> Callable[[str, Any], dict[str, float]]:
    """流通市值 as-of（R43 ``_shares_series`` 同源：raw_close × 流通股本事件；
    读不到 ⇒ 全 NaN，分层落入 "na" 桶）。返回 {date: mcap}。

    ⚠️ 分层偏差注记：df 缺 ``raw_close`` 列时**静默回退前复权 close**——qfq 价 ×
    当前流通股本会把早年市值系统性压低（除权因子 <1 压缩历史价格），五分位分层
    落位有偏；生产加载链（``bt._load_one_bars``）带 raw_close，回退只是合成注入
    件/缺列时的兜底，不另行 WARN。"""
    from custos.research import regime_style_study as rs  # noqa: PLC0415

    def fn(code: str, df: Any) -> dict[str, float]:
        dts = df["date"].astype(str).str[:10].reset_index(drop=True)
        close = df["close"].astype("float64").reset_index(drop=True)
        raw_close = (
            df["raw_close"].astype("float64").reset_index(drop=True)
            if "raw_close" in df.columns
            else close
        )
        fsh = rs._shares_series(dts, rs._default_shares_fn(code), "float_shares")
        m = raw_close * fsh
        return {d: (float(v) if v == v else float("nan")) for d, v in zip(dts, m)}

    return fn


# ---------------------------------------------------------------------------
# 匹配（信号 bar vs 波段起涨窗口）
# ---------------------------------------------------------------------------


def _code_windows(
    swings: list[dict[str, Any]], lo: int, hi: int
) -> list[tuple[int, int, int]]:
    """每股起涨窗口区间清单 [(w0, w1, swing_idx)]（w0 可负——截断交给信号侧比较）。"""
    return [(sw["t0"] + lo, sw["t0"] + hi, k) for k, sw in enumerate(swings)]


def _signal_hits(wins: list[tuple[int, int, int]], sigs: list[int]) -> list[bool]:
    """逐信号判定是否落在任一起涨窗口内（窗口按 w0 排序，二分后回扫重叠段）。"""
    if not wins or not sigs:
        return [False] * len(sigs)
    ordered = sorted(wins)
    w0s = [w[0] for w in ordered]
    width = ordered[0][1] - ordered[0][0]
    out = []
    for i in sigs:
        j = bisect.bisect_right(w0s, i) - 1
        hit = False
        while j >= 0 and ordered[j][0] >= i - width:
            if ordered[j][0] <= i <= ordered[j][1]:
                hit = True
                break
            j -= 1
        out.append(hit)
    return out


def _swing_first_match(
    wins: list[tuple[int, int, int]], sigs: list[int]
) -> list[Optional[int]]:
    """逐波段的窗口内**首个**信号 bar（无 ⇒ None；提前量分布与漏斗用它）。"""
    out: list[Optional[int]] = [None] * len(wins)
    for w0, w1, k in wins:
        pos = bisect.bisect_left(sigs, w0)
        if pos < len(sigs) and sigs[pos] <= w1:
            out[k] = sigs[pos]
    return out


def _quantiles(xs: list[float]) -> Optional[dict[str, float]]:
    if not xs:
        return None
    q = np.percentile(np.asarray(xs, dtype=float), [10, 25, 50, 75, 90])
    return {k: float(v) for k, v in zip(("p10", "p25", "p50", "p75", "p90"), q)}


# ---------------------------------------------------------------------------
# 窗内装配：读数 + 分层随机 bar 对照 + 局部低点精确基线
# ---------------------------------------------------------------------------


def _build_window(
    name: str,
    start: str,
    end: str,
    *,
    bars: dict[str, Any],
    b1_layers: dict[str, list[dict[str, Any]]],
    qn_scan_fn: Callable[[str, Any, str, str], dict[str, list[int]]],
    mcap_fn: Callable[[str, Any], dict[str, float]],
    scan_workers: int = 1,
) -> dict[str, Any]:
    """单挖掘段装配：波段识别 + QN as-of 扫描 + B1 层 + 对照集分层母体。

    逐股块（识别 + 扫描 + 市值）在 ``scan_workers`` > 1 时线程并行
    （``bt.run_codes_parallel``，按 codes 序装配，产物与串行逐位一致）。
    返回窗内全部中间态（``_seg`` 供簇划分单源 ``kit.cluster_ids`` 分段）。"""

    def _one(code: str, df: Any) -> dict[str, Any]:
        dates = df["date"].astype(str).str[:10].tolist()
        closes = df["close"].astype(float).tolist()
        sw, dr = identify_swings(dates, closes, seg_start=start, seg_end=end)
        mc = mcap_fn(code, df)
        return {
            "dates": dates,
            "swings": sw,
            "dropped": dr,
            "qn": qn_scan_fn(code, df, start, end),
            "mcap": mc,
            "low": local_low_mask(closes),
        }

    codes = sorted(bars)
    if scan_workers > 1 and len(codes) > 1:
        from custos.research import backtest_factors as bt  # noqa: PLC0415

        scanned = bt.run_codes_parallel(codes, lambda c: _one(c, bars[c]), scan_workers)
    else:
        scanned = {c: _one(c, bars[c]) for c in codes}

    swings_of: dict[str, list[dict[str, Any]]] = {}
    dates_of: dict[str, list[str]] = {}
    dropped: dict[str, int] = {}
    mcap_rep: dict[tuple[str, str], float] = {}  # (code, year) → 年内段内末根 mcap
    year_bars: dict[tuple[str, str], list[int]] = {}  # (code, year) → 段内 bar 序号
    year_lowbars: dict[
        tuple[str, str], list[int]
    ] = {}  # 其中局部低点 bar（v0.458 对照）
    rows_of: dict[str, dict[str, list[dict[str, Any]]]] = {o: {} for o in OBJECTS}
    for o in B1_OBJECTS:
        for r in b1_layers[o]:
            rows_of[o].setdefault(r["code"], []).append(
                {"code": r["code"], "i": int(r["i"]), "date": r["date"], "_seg": name}
            )
    for code in codes:
        r0 = scanned[code]
        dates = r0["dates"]
        for k, v in r0["dropped"].items():
            dropped[k] = dropped.get(k, 0) + v
        if r0["swings"]:
            swings_of[code] = r0["swings"]
        dates_of[code] = dates
        for g in QN_GATES:
            for i in r0["qn"].get(g, []):
                rows_of[g].setdefault(code, []).append(
                    {"code": code, "i": int(i), "date": dates[i], "_seg": name}
                )
        mc = r0["mcap"]
        for i, d in enumerate(dates):
            if not (start <= d <= end):
                continue  # 段前预热 bar 不进对照母体
            y = d[:4]
            year_bars.setdefault((code, y), []).append(i)
            if r0["low"][i]:
                year_lowbars.setdefault((code, y), []).append(i)
            mcap_rep[(code, y)] = mc.get(d, float("nan"))  # 年内末根覆盖前值

    # 排序（同码按 bar）——簇划分与二分匹配的前提；摊平 rows 供簇形状统计（零假设臂用）
    sig_rows: dict[str, list[dict[str, Any]]] = {}
    for o in OBJECTS:
        flat: list[dict[str, Any]] = []
        for code in sorted(rows_of[o]):
            rows_of[o][code].sort(key=lambda r: r["i"])
            flat.extend(rows_of[o][code])
        sig_rows[o] = flat

    # 市值五分位边界（逐年内代码代表值；NaN 不进分位）
    q_edges: dict[str, np.ndarray] = {}
    for y in sorted({yy for _c, yy in mcap_rep}):
        vals = np.array(
            [v for (c, yy), v in mcap_rep.items() if yy == y and v == v], dtype=float
        )
        q_edges[y] = (
            np.percentile(vals, [20, 40, 60, 80]) if len(vals) >= 5 else np.array([])
        )
    # 分层母体：(year, quintile|"na") → 该层有 bar 的代码
    strata_codes: dict[tuple[str, Any], list[str]] = {}
    codes_by_year: dict[str, list[str]] = {}
    for (c, y), v in mcap_rep.items():
        codes_by_year.setdefault(y, []).append(c)
        q: Any = "na"
        if v == v and len(q_edges[y]):
            q = int(np.searchsorted(q_edges[y], v, side="right"))
        strata_codes.setdefault((y, q), []).append(c)
    return {
        "name": name,
        "span": (start, end),
        "swings_of": swings_of,
        "swing_dropped": dropped,
        "dates_of": dates_of,
        "sig_rows": sig_rows,
        "rows_of": rows_of,
        "year_bars": year_bars,
        "year_lowbars": year_lowbars,
        "mcap_rep": mcap_rep,
        "q_edges": q_edges,
        "strata_codes": strata_codes,
        "codes_by_year": codes_by_year,
    }


def _stratum_of(win: dict[str, Any], code: str, year: str) -> tuple[str, Any]:
    v = win["mcap_rep"].get((code, year), float("nan"))
    edges = win["q_edges"].get(year)
    if v != v or edges is None or not len(edges):
        return (year, "na")
    return (year, int(np.searchsorted(edges, v, side="right")))


def _baseline(
    win: dict[str, Any],
    obj: str,
    n_signals: int,
    seed: int,
    *,
    pool_key: str = "year_bars",
) -> tuple[Optional[float], dict[str, dict[str, dict[str, int]]]]:
    """对照集（R50 写死）：同段同宇宙按 年×市值五分位 分层随机 bar，样本量 = 信号数。

    分层比例 = 该对象信号的 (年, 五分位) 组成；返回（随机 bar 落在起涨窗口内的比例,
    分组在案计数 {"by_year"/"by_month"}——年/月分组 lift 的基线侧）。
    """
    if n_signals <= 0:
        return None, {"by_year": {}, "by_month": {}}
    tag = "baseline" if pool_key == "year_bars" else f"baseline|{pool_key}"
    rng = random.Random(f"{seed}|{win['name']}|{obj}|{tag}")
    bars_of = win[pool_key]
    counts: dict[tuple[str, Any], int] = {}
    for r in win["sig_rows"][obj]:
        k = _stratum_of(win, r["code"], r["date"][:4])
        counts[k] = counts.get(k, 0) + 1
    wins_by_code = {
        c: _code_windows(win["swings_of"].get(c, []), WIN_LO, WIN_HI)
        for c in win["dates_of"]
    }
    hit = 0
    total = 0
    per_year: dict[str, dict[str, int]] = {}
    per_month: dict[str, dict[str, int]] = {}
    for (y, q), cnt in sorted(counts.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        pool = win["strata_codes"].get((y, q)) or win["codes_by_year"].get(y) or []
        pool = [c for c in pool if bars_of.get((c, y))]
        if not pool:
            continue
        for _ in range(cnt):
            c = rng.choice(pool)
            i = rng.choice(bars_of[(c, y)])
            total += 1
            h = bool(_signal_hits(wins_by_code.get(c, []), [i])[0])
            hit += int(h)
            d = win["dates_of"][c][i]
            for grp, g in (("by_year", d[:4]), ("by_month", d[5:7])):
                blk = per_year if grp == "by_year" else per_month
                b = blk.setdefault(g, {"n": 0, "hit": 0})
                b["n"] += 1
                b["hit"] += int(h)
    return (hit / total if total else None), {
        "by_year": per_year,
        "by_month": per_month,
    }


def _baseline_low_exact(win: dict[str, Any], obj: str) -> Optional[float]:
    """局部低点对照（v0.458）：按该对象信号的 (年, 市值五分位) 组成加权，各层取**全部**局部低点 bar
    落在起涨窗口内的精确比例（不抽样——抽样基线在小样本对象上噪声大，max-of-16 置换会被噪声顶高）。"""
    counts: dict[tuple[str, Any], int] = {}
    for r in win["sig_rows"][obj]:
        k = _stratum_of(win, r["code"], r["date"][:4])
        counts[k] = counts.get(k, 0) + 1
    if not counts:
        return None
    cache = win.setdefault("_low_rate_cache", {})
    num = den = 0.0
    for (y, q), cnt in counts.items():
        if (y, q) not in cache:
            pool = win["strata_codes"].get((y, q)) or win["codes_by_year"].get(y) or []
            hit = tot = 0
            for c in pool:
                idx = win["year_lowbars"].get((c, y)) or []
                if not idx:
                    continue
                ws = _code_windows(win["swings_of"].get(c, []), WIN_LO, WIN_HI)
                hs = _signal_hits(ws, idx)
                hit += sum(bool(h) for h in hs)
                tot += len(idx)
            cache[(y, q)] = (hit / tot) if tot else None
        rate = cache[(y, q)]
        if rate is None:
            continue
        num += cnt * rate
        den += cnt
    return (num / den) if den else None


def window_readings(
    win: dict[str, Any], *, seed: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """窗内 16 对象读数 + aux（各对象信号数，主流程臂资格判定用）。

    读数（R50 写死）：召回率、信号精度 P(信号日 ∈ 起涨窗口)、精度 lift_low（÷ 局部
    低点精确基线——v0.458 起为判据量）与 lift（÷ 分层随机 bar 基线，只作描述）、
    提前天数分位、按涨幅分带的召回、宽窗召回（只描述）。

    零假设原料不在此处组装：v0.458 起为「保簇形状的随机局部低点入场」——
    ``low_null_material``（局部低点命中前缀和 + 分层下标池）× ``cluster_lengths``
    （对象簇形状）在主流程组装（口径见 R50 文档「跑数前修订 v0.458」节）。
    """
    n_swings = sum(len(v) for v in win["swings_of"].values())
    # 分带分母 = 全部波段（含无信号代码——它们的波段本来就不可能匹配）
    band_totals: dict[str, int] = {b: 0 for b in AMP_BANDS}
    for swings in win["swings_of"].values():
        for sw in swings:
            band_totals[sw["band"]] += 1

    # ── 事件层：全部对象的信号位置去重 → 每位置一个「∈起涨窗口」标签
    #    （逐对象精度与年/月分组复用同一份判定，同位置不重复算）──
    seen: set[tuple[str, int]] = set()
    ev_by_code: dict[str, list[int]] = {}
    for o in OBJECTS:
        for r in win["sig_rows"][o]:
            key = (r["code"], int(r["i"]))
            if key not in seen:
                seen.add(key)
                ev_by_code.setdefault(r["code"], []).append(int(r["i"]))
    ev_hits: dict[tuple[str, int], bool] = {}
    for code, idxs in ev_by_code.items():
        wins = _code_windows(win["swings_of"].get(code, []), WIN_LO, WIN_HI)
        for i, h in zip(idxs, _signal_hits(wins, idxs)):
            ev_hits[(code, i)] = h

    readings: dict[str, Any] = {}
    for obj in OBJECTS:
        flat = win["sig_rows"][obj]
        offsets: list[float] = []
        matched_swings = 0
        wide_matched = 0
        band_match: dict[str, int] = {b: 0 for b in AMP_BANDS}
        for code, crows in win["rows_of"][obj].items():
            sigs = [int(r["i"]) for r in crows]
            swings = win["swings_of"].get(code, [])
            wins = _code_windows(swings, WIN_LO, WIN_HI)
            for r in crows:
                r["_hit"] = ev_hits[(r["code"], int(r["i"]))]
            first = _swing_first_match(wins, sigs)
            first_wide = _swing_first_match(
                _code_windows(swings, WIDE_LO, WIDE_HI), sigs
            )
            for k, sw in enumerate(swings):
                if first[k] is not None:
                    matched_swings += 1
                    band_match[sw["band"]] += 1
                    offsets.append(float(first[k] - sw["t0"]))
                if first_wide[k] is not None:
                    wide_matched += 1
        n_sig = len(flat)
        n_hit = sum(bool(r.get("_hit")) for r in flat)
        base, base_grp = _baseline(win, obj, n_sig, seed)
        base_low = _baseline_low_exact(win, obj)
        prec = (n_hit / n_sig) if n_sig else None
        readings[obj] = {
            "n_signals": n_sig,
            "n_matched": n_hit,
            "precision": prec,
            "baseline": base,
            "lift": (prec / base if prec is not None and base else None),
            "baseline_low": base_low,
            # 判据量（v0.458）：对局部低点基线的精度 lift
            "lift_low": (prec / base_low if prec is not None and base_low else None),
            "recall": (matched_swings / n_swings if n_swings else None),
            "n_swings_matched": matched_swings,
            "lead_quantiles": _quantiles(offsets),
            "recall_wide": (wide_matched / n_swings if n_swings else None),
            "by_band": {
                b: {
                    "n": band_totals[b],
                    "recall": (
                        band_match[b] / band_totals[b] if band_totals[b] else None
                    ),
                }
                for b in AMP_BANDS
            },
            "baseline_by_year": base_grp["by_year"],
            "baseline_by_month": base_grp["by_month"],
        }
    aux = {"n_signals": {o: readings[o]["n_signals"] for o in OBJECTS}}
    return readings, aux


def _funnel(win: dict[str, Any], readings: dict[str, Any]) -> dict[str, Any]:
    """B1 拦截漏斗（逐层累计，R50 写死）：波段在 L1→L4 逐层剩余比例与流失计数。"""
    n = sum(len(v) for v in win["swings_of"].values())
    ns = [readings[o]["n_swings_matched"] for o in B1_OBJECTS]
    out: dict[str, Any] = {"n_swings": n}
    for k, tag in enumerate(("l1", "l2", "l3", "l4")):
        out[f"n_{tag}"] = ns[k]
        out[f"share_{tag}"] = ns[k] / n if n else None
    out["loss"] = {
        "no_j13": n - ns[0],
        "amv_not_long": ns[0] - ns[1],
        "below_score_line": ns[1] - ns[2],
        "not_top_n": ns[2] - ns[3],
    }
    return out


def _by_year_month(
    win: dict[str, Any], readings: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """年 / 月分组（R50 写死）：召回按 T0 年/月，精度 lift 按信号年/月逐对象。"""

    def _group(key_fn: Callable[[str], str], base_key: str) -> dict[str, Any]:
        sw_tot: dict[str, int] = {}
        sw_hit: dict[str, dict[str, int]] = {}
        for code, swings in win["swings_of"].items():
            wins = _code_windows(swings, WIN_LO, WIN_HI)
            sigs = {
                o: [int(r["i"]) for r in win["rows_of"][o].get(code, [])]
                for o in OBJECTS
            }
            firsts = {o: _swing_first_match(wins, sigs[o]) for o in OBJECTS}
            for k, sw in enumerate(swings):
                g = key_fn(sw["t0_date"])
                sw_tot[g] = sw_tot.get(g, 0) + 1
                for o in OBJECTS:
                    if firsts[o][k] is not None:
                        blk = sw_hit.setdefault(g, {})
                        blk[o] = blk.get(o, 0) + 1
        sig_tot: dict[str, dict[str, int]] = {}
        sig_hit: dict[str, dict[str, int]] = {}
        for o in OBJECTS:
            for r in win["sig_rows"][o]:
                g = key_fn(r["date"])
                sig_tot.setdefault(g, {}).setdefault(o, 0)
                sig_tot[g][o] += 1
                sig_hit.setdefault(g, {}).setdefault(o, 0)
                sig_hit[g][o] += int(bool(r.get("_hit")))
        out: dict[str, Any] = {}
        for g in sorted(set(sw_tot) | set(sig_tot)):
            objs: dict[str, Any] = {}
            for o in OBJECTS:
                prec = None
                if sig_tot.get(g, {}).get(o):
                    prec = sig_hit[g][o] / sig_tot[g][o]
                base_blk = (readings[o].get(base_key) or {}).get(g)
                base = (
                    base_blk["hit"] / base_blk["n"]
                    if base_blk and base_blk["n"]
                    else None
                )
                objs[o] = {
                    "recall": (
                        sw_hit.get(g, {}).get(o, 0) / sw_tot[g]
                        if sw_tot.get(g)
                        else None
                    ),
                    "lift": (prec / base if prec is not None and base else None),
                }
            out[g] = {"n_swings": sw_tot.get(g, 0), "objects": objs}
        return out

    return _group(lambda d: d[:4], "baseline_by_year"), _group(
        lambda d: d[5:7], "baseline_by_month"
    )


def _realization(
    win: dict[str, Any],
    per_code: dict[str, dict],
    regime: dict[str, str],
    cost_bps: float,
    replay_fn: Optional[Callable[[list[dict], dict[str, Any]], list[dict]]],
) -> tuple[dict[str, Any], list[dict]]:
    """兑现（R50 写死）：起涨窗口内出过 L1 信号的波段 → 按 ``LIVE_PARAMS`` 重放
    → 已实现收益、捕获率 ret/amp、出场原因分布。每波段取窗口内首个 L1 信号；
    同一信号被多个波段共享时只重放一次，捕获率归入 |提前量| 最近的波段。
    返回（读数块，原始交易集——成本副读数用）。"""
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import factor_exit_study as fes  # noqa: PLC0415
    from custos.research import plan_rules_replay as prr  # noqa: PLC0415

    recs: dict[tuple[str, str], dict[str, Any]] = {}
    amp_of: dict[tuple[str, str], float] = {}
    off_of: dict[tuple[str, str], int] = {}
    n_pairs = 0
    for code, swings in win["swings_of"].items():
        sigs = [int(r["i"]) for r in win["rows_of"]["b1_l1"].get(code, [])]
        if not sigs:
            continue
        scores = (per_code.get(code) or {}).get("scores") or {}
        first = _swing_first_match(_code_windows(swings, WIN_LO, WIN_HI), sigs)
        for k, sw in enumerate(swings):
            i = first[k]
            if i is None:
                continue
            n_pairs += 1
            d = win["dates_of"][code][i]
            key = (code, d)
            off = abs(i - int(sw["t0"]))
            if key not in amp_of or off < off_of[key]:
                amp_of[key] = float(sw["amp"])
                off_of[key] = off
            recs[key] = {
                "code": code,
                "date": d,
                "i": i,
                "sig": {"i": i, "date": d},
                "score": scores.get(d),
            }
    rec_list = [recs[k] for k in sorted(recs)]
    if not rec_list:
        return {"n_pairs": n_pairs, "n_trades": 0, "n_realized": 0}, []
    trades = (
        replay_fn(rec_list, prr.LIVE_PARAMS)
        if replay_fn is not None
        else fes.replay_signals(per_code, rec_list, prr.LIVE_PARAMS, regime, cost_bps)
    )
    rets: list[float] = []
    caps: list[float] = []
    by_reason: dict[str, int] = {}
    n_unreal = 0
    for t in trades:
        rsn = str(t.get("reason"))
        by_reason[rsn] = by_reason.get(rsn, 0) + 1
        if bt.is_unrealized_reason(t.get("reason")) or not isinstance(
            t.get("ret"), (int, float)
        ):
            n_unreal += 1
            continue
        rets.append(float(t["ret"]))
        amp = amp_of.get((str(t.get("code")), str(t.get("entry_date"))[:10]))
        if amp:
            caps.append(float(t["ret"]) / amp)
    return {
        "n_pairs": n_pairs,
        "n_trades": len(trades),
        "n_realized": len(rets),
        "n_unrealized": n_unreal,
        "ret_mean": (float(np.mean(rets)) if rets else None),
        "ret_median": (float(np.median(rets)) if rets else None),
        "capture_mean": (float(np.mean(caps)) if caps else None),
        "capture_median": (float(np.median(caps)) if caps else None),
        "by_reason": by_reason,
    }, trades


# ---------------------------------------------------------------------------
# 线索筛选（16 对象 × 精度 lift_low；保簇随机局部低点零假设定标尺）
# ---------------------------------------------------------------------------


def cluster_lengths(rows: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    """对象信号的簇形状（``kit.cluster_ids`` 单源）：[(簇长, 簇首行)]。回归哨兵 monkeypatch 成全 1。"""
    from custos.research import criteria_kit as kit  # noqa: PLC0415

    if not rows:
        return []
    cids = kit.cluster_ids(rows)
    out: list[tuple[int, dict[str, Any]]] = []
    prev = None
    for cid, r in zip(cids, rows):
        if cid != prev:
            out.append((1, r))
            prev = cid
        else:
            out[-1] = (out[-1][0] + 1, out[-1][1])
    return out


def low_null_material(win: dict[str, Any]) -> dict[str, Any]:
    """零假设原料（v0.458）：全部局部低点 bar 按 (code, bar) 排成全局命中数组的
    前缀和 + 各分层的全局下标池。臂 = 每对象按其簇形状，逐簇抽同层随机局部低点
    锚点、整簇取锚点命中。"""
    codes = sorted({c for (c, _y) in win["year_lowbars"]})
    hit_list: list[int] = []
    strata: dict[tuple[str, Any], list[int]] = {}
    for c in codes:
        idx = sorted(
            i for (cc, _y), ii in win["year_lowbars"].items() if cc == c for i in ii
        )
        if not idx:
            continue
        hs = _signal_hits(
            _code_windows(win["swings_of"].get(c, []), WIN_LO, WIN_HI), idx
        )
        g0 = len(hit_list)
        hit_list.extend(int(bool(h)) for h in hs)
        for k, i in enumerate(idx):
            y = win["dates_of"][c][i][:4]
            strata.setdefault(_stratum_of(win, c, y), []).append(g0 + k)
    cum = np.concatenate([[0], np.cumsum(np.asarray(hit_list, dtype=np.int64))])
    return {
        "cum": cum,
        "strata": {k: np.asarray(v, dtype=np.int64) for k, v in strata.items()},
        "all": np.arange(len(hit_list), dtype=np.int64),
    }


def _null_lifts(
    win: dict[str, Any],
    mat: dict[str, Any],
    shapes: dict[str, Any],
    base_low: dict[str, Any],
    rng: Any,
) -> dict[str, Optional[float]]:
    """单臂：每对象按簇形状逐簇抽同层随机局部低点作锚点，整簇取锚点命中；精度 ÷ 局部低点基线。"""
    out: dict[str, Optional[float]] = {}
    for o in OBJECTS:
        sh = shapes[o]
        b = base_low.get(o)
        if not sh or not b or not len(mat["all"]):
            out[o] = None
            continue
        hit = cnt = 0
        for key, lens in sh.items():
            pool = mat["strata"].get(key)
            if pool is None or not len(pool):
                pool = mat["all"]
            g = pool[rng.integers(0, len(pool), size=len(lens))]
            h = (
                mat["cum"][g + 1] - mat["cum"][g]
            )  # 锚点（随机局部低点）是否落在起涨窗口
            # 整簇取锚点命中：簇内命中完全相关——真实信号簇（相邻 bar）与 16 根宽的起涨窗口
            # 高度同进同出；取完全相关是保守端（零假设方差 ≥ 真实）
            hit += int((h * lens).sum())
            cnt += int(lens.sum())
        out[o] = (hit / cnt / b) if cnt else None
    return out


def _qualifies(rd: dict[str, Any], min_n: int) -> bool:
    """A 合格 / B 复现同门槛（R50 写死）：信号数 ≥ min_n ∧ 精度 lift_low > LIFT_MIN。"""
    return bool(
        rd.get("n_signals", 0) >= min_n
        and rd.get("lift_low") is not None
        and rd["lift_low"] > LIFT_MIN
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _replay_session(fn: Any) -> Any:
    """v0.464：逐笔重放备忘会话（``factor_exit_study.replay_session``，惰性导入防环）。"""
    import functools  # noqa: PLC0415

    @functools.wraps(fn)
    def wrapper(*a: Any, **kw: Any) -> Any:
        from custos.research import factor_exit_study as _fes  # noqa: PLC0415

        return _fes.replay_session(fn)(*a, **kw)

    return wrapper


@_replay_session
def run_study(
    args: Any,
    *,
    warm_fn: Optional[Callable[[str, str], dict[str, dict]]] = None,
    bars_fn: Optional[Callable[[str, str], dict[str, Any]]] = None,
    qn_scan_fn: Optional[Callable[[str, Any, str, str], dict[str, list[int]]]] = None,
    mcap_fn: Optional[Callable[[str, Any], dict[str, float]]] = None,
    replay_fn: Optional[Callable[[list[dict], dict[str, Any]], list[dict]]] = None,
    regime: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """R50 主研究（只读挖掘 A∪B；判定段不读、批次门不触发、台账不登记）。

    注入件（任一非 None = 合成运行）：``warm_fn``（B1 L1 信号扫描，
    生产=``exit_campaign.warm_v0_signals(regime={})``）/ ``bars_fn``（全宇宙
    per-code K 线，含段前预热 300 根——波段识别与 QN 扫描共用）/ ``qn_scan_fn`` /
    ``mcap_fn`` / ``replay_fn`` / ``regime``（0AMV 台账映射；注入路径必须显式给）。
    """
    from custos.research import criteria_kit as kit  # noqa: PLC0415
    from custos.research import provenance as pv  # noqa: PLC0415

    segs = [("mining_a", *lw.SEG_MINING_A), ("mining_b", *lw.SEG_MINING_B)]
    for nm, s, e in segs:
        bad = lw.mining_violation(s, e)
        if bad:  # 段常量漂移时 fail-closed（窗口写死，mining_violation 单源校验）
            raise RuntimeError(f"swing_recall_study 窗口校验失败 {nm}: {bad}")
    if (warm_fn is None) != (bars_fn is None):
        raise ValueError("warm_fn 与 bars_fn 必须同时注入或同时缺省（生产路径）")
    if warm_fn is None:
        warm_fn, bars_fn, regime = _production(args)
    elif regime is None:
        raise ValueError("注入路径必须显式给 regime（0AMV 台账映射）")
    assert bars_fn is not None and regime is not None  # noqa: S101 — 收窄类型
    qn_scan_fn = qn_scan_fn or default_qn_scan_fn()
    mcap_fn = mcap_fn or default_mcap_fn()

    wins: dict[str, dict[str, Any]] = {}
    per_code_of: dict[str, dict[str, dict]] = {}
    for nm, s, e in segs:
        pc = warm_fn(s, e)
        per_code_of[nm] = pc
        layers = collect_b1_layers(pc, regime, args.top_n)
        bars = bars_fn(s, e)
        wins[nm] = _build_window(
            nm,
            s,
            e,
            bars=bars,
            b1_layers=layers,
            qn_scan_fn=qn_scan_fn,
            mcap_fn=mcap_fn,
            scan_workers=int(getattr(args, "load_workers", 1) or 1),
        )
    n_sw = {w: sum(len(v) for v in wins[w]["swings_of"].values()) for w in wins}
    if any(v == 0 for v in n_sw.values()):
        raise RuntimeError(f"空结果护栏：某挖掘窗 0 个 ≥20% 波段（{n_sw}）——不落盘")
    if not any(len(wins[w]["sig_rows"][o]) for w in wins for o in OBJECTS):
        raise RuntimeError("空结果护栏：两挖掘窗 16 对象全 0 信号——不落盘")

    readings: dict[str, dict[str, Any]] = {}
    aux: dict[str, dict[str, Any]] = {}
    for w in wins:
        readings[w], aux[w] = window_readings(wins[w], seed=args.seed)
    wa, wb = segs[0][0], segs[1][0]

    # ── 线索筛选：A 合格 → B 复现 → min(lift_low_A, lift_low_B) vs 保簇随机局部低点 q95 ──
    objs: dict[str, Any] = {}
    for o in OBJECTS:
        ra, rb = readings[wa][o], readings[wb][o]
        qa = _qualifies(ra, args.min_n)
        qb = _qualifies(rb, args.min_n)
        objs[o] = {
            "windows": {wa: ra, wb: rb},
            "qualify_a": qa,
            "replicated": bool(qa and qb),
            "lift_a": ra.get("lift_low"),
            "lift_b": rb.get("lift_low"),
            "lift_any_a": ra.get("lift"),  # 描述：对任意 bar
            "lift_any_b": rb.get("lift"),
            "stat": (min(ra["lift_low"], rb["lift_low"]) if (qa and qb) else None),
        }

    # 零假设（v0.458 跑数前修订）：信号 = 同层随机局部低点、保持各对象簇形状 ⇒ 臂统计量与候选同口径
    # （精度 ÷ 局部低点基线）；原「事件位置换签」实际比的是 16 对象信号并集的平均精度，语义不对。
    mats = {w: low_null_material(wins[w]) for w in (wa, wb)}
    shapes: dict[str, dict[str, dict[Any, np.ndarray]]] = {}
    for w in (wa, wb):
        shapes[w] = {}
        for o in OBJECTS:
            by: dict[Any, list[int]] = {}
            for ln, r0 in cluster_lengths(wins[w]["sig_rows"][o]):
                by.setdefault(
                    _stratum_of(wins[w], r0["code"], r0["date"][:4]), []
                ).append(ln)
            shapes[w][o] = {k: np.asarray(v, dtype=np.int64) for k, v in by.items()}
    base_low = {
        w: {o: readings[w][o]["baseline_low"] for o in OBJECTS} for w in (wa, wb)
    }

    def _arm(i: int) -> Optional[float]:
        rng = np.random.default_rng(
            [int(args.seed), 50, i]
        )  # 确定性（勿用 hash()：str 哈希随进程随机）
        lifts = {}
        for w in (wa, wb):
            lifts[w] = _null_lifts(wins[w], mats[w], shapes[w], base_low[w], rng)
        best = 0.0
        for o in OBJECTS:
            la, lb = lifts[wa][o], lifts[wb][o]
            if (
                aux[wa]["n_signals"][o] >= args.min_n
                and aux[wb]["n_signals"][o] >= args.min_n
                and la is not None
                and lb is not None
                and la > LIFT_MIN
                and lb > LIFT_MIN
            ):
                best = max(best, min(la, lb))
        return best  # 无复现记 0（R49 同口径——臂恒有值，池必满 N）

    pool_blk = kit.assemble_c4_pool(args.n_random, _arm)
    pool = pool_blk["pool"]
    q95 = kit.q95(pool)
    for o in OBJECTS:
        st = (
            kit.c4_state_of(pool, min_pool=args.c4_min_pool, plan_delta=objs[o]["stat"])
            if objs[o]["replicated"]
            else None
        )
        objs[o]["c4_state"] = st
        objs[o]["status"] = (
            ("lead_robust" if st == "confirmed_pass" else "lead_replicated")
            if objs[o]["replicated"]
            else ("a_only" if objs[o]["qualify_a"] else "not_qualified")
        )
    leads = [
        o for o in OBJECTS if objs[o]["status"] in ("lead_robust", "lead_replicated")
    ]
    robust = [o for o in OBJECTS if objs[o]["status"] == "lead_robust"]

    funnel = {w: _funnel(wins[w], readings[w]) for w in wins}
    by_year: dict[str, Any] = {}
    by_month: dict[str, Any] = {}
    for w in wins:
        by_year[w], by_month[w] = _by_year_month(wins[w], readings[w])
    realization: dict[str, Any] = {}
    trades_by_w: dict[str, list[dict]] = {}
    for w in wins:
        realization[w], trades_by_w[w] = _realization(
            wins[w], per_code_of[w], regime, args.cost_bps, replay_fn
        )
    realization["merged"] = {
        "n_pairs": sum(realization[w]["n_pairs"] for w in wins),
        "n_trades": sum(realization[w]["n_trades"] for w in wins),
        "n_realized": sum(realization[w]["n_realized"] for w in wins),
        "by_reason": {
            rsn: sum(realization[w].get("by_reason", {}).get(rsn, 0) for w in wins)
            for rsn in sorted(
                {r for w in wins for r in realization[w].get("by_reason", {})}
            )
        },
    }

    from custos.research.cost_sensitivity import cost_side_block  # noqa: PLC0415

    cost_sens = cost_side_block(trades_by_w, base_bps=args.cost_bps)

    return {
        "schema": "swing_recall_study/v1",
        "unit": "R50",
        "tag": args.tag,
        "verdict": "leads" if robust else "no_robust_lead",
        "robust_leads": robust,
        "leads": leads,
        "windows": {w: list(wins[w]["span"]) for w in wins},
        "params": {
            "min_rise": MIN_RISE,
            "max_dd": MAX_DD,
            "win": [WIN_LO, WIN_HI],
            "wide": [WIDE_LO, WIDE_HI],
            "min_prior_bars": MIN_PRIOR_BARS,
            "max_gap_days": MAX_GAP_DAYS,
            "score_line": B1_SCORE_LINE,
            "top_n": args.top_n,
            "min_n": args.min_n,
            "lift_min": LIFT_MIN,
            "cost_bps": args.cost_bps,
            "warmup_bars": 300,
        },
        "n_swings": n_sw,
        "swing_bands": {
            w: {
                b: sum(
                    1
                    for swings in wins[w]["swings_of"].values()
                    for sw in swings
                    if sw["band"] == b
                )
                for b in AMP_BANDS
            }
            for w in wins
        },
        "swing_dropped": {w: wins[w]["swing_dropped"] for w in wins},
        "objects": objs,
        "funnel": funnel,
        "realization": realization,
        "cost_sensitivity": cost_sens,
        "by_year": by_year,
        "by_month": by_month,
        "c4_pool": {
            "q95": q95,
            "pool": pool,
            **{k: pool_blk[k] for k in ("evaluated", "gate_pass_rate", "target_pool")},
        },
        "window_usage": {
            "window": "not_read",
            "k": None,
            "note": "只读挖掘 A∪B——判定段不读、批次门不触发、window_usage 不登记",
        },
        "provenance": pv.build(
            args,
            unit="R50",
            criteria_version="v0.458",
            pre_reg_doc=RESEARCH_DIR / "R50_swing_winner_recall.md",
            data_last_date=max(
                filter(None, (pv.last_date_of(per_code_of[w]) for w in wins)),
                default=None,
            ),
        ),
    }


def _production(
    args: Any,
) -> tuple[
    Callable[[str, str], dict[str, dict]],
    Callable[[str, str], dict[str, Any]],
    dict[str, str],
]:
    """生产输入三件套：B1 信号 warm（复用 ``exit_campaign.warm_v0_signals``，
    regime={} ⇒ L1=J<13 全量不叠 0AMV（空 regime ⇒ 引擎
    ``_amv_checker`` 恒真）——L2 由本模块按日期再筛，同语义）+ 全宇宙 K 线加载（同参同形状，bar 序号逐位对齐）
    + 0AMV regime。宇宙解析走 ``universe.resolve``（bt._resolve_universe 单源）。"""
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import exit_campaign as ec  # noqa: PLC0415
    from custos.research import score_return_study as srs  # noqa: PLC0415

    ns = argparse.Namespace(
        codes_file=args.codes_file,
        universe_local=args.universe_local,
        universe_sample=args.universe_sample,
        seed=args.universe_seed,
        codes=args.codes,
    )
    codes = bt._resolve_universe(ns, _build_parser())
    args.universe_meta = getattr(ns, "universe_meta", None)
    regime = bt.load_amv_regime(since=lw.SEG_MINING_A[0])
    if not regime:
        raise RuntimeError("0AMV regime 读不到（compass_amv）——本机无数据？")
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    index_df = (
        local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
        .sort_values("date")
        .reset_index(drop=True)
    )
    workers = int(getattr(args, "load_workers", 1) or 1)

    def warm_fn(start: str, end: str) -> dict[str, dict]:
        return ec.warm_v0_signals(
            codes,
            {},  # L1 = J<13 全量：空 regime ⇒ 引擎 _amv_checker 恒真（不叠 0AMV，
            index_df,  # 0AMV 做多由本模块 L2 层按日期再筛，语义逐位一致）
            count=args.count,
            cost_bps=args.cost_bps,
            start=start,
            end=end,
            warmup_bars=300,  # 段前预热（v2 纪律；预热只读价格）
            load_workers=workers,
            score_workers=int(getattr(args, "score_workers", 1) or 1),
        )

    def bars_fn(start: str, end: str) -> dict[str, Any]:
        def _one(code: str) -> Any:
            return bt._load_one_bars(code, args.count, start, end, warmup_bars=300)

        if workers > 1 and len(codes) > 1:
            return bt.run_codes_parallel(codes, _one, workers)
        out: dict[str, Any] = {}
        for i, code in enumerate(codes, 1):
            if i % 200 == 0:
                print(f"[bars] {start}~{end} {i}/{len(codes)}", file=sys.stderr)
            df = _one(code)
            if df is not None and len(df):
                out[code] = df
        return out

    return warm_fn, bars_fn, regime


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R50 ≥20% 波段逆向匹配：B1/QN 信号召回·时点·漏斗（探索，只读挖掘段）"
    )
    ap.add_argument("--codes", default="")
    ap.add_argument("--codes-file", default="")
    ap.add_argument("--universe-sample", type=int, default=0)
    ap.add_argument("--universe-local", action="store_true")
    ap.add_argument("--universe-seed", type=int, default=42)
    ap.add_argument(
        "--count",
        type=int,
        default=None,
        help="每股回溯 K 线根数（缺省按挖掘 A 起点推算）",
    )
    ap.add_argument("--cost-bps", type=float, default=25.0)
    ap.add_argument("--top-n", type=int, default=20, help="B1 漏斗 L4 当日 top-N")
    ap.add_argument(
        "--n-random", type=int, default=N_RANDOM, help="零假设臂数（保簇随机局部低点）"
    )
    ap.add_argument("--c4-min-pool", type=int, default=C4_MIN_POOL)
    ap.add_argument(
        "--min-n",
        type=int,
        default=MIN_N,
        help="线索筛选信号数门槛（写死 1000，测试注入用）",
    )
    ap.add_argument("--seed", type=int, default=20261023)
    ap.add_argument(
        "--load-workers",
        type=int,
        default=1,
        help="warm/K 线加载并行路数（同 tail_feature_study，TODO #112 惯例；"
        "逐股 加载+扫描+打分 线程并行，产物与串行逐位一致；默认 1=原行为）",
    )
    ap.add_argument(
        "--score-workers",
        type=int,
        default=1,
        help="as-of V0 分批量打分进程数（v0.447 score_pool：多进程 + 跨研究落盘缓存，读数逐位不变）",
    )
    ap.add_argument("--tag", default="r50_v1")
    ap.add_argument("--out-dir", default=str(LOGS / "swing_recall"))
    return ap


def main(argv: Optional[list[str]] = None, **inj: Any) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = _build_parser().parse_args(argv)
    args.cmdline = " ".join(sys.argv if argv is None else ["swing_recall_study", *argv])
    if not inj.get("warm_fn"):
        from custos.research.exit_c5_terminal import check_reach  # noqa: PLC0415

        args.count = lw.resolve_count(args.count, lw.SEG_MINING_A[0])
        check_reach(args.count, lw.SEG_MINING_A[0])
    try:
        rep = run_study(args, **inj)
    except (RuntimeError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    out = Path(args.out_dir) / f"_swing_recall__{args.tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, default=str, allow_nan=True),
        encoding="utf-8",
    )
    print(
        f"[R50] {rep['verdict']}  robust={rep['robust_leads'][:8]}  "
        f"q95={rep['c4_pool']['q95']}"
    )
    print(f"[OK] 写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
