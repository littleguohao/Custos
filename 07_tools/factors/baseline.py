# -*- coding: utf-8 -*-
"""对照基线：任何 as-of 日都判「可买」

**不是因子，是对照臂。** 同样的止损+BBI 出场规则下，无差别进场能拿到多少期望/盈亏比。
任何进场信号必须**显著优于**它，才证明信号本身有价值 ——
否则 edge 全来自出场规则而非进场指纹（R1 的核心判据之一）。
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

FACTOR: dict[str, Any] = {
    "id": "baseline",
    "name": "对照基线：任何 as-of 日都判「可买」",
    "kind": "control",
    "status": "active",
    "evidence": "00_governance/research/R1_core_framework.md",
    "note": "所有进场信号的对照臂，必须保留",
    "min_bars": 1,
    "live_use": "none",
    "stage": "debug",
}

def score(df: pd.DataFrame, code: str):
    """基线打分器：任何 as-of 日都判「可买」。用于对照——同样的止损+BBI出场规则下，
    无差别进场能拿到多少期望/盈亏比；b1_pullback 需**显著优于**它，才证明进场信号本身有价值
    (否则 edge 全来自出场规则而非进场指纹)。"""
    return {"score": 0.0, "suggestion": "可买", "aux": {}, "components": {}}
