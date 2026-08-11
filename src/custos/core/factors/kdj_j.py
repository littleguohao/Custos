# -*- coding: utf-8 -*-
"""当日 KDJ 的 J 值（纯特征）

信号池内 J 的具体深度（J=2 vs J=12）可能有判别力，而门槛（J<13）会把这条信息
「吃掉」，故显式记录。**恒判可买**，只作可排序特征。

⚠️ 判别力研究里同号率仅 50%，**不稳定**。
"""

from __future__ import annotations

from typing import Any
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


def score(df: pd.DataFrame, code: str):
    """当日 KDJ 的 J 值(纯特征,恒可买)——信号池内 J 的具体深度(J=2 vs J=12)可作判别子,
    门槛(J<13)会把这条信息"吃掉",故显式记录。kdj 不可用 → None。"""
    if len(df) < 12:
        return None
    # 直接用共享指标（原实现委托 technical_monitor.kdj，那层只是加了 available/state 包装）
    k, d, j = kdj_series(df, fill_na=50.0)
    jv = float(j.iloc[-1])
    if jv != jv:
        return None
    return {
        "score": round(jv, 3),
        "suggestion": "可买",
        "aux": {"k": round(float(k.iloc[-1]), 4), "d": round(float(d.iloc[-1]), 4)},
        "components": {},
    }
