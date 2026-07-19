# -*- coding: utf-8 -*-
"""Collect incremental market data: A50 futures, CNH exchange rate, limit-up/down ladder, northbound."""
from __future__ import annotations
import json, os, sys, warnings, time
from datetime import date, datetime
from pathlib import Path

warnings.filterwarnings("ignore")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from paths import BASE, TDX_ROOT

import argparse
ap = argparse.ArgumentParser()
ap.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
args = ap.parse_args()
target = args.date

result = {"date": target, "collected_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")}

# ========== 1. A50 futures + CNH via web search (Yahoo Finance) ==========
import urllib.request, urllib.parse

from net_retry import retry_call

def fetch_yahoo(symbol: str) -> dict:
    encoded = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=5d&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with retry_call(lambda: urllib.request.urlopen(req, timeout=15)) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    r = (data.get("chart") or {}).get("result", [{}])[0]
    meta = r.get("meta") or {}
    price = meta.get("regularMarketPrice")
    prev = meta.get("previousClose") or meta.get("chartPreviousClose")
    chg = meta.get("regularMarketChangePercent")
    if chg is None and price and prev:
        chg = (price / prev - 1) * 100
    mtime = meta.get("regularMarketTime")
    as_of = datetime.fromtimestamp(mtime).astimezone().isoformat(timespec="seconds") if isinstance(mtime, (int, float)) else None
    return {"symbol": symbol,
            "price": round(float(price), 4) if price else None,
            "previous_close": round(float(prev), 4) if prev else None,
            "change_pct": round(float(chg), 4) if chg else None,
            "as_of": as_of,
            "source": "Yahoo Finance"}

try:
    result["a50_futures"] = fetch_yahoo("CFF=A50")
except Exception:
    try:
        result["a50_futures"] = fetch_yahoo("XIN9.FGI")
    except Exception as e:
        result["a50_futures"] = {"error": str(e), "note": "A50 CFD unavailable via Yahoo, use web_search in report"}

# A50 sanity guard: |change_pct| > 3% usually means a misaligned previous_close
# (contract roll / stale meta), not a real move. Flag it; do not alter values.
_a50 = result.get("a50_futures") or {}
_a50_chg = _a50.get("change_pct")
if isinstance(_a50_chg, (int, float)) and abs(_a50_chg) > 3:
    _a50["suspect"] = True
    _a50["note"] = "change_pct 超过 ±3%，存在 previous_close 错位(换月/元数据滞后)风险，使用前需人工核对"

try:
    result["cnh_usd"] = fetch_yahoo("USDCNH=X")
except Exception as e:
    result["cnh_usd"] = {"error": str(e)}

# ========== 2. Market breadth via mootdx Reader (local) ==========
# Reader creation and all vipdoc reads are guarded: a local-data failure must
# land in the output JSON's error fields, not crash the script before writing.
reader = None
reader_error = None
try:
    from mootdx.reader import Reader
    TDXDIR = str(TDX_ROOT)
    reader = Reader.factory(market="std", tdxdir=TDXDIR)
except Exception as e:
    reader_error = str(e)

breadth_data = {}
if reader is None:
    breadth_data["_error"] = {"error": f"mootdx Reader unavailable: {reader_error}"}
else:
    for code, name in [("880001", "平均股价"), ("880005", "涨跌家数"), ("880006", "停板家数"),
                        ("880390", "融资融券"), ("880863", "北向资金")]:
        try:
            df = reader.daily(symbol=code)
            if df is not None and len(df) >= 2:
                last = df.iloc[-1]
                prev = df.iloc[-2]
                prev_close = float(prev["close"])
                close = float(last["close"])
                breadth_data[code] = {
                    "name": name, "close": close, "previous_close": prev_close,
                    "change_pct": round((close / prev_close - 1) * 100, 2) if prev_close else None,
                    "amount": (float(last["amount"]) if "amount" in df.columns and last["amount"] == last["amount"] else None),
                    "previous_amount": (float(prev["amount"]) if "amount" in df.columns and prev["amount"] == prev["amount"] else None),
                    "date": str(last.name if hasattr(last.name, 'strftime') else ''),
                    "up_count": int(last.get("up_count", 0)) if "up_count" in df.columns else None,
                    "down_count": int(last.get("down_count", 0)) if "down_count" in df.columns else None,
                }
        except Exception as e:
            breadth_data[code] = {"name": name, "error": str(e)}

result["breadth"] = breadth_data

# ========== 3. Limit-up/down ladder via tdx_screener (needs LLM, skip in script) ==========
# This will be collected by the post-close review enrichment
result["limit_ladder"] = {"note": "collected via tdx_screener in post-close enrichment"}

# ========== 4. Northbound via mootdx index ==========
if reader is None:
    result["northbound"] = {"error": f"mootdx Reader unavailable: {reader_error}"}
else:
    try:
        df = reader.daily(symbol="880863")
        if df is not None and len(df) >= 5:
            last5 = df.tail(5)
            result["northbound"] = {
                "latest_close": float(last5.iloc[-1]["close"]),
                "last_5d_change": round((float(last5.iloc[-1]["close"]) / float(last5.iloc[0]["close"]) - 1) * 100, 2),
                "trend": "up" if float(last5.iloc[-1]["close"]) > float(last5.iloc[0]["close"]) else "down",
            }
    except Exception as e:
        result["northbound"] = {"error": str(e)}

# ========== Write output ==========
out_path = BASE / "01_data" / "market" / f"{target}_incremental_market.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"incremental market data -> {out_path.name}")
for k, v in result.items():
    if isinstance(v, dict) and "change_pct" in v:
        print(f"  {k}: {v.get('change_pct', '?')}%")
    elif isinstance(v, dict) and "close" in v:
        print(f"  {k}: close={v['close']}")
    elif isinstance(v, dict) and "latest_close" in v:
        print(f"  {k}: {v['latest_close']} (5d={v.get('last_5d_change','?')}%)")
