# -*- coding: utf-8 -*-
"""Collect holding quotes + index quotes via mootdx / tq_http / eastmoney.

持仓报价数据源优先级：
- intraday 非BJ: tq_http 快照 → mootdx 在线 bars → 域B在线日K(腾讯/新浪) → mootdx Reader 本地
- intraday BJ:   tq_http 快照 → mootdx Reader 本地 → 东财 push2（域B不支持BJ，不接）
- postclose 非BJ: mootdx Reader 本地 → tq_http 快照 → 域B在线日K(腾讯/新浪)
- postclose BJ:   mootdx Reader 本地 → tq_http 快照 → 东财 push2

域B = online_quotes 模块（腾讯/新浪日 K），不依赖 TdxW / mootdx / key，
作为 TDX 链路整体不可用时的独立在线兜底源。

指数报价数据源优先级（intraday/postclose 统一）：
tq_http 快照（999999.SH/399001.SZ/399006.SZ）→ mootdx 在线 index() →
mootdx Reader 本地（999999/399001/399006；注意 reader 里 000001 是平安银行）→
域B在线日K（sh000001/sz399001/sz399006；北证50 两源不支持，不接）。

tq_http 快照走 TdxW 本地 HTTP 服务（127.0.0.1:17709）；TdxW 未运行时
tq_http 干净返回 error，自然 fall through 到下一数据源。

CLI::

    uv run python src/custos/datasource/collect/collect_holding_quotes.py --date YYYY-MM-DD --session intraday
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import warnings

warnings.filterwarnings("ignore")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from custos.core.paths import TDX_ROOT, cn_today, cn_now, MARKET_DIR, TRADES_DIR  # noqa: E402
from custos.core.indicators import pct_change
from custos.core.code_utils import norm_code, fnum as _fnum  # noqa: E402
from custos.core.code_utils import market_of, is_a_share_position  # noqa: E402
from custos.core.contracts import require  # noqa: E402
from custos.datasource.local_tdx import tq_http  # noqa: E402
from custos.datasource.collect import online_quotes  # noqa: E402

from mootdx.reader import Reader  # noqa: E402
from mootdx.quotes import Quotes  # noqa: E402
from mootdx.consts import MARKET_SH, MARKET_SZ  # noqa: E402

_reader = None  # lazy init: local .day access only when actually needed
_client = None  # lazy init: only connect when online access is actually needed

# 东方财富 push2 的**公开** ut 参数(网页端前端 JS 里的固定串,全网通用):不是账号
# 凭据、不是密钥,所以留在源码里没有泄密问题;抽成常量只为不再散落魔法串。
EM_QUOTE_UT = "bd1d9ddb04089700cf256c0c7f8fe813"
EM_QUOTE_FIELDS = "f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f170"
# 代码白名单:6 位纯数字。code 来自 data/trades/current_positions.json —— 一个
# 由外部导出/人工编辑的文件,内容不可信。直接插进 URL 的 `secid=0.{code}` 里,
# 一个带 `&`/路径片段的脏值就能改写查询串(甚至换掉 secid 指向别的标的),
# 拿回来的价格却会被当成这只持仓的价格写进快照。故下标前先做字符集校验。
_CODE_RE = re.compile(r"^\d{6}$")


def _valid_code(code) -> bool:
    return bool(_CODE_RE.match(str(code))) if code is not None else False


def em_stock_url(code) -> str:
    """构造东财 push2 个股快照 URL;code 非 6 位纯数字直接 ValueError(不发请求)。"""
    if not _valid_code(code):
        raise ValueError(f"非法证券代码（要求 6 位数字）: {code!r}")
    return (
        "https://push2.eastmoney.com/api/qt/stock/get"
        f"?ut={EM_QUOTE_UT}&fltt=2&invt=2&fields={EM_QUOTE_FIELDS}&secid=0.{code}"
    )


def _get_reader():
    global _reader
    if _reader is None:
        _reader = Reader.factory(market="std", tdxdir=str(TDX_ROOT))
    return _reader


def _get_client(force_new: bool = False):
    """TDX 协议客户端。**复用 local_tdx_data 的重连实现**（单一定义）。

    原实现是「永不重连的进程级单例」——连接一断（TCP 空闲超时、服务器踢连接）之后
    每次调用都失败，而本模块是 14:45 / 17:00 采集持仓行情的必经之路，
    连接死了整条链的行情就没了。

    这是同一反模式在仓库里的**第三处**（`local_tdx_data._get_client` 与
    `market_timing/tdx_ext_quotes` 已分别修过）。见
    `governance/data/DATA_SOURCE_PRINCIPLE.md`「连接管理要求」，
    以及 `tests/test_tdx_connection_hygiene.py` 的自动检查。
    """
    global _client
    try:
        from custos.datasource.local_tdx import local_tdx_data as _ltd

        return _ltd._get_client(force_new=force_new)
    except Exception:  # noqa: BLE001 —— 导不到就退回本地实现，但仍带重建能力
        if force_new or _client is None:
            _client = Quotes.factory(market="std", quiet=True)
        return _client


def _client_call(fn, *, tries: int = 2, what: str = "tdx"):
    """调用 TDX 协议，连接失效时**重建连接**再试（用同一个死连接重试没有意义）。"""
    last = None
    for i in range(max(1, tries)):
        try:
            return fn(_get_client(force_new=(i > 0)))
        except Exception as e:  # noqa: BLE001
            last = e
            if i + 1 < tries:
                print(
                    f"[WARN] {what} 第 {i + 1} 次失败（{type(e).__name__}: {e}），"
                    f"重建连接重试",
                    file=sys.stderr,
                )
    raise RuntimeError(f"{what} 连续 {tries} 次失败: {last}")


def get_market(code: str) -> int:
    return {"BJ": 2, "SH": 1}.get(market_of(code), 0)


def _market_name(mkt: int) -> str:
    return "BJ" if mkt == 2 else ("SH" if mkt == 1 else "SZ")


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
    chg = pct_change(now, prev_close, digits=2)
    return {
        "code": code,
        "name": name,
        "market": _market_name(mkt),
        "available": True,
        # 快照没有日期字段,只能假定它就是目标日。**必须留痕**:postclose 时
        # TdxW 若未刷新,Now 其实是 T-1 收盘价,而下游唯一的陈旧检测是
        # `q["date"] != target`——硬写 target 会把这个检测消解掉(审计 C1)。
        "date": target,
        "date_verified": False,
        "time": cn_now().strftime("%Y-%m-%d %H:%M:%S"),
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
    df = _client_call(
        lambda c: c.bars(symbol=code, frequency=9, offset=2), what=f"bars({code})"
    )
    if df is None or len(df) == 0:
        return None
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else None
    prev_close = float(prev["close"]) if prev is not None else 0
    close = float(last["close"])
    chg = pct_change(close, prev_close, digits=2)
    dt = last.get("datetime", "")
    return {
        "code": code,
        "name": name,
        "market": _market_name(mkt),
        "available": True,
        "date": str(dt)[:10],
        "time": _fmt_dt(dt),
        "open": float(last["open"]),
        "high": float(last["high"]),
        "low": float(last["low"]),
        "close": close,
        "previous_close": prev_close,
        "change_pct": chg,
        "volume": float(last["volume"]),
        "amount": float(last.get("amount", 0)),
        "source": "mootdx_online_bars",
    }


def _online_daily_quote(code, name, mkt, target):
    """域 B 兜底：腾讯/新浪日 K 最后一根构造 quote（与 mootdx_online_bars 同构）。"""
    bars, source = online_quotes.fetch_online_daily(code, count=3)
    if not bars:
        return None
    last = bars[-1]
    prev_close = float(bars[-2]["close"]) if len(bars) > 1 else 0.0
    close = float(last["close"])
    chg = pct_change(close, prev_close, digits=2)
    return {
        "code": code,
        "name": name,
        "market": _market_name(mkt),
        "available": True,
        "date": str(last["date"])[:10],
        "date_verified": True,  # 日期取自 K 线本身
        "time": cn_now().strftime("%Y-%m-%d %H:%M:%S"),
        "open": float(last["open"]),
        "high": float(last["high"]),
        "low": float(last["low"]),
        "close": close,
        "previous_close": prev_close,
        "change_pct": chg,
        "volume": float(last["volume"]),
        "amount": 0.0,
        "source": source,
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
    chg = pct_change(close, prev_close, digits=2)
    last_date = last.name if hasattr(last.name, "strftime") else ""
    return {
        "code": code,
        "name": name,
        "market": _market_name(mkt),
        "available": True,
        "date": str(last_date)[:10],
        "date_verified": bool(last_date),  # 日期取自 K 线索引
        "time": str(last_date)[:19] if last_date else "",
        "open": float(last["open"]),
        "high": float(last["high"]),
        "low": float(last["low"]),
        "close": close,
        "previous_close": prev_close,
        "change_pct": chg,
        "volume": float(last["volume"]),
        "amount": float(last.get("amount", 0)),
        "source": "mootdx_reader",
    }


def _eastmoney_bj_quote(code, name, target):
    """东财 push2 API：BJ 股最后兜底（mootdx 不支持 BJ）。"""
    import requests as _req

    _url = em_stock_url(code)  # 先校验代码字符集,再建 session/发请求
    _s = _req.Session()
    _s.trust_env = False
    _r = _s.get(
        _url,
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0"},
        proxies={"http": None, "https": None},
    )
    _r.raise_for_status()
    _d = _r.json().get("data", {})
    if not _d or _d.get("f43") is None:
        return None
    close = float(_d["f43"])
    prev_close = float(_d.get("f60", 0))
    chg = pct_change(close, prev_close, digits=2)
    return {
        "code": code,
        "name": name,
        "market": "BJ",
        "available": True,
        "date": target,
        "date_verified": False,  # push2 快照无日期字段,同上
        "time": cn_now().strftime("%Y-%m-%d %H:%M:%S"),
        "open": float(_d.get("f46", 0)),
        "high": float(_d.get("f44", 0)),
        "low": float(_d.get("f45", 0)),
        "close": close,
        "previous_close": prev_close,
        "change_pct": chg,
        "volume": float(_d.get("f47", 0)),
        "amount": float(_d.get("f48", 0)),
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
                # 域 B 兜底（腾讯/新浪日 K，不依赖 TDX 链路）
                q = _try_quote(_online_daily_quote, code, name, mkt, target)
            if q is None:
                q = _try_quote(_reader_quote, code, name, mkt)
    else:  # postclose: reader 本地优先
        q = _try_quote(_reader_quote, code, name, mkt)
        if q is None or q.get("date", "") != target:
            q = _tq_snapshot_quote(code, name, mkt, target)
            if q is None and mkt != 2:
                # 域 B 兜底（腾讯/新浪日 K；BJ 不支持，走东财）
                q = _try_quote(_online_daily_quote, code, name, mkt, target)
    # BJ 最后兜底：东财 push2
    if q is None and mkt == 2:
        q = _try_quote(_eastmoney_bj_quote, code, name, target)
    return q


# 指数 canonical 代码 → 正确 TDX 代码。
# 注意：mootdx index() 里 000001 是上证指数，但 reader.daily() 里 000001 是
# 平安银行股票；reader/tq_http 必须用 999999（上证指数）才不会取错标的。
INDEX_SNAPSHOT_CODES = {
    "000001": "999999.SH",
    "399001": "399001.SZ",
    "399006": "399006.SZ",
}
INDEX_READER_SYMBOLS = {"000001": "999999", "399001": "399001", "399006": "399006"}
# 域 B（腾讯/新浪）指数代码：上证指数 sh000001；北证50(899050) 两源不支持，不接。
INDEX_ONLINE_SYMBOLS = {
    "000001": "sh000001",
    "399001": "sz399001",
    "399006": "sz399006",
}


def _tq_snapshot_index_quote(code, name):
    """TQ-Local HTTP 指数快照（TdxW 本地服务）。失败返回 None，绝不 raise。

    内部映射到正确 TDX 代码（000001→999999.SH），输出 code 保持 canonical。
    """
    try:
        resp = tq_http.snapshot(INDEX_SNAPSHOT_CODES[code])
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
    chg = pct_change(now, prev_close, digits=2)
    now_str = cn_now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "code": code,
        "name": name,
        "date": now_str[:10],
        "date_verified": False,
        "time": now_str,
        "close": now,
        "price": now,
        "previous_close": prev_close,
        "change_pct": chg,
        "volume": _fnum(v.get("Volume")) or 0.0,
        "source": "tq_http_snapshot",
    }


def _online_index_quote(code: str, name: str, mkt: int) -> dict | None:
    """mootdx 在线 index() 最后一根构造指数 quote；无数据返回 None。"""
    df = _client_call(
        lambda c: c.index(frequency=9, market=mkt, symbol=code, start=0, offset=2),
        what=f"index({code})",
    )
    if df is None or len(df) < 1:
        return None
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else None
    prev_close = float(prev["close"]) if prev is not None else 0
    close = float(last["close"])
    chg = pct_change(close, prev_close, digits=2)
    dt = last.get("datetime", "")
    return {
        "code": code,
        "name": name,
        "date": str(dt)[:10],
        "time": str(dt)[:19] if dt else "",
        "close": close,
        "price": close,
        "previous_close": prev_close,
        "change_pct": chg,
        "volume": float(last["volume"]),
        "source": "mootdx_online_index",
    }


def _reader_index_quote(code: str, name: str) -> dict | None:
    """本地 reader 指数日 K（用正确 TDX 代码，避免 000001 取到平安银行）。"""
    df = _get_reader().daily(symbol=INDEX_READER_SYMBOLS[code])
    if df is None or len(df) < 2:
        return None
    last = df.iloc[-1]
    prev = df.iloc[-2]
    prev_close = float(prev["close"])
    close = float(last["close"])
    chg = pct_change(close, prev_close, digits=2)
    last_date = str(last.name)[:19] if hasattr(last.name, "strftime") else ""
    return {
        "code": code,
        "name": name,
        "date": last_date[:10],
        "time": last_date,
        "close": close,
        "price": close,
        "previous_close": prev_close,
        "change_pct": chg,
        "volume": float(last["volume"]),
        "source": "mootdx_reader",
    }


def _online_daily_index_quote(code: str, name: str) -> dict | None:
    """域 B 最后兜底：腾讯/新浪指数日 K（独立在线源，不依赖 TDX 链路）。"""
    bars, source = online_quotes.fetch_online_daily(INDEX_ONLINE_SYMBOLS[code], count=3)
    if not bars:
        return None
    last = bars[-1]
    prev_close = float(bars[-2]["close"]) if len(bars) > 1 else 0.0
    close = float(last["close"])
    chg = pct_change(close, prev_close, digits=2)
    return {
        "code": code,
        "name": name,
        "date": str(last["date"])[:10],
        "time": str(last["date"])[:10],
        "close": close,
        "price": close,
        "previous_close": prev_close,
        "change_pct": chg,
        "volume": float(last["volume"]),
        "source": source,
    }


def _collect_one_index(code: str, name: str, mkt: int) -> dict:
    """按数据源优先级采集单个指数报价，全部失败标 unavailable。"""
    idx = _tq_snapshot_index_quote(code, name)
    # mootdx 在线 index()
    if idx is None:
        try:
            idx = _online_index_quote(code, name, mkt)
        except Exception as e:
            print(f"[WARN] {e}", file=sys.stderr)
    # fallback: local reader
    if idx is None:
        try:
            idx = _reader_index_quote(code, name)
        except Exception as e:
            print(f"[WARN] {e}", file=sys.stderr)
    # 域 B 最后兜底
    if idx is None:
        try:
            idx = _online_daily_index_quote(code, name)
        except Exception as e:
            print(f"[WARN] {e}", file=sys.stderr)
    if idx is None:
        return {"code": code, "name": name, "available": False, "reason": "no data"}
    return idx


def _collect_indices(session):
    """Collect indices: tq_http 快照 → mootdx 在线 index() → reader 本地（三种 session 统一）。"""
    indices = []
    for code, name, mkt in [
        ("000001", "上证指数", MARKET_SH),
        ("399001", "深证成指", MARKET_SZ),
        ("399006", "创业板指", MARKET_SZ),
    ]:
        indices.append(_collect_one_index(code, name, mkt))
    return indices


def _collect_breadth():
    """Collect 880 series market breadth (local Reader first, online fallback)."""
    breadth = {}
    for code, name in [
        ("880001", "平均股价"),
        ("880005", "涨跌家数"),
        ("880006", "停板家数"),
        ("880390", "融资融券"),
        ("880863", "北向资金"),
    ]:
        # Local reader
        try:
            df = _get_reader().daily(symbol=code)
            if df is not None and len(df) >= 2:
                last = df.iloc[-1]
                prev = df.iloc[-2]
                prev_close = float(prev["close"])
                close = float(last["close"])
                breadth[code] = {
                    "name": name,
                    "close": close,
                    "previous_close": prev_close,
                    "change_pct": pct_change(close, prev_close, digits=2),
                    "date": str(last.name if hasattr(last.name, "strftime") else ""),
                    "source": "mootdx_reader",
                }
                continue
        except Exception as e:
            print(f"[WARN] {e}", file=sys.stderr)
        # Online fallback
        try:
            df = _client_call(
                lambda c: c.index(
                    frequency=9, market=MARKET_SH, symbol=code, start=0, offset=2
                ),
                what=f"index({code})",
            )
            if df is not None and len(df) >= 1:
                last = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else None
                prev_close = float(prev["close"]) if prev is not None else 0
                close = float(last["close"])
                breadth[code] = {
                    "name": name,
                    "close": close,
                    "previous_close": prev_close,
                    "change_pct": pct_change(close, prev_close, digits=2),
                    "date": str(last["datetime"]),
                    "source": "mootdx_online",
                }
        except Exception as e:
            breadth[code] = {"name": name, "error": str(e)}
    return breadth


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=cn_today().strftime("%Y-%m-%d"))
    ap.add_argument("--session", choices=["intraday", "postclose"], default="intraday")
    args = ap.parse_args(argv)
    target = args.date

    # Load positions
    raw = json.loads(
        (TRADES_DIR / "current_positions.json").read_text(encoding="utf-8")
    )
    holdings = raw if isinstance(raw, list) else raw.get("holdings", [])

    holding_quotes = []
    skipped_non_a = 0
    for h in holdings:
        code = str(h.get("代码", h.get("code", ""))).zfill(6)
        name = h.get("名称", h.get("name", ""))
        # 非 A 股持仓（如港股 02158）跳过 A 股取价链路：裸码会被 market_of
        # 误判为深市个股（深市 002158 是汉钟精机），取到张冠李戴的价格。
        # 台账/持仓快照保留全账户记录，仅 A 股行情采集跳过。
        if not is_a_share_position(h):
            skipped_non_a += 1
            print(
                f"[INFO] 跳过非A股持仓 {code} {name}（A股取价链不覆盖）",
                file=sys.stderr,
            )
            continue
        # 代码不合白名单一律不进任何数据源(URL/接口参数拼接前的统一拦截点),
        # 如实标 unavailable 而不是拿一个脏 code 去问东财/腾讯。
        if not _valid_code(code):
            print(f"[WARN] 跳过非法持仓代码 {code!r}（要求 6 位数字）", file=sys.stderr)
            holding_quotes.append(
                {
                    "code": code,
                    "name": name,
                    "market": None,
                    "available": False,
                    "reason": "invalid code",
                }
            )
            continue
        mkt = get_market(code)
        q = _holding_quote(code, name, mkt, args.session, target)
        if q is not None:
            q["price"] = q.get("close")
            holding_quotes.append(q)
        else:
            holding_quotes.append(
                {
                    "code": code,
                    "name": name,
                    "market": _market_name(mkt),
                    "available": False,
                    "reason": "no data",
                }
            )

    indices = _collect_indices(args.session)
    breadth = _collect_breadth()

    # Write output (preserve unavailable stocks from previous file)
    out_path = MARKET_DIR / f"{target}_holding_quotes.json"
    if out_path.exists():
        try:
            prev_data = json.loads(out_path.read_text(encoding="utf-8"))
            prev_map = {
                q["code"]: q for q in prev_data.get("quotes", []) if q.get("available")
            }
            for q in holding_quotes:
                if not q.get("available") and q["code"] in prev_map:
                    q.update(prev_map[q["code"]])
        except Exception as e:
            print(f"[WARN] {e}", file=sys.stderr)
    output = {
        "as_of_date": target,
        "captured_at": cn_now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "source": "mootdx",
        "quotes": holding_quotes,
        "indices": indices,
        "breadth": breadth,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # ⚠️ 落盘前校验：5 个消费者、⛔硬失败链。分支型 —— 取不到数的票
    # 只有 code/name/market/available/reason，所以只有普遍字段进契约。
    require("holding_quotes", output)
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    ok = sum(1 for q in holding_quotes if q.get("available"))
    print(
        f"collected {ok}/{len(holdings)} holdings + {len(indices)} indices + {len(breadth)} breadth -> {out_path.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
