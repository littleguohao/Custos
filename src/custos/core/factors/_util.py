# -*- coding: utf-8 -*-
"""因子层共享小工具。放这里而不是各因子内联，是因为它们跨因子复用。"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def ohlcv_arrays(
    df: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    close = df["close"].astype(float).to_numpy()
    high = df["high"].astype(float).to_numpy()
    low = df["low"].astype(float).to_numpy()
    vol = df["volume"].astype(float).to_numpy()
    return close, high, low, vol


def resample_ready(df: pd.DataFrame) -> pd.DataFrame:
    """`indicators.resample` 前置：①date 转日期型（生产链本就是 datetime，合成数据
    常是字符串，to_datetime 幂等）②补 amount 列（resample 聚合它，缺列用
    close×volume 兜底）。qn_three_red / qn_weekly180_setup 共用（v0.197 收敛，
    此前两处各有一份逐字相同的 _prepare）。"""
    x = df.copy()
    x["date"] = pd.to_datetime(x["date"])
    if "amount" not in x.columns:
        x["amount"] = x["close"].astype(float) * x["volume"].astype(float)
    return x


def ts_corr(x: pd.Series, y: pd.Series, n: int) -> Optional[float]:
    """末 n 根的皮尔逊相关；不足 n 根或相关无定义（如恒定量）返回 None。

    ⚠️ 返回 None 而不是 0：**「无定义」与「不相关」是两件事**。
    调用方若要把无定义当中性，须自己显式转 0（`alpha_pvcorr` 就是这么做的，
    并在注释里说明为什么仍产出记录）。
    """
    if len(x) < n:
        return None
    c = x.iloc[-n:].reset_index(drop=True).corr(y.iloc[-n:].reset_index(drop=True))
    return None if (c is None or c != c) else float(c)
