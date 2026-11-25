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


def test_v2_hold_dks_or_prev_low_pinned_v0585():
    """owner 2026-11-25：守住支撑是数值判断，退出人工校准，由本钉测管——DKS 或前一低点守住其一即可。"""
    df = _df()
    assert (
        ig.check_pullback_shrink_v2(df, dks_last=12.0)["conditions"]["hold"] is True
    )  # 守 DKS
    assert (
        ig.check_pullback_shrink_v2(df, dks_last=20.0)["conditions"]["hold"] is True
    )  # 破 DKS 但守前低（上涨起点 10）
    low = df.copy()
    low.loc[low.index[-1], "close"] = 9.5
    low.loc[low.index[-1], "open"] = 9.5 * 1.005
    assert (
        ig.check_pullback_shrink_v2(low, dks_last=20.0)["conditions"]["hold"] is False
    )  # 两者都破
