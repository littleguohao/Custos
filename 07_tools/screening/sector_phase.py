# -*- coding: utf-8 -*-
"""板块相位(MACD 波段)——对板块指数收盘序列判"建仓上水/拉升/冲刺(顶背离)/水下调整"。

用于板块择时 gate:只在板块**有利相位**(DIF>0 且未走完冲刺=无近期顶背离/三打)时,放行其成分股进场。
少参数(lookback/fractal),防过拟合;所有阈值待跨年 walk-forward 验证。纯序列运算,绝不 raise。

⚠️ 数据:通达信 880 板块指数历史约 2021-08 起(~5年,含熊含牛);概念板块更短。跨周期结论以 OOS 为准。
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
PHASE_LOOKBACK = 60          # 顶背离/三打回看窗口(交易日)
PHASE_FRACTAL = 2            # 摆动高点左右确认根数


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    c = close.astype(float)
    dif = c.ewm(span=MACD_FAST, adjust=False).mean() - c.ewm(span=MACD_SLOW, adjust=False).mean()
    dea = dif.ewm(span=MACD_SIGNAL, adjust=False).mean()
    return dif, dea


def _swing_highs(x: np.ndarray, f: int, w0: int) -> list[int]:
    """收盘摆动高点:窗口[i-f,i+f]内 x[i]=max 且至少 2f-1 根严格更低(允许1平台)。"""
    out = []
    n = len(x)
    for i in range(max(w0, f), n - f):
        seg = x[i - f:i + f + 1]
        if x[i] == seg.max() and int((seg < x[i]).sum()) >= 2 * f - 1:
            out.append(i)
    return out


def compute_sector_phase(close, lookback: int = PHASE_LOOKBACK,
                         fractal: int = PHASE_FRACTAL) -> dict[str, Any]:
    """输入板块指数收盘(list/Series)→ 相位字典。favorable=可在该板块选股进场。"""
    c = pd.Series(list(close), dtype=float).reset_index(drop=True)
    n = len(c)
    if n < MACD_SLOW + MACD_SIGNAL + fractal + 5:
        return {"available": False}
    dif, dea = _macd(c)
    dif_v = dif.values
    close_v = c.values
    dif_last = float(dif_v[-1])
    above_zero = bool(dif_last > 0)                          # 建仓已上水/趋势在
    w0 = max(fractal, n - lookback)
    hi = _swing_highs(close_v, fractal, w0)
    top_div = three_peaks = False
    if len(hi) >= 2:
        a, b = hi[-2], hi[-1]
        top_div = bool(close_v[b] > close_v[a] and dif_v[b] < dif_v[a])   # 价新高、DIF不创高=顶背离
    if len(hi) >= 3:
        p1, p2, p3 = hi[-3], hi[-2], hi[-1]
        three_peaks = bool(close_v[p1] < close_v[p2] < close_v[p3]
                           and dif_v[p1] > dif_v[p2] > dif_v[p3])          # 三打白骨精
    exhausted = bool(top_div or three_peaks)                # 冲刺(接近)走完
    favorable = bool(above_zero and not exhausted)
    if not above_zero:
        phase = "水下/调整"
    elif exhausted:
        phase = "冲刺/顶背离(过滤)"
    else:
        phase = "建仓上水/拉升(有利)"
    return {"available": True, "dif": round(dif_last, 4), "above_zero": above_zero,
            "top_divergence": top_div, "three_peaks": three_peaks,
            "exhausted": exhausted, "favorable": favorable, "phase": phase}
