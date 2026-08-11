# -*- coding: utf-8 -*-
"""小市值选择器

`score = -log10(信号日总市值/亿元)` —— 越小越高分。
真市值 = as-of 股本（东财 F10 全史）× 信号日收盘。

🟡 **待优化**：风格终审的跨窗共同点之一（小市值跑，5/6 窗，反向 AUC 0.572），
但净值终审 4 窗对照 baseline **2025 窗惨败 −9.3%、胜率仅 24.7%**。
机理：判别层只量**上涨端**，而 B1 的 8% 止损把垃圾股高波动的**下跌端**
对称兑现成亏损 —— 「垃圾股反弹 beta」用带止损的规则收割不到
（与 alpha101 同死法，第三次独立验证）。
"""
from __future__ import annotations

from typing import Any
import pandas as pd

from custos.core.factors._shares import shares_idx as _shares_idx  # ⚠️ 必须包限定：见 _shares 模块头

FACTOR: dict[str, Any] = {
    "id": "mcap",
    "name": "小市值选择器",
    "kind": "selector",
    "status": "needs_work",
    "evidence": "governance/research/R2_selection_price_volume.md",
    "note": "判别层过线、净值终审惨败；止损把下跌端对称兑现",
    "min_bars": 1,
    "live_use": "none",
    "stage": "debug",
}

def score(df: pd.DataFrame, code: str):
    """小市值选择器：score=-log10(信号日总市值/亿元),越小越高分(风格终审跨窗共同点:小市值反弹更强)。
    真市值=as-of 股本(东财 F10 全史)× 信号日收盘。无股本数据 → None(不参与排序,不误标)。"""
    import bisect as _b
    import math
    if len(df) < 1:
        return None
    evs = _shares_idx().get(str(code)[:6])
    if not evs:
        return None
    day = str(df["date"].iloc[-1])[:10]
    k = _b.bisect_right(evs, (day, float("inf"))) - 1
    close = float(df["close"].iloc[-1])
    if k < 0 or not evs[k][1] or not close:
        return None
    mc = evs[k][1] * close / 1e8
    return {"score": round(-math.log10(mc), 4), "suggestion": "可买",
            "aux": {"factor": "mcap_small", "mcap_yi": round(mc, 1)}, "components": {}}
