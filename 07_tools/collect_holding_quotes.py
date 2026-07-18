# -*- coding: utf-8 -*-
"""Collect holding quotes + index quotes via mootdx, with tdx_quotes fallback for BJ stocks."""
from __future__ import annotations
import os, json, sys, warnings, time, traceback
from datetime import date, datetime
from pathlib import Path

warnings.filterwarnings("ignore")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from paths import BASE, TDX_ROOT  # noqa: E402

import argparse
ap = argparse.ArgumentParser()
ap.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
ap.add_argument("--session", choices=["intraday", "postclose"], default="intraday")
args = ap.parse_args()
target = args.date

# Load positions
raw = json.loads((BASE / "01_data/trades/current_positions.json").read_text(encoding="utf-8"))
holdings = raw if isinstance(raw, list) else raw.get("holdings", [])

def get_market(code: str) -> int:
    code = str(code).zfill(6)
    if code.startswith(("920", "83", "87", "4")):
        return 2  # BJ
    elif code.startswith(("6", "9")):
        return 1  # SH
    elif code.startswith(("0", "3")):
        return 0  # SZ
    return 0

# --- mootdx collection (online bars first for intraday, local Reader for postclose) ---
from mootdx.reader import Reader
from mootdx.quotes import Quotes
from mootdx.consts import MARKET_SH, MARKET_SZ

TDXDIR = str(TDX_ROOT)
reader = Reader.factory(market="std", tdxdir=TDXDIR)
_client = None  # lazy init: only connect when online access is actually needed

def _get_client():
    global _client
    if _client is None:
        _client = Quotes.factory(market="std", quiet=True)
    return _client

def _fmt_dt(dt) -> str:
    """Format datetime to 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD'."""
    s = str(dt).strip()
    if not s:
        return ""
    return s[:19] if len(s) >= 10 else s[:10]

def _online_bars_quote(code, name, mkt):
    """Fetch latest bar from online API."""
    df = _get_client().bars(symbol=code, frequency=9, offset=2)
    if df is None or len(df) == 0:
        return None
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else None
    prev_close = float(prev["close"]) if prev is not None else 0
    close = float(last["close"])
    chg = round((close / prev_close - 1) * 100, 2) if prev_close else None
    dt = last.get("datetime", "")
    return {
        "code": code, "name": name, "market": "BJ" if mkt == 2 else ("SH" if mkt == 1 else "SZ"),
        "available": True,
        "date": str(dt)[:10],
        "time": _fmt_dt(dt),
        "open": float(last["open"]), "high": float(last["high"]),
        "low": float(last["low"]), "close": close,
        "previous_close": prev_close, "change_pct": chg,
        "volume": float(last["volume"]), "amount": float(last.get("amount", 0)),
        "source": "mootdx_online_bars",
    }

def _reader_quote(code, name, mkt):
    """Fetch latest bar from local .day file."""
    df = reader.daily(symbol=code)
    if df is None or len(df) < 2:
        return None
    last = df.iloc[-1]
    prev = df.iloc[-2]
    prev_close = float(prev["close"])
    close = float(last["close"])
    chg = round((close / prev_close - 1) * 100, 2) if prev_close else None
    last_date = last.name if hasattr(last.name, 'strftime') else ''
    return {
        "code": code, "name": name, "market": "BJ" if mkt == 2 else ("SH" if mkt == 1 else "SZ"),
        "available": True,
        "date": str(last_date)[:10],
        "time": str(last_date)[:19] if last_date else "",
        "open": float(last["open"]), "high": float(last["high"]),
        "low": float(last["low"]), "close": close,
        "previous_close": prev_close, "change_pct": chg,
        "volume": float(last["volume"]), "amount": float(last.get("amount", 0)),
        "source": "mootdx_reader",
    }

holding_quotes = []
for h in holdings:
    code = str(h.get("代码", h.get("code", ""))).zfill(6)
    name = h.get("名称", h.get("name", ""))
    mkt = get_market(code)
    
    q = None
    # intraday: always try online first (local .day won't have today's data)
    if args.session == "intraday":
        try:
            q = _online_bars_quote(code, name, mkt)
        except Exception as e:
            import sys as _s; print(f"[WARN] quote failed for {code}: {e}", file=_s.stderr)
            q = None
        # fallback to reader if online failed
        if q is None:
            try:
                q = _reader_quote(code, name, mkt)
            except Exception as e:
                import sys as _s; print(f"[WARN] quote failed for {code}: {e}", file=_s.stderr)
                q = None
    else:  # postclose: try reader first, then online
        try:
            q = _reader_quote(code, name, mkt)
        except Exception as e:
            import sys as _s; print(f"[WARN] quote failed for {code}: {e}", file=_s.stderr)
            q = None
        if q is None or q.get("date", "") != target:
            try:
                q = _online_bars_quote(code, name, mkt)
            except Exception as e:
                import sys as _s; print(f"[WARN] quote failed for {code}: {e}", file=_s.stderr)
                q = None
    
    # BJ stocks: mootdx doesn't support, try East Money push2 API
    if q is None and mkt == 2:
        try:
            import requests as _req
            _s = _req.Session()
            _s.trust_env = False
            _url = f"https://push2.eastmoney.com/api/qt/stock/get?ut=bd1d9ddb04089700cf256c0c7f8fe813&fltt=2&invt=2&fields=f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f170&secid=0.{code}"
            _r = _s.get(_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}, proxies={"http": None, "https": None})
            _r.raise_for_status()
            _d = _r.json().get("data", {})
            if _d and _d.get("f43") is not None:
                close = float(_d["f43"])
                prev_close = float(_d.get("f60", 0))
                chg = round((close / prev_close - 1) * 100, 2) if prev_close else None
                from datetime import datetime as _dt
                q = {
                    "code": code, "name": name, "market": "BJ",
                    "available": True,
                    "date": target,
                    "time": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "open": float(_d.get("f46", 0)), "high": float(_d.get("f44", 0)),
                    "low": float(_d.get("f45", 0)), "close": close,
                    "previous_close": prev_close, "change_pct": chg,
                    "volume": float(_d.get("f47", 0)), "amount": float(_d.get("f48", 0)),
                    "source": "eastmoney_push2_bj",
                }
        except Exception as e:
            import sys as _s; print(f"[WARN] quote failed for {code}: {e}", file=_s.stderr)
            q = None
    
    if q is not None:
        q["price"] = q.get("close")
        holding_quotes.append(q)
    else:
        holding_quotes.append({"code": code, "name": name, "market": "BJ" if mkt == 2 else ("SH" if mkt == 1 else "SZ"), "available": False, "reason": "no data"})

# Collect indices (online first for intraday, local Reader for postclose)
indices = []
for code, name, mkt in [("000001", "上证指数", MARKET_SH), ("399001", "深证成指", MARKET_SZ), ("399006", "创业板指", MARKET_SZ)]:
    idx = None
    # intraday: online first
    if args.session == "intraday":
        try:
            df = _get_client().index(frequency=9, market=mkt, symbol=code, start=0, offset=2)
            if df is not None and len(df) >= 1:
                last = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else None
                prev_close = float(prev["close"]) if prev is not None else 0
                close = float(last["close"])
                chg = round((close / prev_close - 1) * 100, 2) if prev_close else None
                dt = last.get("datetime", "")
                idx = {"code": code, "name": name,
                    "date": str(dt)[:10], "time": str(dt)[:19] if dt else "",
                    "close": close, "price": close, "previous_close": prev_close,
                    "change_pct": chg, "volume": float(last["volume"]),
                    "source": "mootdx_online_index"}
        except Exception as e:
            import sys as _s; print(f"[WARN] {e}", file=_s.stderr)
    # fallback or postclose: local reader
    if idx is None:
        try:
            df = reader.daily(symbol=code)
            if df is not None and len(df) >= 2:
                last = df.iloc[-1]
                prev = df.iloc[-2]
                prev_close = float(prev["close"])
                close = float(last["close"])
                chg = round((close / prev_close - 1) * 100, 2) if prev_close else None
                last_date = str(last.name)[:19] if hasattr(last.name, 'strftime') else ''
                idx = {"code": code, "name": name,
                    "date": last_date[:10], "time": last_date,
                    "close": close, "price": close, "previous_close": prev_close,
                    "change_pct": chg, "volume": float(last["volume"]),
                    "source": "mootdx_reader"}
        except Exception as e:
            import sys as _s; print(f"[WARN] {e}", file=_s.stderr)
    if idx is None:
        indices.append({"code": code, "name": name, "available": False, "reason": "no data"})
    else:
        indices.append(idx)

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
    except Exception as e:
        import sys as _s; print(f"[WARN] {e}", file=_s.stderr)
    # Online fallback
    try:
        df = _get_client().index(frequency=9, market=MARKET_SH, symbol=code, start=0, offset=2)
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

# Write output (preserve unavailable stocks from previous file)
out_path = BASE / "01_data" / "market" / f"{target}_holding_quotes.json"
if out_path.exists():
    try:
        prev_data = json.loads(out_path.read_text(encoding="utf-8"))
        prev_map = {q["code"]: q for q in prev_data.get("quotes", []) if q.get("available")}
        for q in holding_quotes:
            if not q.get("available") and q["code"] in prev_map:
                q.update(prev_map[q["code"]])
    except Exception as e:
        import sys as _s; print(f"[WARN] {e}", file=_s.stderr)
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
