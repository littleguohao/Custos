# -*- coding: utf-8 -*-
"""Refresh and inspect the A-share calendar using local TDX JSON-RPC."""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from runtime_guards import trading_day_status
from paths import BASE, CONTRACTS_DIR, TOOLS, cn_today, cn_now
from paths import read_json as load_json

CONFIG = CONTRACTS_DIR / "CN_TRADING_CALENDAR.json"
CACHE = BASE / "01_data" / "market" / "CN_TRADING_CALENDAR_CACHE.json"
DEFAULT_ENDPOINT = "http://127.0.0.1:17709/"



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
    """向 TQ-Local 取交易日。**传输层走 `local_tdx/tq_http.call`**（2026-08-06 收敛）。

    原先这里自己拼 JSON-RPC + `urlopen`，与 `tq_http` 是同一个服务
    （两处都硬编码 `http://127.0.0.1:17709/`）却各写一套，于是拿不到 `tq_http`
    统一具备的东西：

      · **TdxW 未运行的预检** —— 否则只能收到一个 `connection refused`，
        看不出是「服务没起」还是「端口写错」
      · **统一错误分类**（`tdxw_not_running` / `timeout` / `connection_failed` /
        `request_failed`），会被 `refresh` 记进 `source.last_error` 供排查
      · 将来加在 `tq_http.call` 里的**安全拦截**（危险 down_type、stock_code 校验）
        自动生效，不会漏掉这一条路径

    这里仍**保持 raise 契约**：`refresh` 依赖 `except Exception` 记 `last_error`
    并保住旧缓存（`status="cache_preserved"`），改成返回 dict 会让失败被当成成功。
    """
    import sys as _sys
    d = str(TOOLS / "local_tdx")
    if d not in _sys.path:
        _sys.path.insert(0, d)
    import tq_http                                       # 懒导入：不扩大本模块的导入面
    res = tq_http.call(
        "get_trading_dates",
        {"market": market, "start_time": start.strftime("%Y%m%d"),
         "end_time": end.strftime("%Y%m%d"), "count": 0},
        timeout=timeout, endpoint=endpoint)
    if not res.get("ok"):
        err = res.get("error") or {}
        raise RuntimeError(
            f"TQ get_trading_dates 失败[{err.get('code', 'unknown')}]"
            f"{': ' + str(err['detail']) if err.get('detail') else ''}")
    days = extract_dates(res.get("value"))
    if not days:
        raise RuntimeError("TDX get_trading_dates returned no valid dates")
    return days


def calendar_days(start: date, end: date) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def merge_range(cfg: dict[str, Any], start: date, end: date, trading_days: list[str]) -> dict[str, Any]:
    """Merge an RPC trading-day answer into the cache.

    Only days inside the span the RPC actually answered for
    ``[min(trading_days), max(trading_days)]`` may be inferred as non-trading.
    The requested range is deliberately **not** used for that inference: the
    exchange only publishes the current year's schedule, so a request that
    reaches into next year comes back covering this year only. Treating the
    whole requested range as authoritative marked every remaining day of the
    following year as a confirmed market holiday, which made
    ``runtime_gate --require-trading-day`` exit 3 on real trading days and
    silently stalled every cron job. Days outside the answered span are left
    untouched so ``trading_day_status`` reports ``is_trading_day: None``
    ("don't know") instead of a confident False.
    """
    if not trading_days:
        raise RuntimeError("refuse to merge an empty trading_days set into the calendar cache")
    covered_lo, covered_hi = min(trading_days), max(trading_days)
    range_days = {d for d in calendar_days(start, end) if covered_lo <= d <= covered_hi}
    trading = set(cfg.get("trading_days", [])) - range_days
    closed = set(cfg.get("non_trading_days", [])) - range_days
    trading.update(trading_days)
    closed.update(range_days - set(trading_days))

    ranges = [x for x in cfg.get("covered_ranges", [])
              if x.get("start") != covered_lo or x.get("end") != covered_hi]
    ranges.append({"start": covered_lo, "end": covered_hi, "source": "local_tdx_http",
                   "requested": {"start": start.isoformat(), "end": end.isoformat()}})
    cfg["trading_days"] = sorted(trading)
    cfg["non_trading_days"] = sorted(closed)
    cfg["covered_ranges"] = sorted(ranges, key=lambda x: (x["start"], x["end"]))
    return cfg


def default_range(today: date) -> tuple[date, date]:
    """Requested refresh window.

    Reaching past the published schedule is harmless on purpose: ``merge_range``
    clamps its inference to whatever the RPC actually answers, so asking for
    more only helps when the exchange has already published the next year.
    """
    start = today.replace(day=1)
    return start, today + timedelta(days=370)


def refresh(start: date, end: date, endpoint: str, market: str, timeout: int) -> dict[str, Any]:
    config = load_json(CONFIG, {})
    cfg = load_json(CACHE, {"version": 1, "covered_ranges": [], "trading_days": [], "non_trading_days": []})
    source = cfg.setdefault("source", {})
    source.update({"provider": "local_tdx_http", "method": "get_trading_dates", "market": market, "endpoint": endpoint})
    source["last_refresh_at"] = cn_now().isoformat(timespec="seconds")
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
        # Answered span may be much shorter than the requested one (the exchange
        # only publishes the current year); surface it so a short answer is
        # visible instead of silently narrowing the calendar.
        "covered": ({"start": min(days), "end": max(days)} if days else None),
        "fetched_trading_days": len(days),
        "cached_trading_days": len(cfg.get("trading_days", [])),
        "cached_non_trading_days": len(cfg.get("non_trading_days", [])),
        "last_error": source.get("last_error"),
        "path": str(CACHE),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-date", help="return the deterministic trading-day status without refreshing TDX")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--market", default="SH")
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--require-refresh", action="store_true")
    args = parser.parse_args()
    if args.check_date:
        result = trading_day_status(date.fromisoformat(args.check_date).isoformat())
        print(json.dumps(result, ensure_ascii=True, indent=2))
        if result["is_trading_day"] is None:
            raise SystemExit(2)
        return
    default_start, default_end = default_range(cn_today())
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
