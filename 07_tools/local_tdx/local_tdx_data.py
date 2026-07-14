# -*- coding: utf-8 -*-
"""Unified local TDX data access layer for strategy_team.

This module wraps the local TongDaXin/TQ client and provides stable helpers for:
- stock/index K-line data
- market snapshots
- stock lists
- sector lists and sector members

It intentionally does not modify files under C:\\new_tdx64\\PYPlugins\\user.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(r"C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team")
TDX_ROOT = Path(os.environ.get("TDX_ROOT", r"C:\new_tdx64"))
TQ_USER = TDX_ROOT / "PYPlugins" / "user"
VIPDOC = TDX_ROOT / "vipdoc"
E_ODATA = Path(os.environ.get("TDX_E_ODATA", r"E:\O_DATA"))

if str(TQ_USER) not in sys.path:
    sys.path.insert(0, str(TQ_USER))

try:
    from tqcenter import tq  # type: ignore
except Exception:  # pragma: no cover
    tq = None  # type: ignore


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


def code_to_vipdoc_path(code: str) -> Path:
    tcode = normalize_code(code)
    raw, suffix = tcode.split(".")
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(suffix)
    if not prefix:
        raise LocalTdxError(f"unsupported suffix: {tcode}")
    return VIPDOC / prefix / "lday" / f"{prefix}{raw}.day"


def read_e_odata_daily(code: str) -> pd.DataFrame:
    """Read downloaded CSV cache from E:\\O_DATA.

    Returns normalized lowercase OHLCV columns sorted ascending by date.
    """
    tcode = normalize_code(code)
    path = E_ODATA / f"{tcode}-all-latest.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame()
    rename = {
        "Date": "date",
        "Code": "code",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
        "Amount": "amount",
    }
    df = df.rename(columns=rename)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["code"] = tcode
    df["source"] = "e_odata"
    cols = ["date", "code", "open", "high", "low", "close", "volume", "amount", "source"]
    return df[[c for c in cols if c in df.columns]].dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def read_vipdoc_daily(code: str) -> pd.DataFrame:
    """Read local vipdoc daily .day file.

    Returns columns: date, open, high, low, close, amount, volume.
    Prices are in yuan.
    """
    path = code_to_vipdoc_path(code)
    if not path.exists():
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    raw = path.read_bytes()
    for i in range(0, len(raw), 32):
        chunk = raw[i : i + 32]
        if len(chunk) < 32:
            continue
        date, open_, high, low, close, amount, vol, _ = struct.unpack("IIIIIfII", chunk)
        rows.append(
            {
                "date": pd.to_datetime(str(date), format="%Y%m%d", errors="coerce"),
                "code": normalize_code(code),
                "open": open_ / 100,
                "high": high / 100,
                "low": low / 100,
                "close": close / 100,
                "amount": amount,
                "volume": vol,
                "source": "vipdoc",
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def require_tq() -> Any:
    if tq is None:
        raise LocalTdxError(f"cannot import tqcenter from {TQ_USER}")
    return tq


class TqSession:
    """Context manager for TQ lifecycle."""

    def __init__(self, strategy_path: str | None = None):
        # tqcenter uses the path as strategy identity. Add PID to avoid
        # "same strategy is already running" conflicts across short-lived tools.
        self.strategy_path = strategy_path or f"{__file__}#{os.getpid()}"
        self.tq = require_tq()

    def __enter__(self):
        self.tq.initialize(self.strategy_path)
        return self.tq

    def __exit__(self, exc_type, exc, tb):
        try:
            self.tq.close()
        except Exception:
            pass
        return False


def _price_df(data: dict[str, Any], field: str, columns: list[str]) -> pd.DataFrame:
    q = require_tq()
    try:
        return q.price_df(data, field, column_names=columns)
    except Exception:
        df = data.get(field)
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()


def get_market_data(
    codes: Iterable[str],
    period: str = "1d",
    count: int = 120,
    start_time: str = "",
    end_time: str = "",
    dividend_type: str = "front",
    fields: list[str] | None = None,
    fill_data: bool = True,
) -> dict[str, pd.DataFrame]:
    """Fetch K-line data through TQ and return field -> DataFrame."""
    code_list = [normalize_code(c) for c in codes]
    fields = fields or ["Open", "High", "Low", "Close", "Volume", "Amount"]
    with TqSession() as q:
        data = q.get_market_data(
            field_list=fields,
            stock_list=code_list,
            period=period,
            start_time=start_time,
            end_time=end_time,
            count=count,
            dividend_type=dividend_type,
            fill_data=fill_data,
        )
        return {field: _price_df(data, field, code_list) for field in fields}


def get_ohlcv_table(
    code: str,
    start_time: str = "",
    end_time: str = "",
    count: int = 120,
    dividend_type: str = "front",
    prefer: str = "tq",
) -> pd.DataFrame:
    """Return one-code OHLCV table.

    prefer='vipdoc' reads local .day first; prefer='tq' uses TQ first.
    """
    tcode = normalize_code(code)
    if prefer in ("vipdoc", "e_odata"):
        df = read_vipdoc_daily(tcode) if prefer == "vipdoc" else read_e_odata_daily(tcode)
        if not df.empty:
            if start_time:
                df = df[df["date"] >= pd.to_datetime(start_time, format="%Y%m%d", errors="coerce")]
            if end_time:
                df = df[df["date"] <= pd.to_datetime(end_time, format="%Y%m%d", errors="coerce")]
            if count and count > 0:
                df = df.tail(count)
            return df
    data = get_market_data([tcode], count=count, start_time=start_time, end_time=end_time, dividend_type=dividend_type)
    frames = []
    field_map = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
        "Amount": "amount",
    }
    for src, dst in field_map.items():
        df_field = data.get(src)
        if df_field is None or df_field.empty or tcode not in df_field.columns:
            continue
        frames.append(df_field[tcode].rename(dst))
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1)
    out.index = pd.to_datetime(out.index)
    out.index.name = "date"
    out = out.reset_index()
    out.insert(1, "code", tcode)
    out["source"] = "tq"
    return out


def get_snapshot(code: str) -> dict[str, Any]:
    """Get single stock/index snapshot via TQ."""
    tcode = normalize_code(code)
    with TqSession() as q:
        return q.get_market_snapshot(tcode)


def get_snapshots(codes: Iterable[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with TqSession() as q:
        for code in codes:
            tcode = normalize_code(code)
            try:
                result[tcode] = q.get_market_snapshot(tcode)
            except Exception as e:
                result[tcode] = {"ErrorId": "local_error", "Error": repr(e)}
    return result


def get_stock_list(pool_type: str = "5") -> list[str]:
    """Get stock list. pool_type='5' means all A shares in TQ examples."""
    with TqSession() as q:
        return list(q.get_stock_list(pool_type) or [])


def get_sector_list() -> list[str]:
    with TqSession() as q:
        return list(q.get_sector_list() or [])


def get_stock_list_in_sector(sector: str, block_type: int = 0) -> list[str]:
    with TqSession() as q:
        return list(q.get_stock_list_in_sector(sector, block_type=block_type) or [])


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def save_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def main() -> None:
    ap = argparse.ArgumentParser(description="Local TDX data utility")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_k = sub.add_parser("kline")
    p_k.add_argument("--code", required=True)
    p_k.add_argument("--count", type=int, default=120)
    p_k.add_argument("--start", default="")
    p_k.add_argument("--end", default="")
    p_k.add_argument("--prefer", default="tq", choices=["tq", "vipdoc", "e_odata"])
    p_k.add_argument("--out", default="")

    p_s = sub.add_parser("snapshot")
    p_s.add_argument("--codes", required=True, help="comma-separated codes")
    p_s.add_argument("--out", default="")

    p_l = sub.add_parser("stock-list")
    p_l.add_argument("--pool-type", default="5")
    p_l.add_argument("--out", default="")

    p_b = sub.add_parser("sector-list")
    p_b.add_argument("--out", default="")

    p_m = sub.add_parser("sector-members")
    p_m.add_argument("--sector", required=True)
    p_m.add_argument("--block-type", type=int, default=0)
    p_m.add_argument("--out", default="")

    args = ap.parse_args()

    if args.cmd == "kline":
        df = get_ohlcv_table(args.code, start_time=args.start, end_time=args.end, count=args.count, prefer=args.prefer)
        if args.out:
            save_csv(Path(args.out), df)
        print(df.tail(10).to_string(index=False))
    elif args.cmd == "snapshot":
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        obj = get_snapshots(codes)
        if args.out:
            save_json(Path(args.out), obj)
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str)[:5000])
    elif args.cmd == "stock-list":
        obj = get_stock_list(args.pool_type)
        if args.out:
            save_json(Path(args.out), obj)
        print(json.dumps({"count": len(obj), "preview": obj[:20]}, ensure_ascii=False, indent=2))
    elif args.cmd == "sector-list":
        obj = get_sector_list()
        if args.out:
            save_json(Path(args.out), obj)
        print(json.dumps({"count": len(obj), "preview": obj[:20]}, ensure_ascii=False, indent=2))
    elif args.cmd == "sector-members":
        obj = get_stock_list_in_sector(args.sector, block_type=args.block_type)
        if args.out:
            save_json(Path(args.out), obj)
        print(json.dumps({"count": len(obj), "preview": obj[:50]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
