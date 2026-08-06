# -*- coding: utf-8 -*-
"""共享技术指标：J 值与 BBI 的**唯一实现**。

## 为什么单独一个模块

2026-08-06 清点：`_j_series` 有 **3 份**（screening/enrich_candidates、b2_surge_factor、
main_rally_factor），BBI 公式在 **4 处**代码里各写一遍（backtest_factors ×2、
market_timing/technical_monitor ×2）。这两个指标恰好是 B1 最核心的两个：

    J < 13      —— 入场触发（B1 候选的唯一硬条件）
    BBI         —— 移动止盈与持仓状态（`bbi_above` / 连破 N 日清仓）

⇒ 它们分散在 live 选股链、研究回测器、持仓状态机三处。**只要有一处被单独修改，
回测与 live 就会对同一根 K 线算出不同的 J/BBI，而两边的结论再也无法互相印证。**

实测（合成数据 60 根）当时**尚未发散**：
· BBI 四处公式完全一致
· `b2_surge_factor` 与 `main_rally_factor` 的 J **逐点相同**（max diff 0.0000）
· `enrich_candidates` 的 J 因多一步 `fillna(50)` 最大差 1.44、中位 0.0016，
  但 J<13 触发面 0 根不一致 —— 且它的用法是 `min()`（跳过 NaN），
  填 50 只在整段都是 NaN（序列短于 9 根）时改变结果 ⇒ 当前无实际影响。

**趁还没发散就合并**，而不是等某次改动之后再去比对。

## NaN 策略是显式参数，不是隐含默认

`fill_na` 保留 `enrich_candidates` 原有行为（填 50 = 中性），其余调用方传 None
（保持 NaN）。做成参数而不是统一取一种，是为了**这次合并零行为变化** ——
指标语义的改动应该单独立项、单独回测，不该搭在重构里。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

J_N, J_M1, J_M2 = 9, 3, 3
DKS_MA_WINDOWS = (14, 28, 57, 114)  # 知行多空线的四均线（good_b1 图上参数）          # KDJ 标准参数；com = m - 1 ⇒ com=2


def kdj_series(df: pd.DataFrame, *, n: int = J_N, m1: int = J_M1, m2: int = J_M2,
               fill_na: Optional[float] = None) -> tuple[pd.Series, pd.Series, pd.Series]:
    """返回 `(K, D, J)` 三条序列。

    需要 K/D 的调用方（如 `technical_monitor.kdj` 要输出 k/d 字段）用这个，
    只要 J 的用 `j_series`。**两者共用同一段计算**，不会因为「只暴露 J」
    而逼调用方自己再算一遍 K/D —— 那正是重复实现的起点。
    """
    c = df["close"].astype(float)
    low_n = df["low"].astype(float).rolling(n).min()
    high_n = df["high"].astype(float).rolling(n).max()
    rng = (high_n - low_n).replace(0, np.nan)
    rsv = (c - low_n) / rng * 100
    rsv = rsv.replace([np.inf, -np.inf], np.nan)
    if fill_na is not None:
        rsv = rsv.fillna(fill_na)
    k = rsv.ewm(com=m1 - 1, adjust=False).mean()
    d = k.ewm(com=m2 - 1, adjust=False).mean()
    return k, d, 3 * k - 2 * d


def j_series(df: pd.DataFrame, *, n: int = J_N, m1: int = J_M1, m2: int = J_M2,
             fill_na: Optional[float] = None) -> pd.Series:
    """KDJ 的 J 序列：`RSV → K(EWM) → D(EWM) → J = 3K − 2D`。

    ``fill_na``：``None``（默认）保持 NaN；给数值则在 RSV 层填充
    （`enrich_candidates` 传 50.0，即"数据不足按中性处理"）。

    ⚠️ **零振幅要先变 NaN 再决定怎么填**。`high == low`（一字板/停牌）时
    `(close-low)/(high-low)` 是 0/0：不先 replace 会得到 inf/NaN 混杂，
    而 inf 进了 EWM 会把后续所有值污染成 NaN —— 那是"一根一字板毁掉整条 J 序列"。
    """
    return kdj_series(df, n=n, m1=m1, m2=m2, fill_na=fill_na)[2]


def bbi_series(close: pd.Series) -> pd.Series:
    """BBI = (MA3 + MA6 + MA12 + MA24) / 4。

    B1 的移动止盈与持仓状态都建立在它上面（`bbi_above`、连破 N 日清仓），
    所以它必须在 live 选股链、研究回测器、持仓状态机三处完全一致。
    """
    c = close.astype(float)
    return sum(c.rolling(k).mean() for k in (3, 6, 12, 24)) / 4


def dks_series(close: pd.Series, windows: tuple[int, ...] = DKS_MA_WINDOWS) -> pd.Series:
    """DKS（知行多空线）= (MA14+MA28+MA57+MA114)/4。

    2026-08-06 收敛第 3 份重复指标（前两个是 J 与 BBI）。此前有两处：
    · `screening/enrich_candidates.dks_series` —— docstring 自称「**唯一实现**」，
      并记录了它当初就是为了收敛 `technical_monitor.zhixing_state` 才建的
    · `factors/b1_dual_factor._dks_series` —— **但这份它没收进去**

⇒ 「唯一实现」的声明与事实不符，而两份实测逐点相同（尚未发散）。
    移到这里后才真的唯一，并顺带**断开一处循环依赖**：
    `factors/perfect_b1_fit` 需要 DKS，若从 `enrich_candidates` 取就成了
    factors → screening → factors 的环。
    """
    c = close.astype(float)
    return sum(c.rolling(w).mean() for w in windows) / len(windows)
