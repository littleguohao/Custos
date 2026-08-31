# -*- coding: utf-8 -*-
"""Refresh a_share_indices and turnover in market_timing_input.json from vipdoc.

This runs before final_close_review to ensure index data is current,
even if the 08:50 collector failed (e.g. TdxW not running at that time).

Preserves all other fields in market_timing_input.json (AMV, macro, etc.).
"""

from __future__ import annotations

import json
import math


from custos.core.paths import MARKET_DIR, write_json_atomic  # noqa: E402
from custos.core.contracts import require  # noqa: E402
from custos.core.indicators import pct_change as pct  # noqa: E402


from custos.datasource.local_tdx import local_tdx_data as ltd  # type: ignore

from custos.datasource.breadth_basis import breadth_counts_real  # noqa: E402

INDICES = {
    "上证指数": "999999.SH",
    "创业板指": "399006.SZ",
    "科创50": "000688.SH",
    "北证50": "899050.BJ",
}

# Market breadth/sentiment codes
BREADTH_CODE = "880005.SH"  # close=涨家数, open=涨家数(开盘)
SENTIMENT_CODE = "880006.SH"  # close=涨停数(收盘), high=涨停数(最高), low=跌停数(最低)
# 跌家数口径见 breadth_basis：原先这里有 TOTAL_STOCKS_APPROX = 5530 硬编码近似总数推算
# 跌家数（把平盘/停牌计入下跌 ⇒ up_down_ratio 系统性偏低，而 scorer 直接吃这个比值）。


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


def _is_stale(as_of, target_date) -> bool:
    """as_of（2026-07-17 或 20260717 形态）早于 --date 即视为过期，需要刷新。"""
    s = str(as_of or "").replace("-", "").replace("/", "")[:8]
    return s < target_date.replace("-", "")


def _index_rows(code: str) -> list[dict]:
    """从 vipdoc 读 K 线并压成按日期升序的 {date, close, amount, volume} 行。"""
    df = ltd.get_ohlcv_table(code, count=260, prefer="vipdoc")
    if df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        dt = r.get("date")
        rows.append(
            {
                "date": dt.strftime("%Y%m%d") if hasattr(dt, "strftime") else str(dt),
                "close": to_float(r.get("close")),
                "amount": to_float(r.get("amount")),
                "volume": to_float(r.get("volume")),
            }
        )
    return sorted(rows, key=lambda x: x["date"])


def _ma_fields(closes: list, latest_close: float) -> dict:
    """MA25/60/144/240 与 above_maX 标志。

    ⚠️ 数据不够长时必须给 None 而不是 False：False 会被 market_timing_scorer
    当成「跌破 MA」扣分，把「算不出」当「跌破」方向偏空。
    """
    ma25 = sum(closes[-25:]) / 25 if len(closes) >= 25 else None
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None
    ma144 = sum(closes[-144:]) / 144 if len(closes) >= 144 else None
    ma240 = sum(closes[-240:]) / 240 if len(closes) >= 240 else None
    return {
        "ma25": round(ma25, 4) if ma25 else None,
        "ma60": round(ma60, 4) if ma60 else None,
        "ma144": round(ma144, 4) if ma144 else None,
        "ma240": round(ma240, 4) if ma240 else None,
        "above_ma25": bool(latest_close > ma25) if ma25 else None,
        "above_ma60": bool(latest_close > ma60) if ma60 else None,
        "above_ma144": bool(latest_close > ma144) if ma144 else None,
        "above_ma240": bool(latest_close > ma240) if ma240 else None,
    }


def compute_index(code: str) -> dict:
    """Compute index trend from vipdoc K-line data."""
    rows = _index_rows(code)
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
        **_ma_fields(closes, latest_close),
        "daily_amount": latest_amount,
    }


def _refresh_indices(mkt: dict, date: str) -> tuple[bool, int]:
    """刷新 a_share_indices 里缺失/陈旧的指数。返回 (是否改写, 刷了几只)。"""
    existing = mkt.get("a_share_indices", {})
    updated = False
    indices_fixed = 0

    # Refresh each index
    for name, code in INDICES.items():
        cur = existing.get(name, {})
        # Only refresh if not available, missing daily_change_pct, or stale (latest_date < --date)
        if (
            cur.get("available")
            and cur.get("daily_change_pct") is not None
            and not _is_stale(cur.get("latest_date"), date)
        ):
            continue
        fresh = compute_index(code)
        if fresh.get("available"):
            # Preserve intraday data if it existed —— 14:45 链 collect_intraday_snapshot
            # 回填的真实盘中值（或 collector 的占位），vipdoc K 线刷新不得覆盖它。
            if "intraday" in cur:
                fresh["intraday"] = cur["intraday"]
            existing[name] = fresh
            updated = True
            indices_fixed += 1
            print(
                f"[OK] {name}: close={fresh['latest_close']}, change={fresh.get('daily_change_pct')}%"
            )

    if updated:
        mkt["a_share_indices"] = existing
    return updated, indices_fixed


def _refresh_turnover_from_index(mkt: dict) -> bool:
    """Compute turnover from 上证指数 daily_amount if turnover is missing."""
    turnover = mkt.get("turnover", {})
    if not turnover or turnover.get("quality") in (None, "missing", ""):
        sh = mkt.get("a_share_indices", {}).get("上证指数", {})
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
            print(f"[OK] turnover: {daily_amount} (from 上证指数)")
            return True
    return False


def _refresh_turnover_full_market(mkt: dict, date: str) -> bool:
    """Also try 880001 for full-market turnover and turnover_change_pct."""
    turnover_needs_fix = (
        not mkt.get("turnover")
        or mkt.get("turnover", {}).get("turnover_change_pct") is None
        or _is_stale(mkt.get("turnover", {}).get("as_of"), date)
    )
    if not turnover_needs_fix:
        return False
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
                print(f"[OK] turnover: {amt} (from 880001), change_pct={chg_pct}")
                return True
    except Exception as e:
        print(f"[WARN] 880001 fetch failed: {e}")
    return False


def _refresh_breadth(mkt: dict, date: str) -> bool:
    """Refresh market breadth (涨跌家数) from 880005.SH."""
    breadth = mkt.get("market_breadth", {})
    if (
        not breadth
        or breadth.get("quality") in (None, "missing", "")
        or breadth.get("up_count") is None
        # v0.137：跌家数缺失（旧 unavailable 遗留）也要补
        or breadth.get("down_count") is None
        or _is_stale(breadth.get("as_of"), date)
    ):
        try:
            df_bd = ltd.get_ohlcv_table(BREADTH_CODE, count=3, prefer="vipdoc")
            if not df_bd.empty:
                last_bd = df_bd.iloc[-1]
                up_count = to_float(last_bd.get("close"))
                bd_date = str(last_bd.get("date", ""))
                # 跌/平/停家数由 vipdoc 本地自算真值口径（v0.137，见 breadth_basis
                # .compute_breadth_from_vipdoc）；自算失败才回落总数推算/unavailable。
                counts = breadth_counts_real(
                    int(up_count) if up_count else None,
                    date=bd_date[:10] if bd_date else None,
                )
                mkt["market_breadth"] = {
                    "up_count": int(up_count) if up_count else None,
                    "down_count": counts["down_count"],
                    "flat_count": counts["flat_count"],
                    "suspended_count": counts["suspended_count"],
                    "up_down_ratio": counts["up_down_ratio"],
                    "up_down_ratio_status": counts["up_down_ratio_status"],
                    "total_stocks": counts["total_stocks"],
                    "total_stocks_source": counts["total_stocks_source"],
                    # 自算桶数据日（机器可读；as_of 是 880005 官方涨家数数据日）
                    "vipdoc_as_of": counts["vipdoc_as_of"],
                    "note": counts["note"],
                    "source": "vipdoc_880005",
                    "quality": "auto",
                    "as_of": bd_date[:10] if bd_date else "",
                }
                print(
                    f"[OK] market_breadth: up={int(up_count) if up_count else 'N/A'}, "
                    f"down={counts['down_count'] if counts['down_count'] is not None else 'unavailable'} "
                    f"(from 880005, ratio={counts['up_down_ratio_status']})"
                )
                return True
        except Exception as e:
            print(f"[WARN] 880005 breadth fetch failed: {e}")
    return False


def _refresh_sentiment(mkt: dict, date: str) -> bool:
    """Refresh sentiment (涨跌停) from 880006.SH."""
    sentiment = mkt.get("sentiment", {})
    if (
        not sentiment
        or sentiment.get("quality") in (None, "missing", "")
        or sentiment.get("limit_up_count") is None
        or _is_stale(sentiment.get("as_of"), date)
    ):
        try:
            df_st = ltd.get_ohlcv_table(SENTIMENT_CODE, count=3, prefer="vipdoc")
            if not df_st.empty:
                last_st = df_st.iloc[-1]
                limit_up_close = to_float(last_st.get("close"))
                limit_up_max = to_float(last_st.get("high"))
                limit_down_max = to_float(last_st.get("low"))
                once_up = limit_up_max if limit_up_max else None
                blowup = (
                    round((once_up - limit_up_close) / once_up, 4)
                    if once_up and limit_up_close is not None and once_up
                    else None
                )
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
                print(
                    f"[OK] sentiment: limit_up={int(limit_up_close) if limit_up_close else 'N/A'}, once_up={int(once_up) if once_up else 'N/A'}, limit_down={int(limit_down_max) if limit_down_max else 'N/A'} (from 880006)"
                )
                return True
        except Exception as e:
            print(f"[WARN] 880006 sentiment fetch failed: {e}")
    return False


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()

    market_path = MARKET_DIR / f"{args.date}_market_timing_input.json"
    if not market_path.exists():
        print(f"[SKIP] market_timing_input.json not found for {args.date}")
        return

    mkt = json.loads(market_path.read_text(encoding="utf-8"))

    updated, indices_fixed = _refresh_indices(mkt, args.date)
    # 顺序敏感：先上证窄口径兜底，再由 880001 全市场口径覆盖（两段填同一个键）
    if _refresh_turnover_from_index(mkt):
        updated = True
    if _refresh_turnover_full_market(mkt, args.date):
        updated = True
    if _refresh_breadth(mkt, args.date):
        updated = True
    if _refresh_sentiment(mkt, args.date):
        updated = True

    if updated:
        # ⚠️ 落盘前校验：本 stage 只刷新 a_share_indices/turnover/breadth/sentiment
        # 四节，责任范围就限它们（only 语义见 contracts._narrow）；
        # 四节的 as_of 键本模块恒写（契约「补 as_of 必填」前置普查的结论）。
        require(
            "market_timing_input",
            mkt,
            only=("a_share_indices", "turnover", "market_breadth", "sentiment"),
        )
        # 读-改-写的共享文件 ⇒ 原子写
        write_json_atomic(market_path, mkt)
        print(
            f"[DONE] {indices_fixed} indices refreshed, market_timing_input.json updated"
        )
    else:
        print("[SKIP] all indices already available, no refresh needed")


if __name__ == "__main__":
    main()
