# -*- coding: utf-8 -*-
"""overseas market collector v2（Kimi Work 内置数据源）。

默认经 Kimi Work 内置 Yahoo 获取海外指数 / 科技龙头，写入
``data/market/YYYY-MM-DD_market_timing_input.json``，不再依赖本机 Yahoo/VPN
直连。旧 Yahoo chart + TDX ext 仅在 ``CUSTOS_LEGACY_OVERSEAS=1`` 时应急启用。
任一标的失败均保留缺数与错误，不编造价格或新鲜度。
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


from custos.core.paths import cn_now, write_json_atomic, MARKET_DIR  # noqa: E402
from custos.core.contracts import require  # noqa: E402
from custos.core.indicators import pct_change  # noqa: E402  涨跌幅判定/显示统一 round-2


from custos.core.net_retry import retry_call  # noqa: E402

SYMBOLS = {
    "dow": {
        "symbol": "^DJI",
        "name": "道琼斯工业指数",
        "group": "index",
        "region": "us",
    },
    "nasdaq": {
        "symbol": "^IXIC",
        "name": "纳斯达克综合指数",
        "group": "index",
        "region": "us",
    },
    "sp500": {"symbol": "^GSPC", "name": "标普500", "group": "index", "region": "us"},
    "sox": {
        "symbol": "^SOX",
        "name": "费城半导体指数",
        "group": "index",
        "region": "us",
    },
    "nikkei": {"symbol": "^N225", "name": "日经225", "group": "index", "region": "jp"},
    "kospi": {"symbol": "^KS11", "name": "韩国KOSPI", "group": "index", "region": "kr"},
    "hstech": {
        "symbol": "3067.HK",
        "name": "恒生科技指数(iShares 3067.HK ETF代理,^HSTECH已被Yahoo下架404,^HSCI无数据)",
        "group": "index",
    },
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


def _fetch_chart_json(symbol: str) -> dict[str, Any]:
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
    return json.loads(raw)


def _derive_change_pct(
    closes: list[Any], price: Any, prev: Any, change_pct: Any
) -> Any:
    if change_pct is None and price is not None:
        # chartPreviousClose is the close before the 5d range start, not the
        # previous session; prefer the last two real closes when available.
        close_vals = [float(c) for c in closes if c is not None]
        # 2026-08-11（#56 保留项④，owner 拍板）：判定/显示精度全仓统一 round-2。
        if len(close_vals) >= 2 and close_vals[-2]:
            change_pct = pct_change(close_vals[-1], close_vals[-2], digits=2)
        elif prev:
            change_pct = pct_change(price, prev, digits=2)
    return change_pct


def _chart_result_dict(
    symbol: str,
    meta: dict[str, Any],
    price: Any,
    prev: Any,
    change_pct: Any,
    last_ts: Any,
    closes: list[Any],
    asia_live: bool,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "price": round(float(price), 4) if price is not None else None,
        "previous_close": round(float(prev), 4) if prev is not None else None,
        "change_pct": round(float(change_pct), 4) if change_pct is not None else None,
        "currency": meta.get("currency"),
        "exchange": meta.get("exchangeName") or meta.get("fullExchangeName"),
        "market_state": meta.get("marketState"),
        "data_kind": "最新"
        if meta.get("marketState") == "REGULAR" or asia_live
        else "收盘",
        "last_timestamp": last_ts,
        "last_time_local_hint": datetime.fromtimestamp(
            last_ts, ZoneInfo("Asia/Shanghai")
        ).isoformat()
        if isinstance(last_ts, (int, float))
        else None,
        "recent_closes": [
            round(float(x), 4) if x is not None else None for x in closes[-5:]
        ],
        "source": "Yahoo Finance chart API",
    }


def _legacy_overseas_enabled() -> bool:
    """旧 Yahoo 直连 / TDX ext 仅作显式应急通道，默认不依赖 VPN。"""
    import os

    return os.environ.get("CUSTOS_LEGACY_OVERSEAS") == "1"


def _fetch_chart_legacy(symbol: str, region: str = "") -> dict[str, Any]:
    data = _fetch_chart_json(symbol)
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
    change_pct = _derive_change_pct(closes, price, prev, change_pct)
    last_ts = meta.get("regularMarketTime") or (timestamps[-1] if timestamps else None)
    local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
    asia_live = region in {"jp", "kr"} and 8 <= local_now.hour < 15
    return _chart_result_dict(
        symbol, meta, price, prev, change_pct, last_ts, closes, asia_live
    )


def _fetch_kimiwork_chart(symbol: str, region: str = "") -> dict[str, Any]:
    """内置 Yahoo 日 K → 原有 chart 结构；涨跌幅按最近两根真实收盘计算。"""
    from custos.datasource import kimiwork_data  # noqa: PLC0415

    rows = []
    currency = None
    for row in kimiwork_data.yahoo_daily(symbol):
        try:
            rows.append((str(row["Date"]), float(row["Close"])))
            currency = row.get("currency")
        except (KeyError, TypeError, ValueError):
            continue
    if len(rows) < 2:
        raise ValueError(f"Kimi Work Yahoo 有效日 K 不足 2 根: {symbol}")
    rows.sort(key=lambda item: item[0])
    closes = [close for _, close in rows]
    last_day, price = rows[-1]
    prev = rows[-2][1]
    change_pct = pct_change(price, prev, digits=2)
    last_dt = datetime.fromisoformat(last_day.replace("Z", "+00:00"))
    last_ts = last_dt.timestamp()
    local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
    asia_live = region in {"jp", "kr"} and 8 <= local_now.hour < 15
    # 交易所与实时市场状态若源未提供就留空，不猜测。
    meta = {
        "currency": None if currency in (None, "", "NA") else currency,
        "exchangeName": None,
        "marketState": None,
    }
    result = _chart_result_dict(
        symbol, meta, price, prev, change_pct, last_ts, closes, asia_live
    )
    result["source"] = "Kimi Work / Yahoo Finance"
    return result


def fetch_chart(symbol: str, region: str = "") -> dict[str, Any]:
    if _legacy_overseas_enabled():
        return _fetch_chart_legacy(symbol, region)
    return _fetch_kimiwork_chart(symbol, region)


def classify(details: dict[str, Any]) -> str:
    vals = [
        c
        for v in details.values()
        if isinstance(v, dict) and (c := v.get("change_pct")) is not None
    ]
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
            parts.append(
                "美股AI/半导体链偏强，利于A股AI算力、半导体、光模块、PCB等风险偏好"
            )
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
    inp = (
        Path(args.input)
        if args.input
        else MARKET_DIR / f"{args.date}_market_timing_input.json"
    )
    if inp.exists():
        data = json.loads(inp.read_text(encoding="utf-8"))
    else:
        data = {"date": args.date}

    details: dict[str, Any] = {}
    errors: dict[str, str] = {}
    fallback_used: dict[str, str] = {}
    for key, meta in SYMBOLS.items():
        try:
            details[key] = {
                **meta,
                **fetch_chart(meta["symbol"], meta.get("region", "")),
            }
        except Exception as e:
            errors[key] = repr(e)
            if not _legacy_overseas_enabled():
                # 默认不再尝试 VPN 直连或 TDX ext；缺数显式留痕并快速结束。
                details[key] = {
                    **meta,
                    "symbol": meta["symbol"],
                    "change_pct": None,
                    "error": repr(e),
                    "source": "Kimi Work / Yahoo Finance",
                }
                continue
            # 仅 CUSTOS_LEGACY_OVERSEAS=1 时试 TDX 扩展市场。
            # 只是**降级**不是替代：ext 覆盖不全（无 A50、无 USDCNH、无指数本身，
            # 指数只能用 ETF 代理），且代理有跟踪误差与交易时段差异。
            # 拿到数据时如实标 proxy/proxy_note，不让读报告的人误当成指数本身。
            alt = None
            try:
                # ⚠️ 必须与调用方走同一条导入路径,否则同一文件会被加载成两个模块
                # 包式绝对导入：脚本与包两种模式下都是同一份模块对象
                # （custos 可编辑安装），monkeypatch/异常捕获对得上。
                from custos.datasource.tdx_ext_quotes import fetch_ext_change_bounded  # noqa: PLC0415

                # 硬墙 30s：mootdx ext bars 内部 tenacity 无 stop，接口死时会无限重连
                # （约 40s/轮），必须在调用方切断，否则拖垮整条 daily_pipeline。
                alt = fetch_ext_change_bounded(meta["symbol"], wall_timeout=30.0)
            except Exception as e2:  # noqa: BLE001
                print(
                    f"[WARN] TDX ext fallback 不可用: {type(e2).__name__}: {e2}",
                    file=sys.stderr,
                )
            if alt and alt.get("change_pct") is not None:
                details[key] = {
                    **meta,
                    "symbol": meta["symbol"],
                    **alt,
                    "yahoo_error": repr(e),
                    "degraded": True,
                }
                fallback_used[key] = alt.get("source", "tdx_ext")
                print(
                    f"[INFO] {key} 走 TDX ext 降级：{alt.get('source')}"
                    f"{'（' + alt['proxy_note'] + '）' if alt.get('proxy_note') else ''}",
                    file=sys.stderr,
                )
            else:
                details[key] = {
                    **meta,
                    "symbol": meta["symbol"],
                    "change_pct": None,
                    "error": repr(e),
                    "source": "Yahoo Finance chart API",
                }
        time.sleep(0.2)

    overseas = data.setdefault("overseas_market", {})
    for key, field in FIELD_MAP.items():
        overseas[field] = details.get(key, {}).get("change_pct")
    overseas["overall_signal"] = classify(details)
    overseas["overseas_summary"] = impact_summary(details)
    overseas["details"] = details
    overseas["errors"] = errors
    source_names = sorted(
        {
            str(d.get("source"))
            for d in details.values()
            if isinstance(d, dict) and d.get("source")
        }
    )
    base_source = " + ".join(source_names) or "Kimi Work / Yahoo Finance"
    overseas["source"] = base_source
    if fallback_used:
        # 留痕：下游据此知道这批数字里有代理值，不得当成指数本身
        overseas["fallback_source"] = fallback_used
        overseas["source"] = base_source + " + TDX ext fallback"
    # as_of: latest last_timestamp across all symbols (epoch -> Asia/Shanghai ISO);
    # falls back to collection time when no symbol returned a timestamp.
    ts_vals = [
        d.get("last_timestamp")
        for d in details.values()
        if isinstance(d, dict) and isinstance(d.get("last_timestamp"), (int, float))
    ]
    if ts_vals:
        overseas["as_of"] = datetime.fromtimestamp(
            max(ts_vals), ZoneInfo("Asia/Shanghai")
        ).isoformat(timespec="seconds")
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
        overseas["collected_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(
            timespec="seconds"
        )
    overseas["quality"] = "auto" if not errors else "degraded"

    dq = data.setdefault("data_quality", {})
    dq.setdefault("sources", []).append("overseas_market_collector:kimiwork")
    if errors:
        dq.setdefault("notes", []).append(
            f"外围市场部分标的抓取失败：{', '.join(errors.keys())}"
        )
    else:
        dq.setdefault("notes", []).append("外围市场由 Kimi Work 内置数据源自动采集。")

    MARKET_DIR.mkdir(parents=True, exist_ok=True)
    # ⚠️ 落盘前校验：本采集器只负责 overseas_market / data_quality 两节，
    # 不为 collector/amv_state 写的其他节背责（only 语义见 contracts._narrow）。
    require("market_timing_input", data, only=("overseas_market", "data_quality"))
    # 读-改-写的共享文件：collector → merge → amv_state 依次改写同一份 ⇒ 原子写
    write_json_atomic(inp, data)
    print(inp)
    print(
        json.dumps(
            {
                "overall_signal": overseas.get("overall_signal"),
                "summary": overseas.get("overseas_summary"),
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
