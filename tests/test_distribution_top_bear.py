"""出货 ①② v2 候选：顶部 / 次高点放量阴线（owner 2026-11-24）。"""

import numpy as np
import pandas as pd

from custos.core.factors import distribution as dm


def _df(close, open_, vol):
    close = np.asarray(close, float)
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(close, open_) * 1.01,
            "low": np.minimum(close, open_) * 0.99,
            "close": close,
            "volume": vol,
        }
    )


def _base():
    n = 120
    close = np.r_[np.linspace(10, 20, 100), np.linspace(20, 18, 20)]
    open_ = close * 0.995  # 默认小阳
    open_[96] = (
        close[96] * 1.002
    )  # 顶部红柱段里一根小阴（v0.594：平台顶需要红柱段内有阴线）
    vol = np.full(n, 1000.0)
    return close, open_, vol


def test_top_bear_volume_hit_when_bear_beats_prior_yang():
    close, open_, vol = _base()
    t = 101  # 顶部（第 99 根）后第 2 根
    open_[t] = close[t] * 1.03
    vol[t] = 3000
    r = dm.check_top_bear_volume(_df(close, open_, vol))
    assert r["hit"] and r["bars"][0]["i"] == t and r["bars"][0]["kind"] == "顶部"


def test_top_bear_volume_miss_when_prior_yang_bigger():
    """owner：前面的阳线量 ≥ 这根阴线，不算出货。"""
    close, open_, vol = _base()
    t = 101
    open_[t] = close[t] * 1.03
    vol[t] = 3000
    vol[t - 2] = 3500  # 前面一根阳线更大
    assert dm.check_top_bear_volume(_df(close, open_, vol))["hit"] is False


def test_limit_up_flat_bar_counts_as_yang():
    """一字板（收 = 开）算阳线：后面的阴线量要大过它。"""
    close, open_, vol = _base()
    t = 101
    open_[t] = close[t] * 1.03
    vol[t] = 3000
    close[t - 1] = round(close[t - 2] * 1.10, 2)  # 一字涨停（收 = 开 且 收 > 前收）
    open_[t - 1] = close[t - 1]
    vol[t - 1] = 4000
    assert dm.check_top_bear_volume(_df(close, open_, vol))["hit"] is False
