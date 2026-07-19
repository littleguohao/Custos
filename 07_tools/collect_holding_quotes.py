# -*- coding: utf-8 -*-
"""Collect holding quotes + index quotes via mootdx / tq_http / eastmoney.

持仓报价数据源优先级：
- intraday 非BJ: tq_http 快照 → mootdx 在线 bars → mootdx Reader 本地
- intraday BJ:   tq_http 快照 → mootdx Reader 本地 → 东财 push2
- postclose 非BJ: mootdx Reader 本地 → mootdx 在线 bars（保持现状）
- postclose BJ:   mootdx Reader 本地 → tq_http 快照 → 东财 push2

tq_http 快照走 TdxW 本地 HTTP 服务（127.0.0.1:17709）；TdxW 未运行时
tq_http 干净返回 error，自然 fall through 到下一数据源。

CLI::

    uv run python 07_tools/collect_holding_quotes.py --date YYYY-MM-DD --session intraday
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import date, datetime
from pathlib import Path

warnings.filterwarnings("ignore")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parent
LOCAL_TDX_DIR = TOOLS_DIR / "local_tdx"
if str(LOCAL_TDX_DIR) not in sys.path:
    sys.path.insert(0, str(LOCAL_TDX_DIR))

from paths import BASE, TDX_ROOT  # noqa: E402
from code_utils import norm_code  # noqa: E402
import tq_http  # noqa: E402

from mootdx.reader import Reader  # noqa: E402
from mootdx.quotes import Quotes  # noqa: E402
from mootdx.consts import MARKET_SH, MARKET_SZ  # noqa: E402

_reader = None  # lazy init: local .day access only when actually needed
_client = None  # lazy init: only connect when online access is actually needed


def _get_reader():
    global _reader
    if _reader is None:
        _reader = Reader.factory(market="std", tdxdir=str(TDX_ROOT))
    return _reader


def _get_client():
    global _client
    if _client is None:
        _client = Quotes.factory(market="std", quiet=True)
    return _client


def get_market(code: str) -> int:
    code = str(code).zfill(6)
    if code.startswith(("920", "83", "87", "4")):
        return 2  # BJ
    elif code.startswith(("6", "9")):
        return 1  # SH
    elif code.startswith(("0", "3")):
        return 0  # SZ
    return 0


def _market_name(mkt: int) -> str:
    return "BJ" if mkt == 2 else ("SH" if mkt == 1 else "SZ")


def _fnum(v):
    """快照数值多为字符串；可解析转 float，否则 None。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_dt(dt) -> str:
    """Format datetime to 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD'."""
    s = str(dt).strip()
    if not s:
        return ""
    return s[:19] if len(s) >= 10 else s[:10]


def _tq_snapshot_quote(code, name, mkt, target):
    """TQ-Local HTTP 个股快照（TdxW 本地服务）。失败返回 None，绝不 raise。

    快照字段：Now=现价、LastClose=前收、Open/Max/Min=开高低、Volume/Amount=量额。
    Now 缺失或 <=0 视为失败。
    """
    try:
        resp = tq_http.snapshot(norm_code(code))
    except Exception:
        return None
    if not resp.get("ok"):
        return None
    v = resp.get("value")
    if not isinstance(v, dict):
        return None
    now = _fnum(v.get("Now"))
    if now is None or now <= 0:
        return None
    prev_close = _fnum(v.get("LastClose")) or 0.0
    chg = round((now / prev_close - 1) * 100, 2) if prev_close else None
    return {
        "code": code, "name": name, "market": _market_name(mkt),
        "available": True,
        "date": target,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "open": _fnum(v.get("Open")) or 0.0,
        "high": _fnum(v.get("Max")) or 0.0,
        "low": _fnum(v.get("Min")) or 0.0,
        "close": now,
        "previous_close": prev_close,
        "change_pct": chg,
        "volume": _fnum(v.get("Volume")) or 0.0,
        "amount": _fnum(v.get("Amount")) or 0.0,
        "source": "tq_http_snapshot",
    }


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
        "code": code, "name": name, "market": _market_name(mkt),
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
    df = _get_reader().daily(symbol=code)
    if df is None or len(df) < 2:
        return None
    last = df.iloc[-1]
    prev = df.iloc[-2]
    prev_close = float(prev["close"])
    close = float(last["close"])
    chg = round((close / prev_close - 1) * 100, 2) if prev_close else None
    last_date = last.name if hasattr(last.name, 'strftime') else ''
    return {
        "code": code, "name": name, "market": _market_name(mkt),
        "available": True,
        "date": str(last_date)[:10],
        "time": str(last_date)[:19] if last_date else "",
        "open": float(last["open"]), "high": float(last["high"]),
        "low": float(last["low"]), "close": close,
        "previous_close": prev_close, "change_pct": chg,
        "volume": float(last["volume"]), "amount": float(last.get("amount", 0)),
        "source": "mootdx_reader",
    }


def _eastmoney_bj_quote(code, name, target):
    """东财 push2 API：BJ 股最后兜底（mootdx 不支持 BJ）。"""
    import requests as _req
    _s = _req.Session()
    _s.trust_env = False
    _url = f"https://push2.eastmoney.com/api/qt/stock/get?ut=bd1d9ddb04089700cf256c0c7f8fe813&fltt=2&invt=2&fields=f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f170&secid=0.{code}"
    _r = _s.get(_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}, proxies={"http": None, "https": None})
    _r.raise_for_status()
    _d = _r.json().get("data", {})
    if not _d or _d.get("f43") is None:
        return None
    close = float(_d["f43"])
    prev_close = float(_d.get("f60", 0))
    chg = round((close / prev_close - 1) * 100, 2) if prev_close else None
    return {
        "code": code, "name": name, "market": "BJ",
        "available": True,
        "date": target,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "open": float(_d.get("f46", 0)), "high": float(_d.get("f44", 0)),
        "low": float(_d.get("f45", 0)), "close": close,
        "previous_close": prev_close, "change_pct": chg,
        "volume": float(_d.get("f47", 0)), "amount": float(_d.get("f48", 0)),
        "source": "eastmoney_push2_bj",
    }


def _try_quote(fn, code, *args):
    """Run a quote source; warn + None on exception."""
    try:
        return fn(code, *args)
    except Exception as e:
        print(f"[WARN] quote failed for {code}: {e}", file=sys.stderr)
        return None


def _holding_quote(code, name, mkt, session, target):
    """按数据源优先级采集单只持仓报价，全部失败返回 None。"""
    q = None
    if session == "intraday":
        # tq_http 快照优先（TdxW 未运行时干净返回 None，自然 fall through）
        q = _tq_snapshot_quote(code, name, mkt, target)
        if mkt == 2:
            # BJ: tq_http → reader 本地（mootdx 在线不支持 BJ）→ 东财
            if q is None:
                q = _try_quote(_reader_quote, code, name, mkt)
        else:
            if q is None:
                q = _try_quote(_online_bars_quote, code, name, mkt)
            if q is None:
                q = _try_quote(_reader_quote, code, name, mkt)
    else:  # postclose: reader 本地优先
        q = _try_quote(_reader_quote, code, name, mkt)
        if q is None or q.get("date", "") != target:
            if mkt == 2:
                q = _tq_snapshot_quote(code, name, mkt, target)
            else:
                q = _try_quote(_online_bars_quote, code, name, mkt)
    # BJ 最后兜底：东财 push2
    if q is None and mkt == 2:
        q = _try_quote(_eastmoney_bj_quote, code, name, target)
    return q


def _collect_indices(session):
    """Collect indices (online first for intraday, local Reader for postclose)."""
    indices = []
    for code, name, mkt in [("000001", "上证指数", MARKET_SH), ("399001", "深证成指", MARKET_SZ), ("399006", "创业板指", MARKET_SZ)]:
        idx = None
        # intraday: online first
        if session == "intraday":
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
                print(f"[WARN] {e}", file=sys.stderr)
        # fallback or postclose: local reader
        if idx is None:
            try:
                df = _get_reader().daily(symbol=code)
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
                print(f"[WARN] {e}", file=sys.stderr)
        if idx is None:
            indices.append({"code": code, "name": name, "available": False, "reason": "no data"})
        else:
            indices.append(idx)
    return indices


def _collect_breadth():
    """Collect 880 series market breadth (local Reader first, online fallback)."""
    breadth = {}
    for code, name in [("880001", "平均股价"), ("880005", "涨跌家数"), ("880006", "停板家数"), ("880390", "融资融券"), ("880863", "北向资金")]:
        # Local reader
        try:
            df = _get_reader().daily(symbol=code)
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
            print(f"[WARN] {e}", file=sys.stderr)
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
    return breadth


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
    ap.add_argument("--session", choices=["intraday", "postclose"], default="intraday")
    args = ap.parse_args(argv)
    target = args.date

    # Load positions
    raw = json.loads((BASE / "01_data/trades/current_positions.json").read_text(encoding="utf-8"))
    holdings = raw if isinstance(raw, list) else raw.get("holdings", [])

    holding_quotes = []
    for h in holdings:
        code = str(h.get("代码", h.get("code", ""))).zfill(6)
        name = h.get("名称", h.get("name", ""))
        mkt = get_market(code)
        q = _holding_quote(code, name, mkt, args.session, target)
        if q is not None:
            q["price"] = q.get("close")
            holding_quotes.append(q)
        else:
            holding_quotes.append({"code": code, "name": name, "market": _market_name(mkt), "available": False, "reason": "no data"})

    indices = _collect_indices(args.session)
    breadth = _collect_breadth()

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
            print(f"[WARN] {e}", file=sys.stderr)
    output = {
        "as_of_date": target,
        "captured_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "source": "mootdx",
        "quotes": holding_quotes,
        "indices": indices,
        "breadth": breadth,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(1 for q in holding_quotes if q.get("available"))
    print(f"collected {ok}/{len(holdings)} holdings + {len(indices)} indices + {len(breadth)} breadth -> {out_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
