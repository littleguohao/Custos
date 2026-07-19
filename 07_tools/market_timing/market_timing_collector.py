# -*- coding: utf-8 -*-
"""market_timing daily input collector v4.

Phase 1 collector (08:50, pre-open):
- auto: local TongDaXin vipdoc daily files for key index trends
- auto: local vipdoc 880-series for market breadth / sentiment / turnover
  (previous trading day's EOD — no intraday data exists at 08:50)
- manual placeholders: macro policy, 0AMV, overseas, theme

The old TDX TQ snapshot path was removed: tqcenter is deprecated and the
TqSession stub raised unconditionally, leaving breadth/sentiment/turnover
permanently missing. Intraday index snapshots are no longer collected here;
the intraday field is annotated as pending intraday/post-close flows.

Usage:
python market_timing_collector.py --date 2026-07-09 --amv 1.2
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
LOCAL_TDX_DIR = TOOLS_DIR / "local_tdx"
if str(LOCAL_TDX_DIR) not in sys.path:
    sys.path.insert(0, str(LOCAL_TDX_DIR))

import local_tdx_data as ltd  # type: ignore
from paths import BASE, TDX_ROOT  # noqa: E402
from runtime_guards import previous_confirmed_trading_day  # noqa: E402

OUT_DIR = BASE / "01_data" / "market"

INDICES = {
    "上证指数": {"prefix": "sh", "code": "999999"},
    "创业板指": {"prefix": "sz", "code": "399006"},
    "科创50": {"prefix": "sh", "code": "000688"},
    "北证50": {"prefix": "bj", "code": "899050"},
}

# vipdoc 880-series market-wide statistics (same codes as refresh_market_indices.py)
BREADTH_CODE = "880005.SH"    # close=上涨家数
SENTIMENT_CODE = "880006.SH"  # close=涨停数, high=盘中曾涨停数, low=跌停数
TURNOVER_CODE = "880001.SH"   # amount=全市场成交额(元)
TOTAL_STOCKS_APPROX = 5530    # A股总数近似值（用于由涨家数推算跌家数）


def to_float(x: Any):
    try:
        if x is None:
            return None
        s = str(x).strip()
        if s in ("", "--", "nan", "None"):
            return None
        v = float(s)
        if math.isnan(v):
            return None
        return v
    except Exception:
        return None


def read_day(prefix: str, code: str) -> list[dict]:
    """Read daily K lines through the unified local_tdx_data layer.

    prefix is kept for backward compatibility with old callers.
    """
    tdx_code = {"sh": f"{code}.SH", "sz": f"{code}.SZ", "bj": f"{code}.BJ"}.get(prefix, code)
    df = ltd.get_ohlcv_table(tdx_code, count=260, prefer="vipdoc")
    if df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        dt = r.get("date")
        rows.append({
            "date": dt.strftime("%Y%m%d") if hasattr(dt, "strftime") else str(dt),
            "open": to_float(r.get("open")),
            "high": to_float(r.get("high")),
            "low": to_float(r.get("low")),
            "close": to_float(r.get("close")),
            "amount": to_float(r.get("amount")),
            "volume": to_float(r.get("volume")),
        })
    return rows


def pct(a, b):
    if b in (None, 0) or a is None:
        return None
    return round((a / b - 1) * 100, 4)


def trend(rows: list[dict]) -> dict:
    if not rows:
        return {"available": False, "source": "vipdoc_day"}
    rows = sorted(rows, key=lambda r: r["date"])
    latest = rows[-1]
    closes = [r["close"] for r in rows]
    latest_close = closes[-1]

    def close_n(n):
        return closes[-1-n] if len(closes) > n else None

    ma25 = sum(closes[-25:]) / 25 if len(closes) >= 25 else None
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None
    ma144 = sum(closes[-144:]) / 144 if len(closes) >= 144 else None
    ma240 = sum(closes[-240:]) / 240 if len(closes) >= 240 else None
    return {
        "available": True,
        "source": "vipdoc_day",
        "latest_date": latest["date"],
        "latest_close": round(latest_close, 4),
        "change_5d_pct": pct(latest_close, close_n(5)),
        "change_20d_pct": pct(latest_close, close_n(20)),
        "change_60d_pct": pct(latest_close, close_n(60)),
        "ma25": round(ma25, 4) if ma25 else None,
        "ma60": round(ma60, 4) if ma60 else None,
        "ma144": round(ma144, 4) if ma144 else None,
        "ma240": round(ma240, 4) if ma240 else None,
        "above_ma25": bool(latest_close > ma25) if ma25 else None,
        "above_ma60": bool(latest_close > ma60) if ma60 else None,
        "above_ma144": bool(latest_close > ma144) if ma144 else None,
        "above_ma240": bool(latest_close > ma240) if ma240 else None,
    }


def amv_zone(v):
    if v is None:
        return ""
    if v > 4:
        return "做多"
    if v < -2.3:
        return "空头"
    return "中性"


_mkt_reader = None


def _get_mkt_reader():
    """mootdx Reader for vipdoc 880-series.

    Note: ltd.read_vipdoc_daily also works for suffix-carrying 880xxx.SH
    codes (_is_bj_code respects explicit suffixes), but this module keeps
    its own direct mootdx Reader — the same pattern as
    collect_incremental_market.py.
    """
    global _mkt_reader
    if _mkt_reader is None:
        from mootdx.reader import Reader
        _mkt_reader = Reader.factory(market="std", tdxdir=str(TDX_ROOT))
    return _mkt_reader


def _vipdoc_rows(code: str, count: int = 5) -> list[dict]:
    raw = code.split(".")[0]
    df = _get_mkt_reader().daily(symbol=raw)
    if df is None or df.empty:
        return []
    rows = []
    for idx, r in df.tail(count).iterrows():
        rows.append({
            "date": idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10],
            "high": to_float(r.get("high")),
            "low": to_float(r.get("low")),
            "close": to_float(r.get("close")),
            "amount": to_float(r.get("amount")),
        })
    return sorted(rows, key=lambda r: r["date"])


def _freshness(as_of: str, expected: str | None, label: str, quality: dict) -> str:
    """quality=auto when data date matches the expected previous trading day."""
    if expected is not None and as_of == expected:
        return "auto"
    quality["notes"].append(f"{label} 最新数据日期 {as_of or '无'} 与预期前一交易日 {expected or '无法确认'} 不一致（周末/假日/vipdoc 未更新或已含更新数据），标记 degraded。")
    return "degraded"


def derive_market_fields(target_date: str) -> tuple[dict, dict, dict, dict]:
    """Fill breadth/sentiment/turnover from local vipdoc 880-series EOD data.

    Honest labeling: at 08:50 the latest available bar is the previous trading
    day's close, so each section carries as_of (actual data date) and is marked
    "auto" when fresh vs the trading calendar, "degraded" when vipdoc lags.
    """
    expected = previous_confirmed_trading_day(target_date)
    quality = {
        "notes": [
            "market_breadth/sentiment/turnover 来自本地 vipdoc 880 系列前一交易日 EOD 数据；08:50 盘前无当日盘中数据。",
            "TQ 快照路径已移除（tqcenter 废弃）；指数盘中快照待盘中/盘后流程填充。",
        ],
        "sources": ["vipdoc_880_series"],
        "expected_data_date": expected,
    }

    breadth = {"up_count": None, "down_count": None, "up_down_ratio": None,
               "source": None, "quality": "missing", "as_of": None}
    try:
        rows = _vipdoc_rows(BREADTH_CODE)
        if rows:
            last = rows[-1]
            up = last["close"]
            as_of = last["date"]
            if up is not None:
                down = TOTAL_STOCKS_APPROX - up
                breadth.update({
                    "up_count": int(up),
                    "down_count": int(down),
                    "up_down_ratio": round(up / down, 4) if down else None,
                    "total_stocks_approx": TOTAL_STOCKS_APPROX,
                    "source": "vipdoc_880005",
                    "as_of": as_of,
                    "quality": _freshness(as_of, expected, "880005 涨跌家数", quality),
                })
    except Exception as e:
        quality["notes"].append(f"880005 涨跌家数读取失败: {e!r}")

    sentiment = {"limit_up_count": None, "limit_down_count": None,
                 "once_limit_up_count": None, "once_limit_down_count": None,
                 "blowup_rate": None, "market_height": None, "above_2_board_count": None,
                 "source": None, "quality": "missing", "as_of": None}
    try:
        rows = _vipdoc_rows(SENTIMENT_CODE)
        if rows:
            last = rows[-1]
            limit_up = last["close"]
            once_up = last["high"]
            limit_down = last["low"]
            as_of = last["date"]
            if limit_up is not None:
                sentiment.update({
                    "limit_up_count": int(limit_up),
                    "once_limit_up_count": int(once_up) if once_up is not None else None,
                    "limit_down_count": int(limit_down) if limit_down is not None else None,
                    "blowup_rate": round((once_up - limit_up) / once_up, 4) if once_up else None,
                    "source": "vipdoc_880006",
                    "as_of": as_of,
                    "quality": _freshness(as_of, expected, "880006 涨跌停", quality),
                })
                quality["notes"].append("连板高度/2板以上家数无法从 880006 获取，market_height/above_2_board_count 留空待人工或盘后填充。")
    except Exception as e:
        quality["notes"].append(f"880006 涨跌停读取失败: {e!r}")

    turnover = {"total_turnover": None, "turnover_change_pct": None, "volume_summary": "",
                "source": None, "quality": "missing", "as_of": None}
    try:
        rows = _vipdoc_rows(TURNOVER_CODE)
        if rows:
            last = rows[-1]
            amt = last["amount"]
            as_of = last["date"]
            prev_amt = rows[-2]["amount"] if len(rows) >= 2 else None
            if amt is not None:
                turnover.update({
                    "total_turnover": amt,
                    "turnover_change_pct": pct(amt, prev_amt),
                    "source": "vipdoc_880001",
                    "as_of": as_of,
                    "quality": _freshness(as_of, expected, "880001 成交额", quality),
                    "volume_summary": f"全市场成交额(元)来自 880001.SH vipdoc，数据日期 {as_of}（前一交易日 EOD）。",
                })
    except Exception as e:
        quality["notes"].append(f"880001 成交额读取失败: {e!r}")

    return breadth, sentiment, turnover, quality


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--amv", type=float, default=None, help="0AMV 当日涨跌幅，百分比")
    ap.add_argument("--out", default="", help="可选输出路径；为空则写入正式 market_timing_input.json")
    args = ap.parse_args()

    breadth, sentiment, turnover, quality = derive_market_fields(args.date)

    data = {
        "date": args.date,
        "collector_version": "market_timing_collector_v4_vipdoc_880",
        "macro_policy": {
            "monetary_policy": "",
            "fiscal_policy": "",
            "credit_environment": "",
            "regulation_environment": "",
            "policy_summary": ""
        },
        "amv_0": {
            "amv_change_pct": args.amv,
            "amv_zone": amv_zone(args.amv),
            "note": "0AMV > 4% = 做多；0AMV < -2.3% = 空头"
        },
        "overseas_market": {
            "nasdaq_change_pct": None,
            "sp500_change_pct": None,
            "sox_change_pct": None,
            "nikkei_change_pct": None,
            "kospi_change_pct": None,
            "hstech_change_pct": None,
            "overseas_summary": ""
        },
        "a_share_indices": {},
        "market_breadth": breadth,
        "sentiment": sentiment,
        "turnover": turnover,
        "theme": {"main_themes": [], "theme_clarity": "", "theme_summary": ""},
        "data_quality": quality,
    }

    for name, meta in INDICES.items():
        item = trend(read_day(meta["prefix"], meta["code"]))
        item["intraday"] = {
            "available": False,
            "note": "08:50 盘前采集无当日盘中快照；TQ 快照路径已移除，盘中字段待盘中/盘后流程填充。",
        }
        data["a_share_indices"][name] = item

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else OUT_DIR / f"{args.date}_market_timing_input.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
