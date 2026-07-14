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
