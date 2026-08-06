# -*- coding: utf-8 -*-
"""反转K 成色分（0–4）

缩量 + 量底 + 小实体 + 小振幅，各计 1 分。
用作**选择器**：在宽门槛（如 j_low）候选里按成色排序取 top-N。

🟡 **待优化，且是稳健的负预测**：特征归因 train −3.42% / test −2.75%（Q4−Q1）
—— **越「教科书」越差**。正向择优劣于随机（+33% vs baseline +43%）。

⚠️⚠️ **口径与 live 不一致（2026-08-06 查出）**：本因子的「小实体」判据是
`abs(涨跌幅) <= 2.0`（**对称**），而 live 的反转K是
`-2.0% ~ +1.8%`（**不对称**，B1_w.pdf 纠偏后的口径）。
⇒ **本因子与 live 的反转K不是同一个东西**，而 R2 的结论建立在本因子上。
抽取时**保持原口径不动**（改了会作废已有回测数字，而那些数字已在重跑清单里），
已登记 TODO 待 owner 定：研究口径该不该跟 live 对齐。
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

# ⚠️ 阈值**保持与 backtest_factors 原值一致**（对称 ±2%），不跟 live 的不对称口径对齐。
# 改了会作废已有回测数字，而那些数字已在重跑清单里（R2 P1）。已登记 TODO 待 owner 定。
REVK_VOL_RATIO = 0.5        # 量比 <= 50%
REVK_VOL_PCTILE = 0.10      # 20 日量分位 <= 10%
REVK_CHG_PCT = 2.0          # 🔴 对称；live 是 -2.0 ~ +1.8（不对称）
REVK_AMP_PCT = 7.0          # 振幅 <= 7%

FACTOR: dict[str, Any] = {
    "id": "reversal_quality",
    "name": "反转K 成色分（0–4）",
    "kind": "selector",
    "status": "needs_work",
    "evidence": "00_governance/research/R2_selection_price_volume.md",
    "note": "稳健负预测；且口径与 live 反转K不一致（对称 vs 不对称）",
    "min_bars": 21,
    "live_use": "none",
}

def score(df: pd.DataFrame, code: str):
    """反转K质量分(0-4)：缩量(量比≤50%)+量底(20日底10%)+小实体(收盘±2%)+小振幅(≤7%) 各计1分。
    用作**选择器**：在宽门槛(如 j_low)候选里按"反转成色"排序取 top-N —— 兼得 j_low 的供给 + reversal_k 的质量。"""
    if len(df) < 21:
        return None
    try:
        close = df["close"].astype(float).values
        high = df["high"].astype(float).values
        low = df["low"].astype(float).values
        vol = df["volume"].astype(float).values
        vma5 = vol[-6:-1].mean() if len(vol) >= 6 else vol[:-1].mean()
        v20 = vol[-20:]
        pts = 0
        pts += int(vma5 > 0 and vol[-1] / vma5 <= REVK_VOL_RATIO)             # 缩量
        pts += int((v20 <= vol[-1]).mean() <= REVK_VOL_PCTILE)               # 量底10%
        pts += int(close[-2] and abs(close[-1] / close[-2] - 1) * 100 <= REVK_CHG_PCT)   # 小实体
        pts += int(close[-2] and (high[-1] - low[-1]) / close[-2] * 100 <= REVK_AMP_PCT)  # 小振幅
        return {"score": float(pts), "suggestion": "可买",
                "aux": {"selector": "reversal_quality_0_4"}, "components": {}}
    except Exception:  # noqa: BLE001
        return None
