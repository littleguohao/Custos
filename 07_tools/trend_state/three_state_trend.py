# -*- coding: utf-8 -*-
"""Three-state stock trend classifier.

Classifies daily stock movement into:
- 震荡向上
- 横盘震荡
- 震荡向下

The classifier is intentionally transparent: it records every score component,
then applies a confirmation rule to reduce noisy state switching.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(r"C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team")
TDX_ROOT = Path(r"C:\new_tdx64")
OUT_DIR = BASE / "01_data" / "trend_state"


@dataclass(frozen=True)
class TrendConfig:
    ma_fast: int = 25
    ma_slow: int = 60
    ma_long: int = 144
    ma_year: int = 240
    ma_fast_slope_days: int = 5
    ma_slow_slope_days: int = 10
    structure_window: int = 20
    er_window: int = 20
    adx_window: int = 14
    min_bars: int = 80
    score_threshold: int = 4
    opposite_max: int = 1
    er_threshold: float = 0.25
    adx_threshold: float = 20.0
    confirm_days: int = 3


def normalize_code(code: str) -> str:
    s = str(code).strip().upper()
    if "." in s:
        return s
    if s.startswith(("920", "8", "4")):
        return f"{s}.BJ"
    if s.startswith(("6", "5", "9")):
        return f"{s}.SH"
    if s.startswith(("0", "1", "2", "3")):
        return f"{s}.SZ"
    return s


def _vipdoc_path(code: str) -> Path:
    tcode = normalize_code(code)
    raw, suffix = tcode.split(".")
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(suffix)
    if not prefix:
        raise ValueError(f"unsupported code suffix: {tcode}")
    return TDX_ROOT / "vipdoc" / prefix / "lday" / f"{prefix}{raw}.day"


def read_vipdoc_daily(code: str) -> pd.DataFrame:
    path = _vipdoc_path(code)
    if not path.exists():
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    raw = path.read_bytes()
    for i in range(0, len(raw), 32):
        chunk = raw[i : i + 32]
        if len(chunk) < 32:
            continue
        date, open_, high, low, close, amount, volume, _ = struct.unpack("IIIIIfII", chunk)
        rows.append(
            {
                "date": pd.to_datetime(str(date), format="%Y%m%d", errors="coerce"),
                "code": normalize_code(code),
                "open": open_ / 100,
                "high": high / 100,
                "low": low / 100,
                "close": close / 100,
                "amount": amount,
                "volume": volume,
                "source": "vipdoc",
            }
        )
    return _normalize_ohlcv(pd.DataFrame(rows))


def read_csv_daily(path: str | Path, code: str = "") -> pd.DataFrame:
    df = pd.read_csv(path)
    if code and "code" not in df.columns:
        df["code"] = normalize_code(code)
    return _normalize_ohlcv(df)


def fetch_tq_http_daily(
    code: str,
    count: int = 260,
    endpoint: str = "http://127.0.0.1:17709/",
    timeout: float = 5.0,
) -> pd.DataFrame:
    """Fetch daily K-line through local TQ HTTP service.

    The local service must be enabled in the TongDaXin client. The response
    shape can differ by client version, so the parser accepts common tabular
    and field-matrix layouts and raises a clear error otherwise.
    """
    tcode = normalize_code(code)
    payload = {
        "id": 1,
        "method": "get_market_data",
        "params": {
            "stock_list": [tcode],
            "count": count,
            "dividend_type": "front",
            "period": "1d",
        },
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"TQ HTTP unavailable: {exc}") from exc

    if data.get("error"):
        raise RuntimeError(f"TQ HTTP error: {data['error']}")
    return _parse_tq_result(data.get("result"), tcode)


def _parse_tq_result(result: Any, code: str) -> pd.DataFrame:
    if isinstance(result, list):
        return _normalize_ohlcv(pd.DataFrame(result))

    if not isinstance(result, dict):
        raise RuntimeError("unsupported TQ response: result is not a dict/list")

    lower_keys = {str(k).lower() for k in result.keys()}
    if {"date", "open", "high", "low", "close"}.issubset(lower_keys):
        return _normalize_ohlcv(pd.DataFrame(result))

    # Common field-matrix layout: {"Open": {date: {code: value}}, ...} or
    # {"Open": {code: {date: value}}, ...}. Keep parser defensive because
    # local client versions are not fully consistent.
    field_map = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
        "Amount": "amount",
    }
    frames: list[pd.Series] = []
    for src, dst in field_map.items():
        obj = result.get(src) or result.get(src.lower())
        if obj is None:
            continue
        df = pd.DataFrame(obj)
        series = None
        if code in df.columns:
            series = df[code]
        elif code in df.index:
            series = df.loc[code]
        elif len(df.columns) == 1:
            series = df.iloc[:, 0]
        if series is not None:
            series = series.rename(dst)
            frames.append(series)
    if not frames:
        raise RuntimeError("unsupported TQ response: no OHLCV fields found")
    out = pd.concat(frames, axis=1)
    out.index.name = "date"
    out = out.reset_index()
    out["code"] = code
    out["source"] = "tq_http"
    return _normalize_ohlcv(out)


def load_daily_data(source: str, code: str, count: int, csv_path: str = "") -> pd.DataFrame:
    if source == "tq-http":
        return fetch_tq_http_daily(code, count=count)
    if source == "vipdoc":
        return read_vipdoc_daily(code).tail(count)
    if source == "csv":
        if not csv_path:
            raise ValueError("--csv is required when --source csv")
        return read_csv_daily(csv_path, code).tail(count)
    if source == "auto":
        try:
            return fetch_tq_http_daily(code, count=count)
        except Exception:
            df = read_vipdoc_daily(code).tail(count)
            if not df.empty:
                return df
            if csv_path:
                return read_csv_daily(csv_path, code).tail(count)
            raise
    raise ValueError(f"unsupported source: {source}")


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    rename = {c: str(c).strip().lower() for c in df.columns}
    df = df.rename(columns=rename).copy()
    alias = {
        "datetime": "date",
        "time": "date",
        "vol": "volume",
    }
    df = df.rename(columns={k: v for k, v in alias.items() if k in df.columns})
    required = ["date", "open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"missing OHLCV columns: {missing}")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if "code" not in df.columns:
        df["code"] = ""
    if "source" not in df.columns:
        df["source"] = "csv"
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    keep = ["date", "code", "open", "high", "low", "close", "volume", "amount", "source"]
    return df[[c for c in keep if c in df.columns]].dropna(subset=required).sort_values("date").reset_index(drop=True)


def _slope_pct(series: pd.Series, days: int) -> pd.Series:
    prev = series.shift(days)
    return (series / prev - 1.0) * 100.0


def _efficiency_ratio(close: pd.Series, window: int) -> pd.Series:
    direction = (close - close.shift(window)).abs()
    volatility = close.diff().abs().rolling(window).sum()
    return direction / volatility.replace(0, pd.NA)


def _adx(df: pd.DataFrame, window: int) -> pd.DataFrame:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / window, adjust=False, min_periods=window).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / window, adjust=False, min_periods=window).mean() / atr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)) * 100
    adx = dx.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    return pd.DataFrame({"plus_di": plus_di, "minus_di": minus_di, "adx": adx})


def compute_raw_states(df: pd.DataFrame, cfg: TrendConfig) -> pd.DataFrame:
    if len(df) < cfg.min_bars:
        raise ValueError(f"insufficient bars: {len(df)} < {cfg.min_bars}")
    out = df.copy().reset_index(drop=True)
    close = out["close"]
    out["ma25"] = close.rolling(cfg.ma_fast).mean()
    out["ma60"] = close.rolling(cfg.ma_slow).mean()
    out["ma144"] = close.rolling(cfg.ma_long).mean()
    out["ma240"] = close.rolling(cfg.ma_year).mean()
    out["ma25_slope_5d_pct"] = _slope_pct(out["ma25"], cfg.ma_fast_slope_days)
    out["ma60_slope_10d_pct"] = _slope_pct(out["ma60"], cfg.ma_slow_slope_days)
    out["ma144_slope_20d_pct"] = _slope_pct(out["ma144"], 20)
    out["ma240_slope_20d_pct"] = _slope_pct(out["ma240"], 20)
    out["er20"] = _efficiency_ratio(close, cfg.er_window)
    out = pd.concat([out, _adx(out, cfg.adx_window)], axis=1)

    w = cfg.structure_window
    high_now = out["high"].rolling(w).max()
    high_prev = high_now.shift(w)
    low_now = out["low"].rolling(w).min()
    low_prev = low_now.shift(w)
    out["higher_high_20d"] = high_now >= high_prev
    out["higher_low_20d"] = low_now >= low_prev
    out["lower_high_20d"] = high_now <= high_prev
    out["lower_low_20d"] = low_now <= low_prev

    out["up_score"] = 0
    out["down_score"] = 0
    up_conditions = {
        "up_ma_order": out["close"].gt(out["ma25"]) & out["ma25"].gt(out["ma60"]),
        "up_ma25_slope": out["ma25_slope_5d_pct"].gt(0),
        "up_ma60_slope": out["ma60_slope_10d_pct"].gt(0),
        "up_structure": out["higher_high_20d"] & out["higher_low_20d"],
        "up_efficiency": out["er20"].gt(cfg.er_threshold),
        "up_di": out["plus_di"].gt(out["minus_di"]),
    }
    down_conditions = {
        "down_ma_order": out["close"].lt(out["ma25"]) & out["ma25"].lt(out["ma60"]),
        "down_ma25_slope": out["ma25_slope_5d_pct"].lt(0),
        "down_ma60_slope": out["ma60_slope_10d_pct"].lt(0),
        "down_structure": out["lower_high_20d"] & out["lower_low_20d"],
        "down_efficiency": out["er20"].gt(cfg.er_threshold),
        "down_di": out["minus_di"].gt(out["plus_di"]),
    }
    for name, cond in {**up_conditions, **down_conditions}.items():
        out[name] = cond.fillna(False)
    for name in up_conditions:
        out["up_score"] += out[name].astype(int)
    for name in down_conditions:
        out["down_score"] += out[name].astype(int)

    out["raw_state"] = "横盘震荡"
    up_mask = (out["up_score"] >= cfg.score_threshold) & (out["down_score"] <= cfg.opposite_max)
    down_mask = (out["down_score"] >= cfg.score_threshold) & (out["up_score"] <= cfg.opposite_max)
    out.loc[up_mask, "raw_state"] = "震荡向上"
    out.loc[down_mask, "raw_state"] = "震荡向下"
    out["trend_strength"] = out["adx"].apply(_strength_label)
    return out


def _strength_label(v: Any) -> str:
    if v is None or pd.isna(v):
        return "未知"
    x = float(v)
    if x >= 25:
        return "趋势较强"
    if x >= 20:
        return "趋势形成"
    return "趋势偏弱/震荡"


def apply_confirmation(states: pd.Series, confirm_days: int) -> pd.Series:
    confirmed: list[str] = []
    current = "横盘震荡"
    candidate = ""
    streak = 0
    for state in states.astype(str):
        if state == current:
            candidate = ""
            streak = 0
            confirmed.append(current)
            continue
        if state == candidate:
            streak += 1
        else:
            candidate = state
            streak = 1
        if streak >= confirm_days:
            current = candidate
            candidate = ""
            streak = 0
        confirmed.append(current)
    return pd.Series(confirmed, index=states.index)


def classify(df: pd.DataFrame, cfg: TrendConfig) -> tuple[dict[str, Any], pd.DataFrame]:
    series = compute_raw_states(df, cfg)
    series["confirmed_state"] = apply_confirmation(series["raw_state"], cfg.confirm_days)
    latest = series.iloc[-1]
    summary = {
        "available": True,
        "code": str(latest.get("code") or ""),
        "latest_date": latest["date"].strftime("%Y-%m-%d"),
        "state": latest["confirmed_state"],
        "raw_state": latest["raw_state"],
        "up_score": int(latest["up_score"]),
        "down_score": int(latest["down_score"]),
        "trend_strength": latest["trend_strength"],
        "close": _round(latest["close"]),
        "ma25": _round(latest["ma25"]),
        "ma60": _round(latest["ma60"]),
        "ma144": _round(latest["ma144"]),
        "ma240": _round(latest["ma240"]),
        "above_ma25": bool(latest["close"] > latest["ma25"]),
        "above_ma60": bool(latest["close"] > latest["ma60"]),
        "above_ma144": bool(latest["close"] > latest["ma144"]) if pd.notna(latest["ma144"]) else None,
        "above_ma240": bool(latest["close"] > latest["ma240"]) if pd.notna(latest["ma240"]) else None,
        "ma25_slope_5d_pct": _round(latest["ma25_slope_5d_pct"]),
        "ma60_slope_10d_pct": _round(latest["ma60_slope_10d_pct"]),
        "ma144_slope_20d_pct": _round(latest["ma144_slope_20d_pct"]),
        "ma240_slope_20d_pct": _round(latest["ma240_slope_20d_pct"]),
        "er20": _round(latest["er20"], 4),
        "adx14": _round(latest["adx"], 4),
        "plus_di": _round(latest["plus_di"], 4),
        "minus_di": _round(latest["minus_di"], 4),
        "rules": asdict(cfg),
    }
    return summary, series


def backtest_state_returns(series: pd.DataFrame, horizons: list[int]) -> dict[str, Any]:
    rows = []
    for horizon in horizons:
        future_ret = series["close"].shift(-horizon) / series["close"] - 1.0
        tmp = series.assign(future_ret=future_ret).dropna(subset=["future_ret"])
        for state, group in tmp.groupby("confirmed_state"):
            values = group["future_ret"]
            rows.append(
                {
                    "horizon_days": horizon,
                    "state": state,
                    "sample_count": int(values.count()),
                    "avg_return_pct": _round(values.mean() * 100, 4),
                    "median_return_pct": _round(values.median() * 100, 4),
                    "win_rate_pct": _round((values > 0).mean() * 100, 4),
                    "max_return_pct": _round(values.max() * 100, 4),
                    "min_return_pct": _round(values.min() * 100, 4),
                }
            )
    return {"horizons": horizons, "by_state": rows}


def _round(value: Any, digits: int = 4) -> float | None:
    if value is None or pd.isna(value):
        return None
    x = float(value)
    if math.isinf(x):
        return None
    return round(x, digits)


def save_outputs(code: str, summary: dict[str, Any], series: pd.DataFrame, backtest: dict[str, Any], out_dir: Path) -> dict[str, str]:
    safe_code = normalize_code(code).replace(".", "_")
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / f"{safe_code}_trend_summary.json"
    series_path = out_dir / f"{safe_code}_trend_series.csv"
    backtest_path = out_dir / f"{safe_code}_trend_backtest.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    backtest_path.write_text(json.dumps(backtest, ensure_ascii=False, indent=2), encoding="utf-8")
    export = series.copy()
    export["date"] = export["date"].dt.strftime("%Y-%m-%d")
    export.to_csv(series_path, index=False, encoding="utf-8-sig")
    return {"summary": str(summary_path), "series": str(series_path), "backtest": str(backtest_path)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Classify stock daily trend into three states")
    ap.add_argument("--code", required=True, help="stock code, e.g. 600150 or 600150.SH")
    ap.add_argument("--source", default="auto", choices=["auto", "tq-http", "vipdoc", "csv"])
    ap.add_argument("--csv", default="", help="CSV path for --source csv or auto fallback")
    ap.add_argument("--count", type=int, default=260)
    ap.add_argument("--confirm-days", type=int, default=3)
    ap.add_argument("--score-threshold", type=int, default=4)
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    args = ap.parse_args()

    cfg = TrendConfig(confirm_days=args.confirm_days, score_threshold=args.score_threshold)
    df = load_daily_data(args.source, args.code, args.count, args.csv)
    if df.empty:
        raise SystemExit(f"no data loaded for {args.code}")
    summary, series = classify(df, cfg)
    bt = backtest_state_returns(series, horizons=[1, 3, 5, 10, 20])
    paths = save_outputs(args.code, summary, series, bt, Path(args.out_dir))
    print(json.dumps({"summary": summary, "outputs": paths, "backtest": bt}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


