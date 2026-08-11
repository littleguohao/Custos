# -*- coding: utf-8 -*-
"""前置拉升波分类（建仓/拉升/冲刺），冲刺波首个 B1 禁止买入

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

FACTOR: dict[str, Any] = {
    "id": "wave_type",
    "name": "前置拉升波分类（建仓/拉升/冲刺），冲刺波首个 B1 禁止买入",
    "kind": "pattern",
    "status": "active",
    "evidence": "governance/research/R2_selection_price_volume.md",
    "note": "前置拉升波分类（建仓/拉升/冲刺），冲刺波首个 B1 禁止买入",
    "min_bars": 1,
    "live_use": "gate",
    "stage": "release",
}

WAVE_LOOKBACK = 60                  # 拉升波分析窗口（日）
WAVE_MIN_BARS = 40                  # 拉升波分类最少K线数
WAVE_LIMIT_UP_PCT = 9.8             # 待回测参数：涨停/接近涨停判定（单日涨幅%）
WAVE_SPRINT_WINDOW = 20             # 待回测参数：冲刺波涨停统计窗口（日）
WAVE_SPRINT_MIN_LIMIT_UPS = 2       # 待回测参数：冲刺波涨停次数下限
WAVE_ACCEL_10D_GAIN = 25.0          # 待回测参数：高斜率加速（拉升段内最大10日涨幅%下限，i_low→i_high 段上计算）
WAVE_TOP_VOL_RATIO = 1.5            # 待回测参数：顶部放量（高点日量/前5日均量）
WAVE_BUILDUP_GAIN = (25.0, 50.0)    # 建仓波段涨幅%（B1 §四.0 口径）
WAVE_RALLY_GAIN = (35.0, 50.0)      # 拉升波段涨幅%（B1 §四.0 口径）
WAVE_START_CANDLE_PCT = 5.0         # 待回测参数：启动段长阳单日涨幅%
WAVE_START_CANDLE_VOL = 1.5         # 待回测参数：启动段放量倍数
WAVE_SECOND_START_GAIN = 15.0       # 待回测参数：二次启动（前一段摆动幅度%下限）


def _find_rally_segment(df, lookback: int = WAVE_LOOKBACK) -> Optional[tuple[int, int, int, int]]:
    """在近 lookback 日内定位"有效启动低点→阶段高点"拉升段。

    返回 (seg_start, i_low, i_high, n)（df 内绝对位置）；找不到返回 None。
    口径（待回测）：窗口内最低价日为启动低点，其后最高价为阶段高点。
    """
    n = len(df)
    if n < 10:
        return None
    start = max(0, n - lookback)
    _, high, low, _ = _ohlcv_arrays(df)
    i_low = start + int(low[start:].argmin())
    if i_low >= n - 2:
        return None
    i_high = i_low + int(high[i_low:].argmax())
    if i_high <= i_low:
        return None
    return start, i_low, i_high, n


def detect_wave_type(df) -> dict[str, Any]:
    """拉升波三分类（B1 §四.0）：sprint > rally > buildup，冲突取保守。

    detail.accel_10d_gain_pct 为拉升段（i_low→i_high）内最大 10 日涨幅，
    不以当日为终点（回调时点后置口径会失效）。
    """
    close, high, low, vol = _ohlcv_arrays(df)
    n = len(df)
    detail: dict[str, Any] = {}
    if n < WAVE_MIN_BARS:
        return {"wave_type": "unknown", "available": False, "detail": {"reason": f"K线不足{WAVE_MIN_BARS}根"}}
    seg = _find_rally_segment(df)
    if seg is None:
        return {"wave_type": "unknown", "available": True, "detail": {"reason": "无有效启动低点→阶段高点段"}}
    start, i_low, i_high, _ = seg

    seg_gain = (float(high[i_high]) / float(close[i_low]) - 1) * 100 if close[i_low] else 0.0
    # 近20日涨停/接近涨停计数（全 df 口径；prev close<=0 的脏数据 bar 不计涨停）
    with np.errstate(divide="ignore", invalid="ignore"):
        chg = close[1:] / close[:-1] * 100 - 100
    limit_ups = [i + 1 for i in range(max(0, n - WAVE_SPRINT_WINDOW - 1), n - 1)
                 if close[i] > 0 and chg[i] >= WAVE_LIMIT_UP_PCT]
    # 高斜率加速：拉升段（i_low→i_high）内最大 10 日涨幅。
    # 以今天为终点会在 B1 回调时点必然失效，必须在段上计算（code review 修复）。
    accel_10d = None
    if i_high - i_low >= 10:
        accel_10d = max(
            (float(close[t] / close[t - 10] - 1) * 100)
            for t in range(i_low + 10, i_high + 1)
            if close[t - 10] > 0
        )
    # 顶部放量：阶段高点日量 / 其前5日均量
    top_vol_ratio = None
    if i_high >= 1:
        base = vol[max(0, i_high - 5):i_high].mean() if i_high >= 1 else 0
        top_vol_ratio = float(vol[i_high] / base) if base else None
    # 启动段放量长阳：启动低点后5日内存在涨幅>=5%且量>=前5日均量1.5倍
    start_bull = False
    for t in range(i_low + 1, min(i_low + 6, n)):
        base = vol[max(0, t - 5):t].mean()
        # close[t-1] 守卫与上方 accel_10d（close[t-10] > 0）同款：prev close<=0 的脏数据
        # bar 直接跳过，不算启动长阳（此前缺这个守卫会 RuntimeWarning: divide by zero）。
        if base and close[t - 1] > 0 and close[t] / close[t - 1] - 1 >= WAVE_START_CANDLE_PCT / 100 and vol[t] >= base * WAVE_START_CANDLE_VOL:
            start_bull = True
            break
    # 二次启动：启动低点之前的窗口段已存在 >=15% 摆动（前一段拉升）
    second_start = False
    if i_low - start >= 5:
        prior_swing = (float(high[start:i_low].max()) / float(low[start:i_low].min()) - 1) * 100
        second_start = prior_swing >= WAVE_SECOND_START_GAIN
    else:
        prior_swing = None

    accel_ok = accel_10d is not None and accel_10d >= WAVE_ACCEL_10D_GAIN
    top_vol_ok = top_vol_ratio is not None and top_vol_ratio >= WAVE_TOP_VOL_RATIO
    if len(limit_ups) >= WAVE_SPRINT_MIN_LIMIT_UPS and accel_ok and top_vol_ok:
        wave = "sprint"
    elif second_start and WAVE_RALLY_GAIN[0] <= seg_gain <= WAVE_RALLY_GAIN[1]:
        wave = "rally"
    elif WAVE_BUILDUP_GAIN[0] <= seg_gain <= WAVE_BUILDUP_GAIN[1] and start_bull:
        wave = "buildup"
    else:
        wave = "unknown"

    detail = {
        "seg_gain_pct": round(seg_gain, 2),
        "limit_up_count_20d": len(limit_ups),
        "accel_10d_gain_pct": round(accel_10d, 2) if accel_10d is not None else None,
        "top_vol_ratio": round(top_vol_ratio, 3) if top_vol_ratio is not None else None,
        "start_bull_candle": start_bull,
        "second_start": second_start,
        "prior_swing_pct": round(prior_swing, 2) if prior_swing is not None else None,
    }
    return {"wave_type": wave, "available": True, "detail": detail}
