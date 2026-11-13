# -*- coding: utf-8 -*-
"""b1_structure v2 候选定义（owner 2026-11-13 校准确认；只在 calib 对照，不进 detect / 打分）。"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

from custos.core.factors import b1_structure as bs


def _df(close, vol, opn=None):
    close = np.asarray(close, float)
    opn = close * 0.99 if opn is None else np.asarray(opn, float)
    return pd.DataFrame(
        {
            "open": opn,
            "high": np.maximum(opn, close) * 1.005,
            "low": np.minimum(opn, close) * 0.995,
            "close": close,
            "volume": np.asarray(vol, float),
            "date": pd.bdate_range("2024-01-01", periods=len(close)),
        }
    )


def test_five_day_v2_needs_up_days_rising_volume_and_bullish_2x_spike():
    base = [10.0] * 20
    up = _df(
        base + [10.1, 10.3, 10.6, 10.9], [100] * 20 + [100, 250, 260, 270]
    )  # 第 2 天 2.5 倍且收阳
    assert bs.check_five_day_entry_v2(up)["hit"]
    low_base = _df(
        base + [10.1, 10.3, 10.6, 10.9], [100] * 20 + [100, 150, 160, 170]
    )  # 无 2 倍巨量
    assert not bs.check_five_day_entry_v2(low_base)["hit"]
    bear_spike = _df(
        base + [10.1, 10.3, 10.6, 10.9],
        [100] * 20 + [100, 250, 260, 270],
        opn=[10.0] * 20 + [10.0, 10.5, 10.5, 10.8],
    )  # 巨量日收阴（开 10.5 收 10.3）
    assert not bs.check_five_day_entry_v2(bear_spike)["hit"]


def test_non_one_wave_v2_one_wave_gain_and_fallback_segment():
    n = 80
    c = (
        list(np.linspace(10, 10, 20))
        + list(np.linspace(10, 17, 30))
        + list(np.linspace(17, 15.5, 30))
    )
    v = [100] * 20 + [120] * 30 + [60] * 30
    o = bs.check_non_one_wave_v2(_df(c[:n], v[:n]))
    assert o["rally"]["gain_pct"] > 50 and o["status"] == bs.NOW_V2_ONE_WAVE_STATUS
    # 价格跌穿启动位到窗口最低（v1 找不到段）且放量 ⇒ v2 回退段识别判放量破位
    c2 = list(np.linspace(10, 12, 30)) + list(np.linspace(12, 9.0, 28)) + [8.5, 8.3]
    v2 = [100] * 58 + [400, 420]
    d2 = _df(c2, v2)
    assert bs.check_non_one_wave(d2)["status"] == "insufficient"
    assert bs.check_non_one_wave_v2(d2)["status"] == "revoked"
    assert "break_with_volume" in bs.check_non_one_wave_v2(d2)["revoke_reasons"]


def test_v2_not_wired_into_live():
    src = inspect.getsource(bs.detect)
    assert "_v2" not in src
