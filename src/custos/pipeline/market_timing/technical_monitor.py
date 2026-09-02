# -*- coding: utf-8 -*-
"""Universal technical monitor for sectors/stocks.

Computes:
- trend: up / down / range
- range box: upper/lower/mid for 20d/60d using robust quantiles
- KDJ daily/weekly/monthly
- MACD daily/weekly/monthly

Input can be TDX local vipdoc daily file by code, or future TQ Kline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd


# 2026-08-07 架构审查：以下 7 个纯指标函数已下移到 `indicators`（底层）——
# 它们此前定义在本模块，却被 factors/（底层）与 screening/ 跨层调用，
# 构成「底层依赖决策层」的分层反转。本模块自己也用它们，故导入回来。
from custos.core.indicators import (
    bbi_series,  # noqa: E402
    resample,
    kdj,
    macd,
    bbi_state,
    zhixing_state,
    _infer_price_limit,
    pct_change,
)
from custos.core.indicators import ema  # noqa: E402  包 API 面：market_timing/__init__ re-export

__all__ = [
    "ema",
    "split_code",
]  # re-export 声明（pylint 依此识别非残留），无 star-import 故不影响其他名字

from custos.core.paths import MARKET_DIR  # noqa: E402
from custos.core.code_utils import norm_code, split_code  # noqa: E402  split_code: 包 API re-export
from custos.core.indicators import amplitude_pct as amplitude_pct_of  # noqa: E402
from custos.core.b1_thresholds import (
    REVERSAL_AMPLITUDE_PCT,  # noqa: E402
    REVERSAL_CHANGE_MAX_PCT,
    REVERSAL_CHANGE_MIN_PCT,
    VOL_PCTILE_MAX,
    VOL_RATIO_MAX,
    change_in_range,
)

OUT_DIR = MARKET_DIR


def _read_vipdoc_daily(tdx_code: str) -> pd.DataFrame:
    """统一走数据层 `local_tdx_data.read_vipdoc_daily` 读本地 vipdoc 日线。

    2026-08-24 数据层解耦：此前非 BJ 分支在本模块直调
    `mootdx Reader.factory(tdxdir=TDX_ROOT).daily()`，绕过 datasource 层
    （pipeline 不得直 import 第三方行情包）。等价性已用合成 .day 文件核对：
    read_vipdoc_daily 的沪深路径就是同一个 mootdx Reader（index.name="date" 后
    reset_index），date/open/high/low/close/amount/volume 逐值一致、同为升序、
    单位相同（amount=元，volume=手）；仅多出 code/source 两个信息列，
    消费方（analyze/box/kdj…）不读，无影响。BJ 分支本就走它的 .day 直读
    （mootdx Reader 把 920xxx 误路由到 SH，曙光数创 920808 实盘暴露），不变。
    """
    try:
        from custos.datasource.local_tdx.local_tdx_data import (  # noqa: PLC0415
            read_vipdoc_daily,
        )

        return read_vipdoc_daily(tdx_code)
    except Exception as exc:
        # 外部契约不变：失败返回空 DF（消费方按 "no kline data" 处理），
        # 但不再静默 —— governance/data/DATA_SOURCE_PRINCIPLE.md 原则二。
        print(
            f"[WARN] read_vipdoc({tdx_code}) 读取失败，返回空表: {exc}",
            file=sys.stderr,
        )
        return pd.DataFrame()


def read_vipdoc(tdx_code: str) -> pd.DataFrame:
    """Read K-line via the datasource layer (replaces struct.unpack binary parsing)."""
    return _read_vipdoc_daily(tdx_code)


def box(df: pd.DataFrame, n: int) -> dict[str, Any]:
    if len(df) < min(n, 10):
        return {"available": False}
    x = df.tail(n)
    upper = float(x["high"].quantile(0.85))
    lower = float(x["low"].quantile(0.15))
    mid = (upper + lower) / 2
    close = float(df["close"].iloc[-1])
    width = upper / lower - 1 if lower else None
    if close >= upper:
        pos = "上沿/突破区"
    elif close <= lower:
        pos = "下沿/破位区"
    elif close >= mid:
        pos = "箱体上半区"
    else:
        pos = "箱体下半区"
    return {
        "available": True,
        "period": n,
        "upper": round(upper, 4),
        "lower": round(lower, 4),
        "mid": round(mid, 4),
        "width_pct": round(width * 100, 4) if width is not None else None,
        "position": pos,
    }


def slope(vals: pd.Series, n: int) -> float | None:
    if len(vals) < n + 1:
        return None
    prev = float(vals.iloc[-n - 1])
    now = float(vals.iloc[-1])
    if prev == 0:
        return None
    return (now / prev - 1) * 100


def _pv_snapshot(x: pd.DataFrame) -> dict[str, Any]:
    """最新两根 K 线的量价快照（price_volume_state 的输入段）。"""
    current = x.iloc[-1]
    previous = x.iloc[-2]
    close = float(current["close"])
    previous_close = float(previous["close"])
    open_ = float(current["open"])
    high = float(current["high"])
    low = float(current["low"])
    volume = float(current["volume"])
    volume_ma5 = float(x["volume"].iloc[-6:-1].mean())
    volume_ma20 = (
        float(x["volume"].iloc[-21:-1].mean())
        if len(x) >= 21
        else float(x["volume"].iloc[:-1].tail(20).mean())
    )
    # 2026-08-11（#56 保留项①，owner 拍板）：判定精度统一 round-2 ——
    # 下游小阴/大阴/反转K 判定的区间归属在 ±0.005 尾差带内有方向变化
    # （raw −2.0000000001 原在 [-2,0) 外，round-2 后 −2.0 在内），这是整改目的。
    change_pct = pct_change(close, previous_close, digits=2)
    # ⚠️ 口径 2026-08-10 由 `(high/low - 1)` 改为 **`(high-low)/前收`**（owner 拍板）。
    #    前者分母是**当日最低价**，与治理文档明文（01_swing_rules §反转K）及另两处
    #    live 实现都不一致 ⇒ 同一支票在选股链与持仓链可能得出相反的反转K 结论。
    #    实测约 2% 的日 K 在 7% 门槛上因此翻转，且方向是缩量回踩形态时本式更严。
    amplitude_pct = amplitude_pct_of(high, low, previous_close)
    body_pct = abs(close / open_ - 1) * 100 if open_ else None
    volume_ratio_5 = volume / volume_ma5 if volume_ma5 else None
    volume_ratio_20 = volume / volume_ma20 if volume_ma20 else None
    volume_rank20 = float((x["volume"].tail(20) <= volume).sum()) / 20
    return {
        "date": current["date"].strftime("%Y-%m-%d"),
        "close": close,
        "previous_close": previous_close,
        "open": open_,
        "change_pct": change_pct,
        "amplitude_pct": amplitude_pct,
        "body_pct": body_pct,
        "volume_ratio_5": volume_ratio_5,
        "volume_ratio_20": volume_ratio_20,
        "volume_rank20": volume_rank20,
    }


def _bull_metrics(x: pd.DataFrame, i: int) -> dict[str, Any]:
    row = x.iloc[i]
    prev_close = float(x.iloc[i - 1]["close"])
    day_change = pct_change(float(row["close"]), prev_close, digits=2) or 0
    body = (
        (float(row["close"]) / float(row["open"]) - 1) * 100
        if float(row["open"])
        else 0
    )
    return {
        "bull": float(row["close"]) > float(row["open"]),
        "change_pct": day_change,  # 已是 round-2（2026-08-11 口径统一）
        "body_pct": round(body, 4),
    }


def _pv_bear_flags(snap: dict[str, Any]) -> dict[str, Any]:
    """阴线类判定段：小阴/缩量小阴/大阴/放巨量阴。"""
    close = snap["close"]
    open_ = snap["open"]
    change_pct = snap["change_pct"]
    body_pct = snap["body_pct"]
    volume_ratio_5 = snap["volume_ratio_5"]
    small_bear = (
        close < open_
        and change_pct is not None
        and -2 <= change_pct < 0
        and body_pct is not None
        and body_pct <= 2
    )
    shrink_small_bear = bool(
        small_bear and volume_ratio_5 is not None and volume_ratio_5 <= 0.8
    )
    large_bear = bool(change_pct is not None and change_pct <= -4 and close < open_)
    heavy_large_bear = bool(
        large_bear and volume_ratio_5 is not None and volume_ratio_5 >= 1.5
    )
    return {
        "shrink_small_bear": shrink_small_bear,
        "large_bear": large_bear,
        "heavy_large_bear": heavy_large_bear,
    }


def _pv_shrink_reversal_flags(snap: dict[str, Any]) -> dict[str, Any]:
    """极端缩量与反转K候选判定段（阈值一律取 `b1_thresholds`，运行时读取）。"""
    volume_ratio_5 = snap["volume_ratio_5"]
    volume_rank20 = snap["volume_rank20"]
    change_pct = snap["change_pct"]
    amplitude_pct = snap["amplitude_pct"]
    # `volume_rank20` 是 0~1 的比例，`VOL_PCTILE_MAX` 的单位是 %（10.0）—— 故 /100。
    extreme_shrink = bool(
        volume_ratio_5 is not None
        and volume_ratio_5 <= VOL_RATIO_MAX
        and volume_rank20 <= VOL_PCTILE_MAX / 100
    )
    # ⚠️ 阈值来自 `b1_thresholds`（L0 单一来源）—— 原先硬编码 `-2 <= change_pct <= 2`
    #    与 `amplitude_pct <= 7`，于是 `B1_REVK_*` 只对选股链生效、持仓链无视配置。
    reversal_k_candidate = bool(
        extreme_shrink
        and change_in_range(change_pct)
        and amplitude_pct is not None
        and amplitude_pct <= REVERSAL_AMPLITUDE_PCT
    )
    return {
        "extreme_shrink": extreme_shrink,
        "reversal_k_candidate": reversal_k_candidate,
    }


def _pv_two_medium_large_bull(
    df: pd.DataFrame,
    x: pd.DataFrame,
    code: str,
    close: float,
    latest_bulls: list[dict[str, Any]],
) -> dict[str, Any]:
    """BBI上方连续两根中大阳线判断 (B1第五层止盈)。"""
    price_limit = _infer_price_limit(code, df)
    medium_large_threshold = price_limit / 2  # 半个涨停幅度
    bbi_val = bbi_series(df["close"])
    bbi_latest = float(bbi_val.iloc[-1]) if bbi_val.notna().any() else None
    bbi_prev = (
        float(bbi_val.iloc[-2])
        if len(bbi_val) >= 2 and bbi_val.notna().iloc[-2]
        else None
    )
    close_prev = float(x["close"].iloc[-2])
    above_bbi_now = bbi_latest is not None and close >= bbi_latest
    above_bbi_prev = bbi_prev is not None and close_prev >= bbi_prev
    two_medium_large_bull = None
    two_medium_large_bull_reason = None
    if bbi_latest is not None and bbi_prev is not None:
        bull_today = latest_bulls[-1]
        bull_prev = latest_bulls[-2]
        today_qualifies = bull_today["bull"] and (
            bull_today["change_pct"] >= medium_large_threshold
            or bull_today["body_pct"] >= medium_large_threshold
        )
        prev_qualifies = bull_prev["bull"] and (
            bull_prev["change_pct"] >= medium_large_threshold
            or bull_prev["body_pct"] >= medium_large_threshold
        )
        two_medium_large_bull = bool(
            above_bbi_now and above_bbi_prev and today_qualifies and prev_qualifies
        )
        two_medium_large_bull_reason = (
            f"涨跌幅限制={price_limit}%，中大阳门槛={medium_large_threshold}%；"
            f"T-1阳={bull_prev['bull']}/涨幅{bull_prev['change_pct']}%/实体{bull_prev['body_pct']}%，"
            f"T阳={bull_today['bull']}/涨幅{bull_today['change_pct']}%/实体{bull_today['body_pct']}%；"
            f"BBI上方T-1={above_bbi_prev},T={above_bbi_now}"
        )
    else:
        two_medium_large_bull_reason = "BBI数据不足，无法判断连续中大阳"
    return {
        "two_medium_large_bull": two_medium_large_bull,
        "two_medium_large_bull_reason": two_medium_large_bull_reason,
        "price_limit": price_limit,
        "medium_large_threshold": medium_large_threshold,
    }


def _pv_thresholds_block() -> dict[str, Any]:
    return {
        "medium_large_bull_rule": "单日涨幅或阳线实体幅度达到当日涨跌幅限制的一半",
        "small_bear_change_pct": [-2.0, 0.0],
        "shrink_volume_ratio_5_max": 0.8,
        "heavy_volume_ratio_5_min": 1.5,
        # ⚠️ 上报**实际生效值**而非字面量 —— 原先写死 [-2.0, 2.0]，
        #    环境变量一改它就在谎报自己的阈值。
        "reversal_volume_ratio_5_max": VOL_RATIO_MAX,
        "reversal_volume_rank20_pct_max": VOL_PCTILE_MAX,
        "reversal_close_change_pct": [
            REVERSAL_CHANGE_MIN_PCT,
            REVERSAL_CHANGE_MAX_PCT,
        ],
        "reversal_amplitude_pct_max": REVERSAL_AMPLITUDE_PCT,
    }


def price_volume_state(df: pd.DataFrame, code: str = "") -> dict[str, Any]:
    """Compute deterministic B1 holding signals from completed daily bars."""
    if len(df) < 20:
        return {"available": False, "reason": "少于20根K线"}
    x = df.reset_index(drop=True)
    snap = _pv_snapshot(x)
    latest_bulls = [_bull_metrics(x, -2), _bull_metrics(x, -1)]
    bears = _pv_bear_flags(snap)
    shrinks = _pv_shrink_reversal_flags(snap)
    bbi_part = _pv_two_medium_large_bull(df, x, code, snap["close"], latest_bulls)
    change_pct = snap["change_pct"]
    amplitude_pct = snap["amplitude_pct"]
    body_pct = snap["body_pct"]
    volume_ratio_5 = snap["volume_ratio_5"]
    volume_ratio_20 = snap["volume_ratio_20"]
    return {
        "available": True,
        "date": snap["date"],
        "change_pct": round(change_pct, 4) if change_pct is not None else None,
        "amplitude_pct": round(amplitude_pct, 4) if amplitude_pct is not None else None,
        "body_pct": round(body_pct, 4) if body_pct is not None else None,
        "volume_ratio_5": round(volume_ratio_5, 4)
        if volume_ratio_5 is not None
        else None,
        "volume_ratio_20": round(volume_ratio_20, 4)
        if volume_ratio_20 is not None
        else None,
        "volume_rank20_pct": round(snap["volume_rank20"] * 100, 4),
        "close_raised": bool(snap["close"] > snap["previous_close"]),
        "shrink_small_bear": bears["shrink_small_bear"],
        "large_bear": bears["large_bear"],
        "heavy_large_bear": bears["heavy_large_bear"],
        "last_two_bull_metrics": latest_bulls,
        "two_medium_large_bull": bbi_part["two_medium_large_bull"],
        "two_medium_large_bull_reason": bbi_part["two_medium_large_bull_reason"]
        or "未计算",
        "price_limit": bbi_part["price_limit"],
        "medium_large_bull_threshold": round(bbi_part["medium_large_threshold"], 2),
        "extreme_shrink": shrinks["extreme_shrink"],
        "reversal_k_candidate_without_j": shrinks["reversal_k_candidate"],
        "thresholds": _pv_thresholds_block(),
    }


def _close_at(x: pd.DataFrame, i: int) -> float:
    """收盘价单元格 → float。pandas-stubs 把 ``.at[]`` 标成 Scalar 联合类型，
    运行时本就是 float，集中在这里收口，避免每个调用点各自断言。"""
    v: Any = x.at[i, "close"]
    return float(v)


def _date_at(x: pd.DataFrame, i: int) -> str:
    """date 列单元格 → 'YYYY-MM-DD'（同上，stubs 联合类型的集中收口）。"""
    v: Any = x.at[i, "date"]
    return v.strftime("%Y-%m-%d")


def _closing_pivots(
    x: pd.DataFrame, left: int, right: int, lookback: int
) -> tuple[list[int], list[int]]:
    """近端 lookback 根内的收盘确认分型（窗口内唯一极值）→ (pivot_lows, pivot_highs)。"""
    pivot_lows: list[int] = []
    pivot_highs: list[int] = []
    search_start = max(left, len(x) - max(lookback, left + right + 8))
    for i in range(search_start, len(x) - right):
        close_window = x["close"].iloc[i - left : i + right + 1]
        close = _close_at(x, i)
        if (
            close == float(close_window.min())
            and int((close_window == close).sum()) == 1
        ):
            pivot_lows.append(i)
        if (
            close == float(close_window.max())
            and int((close_window == close).sum()) == 1
        ):
            pivot_highs.append(i)
    return pivot_lows, pivot_highs


def _breach_freshness(
    x: pd.DataFrame,
    anchor: int,
    level: float,
    current_close: float,
    stale_breach_bars: int,
) -> tuple[int | None, int | None, bool, bool]:
    """anchor 之后首次收盘跌破 level 的新鲜度。

    → (first_breach, breach_bars_ago, currently_breached, stale)；
    破位过久(> stale_breach_bars)视为陈旧结构，不应再当作新鲜的 P0 清仓触发。
    """
    breach_rows = x.index[(x.index > anchor) & (x["close"] < level)]
    first_breach = int(breach_rows[0]) if len(breach_rows) else None
    breach_bars_ago = (len(x) - 1 - first_breach) if first_breach is not None else None
    currently_breached = current_close < level
    stale = bool(
        currently_breached
        and breach_bars_ago is not None
        and breach_bars_ago > stale_breach_bars
    )
    return first_breach, breach_bars_ago, currently_breached, stale


def _latest_rising_n(
    x: pd.DataFrame, pivot_lows: list[int], pivot_highs: list[int]
) -> tuple[int, int, int, int | None] | None:
    """最新上升N (l1, h1, l2, breakout)；无已确认分型结构则 None。"""
    for l2 in reversed(pivot_lows):
        prior_lows = [i for i in pivot_lows if i < l2]
        if not prior_lows:
            continue
        l1 = prior_lows[-1]
        highs = [i for i in pivot_highs if l1 < i < l2]
        if not highs:
            continue
        h1 = max(highs, key=lambda i: _close_at(x, i))
        if _close_at(x, l2) <= _close_at(x, l1):
            continue
        breakout_rows = x.index[(x.index > l2) & (x["close"] > _close_at(x, h1))]
        breakout = int(breakout_rows[0]) if len(breakout_rows) else None
        return (l1, h1, l2, breakout)
    return None


def n_structure_state(
    df: pd.DataFrame,
    left: int = 3,
    right: int = 3,
    lookback: int = 90,
    stale_breach_bars: int = 10,
) -> dict[str, Any]:
    """Find the latest rising-N structure using confirmed closing-price pivots.

    L1 is the major closing low, H1 the rebound closing high, and L2 the
    higher pullback closing low. L1 is the hard structural floor; L2 is the
    nearer tactical structure level. A later close above H1 confirms the N.

    仅在近端 lookback 根内搜索分型，避免把很久以前(如顶部区间)的旧N当成当前结构；
    并标记 stale：若价格早在 stale_breach_bars 根之前就已跌破 L1（破位过久），该N已非
    当前生效结构（下跌趋势/箱体信号覆盖），不应再当作新鲜的 P0 清仓触发。
    """
    if len(df) < left + right + 8:
        return {"available": False, "reason": "K线数量不足以确认N型结构"}
    x = df.reset_index(drop=True)
    pivot_lows, pivot_highs = _closing_pivots(x, left, right, lookback)
    latest = _latest_rising_n(x, pivot_lows, pivot_highs)
    if latest is None:
        return {"available": False, "reason": "未发现已确认分型的上升N型结构"}
    l1, h1, l2, breakout = latest
    current_close = float(x["close"].iloc[-1])
    origin_low = _close_at(x, l1)
    pullback_low = _close_at(x, l2)
    swing_high = _close_at(x, h1)
    origin_extreme_low = float(
        x["low"].iloc[max(0, l1 - left) : min(len(x), l1 + right + 1)].min()
    )
    distance_pct = (current_close / origin_low - 1) * 100 if origin_low else None
    # 破位新鲜度：L2 之后首次收盘跌破 L1 的位置；破位过久(> stale_breach_bars)视为陈旧结构
    first_breach, breach_bars_ago, currently_breached, stale = _breach_freshness(
        x, l2, origin_low, current_close, stale_breach_bars
    )
    return {
        "available": True,
        "pattern": "L1-H1-higher_L2"
        + ("-breakout" if breakout is not None else "-candidate"),
        "status": "confirmed" if breakout is not None else "candidate",
        "prior_low": round(origin_low, 4),
        "prior_low_date": _date_at(x, l1),
        "origin_extreme_low": round(origin_extreme_low, 4),
        "breakout_level": round(swing_high, 4),
        "breakout_level_date": _date_at(x, h1),
        "pullback_low": round(pullback_low, 4),
        "pullback_low_date": _date_at(x, l2),
        "confirmed_date": _date_at(x, breakout) if breakout is not None else None,
        "current_close": round(current_close, 4),
        "distance_pct": round(distance_pct, 4) if distance_pct is not None else None,
        "close_above": bool(current_close >= origin_low),
        "breached_on_close": bool(currently_breached),
        "pullback_breached_on_close": bool(current_close < pullback_low),
        "breach_bars_ago": breach_bars_ago,
        "first_breach_date": _date_at(x, first_breach)
        if first_breach is not None
        else None,
        "stale": stale,
        "fresh_breach": bool(currently_breached and not stale),
        "pivot_window": {"left": left, "right": right, "lookback": lookback},
    }


def _latest_descending_n(
    x: pd.DataFrame, pivot_lows: list[int], pivot_highs: list[int]
) -> tuple[int, int, int, bool] | None:
    """最新下降N (h1, l1, h2, confirmed)；无已确认分型结构则 None。"""
    current_close = float(x["close"].iloc[-1])
    for h2 in reversed(pivot_highs):
        prior_highs = [i for i in pivot_highs if i < h2]
        if not prior_highs:
            continue
        h1 = prior_highs[-1]
        if _close_at(x, h2) >= _close_at(x, h1):
            continue  # H2 must be lower than H1
        lows_between = [i for i in pivot_lows if h1 < i < h2]
        if not lows_between:
            continue
        l1 = min(lows_between, key=lambda i: _close_at(x, i))
        # Check if current close is below L1 (confirmation)
        confirmed = current_close < _close_at(x, l1)
        return (h1, l1, h2, confirmed)
    return None


def descending_n_structure_state(
    df: pd.DataFrame,
    left: int = 3,
    right: int = 3,
    lookback: int = 90,
    stale_breach_bars: int = 10,
) -> dict[str, Any]:
    """Find the latest descending-N structure using confirmed closing-price pivots.

    Descending N: H1 -> L1 -> lower H2 -> close below L1.
    - H1 is the major closing high (structural ceiling).
    - L1 is the pullback closing low after H1.
    - H2 is a lower rebound closing high (lower than H1).
    - When price closes below L1, the descending N is confirmed.
    - L1 is the hard structural failure level for short/downside risk.

    仅在近端 lookback 根内搜索分型；破位过久(> stale_breach_bars)标记 stale，避免把
    很久以前(顶部区间)的旧下降N当成当前生效的 P0 清仓触发。
    """
    if len(df) < left + right + 8:
        return {"available": False, "reason": "K线数量不足以确认下降N型结构"}
    x = df.reset_index(drop=True)
    pivot_lows, pivot_highs = _closing_pivots(x, left, right, lookback)
    latest = _latest_descending_n(x, pivot_lows, pivot_highs)
    if latest is None:
        return {"available": False, "reason": "未发现已确认分型的下降N型结构"}
    h1, l1, h2, confirmed = latest
    current_close = float(x["close"].iloc[-1])
    origin_high = _close_at(x, h1)
    pullback_low = _close_at(x, l1)
    lower_high = _close_at(x, h2)
    origin_extreme_high = float(
        x["high"].iloc[max(0, h1 - left) : min(len(x), h1 + right + 1)].max()
    )
    distance_pct = (current_close / pullback_low - 1) * 100 if pullback_low else None
    first_breach, breach_bars_ago, _currently_breached, stale = _breach_freshness(
        x, h2, pullback_low, current_close, stale_breach_bars
    )
    return {
        "available": True,
        "pattern": "H1-L1-lower_H2" + ("-confirmed" if confirmed else "-candidate"),
        "status": "confirmed" if confirmed else "candidate",
        "prior_high": round(origin_high, 4),
        "prior_high_date": _date_at(x, h1),
        "origin_extreme_high": round(origin_extreme_high, 4),
        "structural_low": round(pullback_low, 4),
        "structural_low_date": _date_at(x, l1),
        "lower_high": round(lower_high, 4),
        "lower_high_date": _date_at(x, h2),
        "current_close": round(current_close, 4),
        "distance_to_structural_low_pct": round(distance_pct, 4)
        if distance_pct is not None
        else None,
        "below_structural_low": bool(current_close < pullback_low),
        "breach_bars_ago": breach_bars_ago,
        "first_breach_date": _date_at(x, first_breach)
        if first_breach is not None
        else None,
        "stale": stale,
        "fresh_breach": bool(confirmed and not stale),
        "pivot_window": {"left": left, "right": right, "lookback": lookback},
    }


def _trend_metrics(df: pd.DataFrame) -> dict[str, Any]:
    """趋势判定的原始输入：均线值/斜率与 20 日高低点。"""
    close = df["close"]
    ma25 = close.rolling(25).mean()
    ma60 = close.rolling(60).mean()
    ma144 = close.rolling(144).mean()
    ma240 = close.rolling(240).mean()
    high20_now = float(df["high"].tail(20).max())
    low20_now = float(df["low"].tail(20).min())
    return {
        "c": float(close.iloc[-1]),
        "ma25v": float(ma25.iloc[-1]),
        "ma60v": float(ma60.iloc[-1]),
        "ma144v": float(ma144.iloc[-1]) if pd.notna(ma144.iloc[-1]) else None,
        "ma240v": float(ma240.iloc[-1]) if pd.notna(ma240.iloc[-1]) else None,
        "ma25_slope": slope(ma25.dropna(), 5),
        "ma60_slope": slope(ma60.dropna(), 10),
        "ma144_slope": slope(ma144.dropna(), 20),
        "ma240_slope": slope(ma240.dropna(), 20),
        "high20_now": high20_now,
        "high20_prev": float(df["high"].iloc[-40:-20].max())
        if len(df) >= 40
        else high20_now,
        "low20_now": low20_now,
        "low20_prev": float(df["low"].iloc[-40:-20].min())
        if len(df) >= 40
        else low20_now,
    }


def _trend_classify(m: dict[str, Any]) -> str:
    """上涨/下跌/横盘震荡 三态判定。"""
    if (
        m["c"] > m["ma25v"] > m["ma60v"]
        and (m["ma25_slope"] or 0) > 0
        and m["high20_now"] >= m["high20_prev"]
        and m["low20_now"] >= m["low20_prev"]
    ):
        return "上涨"
    if (
        m["c"] < m["ma25v"] < m["ma60v"]
        and (m["ma25_slope"] or 0) < 0
        and m["high20_now"] <= m["high20_prev"]
        and m["low20_now"] <= m["low20_prev"]
    ):
        return "下跌"
    return "横盘震荡"


def trend_state(df: pd.DataFrame) -> dict[str, Any]:
    if len(df) < 60:
        return {"state": "数据不足", "reason": "少于60根K线"}
    m = _trend_metrics(df)
    c = m["c"]
    ma25v, ma60v = m["ma25v"], m["ma60v"]
    ma144v, ma240v = m["ma144v"], m["ma240v"]
    ma25_slope = m["ma25_slope"]
    ma60_slope = m["ma60_slope"]
    ma144_slope = m["ma144_slope"]
    ma240_slope = m["ma240_slope"]
    high20_now, high20_prev = m["high20_now"], m["high20_prev"]
    low20_now, low20_prev = m["low20_now"], m["low20_prev"]
    state = _trend_classify(m)
    return {
        "state": state,
        "close": round(c, 4),
        "ma25": round(ma25v, 4),
        "ma60": round(ma60v, 4),
        "ma144": round(ma144v, 4) if ma144v is not None else None,
        "ma240": round(ma240v, 4) if ma240v is not None else None,
        "above_ma25": c > ma25v,
        "above_ma60": c > ma60v,
        "above_ma144": c > ma144v if ma144v is not None else None,
        "above_ma240": c > ma240v if ma240v is not None else None,
        "ma25_slope_5d_pct": round(ma25_slope, 4) if ma25_slope is not None else None,
        "ma60_slope_10d_pct": round(ma60_slope, 4) if ma60_slope is not None else None,
        "ma144_slope_20d_pct": round(ma144_slope, 4)
        if ma144_slope is not None
        else None,
        "ma240_slope_20d_pct": round(ma240_slope, 4)
        if ma240_slope is not None
        else None,
        "higher_high_20d": high20_now >= high20_prev,
        "higher_low_20d": low20_now >= low20_prev,
        "lower_high_20d": high20_now <= high20_prev,
        "lower_low_20d": low20_now <= low20_prev,
    }


def analyze(df: pd.DataFrame, code: str = "") -> dict[str, Any]:
    """全套技术面分析。

    ``code`` 必须传:涨跌停幅度按代码前缀推断,不传等于让 300/301/688/920 一律按
    10% 判定,再被 ST 规则(近20日波动 ≤5.2% ⇒ 降为 5%)进一步误降,
    使 two_medium_large_bull 误触发 B1 第五层止盈(审计 B2)。
    """
    if df.empty:
        return {"available": False, "error": "no kline data"}
    weekly = resample(df, "W-FRI")
    monthly = resample(df, "ME")
    daily_trend = trend_state(df)
    return {
        "available": True,
        "latest_date": df["date"].iloc[-1].strftime("%Y-%m-%d"),
        "trend": daily_trend,
        "bbi": bbi_state(df),
        "zhixing": zhixing_state(df),
        "n_structure": n_structure_state(df),
        "descending_n_structure": descending_n_structure_state(df),
        "price_volume": price_volume_state(df, code),
        "box_20d": box(df, 20),
        "box_60d": box(df, 60),
        "daily": {"kdj": kdj(df), "macd": macd(df)},
        "weekly": {"kdj": kdj(weekly), "macd": macd(weekly)},
        "monthly": {"kdj": kdj(monthly), "macd": macd(monthly)},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--code", required=True, help="证券/板块代码，如 600150 或 880xxx.SH"
    )
    ap.add_argument("--name", default="")
    ap.add_argument("--date", default=pd.Timestamp.now().strftime("%Y-%m-%d"))
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    tcode = norm_code(args.code)
    result = {
        "code": tcode,
        "name": args.name,
        "analysis": analyze(read_vipdoc(tcode), tcode),
    }
    out = (
        Path(args.out)
        if args.out
        else OUT_DIR / f"{args.date}_technical_{tcode.replace('.', '_')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:4000])


if __name__ == "__main__":
    main()
