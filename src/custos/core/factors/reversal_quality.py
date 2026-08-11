# -*- coding: utf-8 -*-
"""反转K 成色分（0–4）

缩量 + 量底 + 小实体 + 小振幅，各计 1 分。
用作**选择器**：在宽门槛（如 j_low）候选里按成色排序取 top-N。

🟡 **待优化，且是稳健的负预测**：特征归因 train −3.42% / test −2.75%（Q4−Q1）
—— **越「教科书」越差**。正向择优劣于随机（+33% vs baseline +43%）。

✅ **口径已与 live 默认值一致**（owner 2026-08-06 把 live 改回对称 ±2%）。
本因子的「小实体」判据是 `abs(涨跌幅) <= 2.0`，live 是 `-2.0% ~ +2.0%` —— 同一区间。

⚠️ 但**不是同一个来源**：live 两链（选股 + 持仓）2026-08-07 收敛到
`b1_thresholds`（L0）并跟随 `B1_REVK_*` 环境变量；本因子的常量**刻意钉死**，
不跟随。理由是钉死才能复现既有回测数字（R2 P1 重跑清单依赖它们）。
⇒ 设了环境变量之后，**只有 live 会变，本因子不变**。这是有意的边界，
由 `tests/test_enrich_b1cz.py::TestReversalKThresholdSingleSource` 钉住。
若哪天决定让本因子跟随，改的同时必须作废并重跑相关回测。

⚠️ 此处原写「本因子与 live 的反转K不是同一个东西（live 是 -2.0~+1.8 不对称）」——
owner 08-06 统一后已过时，2026-08-07 订正。那条 TODO 也已闭环。
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from custos.core.indicators import amplitude_pct as amplitude_pct_of  # 振幅唯一实现

# ⚠️ 阈值**保持与 backtest_factors 原值一致**（对称 ±2%）并**刻意不读环境变量** ——
# 改了会作废已有回测数字，而那些数字已在重跑清单里（R2 P1）。
# live 侧的同名阈值在 `src/b1_thresholds.py`，可配置；两者默认值相同。
REVK_VOL_RATIO = 0.5        # 量比 <= 50%
REVK_VOL_PCTILE = 0.10      # 20 日量分位 <= 10%
REVK_CHG_PCT = 2.0          # 对称，与 live 默认值同；刻意不跟随 B1_REVK_CHG_PCT
REVK_AMP_PCT = 7.0          # 振幅 <= 7%

FACTOR: dict[str, Any] = {
    "id": "reversal_quality",
    "name": "反转K 成色分（0–4）",
    "kind": "selector",
    "status": "needs_work",
    "evidence": "governance/research/R2_selection_price_volume.md",
    "note": "稳健负预测；口径已与 live 默认值一致（对称 ±2%），但刻意不跟随 B1_REVK_* env",
    "min_bars": 21,
    "live_use": "none",
    "stage": "debug",
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
        # 振幅≤7%：收敛到 `indicators.amplitude_pct`（全项目唯一实现，2026-08-10）。
        # ⚠️ 它是**纯公式、不读 env**，所以收敛不违反本因子「阈值钉死」的原则
        #    （钉死的是 REVK_AMP_PCT 这个数，不是怎么算振幅）。
        _amp = amplitude_pct_of(high[-1], low[-1], close[-2])
        pts += int(_amp is not None and _amp <= REVK_AMP_PCT)   # 小振幅
        return {"score": float(pts), "suggestion": "可买",
                "aux": {"selector": "reversal_quality_0_4"}, "components": {}}
    except Exception:  # noqa: BLE001
        return None
