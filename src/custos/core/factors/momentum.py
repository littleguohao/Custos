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

from typing import Any, Optional
import pandas as pd

FACTOR: dict[str, Any] = {
    "id": "momentum",
    "name": "动量因子（12-1 类）",
    "kind": "selector",
    "status": "needs_work",
    "evidence": "governance/research/R2_selection_price_volume.md",
    "research_ref": ["R2"],  # TODO #73 谱系回填（与 evidence 同源）
    "note": "三窗对照 rally22/bull2425 垫底、无跨窗稳健性 ⇒ 降级不再研究(2026-08-18)",
    "min_bars": 40,
    "live_use": "none",
    "stage": "debug",
}


def score(
    df: pd.DataFrame, code: str, precomputed: Optional[dict] = None
) -> dict | None:
    """动量因子(12-1类)：score=[t-skip-lb, t-skip]区间收益(跳过最近20日避开短期反转)。中期强势择优。
    历史不足时自适应缩短回看窗口(≥40根即产出)。

    ``precomputed``：evaluate_trades 逐股预计算的全序列（键 c = close 的 float
    np 数组），传入时按 ``len(df)`` 截前缀视图后走**同一段**点查询——astype(float)
    是逐点变换，mom 只读 ``c[-1-skip]`` / ``c[-1-skip-lb]`` 两个点，两路逐位相同。
    ⚠️ 只对「从第 0 根开始的前缀切片」有效（lb 随 ``len(df)`` 自适应，截前缀
    后 n 与前缀切片一致）；不传（默认）走原现算路径。
    """
    if precomputed is not None:
        c = precomputed["c"][: len(df)]
    else:
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
