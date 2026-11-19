# -*- coding: utf-8 -*-
"""走势三态候选 v3（research/swing_state）：只看图上 120 根，至多一次转折。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from custos.research import swing_state as sw


def _df(closes):
    return pd.DataFrame({"close": np.asarray(closes, float)})


def _noise(c, seed=0):
    r = np.random.RandomState(seed)
    return np.asarray(c) * (1 + r.normal(0, 0.01, len(c)))


def test_flat_is_sideways():
    assert sw.swing_state(_df(_noise([10.0] * 120)))["state"] == "横盘震荡"


def test_steady_up_is_up():
    assert sw.swing_state(_df(_noise(np.linspace(10, 15, 120))))["state"] == "震荡上涨"


def test_up_then_down():
    c = list(np.linspace(10, 16, 60)) + list(np.linspace(16, 11, 60))
    assert sw.swing_state(_df(_noise(c)))["state"] == "上涨转下跌"


def test_flat_then_down():
    c = [10.0] * 70 + list(np.linspace(10, 7.5, 50))
    assert sw.swing_state(_df(_noise(c)))["state"] == "横盘转下跌"


def test_only_last_120_bars_used():
    c = list(np.linspace(30, 10, 200)) + [10.0] * 120  # 图外的大跌不算
    assert sw.swing_state(_df(_noise(c)))["state"] == "横盘震荡"


def test_too_short_unavailable():
    assert sw.swing_state(_df([10.0] * 30))["available"] is False
