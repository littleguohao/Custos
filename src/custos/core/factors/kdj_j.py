# -*- coding: utf-8 -*-
"""当日 KDJ 的 J 值（纯特征）

信号池内 J 的具体深度（J=2 vs J=12）可能有判别力，而门槛（J<13）会把这条信息
「吃掉」，故显式记录。**恒判可买**，只作可排序特征。

⚠️ 判别力研究里同号率仅 50%，**不稳定**。
"""

from __future__ import annotations

from typing import Any, Optional
import pandas as pd

from custos.core.indicators import kdj_series

FACTOR: dict[str, Any] = {
    "id": "kdj_j",
    "name": "当日 KDJ 的 J 值（纯特征）",
    "kind": "selector",
    "status": "needs_work",
    "evidence": "governance/research/R3_selection_discriminability_recall.md",
    "note": "同号率仅 50%，不稳定",
    "min_bars": 12,
    "live_use": "none",
    "stage": "debug",
}


def score(
    df: pd.DataFrame, code: str, precomputed: Optional[dict] = None
) -> dict | None:
    """当日 KDJ 的 J 值(纯特征,恒可买)——信号池内 J 的具体深度(J=2 vs J=12)可作判别子,
    门槛(J<13)会把这条信息"吃掉",故显式记录。kdj 不可用 → None。

    ``precomputed``：evaluate_trades 逐股预计算的 KDJ 全序列（与 gate 侧同口径
    ``kdj_series(df, fill_na=50.0)``，键 kdj_k/kdj_d/kdj_j 为与 df 等长的 np 数组），
    传入时按 ``len(df)-1`` 取点——KDJ（RSV→EWM→EWM）从第 0 根递归，前缀末点与
    全序列同位点是**同一串浮点运算**，两路逐位相同。⚠️ 只对「从第 0 根开始的
    前缀切片」有效；不传（默认）走原现算路径。
    """
    if len(df) < 12:
        return None
    if precomputed is not None:
        i = len(df) - 1
        kv = float(precomputed["kdj_k"][i])
        dv = float(precomputed["kdj_d"][i])
        jv = float(precomputed["kdj_j"][i])
    else:
        # 直接用共享指标（原实现委托 technical_monitor.kdj，那层只是加了 available/state 包装）
        k, d, j = kdj_series(df, fill_na=50.0)
        kv = float(k.iloc[-1])
        dv = float(d.iloc[-1])
        jv = float(j.iloc[-1])
    if jv != jv:
        return None
    return {
        "score": round(jv, 3),
        "suggestion": "可买",
        "aux": {"k": round(kv, 4), "d": round(dv, 4)},
        "components": {},
    }
