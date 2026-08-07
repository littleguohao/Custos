# -*- coding: utf-8 -*-
"""动量因子（12-1 类）

`[t-skip-lb, t-skip]` 区间收益，跳过最近 20 日避开短期反转。
历史不足时自适应缩短回看窗口（≥40 根即产出）。

⚠️ 同为「特征溢价选择器」，**未跑过净值终审**。
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

FACTOR: dict[str, Any] = {
    "id": "momentum",
    "name": "动量因子（12-1 类）",
    "kind": "selector",
    "status": "untested",
    "evidence": "00_governance/research/R2_selection_price_volume.md",
    "note": "特征溢价类，未终审",
    "min_bars": 40,
    "live_use": "none",
    "stage": "debug",
}

def score(df: pd.DataFrame, code: str):
    """动量因子(12-1类)：score=[t-skip-lb, t-skip]区间收益(跳过最近20日避开短期反转)。中期强势择优。
    历史不足时自适应缩短回看窗口(≥40根即产出)。"""
    c = df["close"].astype(float).values
    n = len(c)
    if n < 40:
        return None
    skip = 20
    lb = min(100, n - skip - 1)
    base = c[-1 - skip - lb]
    mom = c[-1 - skip] / base - 1 if base else None
    if mom is None:
        return None
    return {"score": round(mom, 4), "suggestion": "可买",
            "aux": {"factor": f"momentum_{lb}_{skip}"}, "components": {}}
