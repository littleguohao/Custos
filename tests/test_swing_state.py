# -*- coding: utf-8 -*-
"""走势三态（v0.604：近 120 根全部 MACD 平台点、最高顶 / 最低底切两段，owner 2026-11-26）+ 一波流 v4。"""

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
    """v0.604：120 根全部平台点在最低平台底处切两段：前段顶底都降 = 下跌，后段顶底都抬 = 上涨。"""
    segs = [
        _seg("L", 10, 1),
        _seg("H", 12, 5),
        _seg("L", 9, 10),
        _seg("H", 11, 15),
        _seg("L", 8, 20),
        _seg("H", 10, 25),
        _seg("L", 9, 30),
        _seg("H", 11, 35),
        _seg("L", 10, 40),
        _seg("H", 12.5, 45),
    ]
    r = _with(monkeypatch, segs)
    assert r["state"] == "下跌转上涨" and r["now"] == "震荡上涨"
    assert r["turn_bars_ago"] == 59 - 20 and r["n_points"] == 10


def test_part_needs_four_points_else_whole(monkeypatch):
    """切点两侧任一段不足 4 个平台点 ⇒ 不切，整段一个态（首尾底、首尾顶）。"""
    segs = [
        _seg("L", 10, 1),
        _seg("H", 12, 5),
        _seg("L", 9, 10),
        _seg("H", 11, 15),
        _seg("L", 9.5, 20),
        _seg("H", 11.5, 25),
    ]
    r = _with(monkeypatch, segs)
    assert r["state"] == "震荡下跌" and r["turn_bars_ago"] is None


def test_only_points_within_120_bars(monkeypatch):
    """只取近 120 根内的平台点；更早的点不参与。"""
    monkeypatch.setattr(
        bs,
        "macd_platform_segments",
        lambda df: (
            [_seg("L", 5, 10), _seg("H", 20, 50)]
            + [
                _seg(k, v, 200 + 10 * i)
                for i, (k, v) in enumerate(
                    [("L", 9), ("H", 11), ("L", 9.2), ("H", 10.8)]
                )
            ]
        ),
    )
    r = bs.check_swing_state(_df(300))
    assert r["n_points"] == 4 and r["state"] == "横盘震荡"


def test_unavailable_when_less_than_four_points(monkeypatch):
    r = _with(monkeypatch, [_seg("L", 9, 1), _seg("H", 10, 5), _seg("L", 9.5, 9)])
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


def test_short_tail_new_low_or_high_v0609(monkeypatch):
    """owner 2026-11-27：切点后不足 4 个点，但最后一个点是窗口最低 L（最高 H）⇒ 后段算下跌（上涨）。"""
    up_then_crash = [
        _seg("L", 9, 1),
        _seg("H", 11, 5),
        _seg("L", 9.5, 10),
        _seg("H", 12, 15),
        _seg("L", 10, 20),
        _seg("H", 13, 25),
        _seg("L", 8, 30),
    ]
    r = _with(monkeypatch, up_then_crash)
    assert r["state"] == "上涨转下跌" and r["turn_bars_ago"] == 59 - 25
    down_then_pop = [
        _seg("H", 12, 1),
        _seg("L", 10, 5),
        _seg("H", 11, 10),
        _seg("L", 9, 15),
        _seg("H", 10, 20),
        _seg("L", 8, 25),
        _seg("H", 13, 30),
    ]
    assert _with(monkeypatch, down_then_pop)["state"] == "下跌转上涨"
    # 最后一个点不是窗口极值 ⇒ 不适用（整段一个态）
    mild = up_then_crash[:-1] + [_seg("L", 9.8, 30)]
    assert _with(monkeypatch, mild)["turn_bars_ago"] is None
