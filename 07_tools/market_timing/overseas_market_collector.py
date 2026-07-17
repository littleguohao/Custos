# -*- coding: utf-8 -*-
"""overseas market collector v1.

Fetches overseas indices / tech leaders from Yahoo Finance chart API and writes them
into strategy_team/01_data/market/YYYY-MM-DD_market_timing_input.json.

No API key required. If a symbol fails, it is preserved as missing with an error note.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
MARKET_DIR = BASE / "01_data" / "market"

SYMBOLS = {
    "dow": {"symbol": "^DJI", "name": "道琼斯工业指数", "group": "index", "region": "us"},
    "nasdaq": {"symbol": "^IXIC", "name": "纳斯达克综合指数", "group": "index", "region": "us"},
    "sp500": {"symbol": "^GSPC", "name": "标普500", "group": "index", "region": "us"},
    "sox": {"symbol": "^SOX", "name": "费城半导体指数", "group": "index", "region": "us"},
    "nikkei": {"symbol": "^N225", "name": "日经225", "group": "index", "region": "jp"},
    "kospi": {"symbol": "^KS11", "name": "韩国KOSPI", "group": "index", "region": "kr"},
    "hstech": {"symbol": "^HSCI", "name": "恒生综合指数", "group": "index"},
    "nvda": {"symbol": "NVDA", "name": "英伟达", "group": "ai_leader"},
    "amd": {"symbol": "AMD", "name": "AMD", "group": "ai_leader"},
    "tsm": {"symbol": "TSM", "name": "台积电ADR", "group": "semiconductor"},
    "samsung": {"symbol": "005930.KS", "name": "三星电子", "group": "semiconductor"},
    "sk_hynix": {"symbol": "000660.KS", "name": "SK海力士", "group": "semiconductor"},
}

FIELD_MAP = {
    "dow": "dow_change_pct",
    "nasdaq": "nasdaq_change_pct",
    "sp500": "sp500_change_pct",
    "sox": "sox_change_pct",
    "nikkei": "nikkei_change_pct",
    "kospi": "kospi_change_pct",
    "hstech": "hstech_change_pct",
}


def fetch_chart(symbol: str, region: str = "") -> dict[str, Any]:
    encoded = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=5d&interval=1d&includePrePost=false"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 OpenClaw strategy_team market collector",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        err = (data.get("chart") or {}).get("error")
        raise RuntimeError(f"empty chart result: {err}")
    r = result[0]
    meta = r.get("meta") or {}
    price = meta.get("regularMarketPrice")
    prev = meta.get("previousClose") or meta.get("chartPreviousClose")
    change_pct = meta.get("regularMarketChangePercent")
    if change_pct is None and price is not None and prev:
        change_pct = (price / prev - 1) * 100
    timestamps = r.get("timestamp") or []
    quote = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    last_ts = meta.get("regularMarketTime") or (timestamps[-1] if timestamps else None)
    local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
    asia_live = region in {"jp", "kr"} and 8 <= local_now.hour < 15
    return {
        "symbol": symbol,
        "price": round(float(price), 4) if price is not None else None,
        "previous_close": round(float(prev), 4) if prev is not None else None,
        "change_pct": round(float(change_pct), 4) if change_pct is not None else None,
        "currency": meta.get("currency"),
        "exchange": meta.get("exchangeName") or meta.get("fullExchangeName"),
        "market_state": meta.get("marketState"),
        "data_kind": "最新" if meta.get("marketState") == "REGULAR" or asia_live else "收盘",
        "last_timestamp": last_ts,
        "last_time_local_hint": datetime.fromtimestamp(last_ts,ZoneInfo("Asia/Shanghai")).isoformat() if isinstance(last_ts, (int, float)) else None,
        "recent_closes": [round(float(x), 4) if x is not None else None for x in closes[-5:]],
        "source": "Yahoo Finance chart API",
    }


def classify(details: dict[str, Any]) -> str:
    vals = [v.get("change_pct") for v in details.values() if isinstance(v, dict) and v.get("change_pct") is not None]
    if not vals:
        return "缺失"
    avg = sum(vals) / len(vals)
    if avg >= 1.0:
        return "利多"
    if avg <= -1.0:
        return "利空"
    return "中性"


def impact_summary(details: dict[str, Any]) -> str:
    def v(k):
        item = details.get(k) or {}
        return item.get("change_pct")
    sox, nvda, amd, tsm = v("sox"), v("nvda"), v("amd"), v("tsm")
    hstech = v("hstech")
    nikkei, kospi, samsung, hynix = v("nikkei"), v("kospi"), v("samsung"), v("sk_hynix")
    parts = []
    tech_vals = [x for x in [sox, nvda, amd, tsm] if x is not None]
    if tech_vals:
        avg_tech = sum(tech_vals) / len(tech_vals)
        if avg_tech > 1:
            parts.append("美股AI/半导体链偏强，利于A股AI算力、半导体、光模块、PCB等风险偏好")
        elif avg_tech < -1:
            parts.append("美股AI/半导体链偏弱，A股科技成长追高权限应下降")
        else:
            parts.append("美股AI/半导体链整体中性")
    asia_vals = [x for x in [nikkei, kospi, samsung, hynix] if x is not None]
    if asia_vals:
        avg_asia = sum(asia_vals) / len(asia_vals)
        if avg_asia > 1:
            parts.append("日韩科技链偏强，强化亚洲半导体/存储/HBM景气映射")
        elif avg_asia < -1:
            parts.append("日韩科技链偏弱，对A股半导体链形成压力")
    if hstech is not None:
        if hstech > 1:
            parts.append("恒生科技偏强，有利于港股科技及A股AI应用/互联网映射")
        elif hstech < -1:
            parts.append("恒生科技偏弱，压制科技成长风险偏好")
    return "；".join(parts) if parts else "外围细分影响暂缺。"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--input", default="")
    args = ap.parse_args()
    inp = Path(args.input) if args.input else MARKET_DIR / f"{args.date}_market_timing_input.json"
    if inp.exists():
        data = json.loads(inp.read_text(encoding="utf-8"))
    else:
        data = {"date": args.date}

    details: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key, meta in SYMBOLS.items():
        try:
            details[key] = {**meta, **fetch_chart(meta["symbol"], meta.get("region", ""))}
        except Exception as e:
            errors[key] = repr(e)
            details[key] = {**meta, "symbol": meta["symbol"], "change_pct": None, "error": repr(e), "source": "Yahoo Finance chart API"}
        time.sleep(0.2)

    overseas = data.setdefault("overseas_market", {})
    for key, field in FIELD_MAP.items():
        overseas[field] = details.get(key, {}).get("change_pct")
    overseas["overall_signal"] = classify(details)
    overseas["overseas_summary"] = impact_summary(details)
    overseas["details"] = details
    overseas["errors"] = errors
    overseas["source"] = "Yahoo Finance chart API"
    overseas["quality"] = "auto" if len(errors) < len(SYMBOLS) else "missing"

    dq = data.setdefault("data_quality", {})
    dq.setdefault("sources", []).append("overseas_market_collector:yahoo_finance")
    if errors:
        dq.setdefault("notes", []).append(f"外围市场部分标的抓取失败：{', '.join(errors.keys())}")
    else:
        dq.setdefault("notes", []).append("外围市场由 Yahoo Finance chart API 自动采集。")

    MARKET_DIR.mkdir(parents=True, exist_ok=True)
    inp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(inp)
    print(json.dumps({"overall_signal": overseas.get("overall_signal"), "summary": overseas.get("overseas_summary"), "errors": errors}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
