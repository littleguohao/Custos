# -*- coding: utf-8 -*-
"""走势三态（v0.589 起由 MACD 平台 N 结构推出，owner 2026-11-25）+ 一波流 v4。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from custos.core.factors import b1_structure as bs
from custos.research import swing_state as sw


def _seg(kind, price, idx, ongoing=False):
    return {
        "kind": kind,
        "price": price,
        "idx": idx,
        "date": None,
        "start": None,
        "end": None,
        "ongoing": ongoing,
    }


def _df(n=60):
    c = np.linspace(10, 11, n)
    return pd.DataFrame(
        {
            "open": c,
            "high": c * 1.01,
            "low": c * 0.99,
            "close": c,
            "volume": np.full(n, 1e6),
        }
    )


def _with(monkeypatch, segs):
    monkeypatch.setattr(bs, "macd_platform_segments", lambda df: segs)
    return bs.check_swing_state(_df())


def test_rising_n_is_up(monkeypatch):
    segs = [
        _seg("L", 9, 1),
        _seg("H", 10, 5),
        _seg("L", 9.5, 10),
        _seg("H", 11, 15),
        _seg("L", 10, 20),
        _seg("H", 12, 25),
    ]
    r = _with(monkeypatch, segs)
    assert (
        r["state"] == "震荡上涨"
        and r["now"] == "震荡上涨"
        and r["turn_bars_ago"] is None
    )


def test_flat_n_is_sideways(monkeypatch):
    segs = [
        _seg("L", 9, 1),
        _seg("H", 11, 5),
        _seg("L", 9.2, 10),
        _seg("H", 10.8, 15),
        _seg("L", 9.1, 20),
        _seg("H", 10.9, 25),
    ]
    assert _with(monkeypatch, segs)["state"] == "横盘震荡"


def test_down_then_up_turn(monkeypatch):
    segs = [
        _seg("L", 10, 1),
        _seg("H", 12, 5),
        _seg("L", 9, 10),
        _seg("H", 11, 15),
        _seg("L", 9.5, 20),
        _seg("H", 11.5, 25),
    ]
    r = _with(monkeypatch, segs)
    assert r["state"] == "下跌转上涨" and r["now"] == "震荡上涨"
    assert r["turn_bars_ago"] == 59 - 10


def test_unavailable_when_less_than_three_groups(monkeypatch):
    r = _with(monkeypatch, [_seg("L", 9, 1), _seg("H", 10, 5)])
    assert r["available"] is False and r["state"] is None


def test_research_wrapper_same_object():
    df = _df(200)
    assert sw.swing_state(df) == bs.check_swing_state(df)


def test_one_wave_v4_fast_large_continuous():
    c = np.r_[np.full(100, 10.0), np.linspace(10, 16, 8), np.linspace(16, 13, 12)]
    df = pd.DataFrame(
        {
            "open": c,
            "high": c * 1.01,
            "low": c * 0.99,
            "close": c,
            "volume": np.full(len(c), 1e6),
        }
    )
    assert bs.check_one_wave_v4(df)["hit"] is True


def test_one_wave_v4_slow_or_broken_not_hit():
    c = np.r_[np.full(60, 10.0), np.linspace(10, 16, 60)]
    df = pd.DataFrame(
        {
            "open": c,
            "high": c * 1.01,
            "low": c * 0.99,
            "close": c,
            "volume": np.full(len(c), 1e6),
        }
    )
    assert bs.check_one_wave_v4(df)["hit"] is False
