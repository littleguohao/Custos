"""回调缩量企稳 v2 候选（owner 2026-11-25）。"""

import numpy as np
import pandas as pd

from custos.core.factors import ignition as ig


def _df(bar_vol=500.0, bar_body=0.005):
    up = np.linspace(10, 15, 40)
    down = np.linspace(15, 14.0, 9)
    close = np.r_[np.full(30, 10.0), up, down]
    vol = np.r_[
        np.full(30, 800.0),
        np.full(40, 1000.0),
        [1500, 1300, 1100, 800, 700, 600, 500, 450],
        [bar_vol],
    ]
    open_ = close * (1 + 0.001)
    open_[-1] = close[-1] * (1 + bar_body)
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) * 1.01,
            "low": np.minimum(open_, close) * 0.99,
            "close": close,
            "volume": vol,
        }
    )


def test_v2_hit_on_decaying_pullback():
    assert ig.check_pullback_shrink_v2(_df(), dks_last=12.0)["hit"] is True


def test_v2_miss_when_bar_volume_spikes():
    """owner #40：当根放量（阴线量 ≥ 前 5 日均量 0.9 倍）不算。"""
    r = ig.check_pullback_shrink_v2(_df(bar_vol=1500.0), dks_last=12.0)
    assert r["hit"] is False and r["conditions"]["bar_vol"] is False


def test_v2_miss_when_bar_body_large():
    """owner #42：缩量但 K 线大（实体 ≥4%）不算。"""
    r = ig.check_pullback_shrink_v2(_df(bar_body=0.05), dks_last=12.0)
    assert r["hit"] is False and r["conditions"]["bar_body"] is False
