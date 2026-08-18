# -*- coding: utf-8 -*-
"""Alpha#6 类：价量负相关

`-correlation(open, volume, 10)` —— 量价负相关高 ⇒ 筹码沉淀。

⚠️ **已降级「不再研究」（2026-08-18，owner 拍板）**：钉死宇宙 300 只
三窗口 × top-5 择优对照（j_low、25bps）——仅 bull2425 跑赢 baseline
（+42.8% vs -1.4%），rally22 / ytd26 均跑输，**无跨窗口稳健性**。
证据见 R2 第 16 条、`artifacts/logs/w35_*_alpha_pvcorr.json`。
"""

from __future__ import annotations

from typing import Any
import pandas as pd

from custos.core.factors._util import ts_corr as _ts_corr

FACTOR: dict[str, Any] = {
    "id": "alpha_pvcorr",
    "name": "Alpha#6 类：价量负相关",
    "kind": "selector",
    "status": "needs_work",
    "evidence": "governance/research/R2_selection_price_volume.md",
    "note": "三窗对照仅 bull2425 跑赢、无跨窗稳健性 ⇒ 降级不再研究(2026-08-18)",
    "min_bars": 10,
    "live_use": "none",
    "stage": "debug",
}


def score(df: pd.DataFrame, code: str) -> dict | None:
    """Alpha#6 类：-correlation(open, volume, 10)：价量背离(量价负相关高→筹码沉淀)。选价量背离的候选。"""
    if len(df) < 10:
        return None
    corr = _ts_corr(df["open"].astype(float), df["volume"].astype(float), 10)
    score = 0.0 if corr is None else -corr  # 相关无定义(如恒定量)→中性0,仍产出记录
    return {
        "score": round(score, 4),
        "suggestion": "可买",
        "aux": {"alpha": "6_neg_corr_open_vol"},
        "components": {},
    }
