# -*- coding: utf-8 -*-
"""TODO #58 效率优化的**逐位等价**钉测：simulate_b1_trade 的 OHLC 数组逐股复用。

背景：evaluate_trades 每笔交易都调用 ``simulate_b1_trade``，原实现每笔对
``df["close"/"low"/"high"/"open"].astype(float).values`` 四列各转一次
（每股 O(n×trades)）。这四列数组是 df 的纯函数、df 在同一股的扫描期间不变
⇒ 逐股算一次、每笔复用是**同一批浮点值**，逐位相同。

本文件钉住这条等价（防未来有人"优化"出口径漂移）：

  ① ``simulate_b1_trade(df, i, bbi, ohlc=预计算)`` 与不传 ohlc（内部现算）
     的返回 dict **完全一致**，覆盖 stop_mode/stop_buffer/scale_out/保本/移动
     止损/cost_zone/can_sell 各参数组合 × 多个 entry_idx。
  ② evaluate_trades 逐笔输出在传/不传预计算 ohlc 下**完全一致**
     （monkeypatch 掉 prep 的 ohlc 模拟旧路径）。
"""

import numpy as np
import pandas as pd
import pytest

from custos.research import backtest_factors as bt


def _bars(n: int = 160, seed: int = 7) -> pd.DataFrame:
    """确定性合成日线：掺入一字板(high==low)、零量停牌，覆盖护栏/止损边界路径。"""
    rng = np.random.default_rng(seed)
    close = np.maximum(10 + np.cumsum(rng.normal(0, 0.15, n)), 1.0)
    high = close + np.abs(rng.normal(0, 0.08, n))
    low = close - np.abs(rng.normal(0, 0.08, n))
    open_ = low + (high - low) * rng.random(n)
    vol = np.abs(rng.normal(1e6, 2e5, n))
    for k in range(5, n, 17):  # 一字板
        high[k] = low[k] = open_[k] = close[k]
    for k in range(9, n, 23):  # 停牌：量 0
        vol[k] = 0.0
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="B").astype(str),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
        }
    )


def _ohlc(df: pd.DataFrame) -> tuple:
    return tuple(df[c].astype(float).values for c in ("close", "low", "high", "open"))


_KW_COMBOS = [
    {},
    {"stop_mode": "pct", "stop_pct": 5.0},
    {"stop_buffer": "pct", "stop_pct_buffer": 0.5, "stop_tick_buffer": 3},
    {"stop_buffer": "atr", "stop_atr_buffer": 0.2},
    {"stop_trigger": "intraday", "stop_tick_buffer": 3},
    {"scale_out_frac": 0.5},
    {"breakeven_trigger": 0.05, "trail_pct": 0.08},
    {"cost_zone_bars": 3, "cost_zone_pct": 2.0},
    {"time_stop_bars": 10, "bbi_exit_consec": 1},
]


@pytest.mark.parametrize("kw", _KW_COMBOS)
@pytest.mark.parametrize("entry_idx", [30, 60, 120])
def test_simulate_ohlc_precomputed_bit_identical(kw, entry_idx):
    df = _bars()
    bbi = bt._bbi_series(df["close"])
    _, sell = bt.tradable_flags(df, "600000")
    bulls = (
        bt._medium_large_bull_flags(df, "600000") if kw.get("scale_out_frac") else None
    )
    base = bt.simulate_b1_trade(
        df, entry_idx, bbi, can_sell=sell, code="600000", bull_flags=bulls, **kw
    )
    fast = bt.simulate_b1_trade(
        df,
        entry_idx,
        bbi,
        can_sell=sell,
        code="600000",
        bull_flags=bulls,
        ohlc=_ohlc(df),
        **kw,
    )
    assert fast == base


def _always_buy(df: pd.DataFrame, code: str) -> dict:
    return {"score": 50.0, "suggestion": "可买", "aux": {}, "components": {}}


def test_evaluate_trades_ohlc_precompute_bit_identical(monkeypatch):
    bars = {f"{600000 + k}": _bars(seed=k) for k in range(3)}
    ref = bt.evaluate_trades(bars, scorer=_always_buy, collect_all=True)

    real_prepare = bt._prepare_stock

    def _no_ohlc(*args, **kwargs):
        prep = real_prepare(*args, **kwargs)
        if prep is not None:
            prep["ohlc"] = None  # 走 simulate_b1_trade 内部现算的旧路径
        return prep

    monkeypatch.setattr(bt, "_prepare_stock", _no_ohlc)
    old = bt.evaluate_trades(bars, scorer=_always_buy, collect_all=True)

    assert old == ref
    assert len(ref) > 0
