# -*- coding: utf-8 -*-
"""Refresh a_share_indices and turnover in market_timing_input.json from vipdoc.

This runs before final_close_review to ensure index data is current,
even if the 08:50 collector failed (e.g. TdxW not running at that time).

Preserves all other fields in market_timing_input.json (AMV, macro, etc.).
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
LOCAL_TDX_DIR = BASE / "07_tools" / "local_tdx"
if str(LOCAL_TDX_DIR) not in sys.path:
    sys.path.insert(0, str(LOCAL_TDX_DIR))

import local_tdx_data as ltd  # type: ignore

INDICES = {
    "上证指数": "999999.SH",
    "创业板指": "399006.SZ",
    "科创50": "000688.SH",
    "北证50": "899050.BJ",
}

# Market breadth/sentiment codes
BREADTH_CODE = "880005.SH"   # close=涨家数, open=涨家数(开盘)
SENTIMENT_CODE = "880006.SH" # close=涨停数(收盘), high=涨停数(最高), low=跌停数(最低)
TOTAL_STOCKS_APPROX = 5530   # A股总数近似值（用于计算跌家数）


def to_float(x):
    try:
        if x is None:
            return None
        s = str(x).strip()
        if s in ("", "--", "nan", "None"):
            return None
        v = float(s)
        return None if math.isnan(v) else v
    except Exception:
        return None


def pct(a, b):
    if b in (None, 0) or a is None:
        return None
    return round((a / b - 1) * 100, 4)


def compute_index(code: str) -> dict:
    """Compute index trend from vipdoc K-line data."""
    df = ltd.get_ohlcv_table(code, count=260, prefer="vipdoc")
    if df.empty:
        return {"available": False, "source": "vipdoc_day"}

    rows = []
    for _, r in df.iterrows():
        dt = r.get("date")
        rows.append({
            "date": dt.strftime("%Y%m%d") if hasattr(dt, "strftime") else str(dt),
            "close": to_float(r.get("close")),
            "amount": to_float(r.get("amount")),
            "volume": to_float(r.get("volume")),
        })
    rows = sorted(rows, key=lambda x: x["date"])
    if not rows:
        return {"available": False, "source": "vipdoc_day"}

    closes = [r["close"] for r in rows if r["close"] is not None]
    amounts = [r["amount"] for r in rows if r["amount"] is not None]
    if len(closes) < 2:
        return {"available": False, "source": "vipdoc_day"}

    latest_close = closes[-1]
    prev_close = closes[-2] if len(closes) >= 2 else None
    daily_change_pct = pct(latest_close, prev_close)

    def close_n(n):
        return closes[-1 - n] if len(closes) > n else None

    ma25 = sum(closes[-25:]) / 25 if len(closes) >= 25 else None
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None
    ma144 = sum(closes[-144:]) / 144 if len(closes) >= 144 else None
    ma240 = sum(closes[-240:]) / 240 if len(closes) >= 240 else None

    latest_date = rows[-1]["date"]
    latest_amount = amounts[-1] if amounts else None

    return {
        "available": True,
        "source": "vipdoc_day",
        "latest_date": latest_date,
        "latest_close": round(latest_close, 4),
        "daily_change_pct": daily_change_pct,
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
        "daily_amount": latest_amount,
    }


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()

    market_path = BASE / "01_data" / "market" / f"{args.date}_market_timing_input.json"
    if not market_path.exists():
        print(f"[SKIP] market_timing_input.json not found for {args.date}")
        return

    mkt = json.loads(market_path.read_text(encoding="utf-8"))
    existing = mkt.get("a_share_indices", {})
    updated = False
    indices_fixed = 0

    # Refresh each index
    for name, code in INDICES.items():
        cur = existing.get(name, {})
        # Only refresh if not available or missing daily_change_pct
        if cur.get("available") and cur.get("daily_change_pct") is not None:
            continue
        fresh = compute_index(code)
        if fresh.get("available"):
            # Preserve intraday data if it existed
            if "intraday" in cur:
                fresh["intraday"] = cur["intraday"]
            existing[name] = fresh
            updated = True
            indices_fixed += 1
            print(f"[OK] {name}: close={fresh['latest_close']}, change={fresh.get('daily_change_pct')}%")

    if updated:
        mkt["a_share_indices"] = existing

    # Compute turnover from 上证指数 daily_amount if turnover is missing
    turnover = mkt.get("turnover", {})
    if not turnover or turnover.get("quality") in (None, "missing", ""):
        sh = existing.get("上证指数", {})
        daily_amount = sh.get("daily_amount")
        if daily_amount:
            mkt["turnover"] = {
                "quality": "auto",
                "as_of": sh.get("latest_date", ""),
                "value": daily_amount,
                "source": "vipdoc_000001_amount",
                "note": "上证指数当日成交额(元)，全市场口径需另采880001",
            }
            mkt.setdefault("market_turnover", mkt["turnover"])
            updated = True
            print(f"[OK] turnover: {daily_amount} (from 上证指数)")

    # Also try 880001 for full-market turnover and turnover_change_pct
    turnover_needs_fix = not mkt.get("turnover") or mkt.get("turnover", {}).get("turnover_change_pct") is None
    if turnover_needs_fix:
        try:
            df_880001 = ltd.get_ohlcv_table("880001.SH", count=5, prefer="vipdoc")
            if not df_880001.empty:
                last_row = df_880001.iloc[-1]
                amt = to_float(last_row.get("amount"))
                dt = str(last_row.get("date", ""))
                # Calculate change pct from previous day
                prev_amt = None
                if len(df_880001) >= 2:
                    prev_amt = to_float(df_880001.iloc[-2].get("amount"))
                chg_pct = pct(amt, prev_amt) if amt and prev_amt else None
                if amt:
                    mkt["turnover"] = {
                        "total_turnover": amt,
                        "turnover_change_pct": chg_pct,
                        "quality": "auto",
                        "as_of": dt,
                        "source": "vipdoc_880001_amount",
                        "note": "全市场成交额(元)及环比变化率，来自880001.SH vipdoc",
                    }
                    mkt["market_turnover"] = {
                        "quality": "auto",
                        "as_of": dt,
                        "value": amt,
                        "source": "vipdoc_880001_amount",
                    }
                    updated = True
                    print(f"[OK] turnover: {amt} (from 880001), change_pct={chg_pct}")
        except Exception as e:
            print(f"[WARN] 880001 fetch failed: {e}")

    # Refresh market breadth (涨跌家数) from 880005.SH
    breadth = mkt.get("market_breadth", {})
    if not breadth or breadth.get("quality") in (None, "missing", "") or breadth.get("up_count") is None:
        try:
            df_bd = ltd.get_ohlcv_table(BREADTH_CODE, count=3, prefer="vipdoc")
            if not df_bd.empty:
                last_bd = df_bd.iloc[-1]
                up_count = to_float(last_bd.get("close"))
                down_count = TOTAL_STOCKS_APPROX - up_count if up_count is not None else None
                bd_date = str(last_bd.get("date", ""))
                mkt["market_breadth"] = {
                    "up_count": int(up_count) if up_count else None,
                    "down_count": int(down_count) if down_count else None,
                    "up_down_ratio": round(up_count / down_count, 4) if up_count and down_count else None,
                    "total_stocks": TOTAL_STOCKS_APPROX,
                    "source": "vipdoc_880005",
                    "quality": "auto",
                    "as_of": bd_date[:10] if bd_date else "",
                }
                updated = True
                print(f"[OK] market_breadth: up={int(up_count) if up_count else 'N/A'}, down={int(down_count) if down_count else 'N/A'} (from 880005)")
        except Exception as e:
            print(f"[WARN] 880005 breadth fetch failed: {e}")

    # Refresh sentiment (涨跌停) from 880006.SH
    sentiment = mkt.get("sentiment", {})
    if not sentiment or sentiment.get("quality") in (None, "missing", "") or sentiment.get("limit_up_count") is None:
        try:
            df_st = ltd.get_ohlcv_table(SENTIMENT_CODE, count=3, prefer="vipdoc")
            if not df_st.empty:
                last_st = df_st.iloc[-1]
                limit_up_close = to_float(last_st.get("close"))
                limit_up_max = to_float(last_st.get("high"))
                limit_down_max = to_float(last_st.get("low"))
                once_up = limit_up_max if limit_up_max else None
                blowup = round((once_up - limit_up_close) / once_up, 4) if once_up and limit_up_close is not None and once_up else None
                st_date = str(last_st.get("date", ""))
                mkt["sentiment"] = {
                    "limit_up_count": int(limit_up_close) if limit_up_close else None,
                    "once_limit_up_count": int(once_up) if once_up else None,
                    "limit_down_count": int(limit_down_max) if limit_down_max else None,
                    "blowup_rate": blowup,
                    "market_height": None,
                    "above_2_board_count": None,
                    "source": "vipdoc_880006",
                    "quality": "auto",
                    "as_of": st_date[:10] if st_date else "",
                }
                updated = True
                print(f"[OK] sentiment: limit_up={int(limit_up_close) if limit_up_close else 'N/A'}, once_up={int(once_up) if once_up else 'N/A'}, limit_down={int(limit_down_max) if limit_down_max else 'N/A'} (from 880006)")
        except Exception as e:
            print(f"[WARN] 880006 sentiment fetch failed: {e}")

    if updated:
        market_path.write_text(json.dumps(mkt, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[DONE] {indices_fixed} indices refreshed, market_timing_input.json updated")
    else:
        print("[SKIP] all indices already available, no refresh needed")


if __name__ == "__main__":
    main()
