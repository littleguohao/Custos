# -*- coding: utf-8 -*-
"""海外行情的 TDX 扩展市场 fallback（owner 原则：本地 TDX 优先，HTTP 不稳定）。

**这是 fallback，不是替代。** Yahoo 仍是主路径，原因是 TDX 扩展市场覆盖不全：

    可用   NVDA / AMD / TSM 等美股个股（market=74，17146 个品种）
           QQQ / SPY / DIA 等**指数 ETF**（只能代理指数，不是指数本身）
           港股（market=71）
    缺失   **A50 期货**（新交所品种，通达信国内版不含）
           **USDCNH 汇率**（只有美股的汇率 ETF：FXA/FXC/FXF/FXY）
           指数本身（^DJI / ^IXIC / ^GSPC / ^SOX / ^N225 / ^KS11 都没有）

既然无法完整替代，混用两个源反而会让口径不一致，所以定位为「Yahoo 失败时的降级」：
有数据总比没有好，但**必须标注它是代理**，不能让读报告的人以为拿到的是指数本身。

实测 2026-08-04：`Quotes.factory(market='ext')` 能取到 NVDA 日线
（2026-08-03 收 206.64）。mootdx 会打警告「目前扩展市场行情接口已经失效」——
该警告是过时的，实测数据正常返回。

ETF 代理与指数的口径差（报告里要如实说明）：
  · ETF 有跟踪误差与溢价/折价
  · 交易时段可能不同（如日经 ETF 在 A 股时段交易，日经指数在东京时段）
  · 因此 change_pct 与指数本身**不会完全一致**，只可用于方向性参考
"""

from __future__ import annotations

import sys
import time

from custos.core.indicators import pct_change
from typing import Any, Optional

# Yahoo symbol → (ext market, code, 是否为代理, 说明)
EXT_MAP: dict[str, tuple[int, str, bool, str]] = {
    # 美股个股：口径一致，非代理
    "NVDA": (74, "NVDA", False, ""),
    "AMD": (74, "AMD", False, ""),
    "TSM": (74, "TSM", False, ""),
    # 指数 → ETF 代理
    "^DJI": (74, "DIA", True, "道琼斯工业ETF(DIA)代理指数"),
    "^IXIC": (74, "QQQ", True, "NASDAQ100 ETF(QQQ)代理纳斯达克综合(成分不同)"),
    "^GSPC": (74, "SPY", True, "标普500 ETF(SPY)代理指数"),
    "^SOX": (74, "SOXX", True, "费城半导体ETF(SOXX)代理指数"),
    # 港股 ETF：Yahoo 那边本来也已经是 ETF 代理
    "3067.HK": (71, "03067", True, "安硕恒生科技ETF(与 Yahoo 同一标的)"),
}

_client = None
_client_created_at = 0.0
CLIENT_MAX_AGE_SEC = 600.0  # 与 local_tdx_data 同口径：超时连接必须重建而非复用


def _get_ext_client(timeout: int = 12):
    """进程级缓存 ext client，但**会重建**——超龄（600s）即新建。

    第一版是"建一次用一辈子"的单例：ext 连接一死，fallback 在进程余生里静默失效
    （返回 None 不报错，降级名存实亡）。这正是 503b77d 在 std 行情修掉、并写进
    DATA_SOURCE_PRINCIPLE.md 连接管理规范的那个反模式。
    """
    global _client, _client_created_at
    now = time.monotonic()
    if _client is None or (now - _client_created_at) > CLIENT_MAX_AGE_SEC:
        from mootdx.quotes import Quotes

        _client = Quotes.factory(market="ext", timeout=timeout)
        _client_created_at = now
    return _client


def _drop_ext_client() -> None:
    """连接判死后丢弃缓存，下次调用重建。"""
    global _client
    _client = None


def fetch_ext_change(symbol: str, *, timeout: int = 12) -> Optional[dict[str, Any]]:
    """取一个品种的最新涨跌幅。不支持的 symbol 返回 None（调用方保留原 error）。

    返回 {change_pct, last_close, prev_close, stale_bars_skipped, source, proxy, proxy_note}。
    ``stale_bars_skipped`` 是被滤掉的脏 bar（close≤0）根数 —— 非 0 意味着「最新」
    收盘其实来自更早的 bar，时间窗已平移，调用方据此自行裁量可信度。
    """
    ent = EXT_MAP.get(symbol)
    if ent is None:
        return None
    market, code, is_proxy, note = ent
    df = None
    for attempt in range(2):
        try:
            q = _get_ext_client(timeout)
            df = q.bars(symbol=code, market=market, frequency=9, offset=4)
            break
        except Exception as e:  # noqa: BLE001
            _drop_ext_client()  # 连接可能已死：丢弃缓存，重建后再试一次
            if attempt == 1:
                print(
                    f"[WARN] TDX ext {symbol}({code}) 取数失败: {type(e).__name__}: {e}",
                    file=sys.stderr,
                )
                return None
    if df is None or len(df) < 2 or "close" not in df.columns:
        return None
    try:
        closes = [float(x) for x in df["close"].tolist() if float(x) > 0]
    except (TypeError, ValueError):
        return None
    if len(closes) < 2:
        return None
    # 脏 bar（close≤0：停牌/缺数）被滤掉后，若**最后一根**恰是脏 bar，「最新」收盘
    # 其实是更早的 bar —— 时间窗悄悄平移。不能静默：跳过根数随结果暴露，并打 WARN。
    stale_bars_skipped = len(df) - len(closes)
    if stale_bars_skipped:
        print(
            f"[WARN] TDX ext {symbol}({code}) 滤掉 {stale_bars_skipped} 根脏 bar（close≤0），"
            f"change_pct 按更早的有效 bar 计算",
            file=sys.stderr,
        )
    last, prev = closes[-1], closes[-2]
    return {
        "change_pct": pct_change(last, prev, digits=3),
        "last_close": round(last, 4),
        "prev_close": round(prev, 4),
        "stale_bars_skipped": stale_bars_skipped,
        "source": f"TDX ext (market={market}, {code})",
        "proxy": is_proxy,
        "proxy_note": note,
    }


def supported() -> list[str]:
    return sorted(EXT_MAP)


__all__ = ["EXT_MAP", "fetch_ext_change", "supported"]
