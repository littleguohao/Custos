# -*- coding: utf-8 -*-
"""域 B 独立在线行情源：腾讯 / 新浪日 K。

不依赖 TdxW / mootdx / 任何 key，用于 TDX 链路整体不可用时的在线兜底。

- 腾讯日 K: ``web.ifzq.gtimg.cn/appstock/app/fqkline/get``
  列序为 ``date, open, close, high, low, volume``（注意 close 在第 3 列）。
- 新浪日 K: ``quotes.sina.cn ... CN_MarketDataService.getKLineData``
  需带 ``Referer: https://finance.sina.com.cn``。

统一返回 ``[{"date","open","high","low","close","volume"}, ...]``（时间升序），
失败返回 None。北交所（bj）两源均不支持，直接返回 None。
"""
from __future__ import annotations

import sys

import requests

from custos.core.net_retry import fetch_with_retry

TENCENT_DAILY_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{count},"
)
SINA_DAILY_URL = (
    "https://quotes.sina.cn/cn/api/json_v2.php/"
    "CN_MarketDataService.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={count}"
)
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
TIMEOUT = 15


def _warn_dropped(source: str, code: str, dropped: int, kept: int) -> None:
    """脏行必须告警计数。

    为什么改成逐行容错:原来行内任一字段解析失败就 ``return None`` 丢掉**整批**——
    腾讯/新浪偶发在最新一根返回 ``"-"``/空串(盘中未成交、停牌),于是"今天有一行脏数据"
    等于"这只票在域 B 完全没有行情",直接把兜底源打成不可用。现在跳过坏行、保留好行,
    并把丢弃数打到 stderr,避免静默降级。
    """
    if dropped:
        print(f"[WARN] {source} {code}: dropped {dropped} malformed row(s), kept {kept}",
              file=sys.stderr)

_session = requests.Session()
_session.trust_env = False  # 与 _eastmoney_bj_quote 一致，避免系统代理干扰直连


def _prefixed_symbol(code: str) -> str | None:
    """6 位代码加市场前缀（6/9→sh、0/3→sz）；已带 sh/sz 前缀的原样返回。

    北交所（920/8/4 开头）两源均不支持，返回 None。
    """
    s = str(code).strip().lower()
    if s[:2] in ("sh", "sz"):
        return s
    s = s.zfill(6)
    if s.startswith(("920", "8", "4")):
        return None
    if s.startswith(("6", "9")):
        return "sh" + s
    if s.startswith(("0", "3")):
        return "sz" + s
    return None


def fetch_tencent_daily(code: str, count: int = 3) -> list[dict] | None:
    """腾讯日 K；返回统一格式 bars（升序），失败/不支持返回 None。"""
    symbol = _prefixed_symbol(code)
    if symbol is None:
        return None
    try:
        resp = fetch_with_retry(
            TENCENT_DAILY_URL.format(symbol=symbol, count=count),
            timeout=TIMEOUT, session=_session)
        payload = resp.json()
    except Exception:
        return None
    data = (payload or {}).get("data") or {}
    node = data.get(symbol) or {}
    rows = node.get("day") or node.get("qfqday") or []
    bars = []
    dropped = 0
    for row in rows:
        try:
            # 腾讯列序: date, open, close, high, low, volume
            bars.append({
                "date": str(row[0])[:10],
                "open": float(row[1]),
                "high": float(row[3]),
                "low": float(row[4]),
                "close": float(row[2]),
                "volume": float(row[5]),
            })
        except (TypeError, ValueError, IndexError, KeyError):
            dropped += 1
    _warn_dropped("tencent_daily", str(code), dropped, len(bars))
    return bars or None


def fetch_sina_daily(code: str, count: int = 3) -> list[dict] | None:
    """新浪日 K；返回统一格式 bars（升序），失败/不支持返回 None。"""
    symbol = _prefixed_symbol(code)
    if symbol is None:
        return None
    try:
        resp = fetch_with_retry(
            SINA_DAILY_URL.format(symbol=symbol, count=count),
            timeout=TIMEOUT, session=_session, headers=SINA_HEADERS)
        rows = resp.json()
    except Exception:
        return None
    if not isinstance(rows, list):
        return None
    bars = []
    dropped = 0
    for r in rows:
        try:
            bars.append({
                "date": str(r["day"])[:10],
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
            })
        except (TypeError, ValueError, KeyError, IndexError):
            dropped += 1
    _warn_dropped("sina_daily", str(code), dropped, len(bars))
    return bars or None


def fetch_online_daily(code: str, count: int = 3) -> tuple[list[dict] | None, str | None]:
    """域 B 入口：tencent → sina 顺序尝试。

    返回 ``(bars, source)``；全部失败返回 ``(None, None)``。
    source 为 ``"tencent_daily"`` 或 ``"sina_daily"``。
    """
    bars = fetch_tencent_daily(code, count)
    if bars:
        return bars, "tencent_daily"
    bars = fetch_sina_daily(code, count)
    if bars:
        return bars, "sina_daily"
    return None, None
