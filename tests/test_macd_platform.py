# -*- coding: utf-8 -*-
"""MACD 平台结构（owner 2026-11-10 通达信公式转译；v0.589 起实现在 b1_structure，全项目走势唯一底座）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from custos.core.indicators import macd_series
from custos.core.factors import b1_structure as bs
from custos.pipeline.market_timing import technical_monitor as tm


def _df(close, open_=None):
    c = np.asarray(close, dtype=float)
    o = np.asarray(open_, dtype=float) if open_ is not None else c * 1.002
    return pd.DataFrame(
        {
            "date": pd.date_range("2023-01-02", periods=len(c), freq="B"),
            "open": o,
            "high": np.maximum(o, c) * 1.01,
            "low": np.minimum(o, c) * 0.99,
            "close": c,
            "volume": np.full(len(c), 1e6),
            "amount": c * 1e6,
        }
    )


def _wave(n=400, seed=0, drift=0.0):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    c = 10 * np.exp(
        drift * t + 0.08 * np.sin(t / 9.0) + np.cumsum(rng.normal(0, 0.004, n))
    )
    o = c * (1 + rng.normal(0, 0.01, n))
    return _df(c, o)


def test_segments_match_formula_definition():
    df = _wave()
    segs = tm.macd_platform_segments(df)
    _d, _e, h = macd_series(df["close"])
    h = h.to_numpy()
    o, c = df["open"].to_numpy(), df["close"].to_numpy()
    assert segs and all(s["kind"] in ("H", "L") for s in segs)
    for s in segs:
        a = int(df.index[df["date"].dt.strftime("%Y-%m-%d") == s["start"]][0])
        b = int(df.index[df["date"].dt.strftime("%Y-%m-%d") == s["end"]][0])
        sign = 1 if s["kind"] == "H" else -1
        assert all(np.sign(h[k]) == sign for k in range(a, b + 1))
        bears = [k for k in range(a, b + 1) if c[k] < o[k]]
        exp = max(o[k] for k in bears) if sign > 0 else min(c[k] for k in bears)
        assert abs(s["price"] - round(exp, 4)) < 1e-9
    assert segs[-1]["ongoing"] in (True, False)


def test_segment_without_bearish_candle_skipped():
    df = _wave()
    df["open"] = df["close"] * 0.99  # 全阳线 ⇒ 每段都无阴线 ⇒ 全部跳过
    assert tm.macd_platform_segments(df) == []
    assert bs.check_swing_state(df)["available"] is False


def test_states_up_and_down_on_drift():
    up = bs.check_swing_state(_wave(seed=1, drift=0.004))
    down = bs.check_swing_state(_wave(seed=1, drift=-0.004))
    assert up["available"] and up["now"] == "震荡上涨"
    assert down["available"] and down["now"] == "震荡下跌"


def test_asof_prefix_consistent():
    """as-of：前缀上算出的已完结段与全序列同段完全一致（只有末段 ongoing 可能不同）。"""
    df = _wave(seed=2)
    full = tm.macd_platform_segments(df)
    for k in (180, 250, 330):
        pre = tm.macd_platform_segments(df.iloc[:k])
        done = [s for s in pre if not s["ongoing"]]
        assert done == [s for s in full if s["end"] <= done[-1]["end"]][: len(done)]


def test_live_trend_and_n_from_platform_v0589():
    """owner 2026-11-25：持仓 analyze 的趋势、上升 N、下降 N 全部由 MACD 平台 N 结构推出（唯一实现）。"""
    df = _wave()
    an = tm.analyze(df, "600000")
    sw = bs.check_swing_state(df)
    assert (
        an["trend"]["basis"] == "macd_platform"
        and an["trend"]["swing_state"] == sw["state"]
    )
    assert an["n_structure"] == bs.check_n_structure(df)
    assert an["descending_n_structure"] == bs.check_descending_n_structure(df)
    assert tm.platform_window is bs.platform_window
    assert not hasattr(
        bs, "macd_platform_structure"
    )  # v0.604：三组判走势停用，只留一份走势实现


def _seg(kind, price, idx):
    return {"kind": kind, "price": price, "idx": idx}


def test_backward_points_merge_same_kind_v0512():
    """owner 2026-11-11：往回交替匹配，同类相邻合并（L 取更低、H 取更高），凑满三组即停。"""
    segs = [
        _seg("L", 9.0, 0),
        _seg("H", 12.0, 1),
        _seg("L", 10.0, 2),
        _seg("L", 9.5, 3),
        _seg("H", 13.0, 4),
        _seg("H", 13.5, 5),
        _seg("L", 11.0, 6),
        _seg("H", 14.0, 7),
    ]
    pts = tm.macd_platform_points(segs, 3)
    assert [(p["kind"], p["price"]) for p in pts] == [
        ("H", 14.0),
        ("L", 11.0),
        ("H", 13.5),
        ("L", 9.5),
        ("H", 12.0),
        ("L", 9.0),
    ]
    # 并列取较新者（往回先见到的较新点保留）
    tie = tm.macd_platform_points([_seg("L", 9.0, 0), _seg("L", 9.0, 1)], 3)
    assert tie == [_seg("L", 9.0, 1)]


def test_rally_segment_from_platform_points_v0591(monkeypatch):
    """owner 2026-11-25：上涨段唯一定位 = 最近一个平台顶 H 与它前面的平台底 L（H 之后到当根 = 回调段）。"""
    import numpy as np

    df = _df(
        np.r_[
            np.linspace(12, 10, 20), np.linspace(10, 15, 20), np.linspace(15, 13.5, 20)
        ]
    )
    segs = [
        {"kind": "H", "price": 12.0, "idx": 2, "ongoing": False},
        {"kind": "L", "price": 10.2, "idx": 18, "ongoing": False},
        {"kind": "H", "price": 14.8, "idx": 38, "ongoing": False},
        {"kind": "L", "price": 13.6, "idx": 59, "ongoing": True},
    ]
    monkeypatch.setattr(bs, "macd_platform_segments", lambda d: segs)
    r = bs.rally_segment(df)
    lo, hi = df["low"].to_numpy(), df["high"].to_numpy()
    assert r["i_low"] == 2 + int(lo[2:39].argmin())  # 前一个平台顶 → H 之间最低价
    assert r["i_high"] == r["i_low"] + int(
        hi[r["i_low"] : 60].argmax()
    )  # 到下一个平台底之间最高价
    assert r["pullback_bars"] == 59 - r["i_high"]
    monkeypatch.setattr(bs, "macd_platform_segments", lambda d: segs[:1])
    assert bs.rally_segment(df) is None  # 没有「L → H」
