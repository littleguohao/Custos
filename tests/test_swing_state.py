# -*- coding: utf-8 -*-
"""走势三态 v2 候选（research/swing_state）：三态、转折、短游程合并。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from custos.research import swing_state as sw


def _df(closes):
    return pd.DataFrame({"close": np.asarray(closes, float)})


def test_flat_is_sideways():
    assert sw.swing_state(_df([10.0] * 120))["state"] == "横盘震荡"


def test_up_then_down_is_transition():
    c = (
        [10.0] * 40
        + list(np.linspace(10, 25, 15))
        + list(np.linspace(24, 12, 15))  # 陡涨陡跌
    )
    o = sw.swing_state(_df(c))
    assert o["available"] and o["state"] == "上涨转下跌"


def test_short_blip_merged():
    c = [10.0] * 60 + [10.9, 11.0, 10.5, 10.0] + [10.0] * 40  # 几根小冲高不算一段
    assert all(n >= sw.SW_MIN_RUN for _s, n in sw.runs(c)) or len(sw.runs(c)) == 1


def test_too_short_unavailable():
    assert sw.swing_state(_df([10.0] * 5))["available"] is False


def test_flat_top_reads_sideways_between():
    """平顶过渡（涨后横住再跌）记「横盘转下跌」——检测器按最后两段，这是口径不是 bug。"""
    c = (
        [10.0] * 20
        + list(np.linspace(10, 20, 25))
        + [20.0] * 20
        + list(np.linspace(20, 14, 25))
    )
    assert sw.swing_state(_df(c))["state"] == "横盘转下跌"
