# -*- coding: utf-8 -*-
"""低波动因子（low-vol anomaly）

`score = -近20日收益率标准差` —— 越稳越高分。

⚠️ 来自 Fama-French 讨论里的「特征溢价选择器」。**已降级「不再研究」
（2026-08-18，owner 拍板）**：钉死宇宙 300 只三窗口 × top-5 择优对照
——bull2425 小幅跑赢（+14.0% vs -1.4%）、rally22 / ytd26 跑输，
无跨窗口稳健性。证据见 R2 第 16 条、`artifacts/logs/w35_*_low_vol.json`。
"""

from __future__ import annotations

from typing import Any
import pandas as pd

FACTOR: dict[str, Any] = {
    "id": "low_vol",
    "name": "低波动因子（low-vol anomaly）",
    "kind": "selector",
    "status": "needs_work",
    "evidence": "governance/research/R2_selection_price_volume.md",
    "note": "三窗对照 rally22/ytd26 跑输、无跨窗稳健性 ⇒ 降级不再研究(2026-08-18)",
    "min_bars": 21,
    "live_use": "none",
    "stage": "debug",
}


def score(df: pd.DataFrame, code: str) -> dict | None:
    """低波动因子(low-vol anomaly)：score=-近20日收益率标准差(越稳越高分)。选波动小的B1候选。"""
    if len(df) < 21:
        return None
    rets = df["close"].astype(float).pct_change().iloc[-20:]
    vol = float(rets.std())
    if vol != vol:
        return None
    return {
        "score": round(-vol, 6),
        "suggestion": "可买",
        "aux": {"factor": "low_vol", "vol_20d": round(vol, 4)},
        "components": {},
    }
