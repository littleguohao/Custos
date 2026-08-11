# -*- coding: utf-8 -*-
"""Alpha#6 类：价量负相关

`-correlation(open, volume, 10)` —— 量价负相关高 ⇒ 筹码沉淀。

⚠️ **未单独终审**：与 alpha101 同批引入（Kakushadze 2016 的思想），
但 findings 里没有它的净值对照记录。按 R2 的整体结论
「无任何价量特征通过验证」推定不可用，**但缺它自己的证据**。
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from _util import ts_corr as _ts_corr
FACTOR: dict[str, Any] = {
    "id": "alpha_pvcorr",
    "name": "Alpha#6 类：价量负相关",
    "kind": "selector",
    "status": "untested",
    "evidence": "governance/research/R2_selection_price_volume.md",
    "note": "缺独立终审记录；按 R2 整体结论推定不可用",
    "min_bars": 10,
    "live_use": "none",
    "stage": "debug",
}

def score(df: pd.DataFrame, code: str):
    """Alpha#6 类：-correlation(open, volume, 10)：价量背离(量价负相关高→筹码沉淀)。选价量背离的候选。"""
    if len(df) < 10:
        return None
    corr = _ts_corr(df["open"].astype(float), df["volume"].astype(float), 10)
    score = 0.0 if corr is None else -corr           # 相关无定义(如恒定量)→中性0,仍产出记录
    return {"score": round(score, 4), "suggestion": "可买",
            "aux": {"alpha": "6_neg_corr_open_vol"}, "components": {}}
