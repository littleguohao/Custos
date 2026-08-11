# -*- coding: utf-8 -*-
"""因子层共享小工具。放这里而不是各因子内联，是因为它们跨因子复用。"""

from __future__ import annotations

from typing import Optional

import pandas as pd


def ohlcv_arrays(df):
    close = df["close"].astype(float).to_numpy()
    high = df["high"].astype(float).to_numpy()
    low = df["low"].astype(float).to_numpy()
    vol = df["volume"].astype(float).to_numpy()
    return close, high, low, vol


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
