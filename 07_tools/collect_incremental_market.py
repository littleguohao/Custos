# -*- coding: utf-8 -*-
"""Collect incremental market data: A50 futures, CNH exchange rate, limit-up/down ladder, northbound."""
from __future__ import annotations
import json, os, sys, warnings, time
from datetime import date, datetime
from pathlib import Path

warnings.filterwarnings("ignore")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent.parent
target = date.today().strftime("%Y-%m-%d")

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
    return {"price": round(float(price), 4) if price else None,
            "previous_close": round(float(prev), 4) if prev else None,
            "change_pct": round(float(chg), 4) if chg else None,
            "source": "Yahoo Finance"}

try:
    result["a50_futures"] = fetch_yahoo("CFF=A50")
except Exception:
    try:
        result["a50_futures"] = fetch_yahoo("XIN9.FGI")
    except Exception as e:
        result["a50_futures"] = {"error": str(e), "note": "A50 CFD unavailable via Yahoo, use web_search in report"}

try:
    result["cnh_usd"] = fetch_yahoo("USDCNH=X")
except Exception as e:
    result["cnh_usd"] = {"error": str(e)}

# ========== 2. Market breadth via mootdx Reader (local) ==========
from mootdx.reader import Reader
TDXDIR = os.environ.get("TDX_ROOT", r"E:\new_tdx64")
reader = Reader.factory(market="std", tdxdir=TDXDIR)

breadth_data = {}
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
