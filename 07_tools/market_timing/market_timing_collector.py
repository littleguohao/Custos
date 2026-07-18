# -*- coding: utf-8 -*-
"""market_timing daily input collector v2.

Phase 1 collector:
- auto: local TongDaXin vipdoc daily files for key index trends
- auto/best-effort: TDX TQ market snapshots for intraday index moves, turnover, breadth-like fields
- auto/best-effort: TQ SC market fields when available
- manual placeholders: macro policy, 0AMV, overseas, theme

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

BASE = Path(__file__).resolve().parents[2]
LOCAL_TDX_DIR = BASE / "07_tools" / "local_tdx"
if str(LOCAL_TDX_DIR) not in sys.path:
    sys.path.insert(0, str(LOCAL_TDX_DIR))

import local_tdx_data as ltd  # type: ignore

TDX_ROOT = Path(os.environ.get("TDX_ROOT", r"E:\new_tdx64"))
OUT_DIR = BASE / "01_data" / "market"

INDICES = {
    "上证指数": {"prefix": "sh", "code": "999999", "tq": "999999.SH"},
    "创业板指": {"prefix": "sz", "code": "399006", "tq": "399006.SZ"},
    "科创50": {"prefix": "sh", "code": "000688", "tq": "000688.SH"},
    "北证50": {"prefix": "bj", "code": "899050", "tq": "899050.BJ"},
}

# TDX all-market style indices observed in the local client. Meanings can vary by installation;
# keep raw snapshots and only use conservative fields.
MARKET_SNAPSHOT_CODES = {
    "通达信全A_候选1": "880003.SH",
    "通达信全A_候选2": "880002.SH",
    "通达信全A_候选3": "880001.SH",
    "涨跌停统计候选": "880006.SH",
}

SC_FIELDS = ["SC03", "SC04", "SC23", "SC24", "SC30", "SC31", "SC35", "SC36", "SC39"]


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


def init_tq():
    """Backward-compatible TQ initializer via local_tdx_data."""
    try:
        session = ltd.TqSession(f"{__file__}#market_timing#{Path(__file__).stat().st_mtime_ns}")
        tq = session.__enter__()
        return (tq, session), None
    except Exception as e:
        return None, repr(e)


def snapshot_to_metrics(snapshot: dict) -> dict:
    now = to_float(snapshot.get("Now"))
    last = to_float(snapshot.get("LastClose"))
    amount = to_float(snapshot.get("Amount"))
    uphome = to_float(snapshot.get("UpHome"))
    downhome = to_float(snapshot.get("DownHome"))
    # In some TDX snapshots for market indices: Inside/Outside can represent down/up counts.
    inside = to_float(snapshot.get("Inside"))
    outside = to_float(snapshot.get("Outside"))
    return {
        "now": now,
        "last_close": last,
        "intraday_change_pct": pct(now, last),
        "amount_raw": amount,
        "uphome": uphome,
        "downhome": downhome,
        "inside_raw": inside,
        "outside_raw": outside,
        "raw": snapshot,
    }


def fetch_tq_data() -> dict:
    result = {"available": False, "error": None, "index_snapshots": {}, "market_snapshots": {}, "sc_fields": {}}
    handle, err = init_tq()
    if handle is None:
        result["error"] = err
        return result
    tq, session = handle
    result["available"] = True
    try:
        for name, meta in INDICES.items():
            try:
                result["index_snapshots"][name] = snapshot_to_metrics(tq.get_market_snapshot(meta["tq"]))
            except Exception as e:
                result["index_snapshots"][name] = {"error": repr(e)}
        for name, code in MARKET_SNAPSHOT_CODES.items():
            try:
                result["market_snapshots"][name] = snapshot_to_metrics(tq.get_market_snapshot(code))
            except Exception as e:
                result["market_snapshots"][name] = {"error": repr(e)}
        try:
            result["sc_fields"] = tq.get_scjy_value(field_list=SC_FIELDS)
        except Exception as e:
            result["sc_fields"] = {"error": repr(e)}
    finally:
        try:
            session.__exit__(None, None, None)
        except Exception:
            pass
    return result


def parse_sc_pair(sc: dict, field: str):
    v = sc.get(field)
    if not v or isinstance(v, dict):
        return None
    # get_scjy_value often returns [[a,b]], by_date returns [a,b]
    if isinstance(v, list) and v and isinstance(v[0], list):
        v = v[0]
    if isinstance(v, list):
        vals = [to_float(x) for x in v]
        return vals
    return None


def derive_market_fields(tq_data: dict) -> tuple[dict, dict, dict, dict]:
    """Return breadth, sentiment, turnover, quality."""
    quality = {"notes": [], "sources": []}
    breadth = {"up_count": None, "down_count": None, "up_down_ratio": None, "source": None, "quality": "missing"}
    sentiment = {
        "limit_up_count": None,
        "limit_down_count": None,
        "once_limit_up_count": None,
        "once_limit_down_count": None,
        "blowup_rate": None,
        "market_height": None,
        "above_2_board_count": None,
        "source": None,
        "quality": "missing",
    }
    turnover = {"total_turnover": None, "turnover_change_pct": None, "volume_summary": "", "source": None, "quality": "missing"}

    sc = tq_data.get("sc_fields") or {}
    sc31 = parse_sc_pair(sc, "SC31")
    if sc31 and all(v is not None for v in sc31[:2]):
        up, down = sc31[:2]
        breadth.update({"up_count": up, "down_count": down, "up_down_ratio": round(up / down, 4) if down else None, "source": "TQ_SC31", "quality": "auto"})
    else:
        # Fallback: local observed TDX market snapshot 880005 has candidate up/down counts in Outside/Inside;
        # keep as low-confidence unless manually confirmed.
        snap = (tq_data.get("market_snapshots") or {}).get("涨跌停统计候选") or {}
        up = snap.get("outside_raw")
        down = snap.get("inside_raw")
        if up is not None and down is not None:
            breadth.update({"up_count": up, "down_count": down, "up_down_ratio": round(up / down, 4) if down else None, "source": "TQ_snapshot_880006_OutsideInside_candidate", "quality": "candidate"})
            quality["notes"].append("涨跌家数使用 880006.SH 快照 Inside/Outside 候选字段，需人工确认口径。")

    sc03 = parse_sc_pair(sc, "SC03")  # current limit-up, once limit-up
    sc04 = parse_sc_pair(sc, "SC04")  # current limit-down, once limit-down
    sc30 = parse_sc_pair(sc, "SC30")  # market height, >2 board count
    sc24 = parse_sc_pair(sc, "SC24")  # non-ST limit up/down
    if sc03 or sc04 or sc30 or sc24:
        limit_up = sc24[0] if sc24 and sc24[0] is not None else (sc03[0] if sc03 else None)
        limit_down = sc24[1] if sc24 and len(sc24) > 1 and sc24[1] is not None else (sc04[0] if sc04 else None)
        once_up = sc03[1] if sc03 and len(sc03) > 1 else None
        once_down = sc04[1] if sc04 and len(sc04) > 1 else None
        blow = round((once_up - limit_up) / once_up, 4) if once_up and limit_up is not None and once_up else None
        sentiment.update({
            "limit_up_count": limit_up,
            "limit_down_count": limit_down,
            "once_limit_up_count": once_up,
            "once_limit_down_count": once_down,
            "blowup_rate": blow,
            "market_height": sc30[0] if sc30 else None,
            "above_2_board_count": sc30[1] if sc30 and len(sc30) > 1 else None,
            "source": "TQ_SC",
            "quality": "auto",
        })
    else:
        snap = (tq_data.get("market_snapshots") or {}).get("涨跌停统计候选") or {}
        # Observed candidate: LastClose may resemble previous limit-up count; Now may resemble current limit-up count;
        # Inside/Outside may resemble down/up counts. Too uncertain for final scoring.
        if snap:
            sentiment.update({"source": "TQ_snapshot_880006_candidate", "quality": "raw_only"})
            quality["notes"].append("涨跌停/连板高度 TQ SC 字段未稳定返回，已保留 880006.SH 原始快照但不自动用于评分。")

    # Conservative turnover: use major index snapshots' Amount raw fields as references, plus all-market candidates.
    idx_amounts = {}
    for name, snap in (tq_data.get("index_snapshots") or {}).items():
        if isinstance(snap, dict) and snap.get("amount_raw") is not None:
            idx_amounts[name] = snap.get("amount_raw")
    market_amounts = {}
    for name, snap in (tq_data.get("market_snapshots") or {}).items():
        if isinstance(snap, dict) and snap.get("amount_raw") is not None:
            market_amounts[name] = snap.get("amount_raw")
    turnover.update({
        "index_amount_raw": idx_amounts,
        "market_amount_raw_candidates": market_amounts,
        "source": "TQ_snapshot_Amount_raw",
        "quality": "candidate",
        "volume_summary": "TQ快照Amount字段已采集；单位/全市场口径需确认后再用于评分。",
    })

    if tq_data.get("available"):
        quality["sources"].append("TQ")
    if tq_data.get("sc_fields"):
        quality["sc_fields_raw"] = tq_data.get("sc_fields")
    return breadth, sentiment, turnover, quality


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--amv", type=float, default=None, help="0AMV 当日涨跌幅，百分比")
    ap.add_argument("--out", default="", help="可选输出路径；为空则写入正式 market_timing_input.json")
    args = ap.parse_args()

    tq_data = fetch_tq_data()
    breadth, sentiment, turnover, quality = derive_market_fields(tq_data)

    data = {
        "date": args.date,
        "collector_version": "market_timing_collector_v3_local_tdx",
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
        "raw_tq": tq_data,
    }

    for name, meta in INDICES.items():
        item = trend(read_day(meta["prefix"], meta["code"]))
        snap = (tq_data.get("index_snapshots") or {}).get(name)
        if isinstance(snap, dict) and "error" not in snap:
            item["intraday"] = {k: snap.get(k) for k in ["now", "last_close", "intraday_change_pct", "amount_raw"]}
        data["a_share_indices"][name] = item

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else OUT_DIR / f"{args.date}_market_timing_input.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
