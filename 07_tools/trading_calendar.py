# -*- coding: utf-8 -*-
"""Refresh and inspect the A-share calendar using local TDX JSON-RPC."""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import request

BASE = Path(__file__).resolve().parents[1]
CONFIG = BASE / "00_governance" / "CN_TRADING_CALENDAR.json"
CACHE = BASE / "01_data" / "market" / "CN_TRADING_CALENDAR_CACHE.json"
DEFAULT_ENDPOINT = "http://127.0.0.1:17709/"


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else default


def normalize_day(value: Any) -> str | None:
    text = str(value or "").strip().replace("-", "")[:8]
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8])).isoformat()
    except ValueError:
        return None


def extract_dates(response: Any) -> list[str]:
    value = response.get("result", response) if isinstance(response, dict) else response
    if isinstance(value, dict):
        value = value.get("Date", value.get("date", value.get("dates", [])))
    if not isinstance(value, list):
        return []
    return sorted({day for item in value if (day := normalize_day(item))})


def rpc_trading_dates(endpoint: str, market: str, start: date, end: date, timeout: int) -> list[str]:
    payload = {
        "id": 1,
        "method": "get_trading_dates",
        "params": {
            "market": market,
            "start_time": start.strftime("%Y%m%d"),
            "end_time": end.strftime("%Y%m%d"),
            "count": 0,
        },
    }
    req = request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if isinstance(body, dict) and body.get("error"):
        raise RuntimeError(str(body["error"]))
    days = extract_dates(body)
    if not days:
        raise RuntimeError("TDX get_trading_dates returned no valid dates")
    return days


def calendar_days(start: date, end: date) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def merge_range(cfg: dict[str, Any], start: date, end: date, trading_days: list[str]) -> dict[str, Any]:
    range_days = set(calendar_days(start, end))
    trading = set(cfg.get("trading_days", [])) - range_days
    closed = set(cfg.get("non_trading_days", [])) - range_days
    trading.update(trading_days)
    closed.update(range_days - set(trading_days))

    ranges = [x for x in cfg.get("covered_ranges", []) if x.get("start") != start.isoformat() or x.get("end") != end.isoformat()]
    ranges.append({"start": start.isoformat(), "end": end.isoformat(), "source": "local_tdx_http"})
    cfg["trading_days"] = sorted(trading)
    cfg["non_trading_days"] = sorted(closed)
    cfg["covered_ranges"] = sorted(ranges, key=lambda x: (x["start"], x["end"]))
    return cfg


def default_range(today: date) -> tuple[date, date]:
    start = today.replace(day=1)
    return start, today + timedelta(days=370)


def refresh(start: date, end: date, endpoint: str, market: str, timeout: int) -> dict[str, Any]:
    config = load_json(CONFIG, {})
    cfg = load_json(CACHE, {"version": 1, "covered_ranges": [], "trading_days": [], "non_trading_days": []})
    source = cfg.setdefault("source", {})
    source.update({"provider": "local_tdx_http", "method": "get_trading_dates", "market": market, "endpoint": endpoint})
    source["last_refresh_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        days = rpc_trading_dates(endpoint, market, start, end, timeout)
        cfg = merge_range(cfg, start, end, days)
        source["last_success_at"] = source["last_refresh_at"]
        source["last_error"] = None
        status = "updated"
    except Exception as exc:
        source["last_error"] = f"{type(exc).__name__}: {exc}"
        status = "cache_preserved"
        days = []
    cfg["timezone"] = config.get("timezone", "Asia/Shanghai")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "status": status,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "fetched_trading_days": len(days),
        "cached_trading_days": len(cfg.get("trading_days", [])),
        "cached_non_trading_days": len(cfg.get("non_trading_days", [])),
        "last_error": source.get("last_error"),
        "path": str(CACHE),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--market", default="SH")
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--require-refresh", action="store_true")
    args = parser.parse_args()
    default_start, default_end = default_range(date.today())
    start = date.fromisoformat(args.start) if args.start else default_start
    end = date.fromisoformat(args.end) if args.end else default_end
    if end < start:
        parser.error("--end must not be earlier than --start")
    result = refresh(start, end, args.endpoint, args.market, args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.require_refresh and result["status"] != "updated":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
