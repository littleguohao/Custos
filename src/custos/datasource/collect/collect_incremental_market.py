# -*- coding: utf-8 -*-
"""Collect incremental market data: A50 futures, CNH exchange rate, limit-up/down ladder, northbound.

结构说明:解析/推导逻辑抽成纯函数(parse_yahoo_payload / derive_breadth /
derive_northbound),CLI 只负责取数与落盘 —— 采集脚本的解析分支正是最容易
静默出错的地方(平盘 0.0 被当缺数、result=null 直接下标),必须可单测。
"""
from __future__ import annotations
import json, sys, warnings
from datetime import datetime

warnings.filterwarnings("ignore")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from custos.core.code_utils import fnum as _fnum

from custos.core.paths import TDX_ROOT, cn_today, cn_now, MARKET_DIR

import urllib.request, urllib.parse

from custos.core.net_retry import retry_call
import sys

BREADTH_CODES = [("880001", "平均股价"), ("880005", "涨跌家数"), ("880006", "停板家数"),
                 ("880390", "融资融券"), ("880863", "北向资金")]
NORTHBOUND_CODE = "880863"
NORTHBOUND_MIN_ROWS = 5


def parse_yahoo_payload(symbol: str, data: dict) -> dict:
    """解析 Yahoo chart 响应。

    两处历史坑:
    1. ``round(float(chg), 4) if chg else None`` —— 涨跌幅**恰好平盘 0.0** 时
       落成 None,下游把"平盘"读成"没采到数据"(A50/汇率平盘并不罕见)。
       所有数值判定改用 ``is not None``。
    2. ``(data.get("chart") or {}).get("result", [{}])[0]`` —— Yahoo 报错时
       ``result`` 是 **JSON null**(不是缺键),``.get`` 返回 None → ``None[0]``
       抛 TypeError,真因(chart.error 里的 Not Found / Invalid symbol)被吞掉。
       现在先校验结构,拿不到就抛 ValueError 带上 error 信息。
    """
    chart = (data or {}).get("chart") or {}
    results = chart.get("result")
    if not isinstance(results, list) or not results:
        err = chart.get("error")
        raise ValueError(f"Yahoo chart 无 result（symbol={symbol}, error={err!r}）")
    r = results[0]
    if not isinstance(r, dict):
        raise ValueError(f"Yahoo chart result[0] 结构异常（symbol={symbol}）: {type(r).__name__}")
    meta = r.get("meta") or {}
    price = _fnum(meta.get("regularMarketPrice"))
    prev = _fnum(meta.get("previousClose"))
    if prev is None:
        prev = _fnum(meta.get("chartPreviousClose"))
    chg = _fnum(meta.get("regularMarketChangePercent"))
    if chg is None and price is not None and prev:
        chg = (price / prev - 1) * 100
    mtime = meta.get("regularMarketTime")
    as_of = (datetime.fromtimestamp(mtime).astimezone().isoformat(timespec="seconds")
             if isinstance(mtime, (int, float)) else None)
    return {"symbol": symbol,
            "price": round(price, 4) if price is not None else None,
            "previous_close": round(prev, 4) if prev is not None else None,
            "change_pct": round(chg, 4) if chg is not None else None,
            "as_of": as_of,
            "source": "Yahoo Finance"}


def fetch_yahoo(symbol: str) -> dict:
    encoded = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=5d&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with retry_call(lambda: urllib.request.urlopen(req, timeout=15)) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return parse_yahoo_payload(symbol, data)


def make_reader() -> tuple[object | None, str | None]:
    """Reader 创建失败必须落进输出 JSON 的 error 字段,而不是让脚本在写盘前崩掉。"""
    try:
        from mootdx.reader import Reader
        return Reader.factory(market="std", tdxdir=str(TDX_ROOT)), None
    except Exception as e:
        return None, str(e)


def derive_breadth(reader, reader_error: str | None) -> dict:
    """880 系列宽度。

    **样本不足/读不到时必须显式写键**:以前是直接不写该 code,下游只能看到"没有
    880005 这一项",无法区分「本次没采到」与「采到了但为空」,更无法归因。
    现在写 ``status: unavailable`` + reason,合并侧据此跳过(不会并出一个空段)。
    """
    if reader is None:
        return {"status": "unavailable",
                "reason": f"mootdx Reader unavailable: {reader_error}",
                "_error": {"error": f"mootdx Reader unavailable: {reader_error}"}}
    out: dict = {}
    for code, name in BREADTH_CODES:
        try:
            df = reader.daily(symbol=code)
            rows = 0 if df is None else len(df)
            if rows < 2:
                out[code] = {"name": name, "status": "unavailable",
                             "reason": f"样本不足（需 ≥2 根 K 线，实得 {rows}）", "rows": rows}
                continue
            last = df.iloc[-1]
            prev = df.iloc[-2]
            prev_close = float(prev["close"])
            close = float(last["close"])
            out[code] = {
                "name": name, "status": "ok", "close": close, "previous_close": prev_close,
                "change_pct": round((close / prev_close - 1) * 100, 2) if prev_close else None,
                "amount": (float(last["amount"]) if "amount" in df.columns and last["amount"] == last["amount"] else None),
                "previous_amount": (float(prev["amount"]) if "amount" in df.columns and prev["amount"] == prev["amount"] else None),
                "date": str(last.name if hasattr(last.name, 'strftime') else ''),
                "up_count": int(last.get("up_count", 0)) if "up_count" in df.columns else None,
                "down_count": int(last.get("down_count", 0)) if "down_count" in df.columns else None,
            }
        except Exception as e:
            out[code] = {"name": name, "status": "error", "error": str(e)}
    return out


def derive_northbound(reader, reader_error: str | None) -> dict:
    """北向资金 5 日趋势;样本不足同样显式标 unavailable(此前整键缺失)。"""
    if reader is None:
        return {"status": "unavailable",
                "error": f"mootdx Reader unavailable: {reader_error}",
                "reason": f"mootdx Reader unavailable: {reader_error}"}
    try:
        df = reader.daily(symbol=NORTHBOUND_CODE)
        rows = 0 if df is None else len(df)
        if rows < NORTHBOUND_MIN_ROWS:
            return {"status": "unavailable", "rows": rows,
                    "reason": f"样本不足（需 ≥{NORTHBOUND_MIN_ROWS} 根 K 线，实得 {rows}）"}
        last5 = df.tail(NORTHBOUND_MIN_ROWS)
        first_close = float(last5.iloc[0]["close"])
        latest_close = float(last5.iloc[-1]["close"])
        return {
            "status": "ok",
            "latest_close": latest_close,
            "last_5d_change": round((latest_close / first_close - 1) * 100, 2) if first_close else None,
            "trend": "up" if latest_close > first_close else "down",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _a50_sanity(result: dict) -> None:
    """|change_pct| > 3% 通常是 previous_close 错位(换月/元数据滞后),仅标记不改值。"""
    a50 = result.get("a50_futures") or {}
    chg = a50.get("change_pct")
    if isinstance(chg, (int, float)) and abs(chg) > 3:
        a50["suspect"] = True
        a50["note"] = "change_pct 超过 ±3%，存在 previous_close 错位(换月/元数据滞后)风险，使用前需人工核对"


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=cn_today().strftime("%Y-%m-%d"))
    args = ap.parse_args(argv)
    target = args.date

    result = {"date": target, "collected_at": cn_now().strftime("%Y-%m-%dT%H:%M:%S+08:00")}

    # ===== 1. A50 futures + CNH (Yahoo Finance) =====
    try:
        result["a50_futures"] = fetch_yahoo("CFF=A50")
    except Exception:
        try:
            result["a50_futures"] = fetch_yahoo("XIN9.FGI")
        except Exception as e:
            result["a50_futures"] = {"error": str(e), "note": "A50 CFD unavailable via Yahoo, use web_search in report"}
    _a50_sanity(result)

    try:
        result["cnh_usd"] = fetch_yahoo("USDCNH=X")
    except Exception as e:
        result["cnh_usd"] = {"error": str(e)}

    # ===== 2/4. Market breadth + northbound via mootdx Reader (local) =====
    reader, reader_error = make_reader()
    result["breadth"] = derive_breadth(reader, reader_error)

    # ===== 3. Limit-up/down ladder (collected by post-close review enrichment) =====
    result["limit_ladder"] = {"note": "collected via tdx_screener in post-close enrichment"}

    result["northbound"] = derive_northbound(reader, reader_error)

    # ===== Write output =====
    out_path = MARKET_DIR / f"{target}_incremental_market.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"incremental market data -> {out_path.name}")
    for k, v in result.items():
        if isinstance(v, dict) and "change_pct" in v:
            print(f"  {k}: {v.get('change_pct', '?')}%")
        elif isinstance(v, dict) and "close" in v:
            print(f"  {k}: close={v['close']}")
        elif isinstance(v, dict) and "latest_close" in v:
            print(f"  {k}: {v['latest_close']} (5d={v.get('last_5d_change','?')}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
