# -*- coding: utf-8 -*-
"""保本止损（盈亏平衡保护）与移动止损回归测试。

保本止损**不是新发明**——`b1_swing_strategy.md:328` 早就写了「已形成有效浮盈后，同时启用
盈亏平衡保护，防止赢转亏」，但回测里一直没有。这和分批止盈是同一类缺口：文档定义了、
检测/规则都在，只有回测没实现，于是回测系统性低估策略、也没法验证这些机制值不值得。

方法论（M1，经 H1/H2 终审进一步印证）：排序类因子已全部跨窗失败，**机制类改进是唯一
验证成功的方向**。判定看 `expectancy_R` / `payoff_ratio` / `total_R`，不看胜率。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from screening.backtest_factors import _bbi_series, simulate_b1_trade

BASE = [(10, 10.1, 9.9, 10)] * 30          # 进场 close=10，初始止损 low=9.9
ENTRY = 29


def _mk(bars):
    a = np.array(bars, float)
    return pd.DataFrame({
        "date": pd.bdate_range("2025-01-01", periods=len(a)),
        "open": a[:, 0], "high": a[:, 1], "low": a[:, 2], "close": a[:, 3],
        "volume": np.full(len(a), 5e5), "amount": a[:, 3] * 5e5})


def _run(bars, **kw):
    df = _mk(bars)
    return simulate_b1_trade(df, ENTRY, _bbi_series(df["close"]), **kw)


# 涨到 +12% 后渐进回落（不跳空），最终跌破初始止损
WIN_TO_LOSS = BASE + [
    (10.4, 10.5, 10.3, 10.4), (10.8, 10.9, 10.7, 10.8), (11.2, 11.3, 11.1, 11.2),
    (11.1, 11.2, 10.85, 10.9), (10.85, 10.95, 10.05, 10.1),
    (10.1, 10.15, 9.85, 9.88), (9.88, 9.9, 9.5, 9.55)]


class TestDefaultOff:
    """默认必须关闭——否则所有历史回测结果都不可对照。"""

    @pytest.mark.parametrize("bars", [WIN_TO_LOSS, BASE + [(10, 10.2, 9.5, 9.6)]])
    def test_zero_params_identical(self, bars):
        assert _run(bars) == _run(bars, breakeven_trigger=0.0, trail_pct=0.0)

    def test_signature_defaults_are_zero(self):
        import inspect
        ps = inspect.signature(simulate_b1_trade).parameters
        assert ps["breakeven_trigger"].default == 0.0
        assert ps["trail_pct"].default == 0.0


class TestBreakevenStop:
    """b1_swing_strategy.md:328「已形成有效浮盈后…防止赢转亏」。"""

    def test_converts_loss_to_flat(self):
        loss = _run(WIN_TO_LOSS)["ret"]
        be = _run(WIN_TO_LOSS, breakeven_trigger=0.05)
        assert loss < 0, "基准场景应是亏损（赢转亏）"
        assert be["reason"] == "breakeven_stop"
        assert be["ret"] == pytest.approx(0.0, abs=1e-9), "止损位=成本价 ⇒ 收益恰为 0"
        assert be["ret"] > loss

    def test_not_armed_below_trigger(self):
        """浮盈没到阈值就不该上移止损。"""
        bars = BASE + [(10, 10.2, 9.6, 10.1)] * 2 + [(10, 10.1, 9.4, 9.5)]
        r = _run(bars, breakeven_trigger=0.05)      # 浮盈仅 2%
        assert r["reason"] == "stop"
        assert not r.get("breakeven_armed")

    def test_gap_down_can_pierce_breakeven(self):
        """跳空低开会穿过保本位——保本止损**不保证**一定保本，必须如实建模。"""
        bars = BASE + [(10.4, 10.6, 10.3, 10.5), (9.8, 9.9, 9.3, 9.4)]
        r = _run(bars, breakeven_trigger=0.03)
        assert r["ret"] == pytest.approx(9.8 / 10 - 1), "按开盘价 9.8 成交，不是保本位 10.0"
        assert r["ret"] < 0

    def test_stop_never_moves_down(self):
        """止损只上移。浮盈达标后回落，止损位不得退回初始值。"""
        bars = BASE + [(10.4, 10.6, 10.35, 10.5), (10.4, 10.45, 9.95, 9.98)]
        r = _run(bars, breakeven_trigger=0.03)
        assert r["reason"] == "breakeven_stop"
        assert r["ret"] == pytest.approx(0.0, abs=1e-9)


class TestTrailingStop:
    def test_locks_in_partial_profit(self):
        r = _run(WIN_TO_LOSS, trail_pct=0.08)
        assert r["reason"] == "trail_stop"
        assert r["ret"] > 0, "峰值 +12%、回撤 8% ⇒ 应锁住正收益"
        assert r["ret"] > _run(WIN_TO_LOSS, breakeven_trigger=0.05)["ret"]

    def test_does_not_cut_trend_without_pullback(self):
        """**关键**：单边上涨（无回撤）不得触发。

        终审证实收益极端幂律——极少数大赢家贡献全部收益。若移动止损把趋势单砍掉，
        代价远大于它保护的那点回撤（结论#15）。
        """
        big = BASE + [(10 + i * 0.35, 10 + i * 0.35 + 0.15, 10 + i * 0.35 - 0.12,
                       10 + i * 0.35) for i in range(1, 25)]
        for tp in (0.05, 0.08, 0.12):
            r = _run(big, trail_pct=tp)
            assert r["reason"] == "open_end", f"trail {tp} 砍掉了无回撤的趋势单"
            assert r["ret"] == pytest.approx(_run(big)["ret"])

    def test_tighter_trail_exits_earlier(self):
        """上涨中夹一次深回撤：紧的 trail 被震出，松的能扛住。"""
        bars = BASE + [
            (10.5, 10.7, 10.4, 10.6), (11.0, 11.2, 10.9, 11.1),     # 峰值 11.2
            (11.0, 11.05, 10.35, 10.4),                              # 回撤 7.6%
            (10.5, 11.5, 10.45, 11.4), (11.6, 12.0, 11.5, 11.9)]     # 继续上涨
        tight = _run(bars, trail_pct=0.05)
        loose = _run(bars, trail_pct=0.15)
        assert tight["reason"] == "trail_stop"
        assert tight["holding"] < loose["holding"]
        assert loose["ret"] > tight["ret"], "扛住回撤才吃到后面的上涨"


class TestNoLookAhead:
    """止损位只能用截至 j-1 的最高价更新。"""

    def test_intraday_spike_does_not_arm_same_bar(self):
        """当日冲高 +15% 后收跌：若用当日 high 更新 trail，会在当日触发（未来函数）。

        11.5×0.92 = 10.58 > 当日 low 9.85 ⇒ 有未来函数就会记 trail_stop@holding=1。
        """
        bars = BASE + [(10.0, 11.5, 9.85, 9.9)] + [(9.9, 10.0, 9.8, 9.9)] * 3
        r = _run(bars, trail_pct=0.08)
        assert not (r["reason"] == "trail_stop" and r["holding"] == 1), "未来函数"

    def test_breakeven_needs_prior_bar_high(self):
        bars = BASE + [(10.0, 10.9, 9.85, 9.9)] + [(9.9, 10.0, 9.8, 9.9)] * 2
        r = _run(bars, breakeven_trigger=0.05)
        assert not (r["reason"] == "breakeven_stop" and r["holding"] == 1)


class TestAttribution:
    """reason 要按**实际决定止损位**的机制归因，便于事后拆解贡献。"""

    def test_higher_level_wins_naming(self):
        bars = BASE + [(10.3, 10.6, 10.2, 10.5), (10.4, 10.45, 9.95, 9.98),
                       (9.9, 9.95, 9.6, 9.7)]
        r = _run(bars, breakeven_trigger=0.05, trail_pct=0.08)
        # 保本位 10.0 > trail 位 10.6×0.92=9.75 ⇒ 是保本位决定的出场
        assert r["reason"] == "breakeven_stop"

    def test_trail_named_when_it_dominates(self):
        r = _run(WIN_TO_LOSS, breakeven_trigger=0.05, trail_pct=0.08)
        assert r["reason"] == "trail_stop", "trail 位 11.3×0.92=10.40 > 保本位 10.0"


class TestCombinedWithScaleOut:
    def test_scale_out_still_applies(self):
        bars = BASE + [(10.4, 10.5, 10.3, 10.45), (10.9, 11.0, 10.8, 10.95),
                       (11.0, 11.1, 10.2, 10.3), (10.2, 10.3, 9.6, 9.7)]
        r = _run(bars, scale_out_frac=0.5, breakeven_trigger=0.05, trail_pct=0.10)
        assert isinstance(r["ret"], float) and np.isfinite(r["ret"])
        if r.get("scale_out_idx") is not None:
            assert "scaled" in r["reason"]


class TestCliWiring:
    def test_flags_reach_evaluate_trades(self):
        import inspect

        from screening.backtest_factors import evaluate_trades
        ps = inspect.signature(evaluate_trades).parameters
        assert ps["breakeven_trigger"].default == 0.0
        assert ps["trail_pct"].default == 0.0
        src = inspect.getsource(evaluate_trades)
        assert "breakeven_trigger=breakeven_trigger" in src
        assert "trail_pct=trail_pct" in src

    def test_cli_exposes_flags(self):
        import pathlib
        src = pathlib.Path("07_tools/screening/backtest_factors.py").read_text(encoding="utf-8")
        assert '"--breakeven"' in src and '"--trail"' in src
        assert "breakeven_trigger=args.breakeven" in src and "trail_pct=args.trail" in src
