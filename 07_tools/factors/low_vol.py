# -*- coding: utf-8 -*-
"""低波动因子（low-vol anomaly）

`score = -近20日收益率标准差` —— 越稳越高分。

⚠️ 来自 Fama-French 讨论里的「特征溢价选择器」，**未跑过净值终审**。
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

FACTOR: dict[str, Any] = {
    "id": "low_vol",
    "name": "低波动因子（low-vol anomaly）",
    "kind": "selector",
    "status": "untested",
    "evidence": "00_governance/research/R2_selection_price_volume.md",
    "note": "特征溢价类，未终审",
    "min_bars": 21,
    "live_use": "none",
    "stage": "debug",
}

def score(df: pd.DataFrame, code: str):
    """低波动因子(low-vol anomaly)：score=-近20日收益率标准差(越稳越高分)。选波动小的B1候选。"""
    if len(df) < 21:
        return None
    rets = df["close"].astype(float).pct_change().iloc[-20:]
    vol = float(rets.std())
    if vol != vol:
        return None
    return {"score": round(-vol, 6), "suggestion": "可买",
            "aux": {"factor": "low_vol", "vol_20d": round(vol, 4)}, "components": {}}
