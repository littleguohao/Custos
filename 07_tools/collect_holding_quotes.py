# -*- coding: utf-8 -*-
"""Collect holding quotes + index quotes via mootdx, with tdx_quotes fallback for BJ stocks."""
from __future__ import annotations
import json, sys, warnings, time
from datetime import date, datetime
from pathlib import Path

warnings.filterwarnings("ignore")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent.parent
target = date.today().strftime("%Y-%m-%d")

# Load positions
raw = json.loads((BASE / "01_data/trades/current_positions.json").read_text(encoding="utf-8"))
holdings = raw if isinstance(raw, list) else raw.get("holdings", [])

def get_market(code: str) -> int:
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return 1  # SH
    elif code.startswith(("0", "3")):
        return 0  # SZ
    elif code.startswith(("8", "4")):
        return 2  # BJ
    return 0

# --- mootdx collection (try local Reader first, fallback to online bars) ---
from mootdx.reader import Reader
from mootdx.quotes import Quotes
from mootdx.consts import MARKET_SH, MARKET_SZ

TDXDIR = r"C:\new_tdx64"
reader = Reader.factory(market="std", tdxdir=TDXDIR)
client = Quotes.factory(market="std", quiet=True)

holding_quotes = []
for h in holdings:
    code = str(h.get("代码", h.get("code", ""))).zfill(6)
    name = h.get("名称", h.get("name", ""))
    mkt = get_market(code)
    
    if mkt == 2:  # BJ - mootdx doesn't support well, try online bars anyway
        try:
            df = client.bars(symbol=code, frequency=9, offset=2)
            if df is not None and len(df) >= 1:
                last = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else None
                prev_close = float(prev["close"]) if prev is not None else 0
                close = float(last["close"])
                chg = round((close / prev_close - 1) * 100, 2) if prev_close else None
                holding_quotes.append({"code": code, "name": name, "market": "BJ",
                    "available": True, "date": str(last["datetime"]),
                    "open": float(last["open"]), "high": float(last["high"]),
                    "low": float(last["low"]), "close": close,
                    "previous_close": prev_close, "change_pct": chg,
                    "volume": float(last["volume"]), "amount": float(last.get("amount", 0)),
                    "source": "mootdx_online_bars"})
                continue
        except Exception:
            pass
        holding_quotes.append({"code": code, "name": name, "market": "BJ", "available": False, "reason": "BJ not supported"})
        continue
    
    # Try local Reader first (0.005s per stock)
    try:
        df = reader.daily(symbol=code)
        if df is not None and len(df) >= 2:
            last = df.iloc[-1]
            prev = df.iloc[-2]
            prev_close = float(prev["close"])
            close = float(last["close"])
            change_pct = round((close / prev_close - 1) * 100, 2) if prev_close else None
            holding_quotes.append({
                "code": code, "name": name, "market": "SH" if mkt == 1 else "SZ",
                "available": True,
                "date": str(last.name if hasattr(last.name, 'strftime') else last.get('datetime', '')),
                "open": float(last["open"]), "high": float(last["high"]),
                "low": float(last["low"]), "close": close,
                "previous_close": prev_close, "change_pct": change_pct,
                "volume": float(last["volume"]), "amount": float(last.get("amount", 0)),
                "source": "mootdx_reader",
            })
            continue
    except Exception:
        pass
    
    # Fallback to online bars
    try:
        df = client.bars(symbol=code, frequency=9, offset=2)
        if df is not None and len(df) >= 1:
            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else None
            prev_close = float(prev["close"]) if prev is not None else float(last.get("last_close", 0))
            close = float(last["close"])
            change_pct = round((close / prev_close - 1) * 100, 2) if prev_close else None
            holding_quotes.append({
                "code": code, "name": name, "market": "SH" if mkt == 1 else "SZ",
                "available": True,
                "date": str(last["datetime"]),
                "open": float(last["open"]), "high": float(last["high"]),
                "low": float(last["low"]), "close": close,
                "previous_close": prev_close, "change_pct": change_pct,
                "volume": float(last["volume"]), "amount": float(last.get("amount", 0)),
                "source": "mootdx_online_bars",
            })
        else:
            holding_quotes.append({"code": code, "name": name, "available": False, "reason": "no data"})
    except Exception as e:
        holding_quotes.append({"code": code, "name": name, "available": False, "reason": str(e)})

# Collect indices (try local Reader, fallback to online)
indices = []
for code, name, mkt in [("000001", "上证指数", MARKET_SH), ("399001", "深证成指", MARKET_SZ), ("399006", "创业板指", MARKET_SZ)]:
    # Local reader first
    try:
        df = reader.daily(symbol=code)
        if df is not None and len(df) >= 2:
            last = df.iloc[-1]
            prev = df.iloc[-2]
            prev_close = float(prev["close"])
            close = float(last["close"])
            change_pct = round((close / prev_close - 1) * 100, 2) if prev_close else None
            indices.append({"code": code, "name": name,
                "date": str(last.name if hasattr(last.name, 'strftime') else ''),
                "close": close, "previous_close": prev_close,
                "change_pct": change_pct, "volume": float(last["volume"]),
                "source": "mootdx_reader"})
            continue
    except Exception:
        pass
    # Online fallback
    try:
        df = client.index(frequency=9, market=mkt, symbol=code, start=0, offset=2)
        if df is not None and len(df) >= 1:
            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else None
            prev_close = float(prev["close"]) if prev is not None else 0
            close = float(last["close"])
            change_pct = round((close / prev_close - 1) * 100, 2) if prev_close else None
            indices.append({"code": code, "name": name,
                "date": str(last["datetime"]), "close": close,
                "previous_close": prev_close, "change_pct": change_pct,
                "volume": float(last["volume"]), "source": "mootdx_index"})
    except Exception as e:
        indices.append({"code": code, "name": name, "available": False, "reason": str(e)})

# Collect 880 series market breadth (local Reader first, online fallback)
breadth = {}
for code, name in [("880001", "平均股价"), ("880005", "涨跌家数"), ("880006", "停板家数"), ("880390", "融资融券"), ("880863", "北向资金")]:
    # Local reader
    try:
        df = reader.daily(symbol=code)
        if df is not None and len(df) >= 2:
            last = df.iloc[-1]
            prev = df.iloc[-2]
            prev_close = float(prev["close"])
            close = float(last["close"])
            breadth[code] = {"name": name, "close": close, "previous_close": prev_close,
                "change_pct": round((close / prev_close - 1) * 100, 2) if prev_close else None,
                "date": str(last.name if hasattr(last.name, 'strftime') else ''), "source": "mootdx_reader"}
            continue
    except Exception:
        pass
    # Online fallback
    try:
        df = client.index(frequency=9, market=MARKET_SH, symbol=code, start=0, offset=2)
        if df is not None and len(df) >= 1:
            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else None
            prev_close = float(prev["close"]) if prev is not None else 0
            close = float(last["close"])
            breadth[code] = {"name": name, "close": close, "previous_close": prev_close,
                "change_pct": round((close / prev_close - 1) * 100, 2) if prev_close else None,
                "date": str(last["datetime"]), "source": "mootdx_online"}
    except Exception as e:
        breadth[code] = {"name": name, "error": str(e)}

# Write output
output = {
    "as_of_date": target,
    "captured_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
    "source": "mootdx",
    "quotes": holding_quotes,
    "indices": indices,
    "breadth": breadth,
}

out_path = BASE / "01_data" / "market" / f"{target}_holding_quotes.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

ok = sum(1 for q in holding_quotes if q.get("available"))
print(f"collected {ok}/{len(holdings)} holdings + {len(indices)} indices + {len(breadth)} breadth -> {out_path.name}")
