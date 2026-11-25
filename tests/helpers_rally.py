# -*- coding: utf-8 -*-
"""测试用上涨段替身（v0.591）。

生产的上涨段唯一定位是 ``b1_structure.rally_segment``（MACD 平台 N 结构），它需要红 / 绿柱段里有阴线。
很多下游测试的合成序列是平滑直线、没有阴线，形不成平台点；这些测试钉的是「给定上涨段之后」的判定逻辑，
用本替身（窗口最低价 → 其后最高价）把上涨段固定下来，``rally_segment`` 本身由 test_macd_platform / test_swing_state 管。
"""

from __future__ import annotations

import pytest


def window_rally(df, lookback: int = 60):
    lo = df["low"].astype(float).to_numpy()
    hi = df["high"].astype(float).to_numpy()
    n = len(df)
    a = max(0, n - lookback)
    i_low = a + int(lo[a:].argmin())
    if i_low >= n - 1:
        return None
    i_high = i_low + int(hi[i_low:].argmax())
    if i_high <= i_low:
        return None
    return {
        "start": a,
        "i_low": i_low,
        "i_high": i_high,
        "n": n,
        "pullback_bars": n - 1 - i_high,
        "basis": "test_window",
    }


@pytest.fixture
def fixed_rally(monkeypatch):
    from custos.core.factors import b1_structure as bs

    monkeypatch.setattr(bs, "rally_segment", window_rally)
    return window_rally
