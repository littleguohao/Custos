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
import struct
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd

TDX_ROOT = Path(r"C:\new_tdx64")
OUT_DIR = Path(r"C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team\01_data\market")


def norm_code(code: str) -> str:
    s = str(code).strip().upper()
    if s.endswith((".SH", ".SZ", ".BJ")):
        return s
    # 北交所常见代码含 4/8 开头，也包含 920xxx。
    if s.startswith(("920", "8", "4")):
        return s + ".BJ"
    if s.startswith(("6", "5", "9")):
        return s + ".SH"
    if s.startswith(("0", "1", "2", "3")):
        return s + ".SZ"
    return s


def split_code(tdx_code: str):
    s = norm_code(tdx_code)
    code, suf = s.split(".")
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(suf, "")
    return prefix, code


def read_vipdoc(tdx_code: str) -> pd.DataFrame:
    prefix, code = split_code(tdx_code)
    path = TDX_ROOT / "vipdoc" / prefix / "lday" / f"{prefix}{code}.day"
    if not path.exists():
        return pd.DataFrame()
    rows = []
    raw = path.read_bytes()
    for i in range(0, len(raw), 32):
        chunk = raw[i:i+32]
        if len(chunk) < 32:
            continue
        date, open_, high, low, close, amount, vol, _ = struct.unpack("IIIIIfII", chunk)
        rows.append({
            "date": pd.to_datetime(str(date), format="%Y%m%d", errors="coerce"),
            "open": open_ / 100,
            "high": high / 100,
            "low": low / 100,
            "close": close / 100,
            "amount": amount,
            "volume": vol,
        })
    df = pd.DataFrame(rows).dropna(subset=["date"]).sort_values("date")
    return df.reset_index(drop=True)


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def macd(df: pd.DataFrame) -> dict[str, Any]:
    if len(df) < 35:
        return {"available": False}
    close = df["close"]
    dif = ema(close, 12) - ema(close, 26)
    dea = ema(dif, 9)
    hist = (dif - dea) * 2
    return {
        "available": True,
        "dif": round(float(dif.iloc[-1]), 4),
        "dea": round(float(dea.iloc[-1]), 4),
        "hist": round(float(hist.iloc[-1]), 4),
        "hist_prev": round(float(hist.iloc[-2]), 4),
        "hist_direction": "扩张" if hist.iloc[-1] > hist.iloc[-2] else "收缩",
        "golden_cross": bool(dif.iloc[-2] <= dea.iloc[-2] and dif.iloc[-1] > dea.iloc[-1]),
        "death_cross": bool(dif.iloc[-2] >= dea.iloc[-2] and dif.iloc[-1] < dea.iloc[-1]),
    }


def kdj(df: pd.DataFrame, n=9, m1=3, m2=3) -> dict[str, Any]:
    if len(df) < n + 3:
        return {"available": False}
    low_n = df["low"].rolling(n).min()
    high_n = df["high"].rolling(n).max()
    rsv = (df["close"] - low_n) / (high_n - low_n) * 100
    rsv = rsv.replace([float("inf"), -float("inf")], pd.NA).fillna(50)
    k = rsv.ewm(com=m1-1, adjust=False).mean()
    d = k.ewm(com=m2-1, adjust=False).mean()
    j = 3 * k - 2 * d
    jv = float(j.iloc[-1])
    if jv < 12:
        state = "低位调整到位观察"
    elif jv > 90:
        state = "高位过热"
    elif j.iloc[-1] > j.iloc[-2] and jv < 30:
        state = "低位拐头"
    else:
        state = "中性"
    return {
        "available": True,
        "k": round(float(k.iloc[-1]), 4),
        "d": round(float(d.iloc[-1]), 4),
        "j": round(jv, 4),
        "j_prev": round(float(j.iloc[-2]), 4),
        "golden_cross": bool(k.iloc[-2] <= d.iloc[-2] and k.iloc[-1] > d.iloc[-1]),
        "death_cross": bool(k.iloc[-2] >= d.iloc[-2] and k.iloc[-1] < d.iloc[-1]),
        "state": state,
    }


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if df.empty:
        return df
    x = df.set_index("date").resample(rule).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "amount": "sum",
        "volume": "sum",
    }).dropna().reset_index()
    return x


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
    prev = float(vals.iloc[-n-1])
    now = float(vals.iloc[-1])
    if prev == 0:
        return None
    return (now / prev - 1) * 100


def bbi_state(df: pd.DataFrame) -> dict[str, Any]:
    """Return the standard TDX BBI state used by the B1 holding rules."""
    if len(df) < 24:
        return {"available": False, "reason": "少于24根K线"}
    close = df["close"]
    bbi = sum(close.rolling(n).mean() for n in (3, 6, 12, 24)) / 4
    valid = bbi.notna()
    if not valid.any():
        return {"available": False, "reason": "BBI无法计算"}

    c = float(close.iloc[-1])
    value = float(bbi.iloc[-1])
    below = close < bbi
    consecutive_below = 0
    for is_below in reversed(below.tolist()):
        if not is_below:
            break
        consecutive_below += 1

    distance_pct = (c / value - 1) * 100 if value else None
    return {
        "available": True,
        "formula": "(MA3+MA6+MA12+MA24)/4",
        "value": round(value, 4),
        "close_above": bool(c >= value),
        "distance_pct": round(distance_pct, 4) if distance_pct is not None else None,
        "consecutive_closes_below": consecutive_below,
        "previous_close_above": bool(close.iloc[-2] >= bbi.iloc[-2]) if len(df) >= 25 else None,
    }


def price_volume_state(df: pd.DataFrame) -> dict[str, Any]:
    """Compute deterministic B1 holding signals from completed daily bars."""
    if len(df) < 20:
        return {"available": False, "reason": "少于20根K线"}
    x = df.reset_index(drop=True)
    current = x.iloc[-1]
    previous = x.iloc[-2]
    close = float(current["close"])
    previous_close = float(previous["close"])
    open_ = float(current["open"])
    high = float(current["high"])
    low = float(current["low"])
    volume = float(current["volume"])
    volume_ma5 = float(x["volume"].iloc[-6:-1].mean())
    volume_ma20 = float(x["volume"].iloc[-21:-1].mean()) if len(x) >= 21 else float(x["volume"].iloc[:-1].tail(20).mean())
    change_pct = (close / previous_close - 1) * 100 if previous_close else None
    amplitude_pct = (high / low - 1) * 100 if low else None
    body_pct = abs(close / open_ - 1) * 100 if open_ else None
    volume_ratio_5 = volume / volume_ma5 if volume_ma5 else None
    volume_ratio_20 = volume / volume_ma20 if volume_ma20 else None
    volume_rank20 = float((x["volume"].tail(20) <= volume).sum()) / 20

    def bull_metrics(i: int) -> dict[str, Any]:
        row = x.iloc[i]
        prev_close = float(x.iloc[i - 1]["close"])
        day_change = (float(row["close"]) / prev_close - 1) * 100 if prev_close else 0
        body = (float(row["close"]) / float(row["open"]) - 1) * 100 if float(row["open"]) else 0
        return {"bull": float(row["close"]) > float(row["open"]), "change_pct": round(day_change, 4), "body_pct": round(body, 4)}

    latest_bulls = [bull_metrics(-2), bull_metrics(-1)]
    small_bear = close < open_ and change_pct is not None and -2 <= change_pct < 0 and body_pct is not None and body_pct <= 2
    shrink_small_bear = bool(small_bear and volume_ratio_5 is not None and volume_ratio_5 <= 0.8)
    large_bear = bool(change_pct is not None and change_pct <= -4 and close < open_)
    heavy_large_bear = bool(large_bear and volume_ratio_5 is not None and volume_ratio_5 >= 1.5)
    extreme_shrink = bool(
        volume_ratio_5 is not None and volume_ratio_5 <= 0.5 and volume_rank20 <= 0.10
    )
    reversal_k_candidate = bool(
        extreme_shrink and change_pct is not None and -2 <= change_pct <= 2
        and amplitude_pct is not None and amplitude_pct <= 7
    )
    return {
        "available": True,
        "date": current["date"].strftime("%Y-%m-%d"),
        "change_pct": round(change_pct, 4) if change_pct is not None else None,
        "amplitude_pct": round(amplitude_pct, 4) if amplitude_pct is not None else None,
        "body_pct": round(body_pct, 4) if body_pct is not None else None,
        "volume_ratio_5": round(volume_ratio_5, 4) if volume_ratio_5 is not None else None,
        "volume_ratio_20": round(volume_ratio_20, 4) if volume_ratio_20 is not None else None,
        "volume_rank20_pct": round(volume_rank20 * 100, 4),
        "close_raised": bool(close > previous_close),
        "shrink_small_bear": shrink_small_bear,
        "large_bear": large_bear,
        "heavy_large_bear": heavy_large_bear,
        "last_two_bull_metrics": latest_bulls,
        "two_medium_large_bull": None,
        "two_medium_large_bull_reason": "缺少证券当日实际涨跌幅限制，不能按代码静态猜测中大阳门槛",
        "extreme_shrink": extreme_shrink,
        "reversal_k_candidate_without_j": reversal_k_candidate,
        "thresholds": {
            "medium_large_bull_rule": "单日涨幅或阳线实体幅度达到当日涨跌幅限制的一半",
            "small_bear_change_pct": [-2.0, 0.0],
            "shrink_volume_ratio_5_max": 0.8,
            "heavy_volume_ratio_5_min": 1.5,
            "reversal_volume_ratio_5_max": 0.5,
            "reversal_volume_rank20_pct_max": 10.0,
            "reversal_close_change_pct": [-2.0, 2.0],
            "reversal_amplitude_pct_max": 7.0,
        },
    }


def n_structure_state(df: pd.DataFrame, left: int = 3, right: int = 3) -> dict[str, Any]:
    """Find the latest rising-N structure using confirmed closing-price pivots.

    L1 is the major closing low, H1 the rebound closing high, and L2 the
    higher pullback closing low. L1 is the hard structural floor; L2 is the
    nearer tactical structure level. A later close above H1 confirms the N.
    """
    if len(df) < left + right + 8:
        return {"available": False, "reason": "K线数量不足以确认N型结构"}
    x = df.reset_index(drop=True)
    pivot_lows: list[int] = []
    pivot_highs: list[int] = []
    for i in range(left, len(x) - right):
        close_window = x["close"].iloc[i-left:i+right+1]
        close = float(x.at[i, "close"])
        if close == float(close_window.min()) and int((close_window == close).sum()) == 1:
            pivot_lows.append(i)
        if close == float(close_window.max()) and int((close_window == close).sum()) == 1:
            pivot_highs.append(i)

    latest = None
    for l2 in reversed(pivot_lows):
        prior_lows = [i for i in pivot_lows if i < l2]
        if not prior_lows:
            continue
        l1 = prior_lows[-1]
        highs = [i for i in pivot_highs if l1 < i < l2]
        if not highs:
            continue
        h1 = max(highs, key=lambda i: float(x.at[i, "close"]))
        if float(x.at[l2, "close"]) <= float(x.at[l1, "close"]):
            continue
        breakout_rows = x.index[(x.index > l2) & (x["close"] > float(x.at[h1, "close"]))]
        breakout = int(breakout_rows[0]) if len(breakout_rows) else None
        latest = (l1, h1, l2, breakout)
        break

    if latest is None:
        return {"available": False, "reason": "未发现已确认分型的上升N型结构"}
    l1, h1, l2, breakout = latest
    current_close = float(x["close"].iloc[-1])
    origin_low = float(x.at[l1, "close"])
    pullback_low = float(x.at[l2, "close"])
    swing_high = float(x.at[h1, "close"])
    origin_extreme_low = float(x["low"].iloc[max(0,l1-left):min(len(x),l1+right+1)].min())
    distance_pct = (current_close / origin_low - 1) * 100 if origin_low else None
    return {
        "available": True,
        "pattern": "L1-H1-higher_L2" + ("-breakout" if breakout is not None else "-candidate"),
        "status": "confirmed" if breakout is not None else "candidate",
        "prior_low": round(origin_low, 4),
        "prior_low_date": x.at[l1, "date"].strftime("%Y-%m-%d"),
        "origin_extreme_low": round(origin_extreme_low, 4),
        "breakout_level": round(swing_high, 4),
        "breakout_level_date": x.at[h1, "date"].strftime("%Y-%m-%d"),
        "pullback_low": round(pullback_low, 4),
        "pullback_low_date": x.at[l2, "date"].strftime("%Y-%m-%d"),
        "confirmed_date": x.at[breakout, "date"].strftime("%Y-%m-%d") if breakout is not None else None,
        "current_close": round(current_close, 4),
        "distance_pct": round(distance_pct, 4) if distance_pct is not None else None,
        "close_above": bool(current_close >= origin_low),
        "breached_on_close": bool(current_close < origin_low),
        "pullback_breached_on_close": bool(current_close < pullback_low),
        "pivot_window": {"left": left, "right": right},
    }


def trend_state(df: pd.DataFrame) -> dict[str, Any]:
    if len(df) < 60:
        return {"state": "数据不足", "reason": "少于60根K线"}
    close = df["close"]
    ma25 = close.rolling(25).mean()
    ma60 = close.rolling(60).mean()
    ma144 = close.rolling(144).mean()
    ma240 = close.rolling(240).mean()
    c = float(close.iloc[-1])
    ma25v, ma60v = float(ma25.iloc[-1]), float(ma60.iloc[-1])
    ma144v = float(ma144.iloc[-1]) if pd.notna(ma144.iloc[-1]) else None
    ma240v = float(ma240.iloc[-1]) if pd.notna(ma240.iloc[-1]) else None
    ma25_slope = slope(ma25.dropna(), 5)
    ma60_slope = slope(ma60.dropna(), 10)
    ma144_slope = slope(ma144.dropna(), 20)
    ma240_slope = slope(ma240.dropna(), 20)
    high20_now = float(df["high"].tail(20).max())
    high20_prev = float(df["high"].iloc[-40:-20].max()) if len(df) >= 40 else high20_now
    low20_now = float(df["low"].tail(20).min())
    low20_prev = float(df["low"].iloc[-40:-20].min()) if len(df) >= 40 else low20_now

    if c > ma25v > ma60v and (ma25_slope or 0) > 0 and high20_now >= high20_prev and low20_now >= low20_prev:
        state = "上涨"
    elif c < ma25v < ma60v and (ma25_slope or 0) < 0 and high20_now <= high20_prev and low20_now <= low20_prev:
        state = "下跌"
    else:
        state = "横盘震荡"
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
        "ma144_slope_20d_pct": round(ma144_slope, 4) if ma144_slope is not None else None,
        "ma240_slope_20d_pct": round(ma240_slope, 4) if ma240_slope is not None else None,
        "higher_high_20d": high20_now >= high20_prev,
        "higher_low_20d": low20_now >= low20_prev,
        "lower_high_20d": high20_now <= high20_prev,
        "lower_low_20d": low20_now <= low20_prev,
    }


def analyze(df: pd.DataFrame) -> dict[str, Any]:
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
        "n_structure": n_structure_state(df),
        "price_volume": price_volume_state(df),
        "box_20d": box(df, 20),
        "box_60d": box(df, 60),
        "daily": {"kdj": kdj(df), "macd": macd(df)},
        "weekly": {"kdj": kdj(weekly), "macd": macd(weekly)},
        "monthly": {"kdj": kdj(monthly), "macd": macd(monthly)},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True, help="证券/板块代码，如 600150 或 880xxx.SH")
    ap.add_argument("--name", default="")
    ap.add_argument("--date", default=pd.Timestamp.now().strftime("%Y-%m-%d"))
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    tcode = norm_code(args.code)
    result = {"code": tcode, "name": args.name, "analysis": analyze(read_vipdoc(tcode))}
    out = Path(args.out) if args.out else OUT_DIR / f"{args.date}_technical_{tcode.replace('.', '_')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:4000])


if __name__ == "__main__":
    main()
