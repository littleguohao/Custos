# -*- coding: utf-8 -*-
"""v0.505 进场口径 entry_timing（借鉴 qteasy use_latest_data_cycle）：close 逐位不变 / next_open 语义。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from custos.research import backtest_factors as bt


def _df(n=80, seed=0):
    rng = np.random.default_rng(seed)
    c = 10 * np.cumprod(1 + rng.normal(0, 0.02, n))
    o = c * (1 + rng.normal(0, 0.01, n))
    h = np.maximum(o, c) * (1 + abs(rng.normal(0, 0.01, n)))
    lo = np.minimum(o, c) * (1 - abs(rng.normal(0, 0.01, n)))
    return pd.DataFrame(
        {
            "date": pd.date_range("2023-01-02", periods=n, freq="B"),
            "open": o,
            "high": h,
            "low": lo,
            "close": c,
            "volume": np.full(n, 1e6),
        }
    )


def _bbi(df):
    return df["close"].rolling(5, min_periods=1).mean()


def test_default_close_unchanged():
    df = _df()
    a = bt.simulate_b1_trade(df, 30, _bbi(df), code="600000")
    b = bt.simulate_b1_trade(df, 30, _bbi(df), code="600000", entry_timing="close")
    assert a == b


def test_next_open_fills_at_next_open_and_t_plus_1():
    df = _df()
    tr = bt.simulate_b1_trade(df, 30, _bbi(df), code="600000", entry_timing="next_open")
    assert "skipped" not in tr
    assert tr["exit_idx"] >= 32  # 成交根 31 当日不出场（T+1）
    assert tr["holding"] == tr["exit_idx"] - 31
    if not str(tr["reason"]).startswith("open_end") and "scaled" not in tr["reason"]:
        px = df["close"].iloc[tr["exit_idx"]]
        exp = px / df["open"].iloc[31] - 1
        assert (
            abs(tr["ret"] - exp) < 0.05
        )  # 按次日开盘价计收益（出场价可能是止损价/开盘价）


def test_next_open_unfillable_limit_up_or_halt():
    df = _df()
    df.loc[31, "open"] = df.loc[30, "close"] * 1.10  # 开盘即涨停
    assert bt.simulate_b1_trade(
        df, 30, _bbi(df), code="600000", entry_timing="next_open"
    ) == {
        "skipped": "entry_unfilled",
        "exit_idx": 30,
    }
    df = _df()
    df.loc[31, "volume"] = 0
    assert bt.simulate_b1_trade(
        df, 30, _bbi(df), code="600000", entry_timing="next_open"
    )["skipped"]
    df = _df()
    assert bt.simulate_b1_trade(
        df, len(df) - 1, _bbi(df), code="600000", entry_timing="next_open"
    )["skipped"]


def test_bad_value_rejected():
    import pytest

    df = _df()
    with pytest.raises(ValueError):
        bt.simulate_b1_trade(df, 30, _bbi(df), code="600000", entry_timing="vwap")


def test_evaluate_trades_next_open_and_replay_consistent():
    """collect_all 全扫与信号重放两条路径在 next_open 下逐位一致；涨停次日不出交易。"""
    df = _df(200, seed=3)
    bars = {"600000": df}
    sigs: list = []
    kw = dict(
        entry_gate=None, collect_all=True, tradability=False, entry_timing="next_open"
    )
    full = bt.evaluate_trades(bars, signals_out=sigs, **kw)
    rep = bt.evaluate_trades(bars, signals_in={"600000": sigs}, **kw)
    assert full == rep and full
    base = bt.evaluate_trades(
        bars, entry_gate=None, collect_all=True, tradability=False
    )
    assert len(base) >= len(full)
