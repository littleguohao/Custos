# -*- coding: utf-8 -*-
"""走势三态 v4（b1_structure.check_swing_state）：只看末 120 根；转折只在最高 / 最低点；段内直线 ±15%。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from custos.core.factors import b1_structure as bs
from custos.research import swing_state as sw


def _df(c, seed=0):
    r = np.random.RandomState(seed)
    return pd.DataFrame(
        {"close": np.asarray(c, float) * (1 + r.normal(0, 0.005, len(c)))}
    )


def test_flat_is_sideways():
    assert bs.check_swing_state(_df([10.0] * 120))["state"] == "横盘震荡"


def test_steady_up():
    assert bs.check_swing_state(_df(np.linspace(10, 15, 120)))["state"] == "震荡上涨"


def test_up_then_down_turns_at_high():
    c = list(np.linspace(10, 16, 70)) + list(np.linspace(16, 11, 50))
    o = bs.check_swing_state(_df(c))
    assert o["state"] == "上涨转下跌" and 45 <= o["turn_bars_ago"] <= 52


def test_short_tail_drop_counts():
    c = list(np.linspace(10, 16, 110)) + list(
        np.linspace(16, 12.5, 10)
    )  # B1 前 10 根回落
    assert bs.check_swing_state(_df(c))["state"] == "上涨转下跌"


def test_only_last_120_bars():
    c = list(np.linspace(30, 10, 200)) + [10.0] * 120
    assert bs.check_swing_state(_df(c))["state"] == "横盘震荡"


def test_research_wrapper_and_short():
    assert sw.swing_state(_df([10.0] * 120)) == bs.check_swing_state(_df([10.0] * 120))
    assert bs.check_swing_state(_df([10.0] * 20))["available"] is False


def _ohlc(c):
    c = np.asarray(c, float)
    return pd.DataFrame({"close": c, "high": c * 1.01, "low": c * 0.99})


def test_one_wave_v4_fast_large_continuous():
    c = (
        [10.0] * 90 + list(np.linspace(10, 16, 9)) + list(np.linspace(16, 12, 21))
    )  # 9 根 +60%
    o = bs.check_one_wave_v4(_ohlc(c))
    assert o["hit"] and o["rise_pct"] >= 50


def test_one_wave_v4_slow_or_broken_not_hit():
    slow = [10.0] * 60 + list(np.linspace(10, 16, 60))  # 60 根才涨 60%
    assert not bs.check_one_wave_v4(_ohlc(slow))["hit"]
    broken = (
        [10.0] * 100
        + [11, 12.5, 13.5, 11.5, 13, 14.5, 15.5, 15.6, 15.7, 15.8]
        + [15] * 10
    )  # 途中回撤 >10%
    assert not bs.check_one_wave_v4(_ohlc(broken))["hit"]
