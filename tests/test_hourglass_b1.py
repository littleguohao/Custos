# -*- coding: utf-8 -*-
"""沙漏后的 B1 候选（owner 2026-11-17：只看沙漏形态；未接 live）。"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

from custos.core.factors import s_shape as ss


def _df(close, rng, vol):
    c = np.asarray(close, float)
    r = np.asarray(rng, float)
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-01", periods=len(c)),
            "open": c,
            "high": c * (1 + r / 2),
            "low": c * (1 - r / 2),
            "close": c,
            "volume": np.asarray(vol, float),
        }
    )


def _world():
    n0 = 60
    c = [10.0] * n0 + [10.0] * 10 + [10.0] * 10 + [10.6]  # 宽幅 → 收敛 → 放量突破
    r = [0.02] * n0 + [0.04] * 10 + [0.01] * 10 + [0.03]
    v = [100.0] * n0 + [120.0] * 10 + [60.0] * 10 + [300.0]
    # 之后回调到 J 低位
    c += list(np.linspace(10.5, 8.6, 15))
    r += [0.02] * 15
    v += [80.0] * 15
    return _df(c, r, v)


def test_event_found_and_b1_after_hourglass():
    df = _world()
    ev = ss.hourglass_events(df)
    assert any(e["t"] == 80 for e in ev)
    r = ss.detect_hourglass_then_b1(df)
    assert r["event"]["t"] == 80 and r["hit"] is (r["j"] is not None and r["j"] < 13)


def test_no_contraction_no_event():
    df = _world()
    df.loc[70:79, ["high", "low"]] = df.loc[70:79, ["high", "low"]].values * np.array(
        [1.03, 0.97]
    )  # 收敛段改宽
    assert not any(e["t"] == 80 for e in ss.hourglass_events(df))


def test_asof_prefix_consistent():
    df = _world()
    full = {e["t"] for e in ss.hourglass_events(df)}
    pre = {e["t"] for e in ss.hourglass_events(df.iloc[:85])}
    assert pre == {t for t in full if t < 85}


def test_not_wired_into_live():
    assert "hourglass" not in inspect.getsource(ss.compute_s_shape)
