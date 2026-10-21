# -*- coding: utf-8 -*-
"""低波动因子（low-vol anomaly）

`score = -近20日收益率标准差` —— 越稳越高分。

⚠️ 来自 Fama-French 讨论里的「特征溢价选择器」。**已降级「不再研究」
（2026-08-18，owner 拍板）**：钉死宇宙 300 只三窗口 × top-5 择优对照
——bull2425 小幅跑赢（+14.0% vs -1.4%）、rally22 / ytd26 跑输，
无跨窗口稳健性。证据见 R2 第 16 条、`artifacts/logs/w35_*_low_vol.json`。
"""

from __future__ import annotations

from typing import Any, Optional
import pandas as pd

FACTOR: dict[str, Any] = {
    "id": "low_vol",
    "name": "低波动因子（low-vol anomaly）",
    "kind": "selector",
    "status": "needs_work",
    "evidence": "governance/research/R2_selection_price_volume.md",
    "research_ref": ["R2"],  # TODO #73 谱系回填（与 evidence 同源）
    "note": "三窗对照 rally22/ytd26 跑输、无跨窗稳健性 ⇒ 降级不再研究(2026-08-18)",
    "min_bars": 21,
    "live_use": "none",
    "stage": "debug",
}


def score(
    df: pd.DataFrame, code: str, precomputed: Optional[dict] = None
) -> dict | None:
    """低波动因子(low-vol anomaly)：score=-近20日收益率标准差(越稳越高分)。选波动小的B1候选。

    ``precomputed``：evaluate_trades 逐股预计算的全序列（键 rets = close 的
    ``pct_change()`` 全序列，float pd.Series，RangeIndex），传入时按 ``len(df)``
    取末 20 点——pct_change 是逐位运算（pandas 3 无前向填充），前缀末 20 点与
    全序列同位段是**同一串浮点运算**，std 输入逐位相同 ⇒ 结果逐位相同
    （rolling std 是另一套在线算法，不保证逐位，故**不**向量化）。
    ⚠️ 只对「从第 0 根开始的前缀切片」有效；不传（默认）走原现算路径。
    """
    if len(df) < 21:
        return None
    if precomputed is not None:
        n = len(df)
        rets = precomputed["rets"].iloc[n - 20 : n]
    else:
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
