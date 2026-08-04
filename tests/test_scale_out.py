# -*- coding: utf-8 -*-
"""分批止盈回归测试（B1 §六 第五层 / B1.pdf「BBI 之上两根中阳线，放飞一半」）。

为什么这一层重要：在充分竞争市场里单信号胜率上限约 50%，胜率不是可优化的变量——
期望的杠杆几乎全在盈亏比上。而分批止盈**不改变胜率**（赢的次数一样），只改变 avg_win，
所以它是纯粹的盈亏比杠杆。

此前回测完全没有这一层：所有盈利单必须等 BBI 跌破才离场（已经回撤过了），
于是系统性低估了 B1 的 avg_win 与 payoff_ratio。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import backtest_factors as bt


def _mk(rows, entry_idx=39, entry_low_frac=0.965):
    """entry_idx 那根给长下影（B1 买点的典型形态）。

    否则会踩浮点相等:low=min(close,open)*k 且 open[t]=close[t-1] ⇒ 上涨日 low 恰等于
    前一日 low(=止损位)，进场后第一根就"跌破"止损。
    """
    c = np.array([r[0] for r in rows], float)
    v = np.array([r[1] for r in rows], float)
    o = np.concatenate(([c[0]], c[:-1]))
    lo = np.minimum(c, o) * 0.996
    if entry_idx is not None and 0 <= entry_idx < len(lo):
        lo[entry_idx] = c[entry_idx] * entry_low_frac
    return pd.DataFrame({
        "date": pd.bdate_range("2024-01-02", periods=len(c)),
        "open": o, "high": np.maximum(c, o) * 1.004, "low": lo,
        "close": c, "volume": v, "amount": v * c,
    })


def _pump_then_fade(pump_pct, pump_bars=2, after=18, decay=-0.012):
    """低位盘整 → 进场 → 连续大阳冲高 → 缓慢回落跌破 BBI（B1 最典型的赢单形态）。"""
    rows = [(10.0, 4e5)] * 40
    p = 10.0
    for _ in range(pump_bars):
        p *= (1 + pump_pct)
        rows.append((p, 1.2e6))
    for _ in range(after):
        p *= (1 + decay)
        rows.append((p, 5e5))
    return _mk(rows)


def _sim(df, scale, code="600000"):
    return bt.simulate_b1_trade(df, 39, pd.Series(bt._bbi_series_from(df)),
                                scale_out_frac=scale, code=code)


class TestMediumLargeBullFlags:
    """中大阳线口径必须与 technical_monitor / b1_swing_strategy.md 一致：
    阳线 且（单日涨幅 或 实体幅度）≥ 半个涨停幅度。"""

    def test_threshold_is_half_price_limit(self):
        df = _mk([(10.0, 4e5)] * 5 + [(10.0 * 1.06, 4e5)])
        assert bt._medium_large_bull_flags(df, "600000")[-1]      # 主板 6% ≥ 5% ✓
        assert not bt._medium_large_bull_flags(df, "300750")[-1]  # 创业板门槛 10%

    def test_below_threshold_not_counted(self):
        df = _mk([(10.0, 4e5)] * 5 + [(10.0 * 1.03, 4e5)])
        assert not bt._medium_large_bull_flags(df, "600000")[-1]

    def test_bear_never_counted(self):
        """必须先是阳线——大跌不算中大阳。"""
        df = _mk([(10.0, 4e5)] * 5 + [(10.0 * 0.90, 4e5)])
        assert not bt._medium_large_bull_flags(df, "600000")[-1]

    def test_body_or_change_either_qualifies(self):
        """涨幅或实体任一达标即可（原文口径是"或"）。"""
        rows = [(10.0, 4e5)] * 5 + [(10.55, 4e5)]
        flags = bt._medium_large_bull_flags(_mk(rows), "600000")
        assert flags[-1]


class TestScaleOutMechanics:
    def test_raises_return_on_pump_then_fade(self):
        df = _pump_then_fade(0.09)
        a, b = _sim(df, 0.0), _sim(df, 0.5)
        assert b["ret"] > a["ret"], "冲高后回落的单子，分批止盈必须更优"
        assert b["reason"].endswith("+scaled")
        assert b["scale_out_frac"] == 0.5

    def test_no_trigger_below_threshold(self):
        """冲高幅度未达中大阳门槛 → 不触发，收益与不启用完全相同。"""
        df = _pump_then_fade(0.03, after=10, decay=-0.008)
        a, b = _sim(df, 0.0), _sim(df, 0.5)
        assert a["ret"] == b["ret"]
        assert "scaled" not in b["reason"]

    def test_weighted_settlement_is_correct(self):
        df = _pump_then_fade(0.09)
        b = _sim(df, 0.5)
        # scale_out_ret / rest_ret 落盘时 round(4)，故按其精度比对
        expect = 0.5 * b["scale_out_ret"] + 0.5 * b["rest_ret"]
        assert b["ret"] == pytest.approx(expect, abs=1e-4)

    def test_scale_fraction_monotonic(self):
        """冲高后回落时，减仓比例越大收益越高（因为兑现在高点）。"""
        df = _pump_then_fade(0.09)
        rets = [_sim(df, f)["ret"] for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
        assert rets == sorted(rets), f"应单调递增，实际 {rets}"

    def test_only_triggers_once(self):
        """首次触发后不再重复减仓（避免多次计入）。"""
        rows = [(10.0, 4e5)] * 40
        p = 10.0
        for _ in range(6):                       # 连续 6 根大阳（多次满足条件）
            p *= 1.07
            rows.append((p, 1.2e6))
        for _ in range(14):
            p *= 0.988
            rows.append((p, 5e5))
        b = _sim(_mk(rows), 0.5)
        assert b["scale_out_idx"] is not None
        # 触发点应是最早满足"连续两根中大阳且在 BBI 上方"的那根。
        # 注意 simulate_b1_trade 返回绝对索引 scale_out_idx；相对根数 scale_out_bars
        # 只在 evaluate_trades 的记录里计算。
        assert b["scale_out_idx"] - 39 <= 3

    def test_requires_two_consecutive_bulls(self):
        """单根大阳不触发——原文是"两根"。"""
        rows = [(10.0, 4e5)] * 40
        p = 10.0 * 1.09
        rows.append((p, 1.2e6))                  # 只有一根大阳
        for _ in range(14):
            p *= 0.988
            rows.append((p, 5e5))
        b = _sim(_mk(rows), 0.5)
        assert "scaled" not in b["reason"]

    def test_requires_above_bbi(self):
        """必须在 BBI 上方——原文是"BBI 之上两根中阳线"。"""
        # 深跌后反抽两根大阳，但仍在 BBI 下方
        rows = [(20.0, 4e5)] * 30
        p = 20.0
        for _ in range(12):
            p *= 0.94
            rows.append((p, 6e5))
        for _ in range(2):
            p *= 1.06
            rows.append((p, 1.2e6))
        df = _mk(rows, entry_idx=41)
        b = bt.simulate_b1_trade(df, 41, pd.Series(bt._bbi_series_from(df)),
                                 scale_out_frac=0.5, code="600000")
        assert "scaled" not in b["reason"]

    def test_disabled_by_default(self):
        """默认 0（不启用），保证与旧回测结果可对照。"""
        import inspect
        sig = inspect.signature(bt.simulate_b1_trade)
        assert sig.parameters["scale_out_frac"].default == 0.0
        sig2 = inspect.signature(bt.evaluate_trades)
        assert sig2.parameters["scale_out_frac"].default == 0.0


class TestScaleOutRaisesPayoffNotWinRate:
    """**核心断言**：分批止盈提升盈亏比而不改变胜率。

    这是"胜率不是可优化变量、盈亏比才有杠杆"这一判断的直接检验。
    """

    def _portfolio(self, scale):
        trades = []
        for pump in (0.09, 0.08, 0.10, 0.07, 0.11, 0.06):     # 6 笔赢单
            trades.append(_sim(_pump_then_fade(pump), scale))
        for drop in (-0.02, -0.025, -0.03, -0.022):           # 4 笔亏单
            rows = [(10.0, 4e5)] * 40
            p = 10.0
            for _ in range(12):
                p *= (1 + drop)
                rows.append((p, 5e5))
            trades.append(_sim(_mk(rows), scale))
        for t in trades:
            t.setdefault("r_multiple", None)
        return bt.summarize_trades(trades)

    def test_win_rate_unchanged(self):
        a, b = self._portfolio(0.0), self._portfolio(0.5)
        assert a["win_rate"] == b["win_rate"], "分批止盈不该改变赢的次数"

    def test_avg_loss_unchanged(self):
        a, b = self._portfolio(0.0), self._portfolio(0.5)
        assert a["avg_loss"] == pytest.approx(b["avg_loss"], abs=1e-9), \
            "亏损单不触发分批止盈，均亏应完全一致"

    def test_avg_win_and_payoff_improve(self):
        a, b = self._portfolio(0.0), self._portfolio(0.5)
        assert b["avg_win"] > a["avg_win"]
        assert b["payoff_ratio"] > a["payoff_ratio"]
        assert b["expectancy"] > a["expectancy"]


class TestCliWiring:
    def test_scale_out_flag_exists(self):
        import subprocess
        import sys
        from paths import BASE
        r = subprocess.run([sys.executable, str(BASE / "07_tools" / "screening"
                                                / "backtest_factors.py"), "--help"],
                           capture_output=True, text=True, timeout=120)
        assert "--scale-out" in r.stdout
