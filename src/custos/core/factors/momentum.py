# -*- coding: utf-8 -*-
"""动量因子（12-1 类）

`[t-skip-lb, t-skip]` 区间收益，跳过最近 20 日避开短期反转。
历史不足时自适应缩短回看窗口（≥40 根即产出）。

⚠️ 同为「特征溢价选择器」。**已降级「不再研究」（2026-08-18，
owner 拍板）**：钉死宇宙 300 只三窗口 × top-5 择优对照——仅 ytd26
相对最好（-7.3% vs -20.0%，仍负）、rally22 / bull2425 垫底，
无跨窗口稳健性。证据见 R2 第 16 条、`artifacts/logs/w35_*_momentum.json`。
"""

from __future__ import annotations

from typing import Any
import pandas as pd

FACTOR: dict[str, Any] = {
    "id": "momentum",
    "name": "动量因子（12-1 类）",
    "kind": "selector",
    "status": "needs_work",
    "evidence": "governance/research/R2_selection_price_volume.md",
    "note": "三窗对照 rally22/bull2425 垫底、无跨窗稳健性 ⇒ 降级不再研究(2026-08-18)",
    "min_bars": 40,
    "live_use": "none",
    "stage": "debug",
}


def score(df: pd.DataFrame, code: str) -> dict | None:
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
    return {
        "score": round(mom, 4),
        "suggestion": "可买",
        "aux": {"factor": f"momentum_{lb}_{skip}"},
        "components": {},
    }
