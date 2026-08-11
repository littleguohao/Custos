# -*- coding: utf-8 -*-
"""反转成色**反向**选择器

`4 − reversal_quality` —— 选「最不教科书」的丑陋 J<13 回踩。

🟡 **待优化，而且是全研究链最典型的一次翻转**：
同偏样本内大胜（+69.4% vs baseline +43%），
含退市股跨年 walk-forward 后**同一窗口翻转为 −11.9%**（baseline +16.4%）
⇒ 「选丑」选到的相当部分是走向退市的烂票，**当初的 edge 本质是幸存者偏差**。

⚠️ 那次翻转**同时换了宇宙与数据源**，归因未分离（见 R2 重跑 P1）。
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from reversal_quality import score as _rq_score

FACTOR: dict[str, Any] = {
    "id": "reversal_quality_inv",
    "name": "反转成色**反向**选择器",
    "kind": "selector",
    "status": "needs_work",
    "evidence": "governance/research/R2_selection_price_volume.md",
    "note": "样本内大胜、含退市跨年翻转；归因未分离",
    "min_bars": 21,
    "live_use": "none",
    "stage": "debug",
}

def score(df: pd.DataFrame, code: str):
    """反转质量**反向**选择器：归因显示 reversal_quality 是稳健负预测(越"教科书"越差),
    故取 4-分 反向——选"最不教科书"的丑陋 J<13 回踩。⚠️ 仅在同偏样本 train/test 一致,需真样本外验证。"""
    r = _rq_score(df, code)
    if r is None:
        return None
    r["score"] = 4.0 - r["score"]
    r["aux"] = {"selector": "reversal_quality_inv"}
    return r
