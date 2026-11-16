# -*- coding: utf-8 -*-
"""红肥绿瘦 v2 候选（owner 2026-11-16）：阶段位置 + 剔除爆量根后三项比较 + 爆量标注。"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

from custos.core.factors import bottom_patterns as bp


def _df(close, opn, vol):
    close, opn = np.asarray(close, float), np.asarray(opn, float)
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-01", periods=len(close)),
            "open": opn,
            "high": np.maximum(opn, close) * 1.005,
            "low": np.minimum(opn, close) * 0.995,
            "close": close,
            "volume": np.asarray(vol, float),
        }
    )


def _bottom_world(bull_major=True, burst_bear=False):
    # 前 100 根从 20 跌到 12（回撤 40%），近 30 根在 12 附近：阳线 18 根（实体大、量大）/ 阴线 12 根
    c = list(np.linspace(20, 12, 100))
    o = [x * 1.003 for x in c]
    v = [100.0] * 100
    for k in range(30):
        up = (k % 5 != 0 and k % 5 != 3) if bull_major else (k % 5 in (0, 3))
        base = 12.0
        if up:
            o.append(base)
            c.append(base * 1.02)
            v.append(130.0)
        else:
            o.append(base * 1.01)
            c.append(base * 1.0)
            v.append(90.0)
    if burst_bear:
        o[-3], c[-3], v[-3] = 12.5, 11.5, 5000.0
    return _df(c, o, v)


def test_red_fat_at_stage_bottom():
    r = bp.detect_red_green_v2(_bottom_world())
    assert (
        r["stage"] == "bottom" and r["dominance"] == "bull" and r["status"] == "red_fat"
    )


def test_burst_bar_excluded_and_tagged_good_at_bottom():
    r = bp.detect_red_green_v2(_bottom_world(burst_bear=True))
    assert (
        r["detail"]["excluded_burst"] == 1 and r["status"] == "red_fat"
    )  # 单根爆量不左右结论
    assert r["bursts"][0]["kind"] == "底部爆量阴线" and r["bursts"][0]["tone"] == "good"


def test_bull_dominance_not_at_bottom_is_neutral():
    df = _bottom_world()
    df[["open", "high", "low", "close"]] *= 1.0
    flat = _df(
        [12.0] * 100 + list(df["close"][100:]),
        [12.0] * 100 + list(df["open"][100:]),
        [100.0] * 130,
    )
    r = bp.detect_red_green_v2(flat)
    assert (
        r["dominance"] == "bull" and r["stage"] != "bottom" and r["status"] == "neutral"
    )


def test_not_wired_into_live():
    assert "red_green_v2" not in inspect.getsource(bp.detect)
