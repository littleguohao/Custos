# -*- coding: utf-8 -*-
"""overseas market collector v1.

Fetches overseas indices / tech leaders from Yahoo Finance chart API and writes them
into strategy_team/01_data/market/YYYY-MM-DD_market_timing_input.json.

No API key required. If a symbol fails, it is preserved as missing with an error note.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import BASE, cn_now  # noqa: E402

MARKET_DIR = BASE / "01_data" / "market"

from net_retry import retry_call  # noqa: E402

SYMBOLS = {
    "dow": {"symbol": "^DJI", "name": "道琼斯工业指数", "group": "index", "region": "us"},
    "nasdaq": {"symbol": "^IXIC", "name": "纳斯达克综合指数", "group": "index", "region": "us"},
    "sp500": {"symbol": "^GSPC", "name": "标普500", "group": "index", "region": "us"},
    "sox": {"symbol": "^SOX", "name": "费城半导体指数", "group": "index", "region": "us"},
    "nikkei": {"symbol": "^N225", "name": "日经225", "group": "index", "region": "jp"},
    "kospi": {"symbol": "^KS11", "name": "韩国KOSPI", "group": "index", "region": "kr"},
    "hstech": {"symbol": "3067.HK", "name": "恒生科技指数(iShares 3067.HK ETF代理,^HSTECH已被Yahoo下架404,^HSCI无数据)", "group": "index"},
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
    with retry_call(lambda: urllib.request.urlopen(req, timeout=20)) as resp:
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
    timestamps = r.get("timestamp") or []
    quote = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    if change_pct is None and price is not None:
        # chartPreviousClose is the close before the 5d range start, not the
        # previous session; prefer the last two real closes when available.
        close_vals = [float(c) for c in closes if c is not None]
        if len(close_vals) >= 2 and close_vals[-2]:
            change_pct = (close_vals[-1] / close_vals[-2] - 1) * 100
        elif prev:
            change_pct = (price / prev - 1) * 100
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
    ap.add_argument("--date", default=cn_now().strftime("%Y-%m-%d"))
    ap.add_argument("--input", default="")
    args = ap.parse_args()
    inp = Path(args.input) if args.input else MARKET_DIR / f"{args.date}_market_timing_input.json"
    if inp.exists():
        data = json.loads(inp.read_text(encoding="utf-8"))
    else:
        data = {"date": args.date}

    details: dict[str, Any] = {}
    errors: dict[str, str] = {}
    fallback_used: dict[str, str] = {}
    for key, meta in SYMBOLS.items():
        try:
            details[key] = {**meta, **fetch_chart(meta["symbol"], meta.get("region", ""))}
        except Exception as e:
            errors[key] = repr(e)
            # Yahoo 失败 → 试 TDX 扩展市场（owner 原则：本地 TDX 优先，HTTP 不稳定）。
            # 只是**降级**不是替代：ext 覆盖不全（无 A50、无 USDCNH、无指数本身，
            # 指数只能用 ETF 代理），且代理有跟踪误差与交易时段差异。
            # 拿到数据时如实标 proxy/proxy_note，不让读报告的人误当成指数本身。
            alt = None
            try:
                # ⚠️ 必须与调用方走同一条导入路径,否则同一文件会被加载成两个模块
                # (tdx_ext_quotes 与 market_timing.tdx_ext_quotes),monkeypatch/异常
                # 捕获都会对不上。本模块既当脚本跑也被当包模块导入,故包内优先、脚本回退。
                try:
                    from .tdx_ext_quotes import fetch_ext_change  # noqa: PLC0415
                except ImportError:
                    from tdx_ext_quotes import fetch_ext_change   # noqa: PLC0415
                alt = fetch_ext_change(meta["symbol"])
            except Exception as e2:  # noqa: BLE001
                print(f"[WARN] TDX ext fallback 不可用: {type(e2).__name__}: {e2}",
                      file=sys.stderr)
            if alt and alt.get("change_pct") is not None:
                details[key] = {**meta, "symbol": meta["symbol"], **alt,
                                "yahoo_error": repr(e), "degraded": True}
                fallback_used[key] = alt.get("source", "tdx_ext")
                print(f"[INFO] {key} 走 TDX ext 降级：{alt.get('source')}"
                      f"{'（' + alt['proxy_note'] + '）' if alt.get('proxy_note') else ''}",
                      file=sys.stderr)
            else:
                details[key] = {**meta, "symbol": meta["symbol"], "change_pct": None,
                                "error": repr(e), "source": "Yahoo Finance chart API"}
        time.sleep(0.2)

    overseas = data.setdefault("overseas_market", {})
    for key, field in FIELD_MAP.items():
        overseas[field] = details.get(key, {}).get("change_pct")
    overseas["overall_signal"] = classify(details)
    overseas["overseas_summary"] = impact_summary(details)
    overseas["details"] = details
    overseas["errors"] = errors
    overseas["source"] = "Yahoo Finance chart API"
    if fallback_used:
        # 留痕：下游据此知道这批数字里有代理值，不得当成指数本身
        overseas["fallback_source"] = fallback_used
        overseas["source"] = "Yahoo Finance chart API + TDX ext fallback"
    # as_of: latest last_timestamp across all symbols (epoch -> Asia/Shanghai ISO);
    # falls back to collection time when no symbol returned a timestamp.
    ts_vals = [d.get("last_timestamp") for d in details.values() if isinstance(d, dict) and isinstance(d.get("last_timestamp"), (int, float))]
    if ts_vals:
        overseas["as_of"] = datetime.fromtimestamp(max(ts_vals), ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        overseas["as_of_basis"] = "max(last_timestamp) across symbols"
    else:
        # ⚠️ **不编 as_of**（2026-08-10，TODO #52 路子①）。
        #    此前这里写 `datetime.now()`，于是门控的判据「有值 且 as_of 非空」
        #    对一批**没有任何时间戳**的数字给出最强结论 `confirmed`。
        #    而这条路径不是罕见分支：Yahoo 不可达时走的 TDX ext 降级
        #    (`tdx_ext_quotes.fetch_ext_change`) **根本不返回 last_timestamp**
        #    ⇒ Yahoo 全挂时必然走到这里，而那恰恰是最需要判新鲜度的时候
        #    （实测那一跑的 sox 值还是 SOXX ETF 的代理值）。
        #    与契约层已拍板的 `amv_0.as_of` 同一原则 —— 原话：
        #    **「编一个 as_of 等于给门控一个假的新鲜度」**。
        #    形状也对齐 amv_0：**键存在、值为 None**（不是省略键）——
        #    null 与缺失在下游会走不同分支。门控据此判 `candidate`（值还在）。
        overseas["as_of"] = None
        overseas["as_of_basis"] = "no_timestamp_from_any_symbol"
        # 采集时刻本身有排障价值，但它**不是数据新鲜度** —— 换个键名存，别让它冒充。
        overseas["collected_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    overseas["quality"] = "auto" if not errors else "degraded"

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
