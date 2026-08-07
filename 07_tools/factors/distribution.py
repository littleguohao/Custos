# -*- coding: utf-8 -*-
"""主力出货五方式（顶部派发形态），用于清仓与选股规避

2026-08-06 从 `screening/enrich_candidates.py` 抽出（**零行为变化**，逐字搬）。
抽出的动因：因子实现必须**全项目唯一一份**，其他模块通过调用访问 ——
内联在 1723 行的选股链主流程里，既无法单独回测，也无法防止别处再写一份。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
from _util import ohlcv_arrays as _ohlcv_arrays

_TOOLS = Path(__file__).resolve().parents[1]
for _p in (str(_TOOLS), str(_TOOLS / "market_timing")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from technical_monitor import _infer_price_limit  # noqa: E402

FACTOR: dict[str, Any] = {
    "id": "distribution",
    "name": "主力出货五方式（顶部派发形态），用于清仓与选股规避",
    "kind": "pattern",
    "status": "active",
    "evidence": "00_governance/research/R2_selection_price_volume.md",
    "note": "主力出货五方式（顶部派发形态），用于清仓与选股规避",
    "min_bars": 1,
    "live_use": "gate",
    "stage": "release",
}

DIST_RECENT = 5                    # 待回测：出货形态观察最近N根
DIST_ACCEL_WIN = 10               # 待回测：加速涨幅窗口（日）
DIST_ACCEL_GAIN = 25.0           # 待回测：加速涨幅%下限（阴线前）
DIST_BIG_BEAR_FRAC = 0.5        # 待回测：大阴=跌幅≥涨跌幅制度×0.5
DIST_LONG_BEAR_FRAC = 0.8      # 待回测：长阴/近跌停=跌幅≥涨跌幅制度×0.8
DIST_HUGE_VOL_RATIO = 2.0      # 待回测：天量/巨量=≥20日均量×2
DIST_HUGE_VOL_WIN = 20         # 待回测：天量对比窗口（日）
DIST_STAIR_MIN_BARS = 3        # 待回测：阶梯放量阴线最少连续根数
DIST_STAIR_BREAK_VR = 1.2      # 待回测：放量跌破QSX的量比下限
DIST_TOP_WINDOW = 10           # 待回测：顶部区间（绿肥红瘦/双头）窗口
DIST_DOUBLE_TOP_TOL = 3.0      # 待回测：双头两顶相近容差%
DIST_SUBHIGH_SHRINK = 0.9      # 待回测：次高前一日缩量量比上限
DIST_MIN_VOL_MA20_FRAC = 0.05  # 待回测：vol_ma20 低于全序列均量×此比例时视为近零（派发检测器 available=False）


def detect_distribution(df, code: str = "") -> dict[str, Any]:
    """主力出货五方式（顶部派发，B1 §七.3）：负向因子，用于选股规避/降档。

    ① 顶部天量大阴、② 次高点巨量长阴、③ 阶梯放量跌破QSX、④ 双头双巨阴、
    ⑤ 顶部绿肥红瘦。命中≥1→watch；命中①/②或≥2→high。阈值均为待回测参数。
    """
    close, high, low, vol = _ohlcv_arrays(df)
    open_ = df["open"].astype(float).to_numpy()
    n = len(df)
    if n < 30:
        return {"available": False, "signals": {}, "hits": [], "hit_count": 0,
                "severe": False, "risk_level": "none"}
    limit = _infer_price_limit(code, df)
    big_bear = limit * DIST_BIG_BEAR_FRAC
    long_bear = limit * DIST_LONG_BEAR_FRAC
    vol_ma20 = float(vol[max(0, n - DIST_HUGE_VOL_WIN - 1):n - 1].mean())
    # vol_ma20 近零（长期停牌/零成交脏数据）时量比类判定全部失真 → 检测器不可用
    series_vol_mean = float(vol.mean()) if n else 0.0
    if not series_vol_mean or vol_ma20 < series_vol_mean * DIST_MIN_VOL_MA20_FRAC:
        return {"available": False, "signals": {}, "hits": [], "hit_count": 0,
                "severe": False, "risk_level": "none",
                "reason": f"vol_ma20 近零（{vol_ma20:.1f} < 全序列均量 {series_vol_mean:.1f}×{DIST_MIN_VOL_MA20_FRAC}）"}
    qsx = df["close"].astype(float).ewm(span=10, adjust=False).mean().ewm(span=10, adjust=False).mean().to_numpy()

    def chg(t: int) -> float:
        return (close[t] / close[t - 1] - 1) * 100 if t >= 1 and close[t - 1] else 0.0

    def vr5(t: int):
        base = vol[max(0, t - 5):t].mean()
        return float(vol[t] / base) if base else None

    sig: dict[str, Any] = {}

    # ① 顶部天量大阴：近DIST_RECENT根内 大阴 + 天量 + 阴线前加速
    hit1 = None
    for t in range(n - DIST_RECENT, n):
        if t < DIST_ACCEL_WIN + 1:
            continue
        c = chg(t)
        # 「天量」＝ 量 ≥ 20日均量×DIST_HUGE_VOL_RATIO（与②同一口径，见顶部常量）。
        # 审计：原先还 or 了 `vol[t] >= vol[t-20:t+1].max()`——该切片**含 t 自身**，
        # 于是它恒等于"当日是窗口最大量"，即 20 日量新高，完全旁路了 2×MA20 阈值：
        # 一只均量平稳的票只要今天量比昨天高一点点就算"天量"，配上大阴+加速就被判出货。
        huge = bool(vol_ma20) and vol[t] >= vol_ma20 * DIST_HUGE_VOL_RATIO
        accel = (close[t - 1] / close[t - DIST_ACCEL_WIN] - 1) * 100 if close[t - DIST_ACCEL_WIN] else 0.0
        if close[t] < open_[t] and c <= -big_bear and huge and accel >= DIST_ACCEL_GAIN:
            hit1 = {"bars_ago": n - 1 - t, "change_pct": round(c, 2),
                    "vol_ratio_ma20": round(float(vol[t] / vol_ma20), 3) if vol_ma20 else None,
                    "accel_pct": round(accel, 2)}
            break
    sig["top_huge_vol_bear"] = {"hit": hit1 is not None, "detail": hit1}

    # ② 次高点巨量长阴：前一日缩量创新高/次高 + 当日巨量长阴
    hit2 = None
    for t in range(n - DIST_RECENT, n):
        if t < 25:
            continue
        c = chg(t)
        prev_new_high = high[t - 1] >= high[max(0, t - 21):t - 1].max()
        prev_shrink = (vr5(t - 1) is not None and vr5(t - 1) <= DIST_SUBHIGH_SHRINK)
        huge = vol_ma20 and vol[t] >= vol_ma20 * DIST_HUGE_VOL_RATIO
        if close[t] < open_[t] and c <= -long_bear and huge and prev_new_high and prev_shrink:
            hit2 = {"bars_ago": n - 1 - t, "change_pct": round(c, 2),
                    "prev_vol_ratio5": round(float(vr5(t - 1)), 3),
                    "vol_ratio_ma20": round(float(vol[t] / vol_ma20), 3)}
            break
    sig["subhigh_vol_bear"] = {"hit": hit2 is not None, "detail": hit2}

    # ③ 阶梯放量跌破QSX：近DIST_RECENT根内收盘放量跌破QSX，且此前连续≥3根放量阴
    hit3 = None
    for t in range(n - DIST_RECENT, n):
        if t < DIST_STAIR_MIN_BARS + 6:
            continue
        vrt = vr5(t)
        broke = close[t] < qsx[t] and vrt is not None and vrt >= DIST_STAIR_BREAK_VR
        cnt = 0
        for k in range(t, max(0, t - 8), -1):
            vrk = vr5(k)
            if close[k] < open_[k] and vrk is not None and (vol[k] >= vol[k - 1] or vrk >= 1.0):
                cnt += 1
            else:
                break
        if broke and cnt >= DIST_STAIR_MIN_BARS:
            hit3 = {"bars_ago": n - 1 - t, "consecutive_vol_bears": cnt,
                    "vol_ratio5": round(vrt, 3), "below_qsx": True}
            break
    sig["stairstep_vol_decline"] = {"hit": hit3 is not None, "detail": hit3}

    # ④ 双头双巨阴：近窗口内两个相近高点，各自其后≤2根内出现放量阴
    hit4 = None
    w0 = max(0, n - DIST_TOP_WINDOW * 2)
    peaks = [i for i in range(w0 + 2, n - 2)
             if high[i] == high[i - 2:i + 3].max() and float((high[i - 2:i + 3] == high[i]).sum()) == 1]
    if len(peaks) >= 2:
        p2 = peaks[-1]
        p1 = max((p for p in peaks[:-1]), key=lambda i: high[i], default=None)
        if p1 is not None and p2 - p1 >= 3:
            close_tops = abs(high[p1] / high[p2] - 1) * 100 <= DIST_DOUBLE_TOP_TOL

            def bear_vol_after(p: int) -> bool:
                for t in range(p + 1, min(n, p + 3)):
                    vrt = vr5(t)
                    if close[t] < open_[t] and vrt is not None and vrt >= 1.5:
                        return True
                return False
            if close_tops and bear_vol_after(p1) and bear_vol_after(p2):
                hit4 = {"peak1_bars_ago": n - 1 - p1, "peak2_bars_ago": n - 1 - p2,
                        "tops_gap_pct": round(abs(high[p1] / high[p2] - 1) * 100, 2)}
    sig["double_top_vol_bear"] = {"hit": hit4 is not None, "detail": hit4}

    # ⑤ 顶部绿肥红瘦：顶部区间阴线实体均值 > 阳线实体均值 且 阴量 > 阳量
    seg = range(n - DIST_TOP_WINDOW, n)
    near_top = True if n < 60 else high[-DIST_TOP_WINDOW:].max() >= high[-60:].max() * 0.98
    bear_bodies = [abs(close[t] / open_[t] - 1) * 100 for t in seg if close[t] < open_[t] and open_[t]]
    bull_bodies = [abs(close[t] / open_[t] - 1) * 100 for t in seg if close[t] > open_[t] and open_[t]]
    bear_vols = [vol[t] for t in seg if close[t] < open_[t]]
    bull_vols = [vol[t] for t in seg if close[t] > open_[t]]
    hit5 = bool(near_top and bear_bodies and bull_bodies
                and (sum(bear_bodies) / len(bear_bodies) > sum(bull_bodies) / len(bull_bodies))
                and bear_vols and bull_vols
                and (sum(bear_vols) / len(bear_vols) > sum(bull_vols) / len(bull_vols)))
    sig["top_green_heavy_red_light"] = {
        "hit": hit5,
        "detail": {"bear_body_mean_pct": round(sum(bear_bodies) / len(bear_bodies), 3) if bear_bodies else None,
                   "bull_body_mean_pct": round(sum(bull_bodies) / len(bull_bodies), 3) if bull_bodies else None} if near_top else None,
    }

    hits = [k for k, v in sig.items() if v["hit"]]
    severe = sig["top_huge_vol_bear"]["hit"] or sig["subhigh_vol_bear"]["hit"]
    risk = "high" if (severe or len(hits) >= 2) else ("watch" if hits else "none")
    return {"available": True, "signals": sig, "hits": hits, "hit_count": len(hits),
            "severe": bool(severe), "risk_level": risk, "price_limit": limit}
