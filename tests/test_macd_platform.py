# -*- coding: utf-8 -*-
"""v0.507 MACD 平台结构（owner 2026-11-10 通达信公式转译，候选，不进 live analyze）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from custos.core.indicators import macd_series
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
    assert tm.macd_platform_structure(df)["available"] is False


def test_states_up_down_and_breach():
    up = tm.macd_platform_structure(_wave(seed=1, drift=0.004))
    down = tm.macd_platform_structure(_wave(seed=1, drift=-0.004))
    assert up["available"] and up["state"] == "上升"
    assert down["available"] and down["state"] == "下降"
    assert (
        up["L2"]["price"] > up["L1"]["price"] and up["H2"]["price"] > up["H1"]["price"]
    )
    df = _wave(seed=1, drift=0.004)
    st = tm.macd_platform_structure(df)
    df.loc[len(df) - 1, "close"] = st["L2"]["price"] * 0.9  # 末根收盘砸穿 L2
    after = tm.macd_platform_structure(df)
    assert after["state"] != "上升" or after["breached"]


def test_asof_prefix_consistent():
    """as-of：前缀上算出的已完结段与全序列同段完全一致（只有末段 ongoing 可能不同）。"""
    df = _wave(seed=2)
    full = tm.macd_platform_segments(df)
    for k in (180, 250, 330):
        pre = tm.macd_platform_segments(df.iloc[:k])
        done = [s for s in pre if not s["ongoing"]]
        assert done == [s for s in full if s["end"] <= done[-1]["end"]][: len(done)]


def test_not_in_live_analyze():
    an = tm.analyze(_wave(), "600000")
    assert "n_structure_macd" not in an  # 候选不进 live 输出
