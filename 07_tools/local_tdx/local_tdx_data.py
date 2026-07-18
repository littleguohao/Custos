# -*- coding: utf-8 -*-
"""Unified local TDX data access layer for strategy_team.

This module wraps mootdx (online + offline) and provides stable helpers for:
- stock/index/880-series K-line data (via mootdx Reader + online bars)
- real-time quotes (via mootdx quotes)
- financial data (via mootdx Affair)
- adjusted prices (via mootdx get_adjust_year)
- sector lists (via mootdx Reader.block)

Replaces the previous tqcenter/vipdoc binary parsing with community-maintained mootdx.
"""
from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

warnings.filterwarnings("ignore")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import BASE, TDX_ROOT  # noqa: E402

# --- mootdx lazy initialization ---
_reader = None
_client = None


def _get_reader():
    global _reader
    if _reader is None:
        from mootdx.reader import Reader
        _reader = Reader.factory(market="std", tdxdir=str(TDX_ROOT))
    return _reader


def _get_client():
    global _client
    if _client is None:
        from mootdx.quotes import Quotes
        _client = Quotes.factory(market="std", quiet=True)
    return _client


class LocalTdxError(RuntimeError):
    pass


def normalize_code(code: str) -> str:
    """Normalize code to TQ suffix format."""
    s = str(code).strip().upper()
    if not s:
        return s
    if "." in s:
        return s
    if s.startswith(("920", "8", "4")):
        return f"{s}.BJ"
    if s.startswith(("6", "5", "9")):
        return f"{s}.SH"
    if s.startswith(("0", "1", "2", "3")):
        return f"{s}.SZ"
    return s


def _strip_suffix(code: str) -> str:
    """Return pure 6-digit code without suffix."""
    s = str(code).strip().upper()
    if "." in s:
        s = s.split(".")[0]
    return s.zfill(6)


def _get_market_code(code: str) -> int:
    """Return mootdx market int: 0=SZ, 1=SH."""
    s = _strip_suffix(code)
    if s.startswith(("6", "9", "5")):
        return 1  # SH
    return 0  # SZ


def _is_bj_code(code: str) -> bool:
    """Check if code is a Beijing Stock Exchange stock."""
    s = _strip_suffix(code)
    # BJ: 4xx, 8xx, 920xxx (North Exchange)
    return s.startswith("4") or s.startswith("8") or s.startswith("920")


def _read_bj_vipdoc_daily(code: str) -> "pd.DataFrame":
    """Read BJ vipdoc .day file directly (mootdx Reader misroutes 920xxx to SH)."""
    import struct
    raw = _strip_suffix(code)
    path = TDX_ROOT / "vipdoc" / "bj" / "lday" / f"bj{raw}.day"
    if not path.exists():
        return pd.DataFrame()
    # TDX .day format: 32 bytes per record
    # int date, int open, int high, int low, int close, float amount, int volume, int reserved
    records = []
    with open(path, "rb") as f:
        while True:
            buf = f.read(32)
            if len(buf) < 32:
                break
            date_int, o, h, l, c, amt, vol, _ = struct.unpack("<IIIIIfII", buf[:32])
            if date_int == 0:
                continue
            dt = pd.Timestamp(year=date_int // 10000, month=(date_int // 100) % 100, day=date_int % 100)
            records.append({
                "date": dt,
                "open": o / 100.0,
                "high": h / 100.0,
                "low": l / 100.0,
                "close": c / 100.0,
                "amount": amt,
                "volume": vol,
            })
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)


# ========== K-line data ==========

def read_vipdoc_daily(code: str) -> pd.DataFrame:
    """Read local vipdoc daily K-line via mootdx Reader.

    Returns columns: date, open, high, low, close, amount, volume.
    """
    # BJ stocks: mootdx Reader misroutes 920xxx to SH, parse .day directly
    if _is_bj_code(code):
        df = _read_bj_vipdoc_daily(code)
        if df.empty:
            return pd.DataFrame()
        df["code"] = normalize_code(code)
        df["source"] = "vipdoc_bj_direct"
        return df[["date", "code", "open", "high", "low", "close", "amount", "volume", "source"]]

    reader = _get_reader()
    raw = _strip_suffix(code)
    try:
        df = reader.daily(symbol=raw)
    except Exception as e:
        raise LocalTdxError(f"Reader.daily({raw}) failed: {e}")
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["code"] = normalize_code(code)
    df["source"] = "mootdx_reader"
    df.index.name = "date"
    df = df.reset_index()
    return df[["date", "code", "open", "high", "low", "close", "amount", "volume", "source"]]


def read_e_odata_daily(code: str) -> pd.DataFrame:
    """Read downloaded CSV cache from E:\\O_DATA (kept for backward compat)."""
    tcode = normalize_code(code)
    path = Path(os.environ.get("TDX_E_ODATA", r"E:\O_DATA")) / f"{tcode}-all-latest.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame()
    rename = {"Date": "date", "Code": "code", "Open": "open", "High": "high",
              "Low": "low", "Close": "close", "Volume": "volume", "Amount": "amount"}
    df = df.rename(columns=rename)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["code"] = tcode
    df["source"] = "e_odata"
    cols = ["date", "code", "open", "high", "low", "close", "volume", "amount", "source"]
    return df[[c for c in cols if c in df.columns]].dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def get_online_bars(code: str, frequency: int = 9, offset: int = 120, adjust: str = "") -> pd.DataFrame:
    """Fetch K-line from mootdx online server.

    frequency: 0=5m, 1=15m, 2=30m, 3=1h, 9=day, 5=week, 6=month
    adjust: "" = no adjust, "qfq" = front, "hfq" = back
    """
    client = _get_client()
    raw = _strip_suffix(code)
    try:
        kwargs = {"symbol": raw, "frequency": frequency, "offset": offset}
        if adjust:
            kwargs["adjust"] = adjust
        df = client.bars(**kwargs)
    except Exception as e:
        raise LocalTdxError(f"online bars({raw}) failed: {e}")
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["code"] = normalize_code(code)
    df["source"] = f"mootdx_online{'_'+adjust if adjust else ''}"
    df.index.name = "date"
    df = df.reset_index()
    return df


def get_online_index(code: str, market: int = 1, frequency: int = 9, offset: int = 120) -> pd.DataFrame:
    """Fetch index K-line (including 880 series) from mootdx online server.

    market: 0=SZ, 1=SH (880 series use SH)
    """
    client = _get_client()
    raw = _strip_suffix(code)
    try:
        df = client.index(frequency=frequency, market=market, symbol=raw, start=0, offset=offset)
    except Exception as e:
        raise LocalTdxError(f"online index({raw}) failed: {e}")
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["code"] = raw
    df["source"] = "mootdx_index"
    df.index.name = "date"
    df = df.reset_index()
    return df


def get_adjusted_daily(code: str, year: str = "", factor: str = "01") -> pd.DataFrame:
    """Get adjusted (qfq/hfq) daily data via mootdx contrib.

    factor: "00"=不复权, "01"=前复权, "02"=后复权
    """
    from mootdx.contrib.adjust import get_adjust_year
    raw = _strip_suffix(code)
    if not year:
        from datetime import date
        year = str(date.today().year)
    try:
        df = get_adjust_year(symbol=raw, year=year, factor=factor)
    except Exception as e:
        raise LocalTdxError(f"get_adjust_year({raw}) failed: {e}")
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["code"] = normalize_code(code)
    df["source"] = f"mootdx_adjust_{factor}"
    df.index.name = "date"
    df = df.reset_index()
    return df


# ========== Real-time quotes ==========

def get_snapshot(code: str) -> dict[str, Any]:
    """Get real-time quote for a single stock."""
    client = _get_client()
    raw = _strip_suffix(code)
    try:
        df = client.quotes(symbol=[raw])
    except Exception as e:
        raise LocalTdxError(f"quotes({raw}) failed: {e}")
    if df is None or df.empty:
        return {}
    row = df.iloc[0]
    return {
        "code": raw, "price": float(row.get("price", 0)),
        "last_close": float(row.get("last_close", 0)),
        "open": float(row.get("open", 0)), "high": float(row.get("high", 0)),
        "low": float(row.get("low", 0)),
    }


def get_snapshots(codes: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Get real-time quotes for multiple stocks."""
    client = _get_client()
    raw_codes = [_strip_suffix(c) for c in codes]
    try:
        df = client.quotes(symbol=raw_codes)
    except Exception as e:
        raise LocalTdxError(f"quotes batch failed: {e}")
    if df is None or df.empty:
        return {}
    result = {}
    for _, row in df.iterrows():
        code = str(row.get("code", ""))
        result[code] = {
            "price": float(row.get("price", 0)),
            "last_close": float(row.get("last_close", 0)),
            "open": float(row.get("open", 0)),
            "high": float(row.get("high", 0)),
            "low": float(row.get("low", 0)),
        }
    return result


# ========== Financial data ==========

_financial_cache: dict[str, pd.DataFrame] = {}


def get_financial_data(report_period: str = "") -> pd.DataFrame:
    """Download and parse TDX financial data (gpcwYYYYMMDD).

    Returns DataFrame with 585 columns for ~5500 stocks.
    """
    from mootdx.affair import Affair
    if not report_period:
        files = Affair.files()
        gpcw = sorted([f for f in files if f["filename"].startswith("gpcw")],
                       key=lambda x: x["filename"], reverse=True)
        # Skip empty future reports
        for f in gpcw:
            if f.get("filesize", 0) > 100000:
                report_period = f["filename"].replace("gpcw", "").replace(".zip", "")
                break
    cache_key = report_period
    if cache_key in _financial_cache:
        return _financial_cache[cache_key]
    fname = f"gpcw{report_period}.zip"
    download_dir = str(BASE / ".." / "tdx_affair_cache")
    Affair.fetch(downdir=download_dir, filename=fname)
    df = Affair.parse(downdir=download_dir, filename=fname)
    if df is not None:
        _financial_cache[cache_key] = df
    return df if df is not None else pd.DataFrame()


# ========== Sector data ==========

def get_sector_list() -> list[str]:
    """Get sector names from local TDX block files."""
    reader = _get_reader()
    try:
        blocks = reader.block(symbol="block_zs", group=False)
        if blocks is not None and not blocks.empty:
            return blocks["name"].tolist() if "name" in blocks.columns else []
    except Exception as e:
        print(f"[WARN] get_sector_list failed: {e}", file=sys.stderr)
    return []


def get_stock_list_in_sector(sector: str, block_type: int = 0) -> list[str]:
    """Get stock codes in a sector."""
    reader = _get_reader()
    try:
        blocks = reader.block(symbol="block_zs", group=False)
        if blocks is not None and not blocks.empty:
            mask = blocks["name"] == sector if "name" in blocks.columns else pd.Series([False] * len(blocks))
            subset = blocks[mask]
            return subset["code"].tolist() if "code" in subset.columns else []
    except Exception as e:
        print(f"[WARN] get_stock_list_in_sector({sector}) failed: {e}", file=sys.stderr)
    return []


def get_stock_list(pool_type: str = "5") -> list[str]:
    """Get stock list via mootdx online."""
    client = _get_client()
    from mootdx.consts import MARKET_SH, MARKET_SZ
    result = []
    for mkt in [MARKET_SH, MARKET_SZ]:
        try:
            stocks = client.stocks(market=mkt)
            if stocks is not None and not stocks.empty:
                result.extend(stocks["code"].tolist() if "code" in stocks.columns else [])
        except Exception as e:
            print(f"[WARN] get_stock_list market={mkt} failed: {e}", file=sys.stderr)
    return result


# ========== JSON/CSV helpers ==========

def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def save_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


# ========== TQ compat stubs (deprecated, kept for backward compat) ==========

def require_tq() -> Any:
    raise LocalTdxError("tqcenter is deprecated, use mootdx interfaces directly")


class TqSession:
    def __init__(self, *args, **kwargs):
        raise LocalTdxError("tqcenter is deprecated, use mootdx interfaces directly")


def get_ohlcv_table(code: str, count: int = 260, prefer: str = "vipdoc") -> pd.DataFrame:
    """Unified OHLCV reader: try local vipdoc first, fallback to online bars."""
    df = pd.DataFrame()
    if prefer == "vipdoc":
        try:
            df = read_vipdoc_daily(code)
        except Exception as e:
            print(f"[WARN] read_vipdoc_daily({code}) failed, fallback to online: {e}", file=sys.stderr)
            df = pd.DataFrame()
    if df.empty:
        try:
            df = get_online_bars(code, offset=count)
        except Exception as e:
            print(f"[WARN] get_online_bars({code}) failed: {e}", file=sys.stderr)
            df = pd.DataFrame()
    if not df.empty and len(df) > count:
        df = df.tail(count).reset_index(drop=True)
    return df


def get_market_data(*args, **kwargs):
    raise LocalTdxError("get_market_data is deprecated, use read_vipdoc_daily or get_online_bars")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="600150")
    ap.add_argument("--mode", choices=["daily", "online", "index", "adjust", "finance"], default="daily")
    ap.add_argument("--offset", type=int, default=10)
    args = ap.parse_args()
    if args.mode == "daily":
        df = read_vipdoc_daily(args.code)
    elif args.mode == "online":
        df = get_online_bars(args.code, offset=args.offset)
    elif args.mode == "index":
        df = get_online_index(args.code, offset=args.offset)
    elif args.mode == "adjust":
        df = get_adjusted_daily(args.code)
    elif args.mode == "finance":
        df = get_financial_data()
    print(df.tail(args.offset).to_string() if not df.empty else "No data")


if __name__ == "__main__":
    main()
