# -*- coding: utf-8 -*-
"""Alpha#101：进场 K 的日内实体强度

`(close-open)/((high-low)+.001)` —— 收盘越靠上越强。

🟡 **待优化**：判别层是唯一同号率 100% 的候选（5 窗，中位 AUC 0.54），
但净值对照 4 窗里 **2025 最近窗明确输**（+0.31→-0.24）。
判别增益（+7.6pp）在实现层被并发上限 + 成本吃净。
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

FACTOR: dict[str, Any] = {
    "id": "alpha101",
    "name": "Alpha#101：进场 K 的日内实体强度",
    "kind": "selector",
    "status": "needs_work",
    "evidence": "00_governance/research/R2_selection_price_volume.md",
    "note": "判别层过线但净值终审未过；2025 窗明确输",
    "min_bars": 1,
}

def score(df: pd.DataFrame, code: str):
    """Alpha#101 = (close-open)/((high-low)+.001)：进场K日内强度(收盘越靠上越强)。选强收盘的B1候选。"""
    if len(df) < 1:
        return None
    o = float(df["open"].iloc[-1]); c = float(df["close"].iloc[-1])
    h = float(df["high"].iloc[-1]); l = float(df["low"].iloc[-1])
    return {"score": round((c - o) / ((h - l) + 0.001), 4), "suggestion": "可买",
            "aux": {"alpha": "101_close_open_range"}, "components": {}}
