# -*- coding: utf-8 -*-
"""保本止损（盈亏平衡保护）与移动止损回归测试。

保本止损**不是新发明**——`01_swing_rules.md:328` 早就写了「已形成有效浮盈后，同时启用
盈亏平衡保护，防止赢转亏」，但回测里一直没有。这和分批止盈是同一类缺口：文档定义了、
检测/规则都在，只有回测没实现，于是回测系统性低估策略、也没法验证这些机制值不值得。

方法论（M1，经 H1/H2 终审进一步印证）：排序类因子已全部跨窗失败，**机制类改进是唯一
验证成功的方向**。判定看 `expectancy_R` / `payoff_ratio` / `total_R`，不看胜率。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from custos.research.backtest_factors import _bbi_series, simulate_b1_trade

BASE = [(10, 10.1, 9.9, 10)] * 30  # 进场 close=10，初始止损 low=9.9
ENTRY = 29


def _mk(bars):
    a = np.array(bars, float)
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-01", periods=len(a)),
            "open": a[:, 0],
            "high": a[:, 1],
            "low": a[:, 2],
            "close": a[:, 3],
            "volume": np.full(len(a), 5e5),
            "amount": a[:, 3] * 5e5,
        }
    )


def _run(bars, **kw):
    df = _mk(bars)
    return simulate_b1_trade(df, ENTRY, _bbi_series(df["close"]), **kw)


# 涨到 +12% 后渐进回落（不跳空），最终跌破初始止损
WIN_TO_LOSS = BASE + [
    (10.4, 10.5, 10.3, 10.4),
    (10.8, 10.9, 10.7, 10.8),
    (11.2, 11.3, 11.1, 11.2),
    (11.1, 11.2, 10.85, 10.9),
    (10.85, 10.95, 10.05, 10.1),
    (10.1, 10.15, 9.85, 9.88),
    (9.88, 9.9, 9.5, 9.55),
]


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
    """01_swing_rules.md:328「已形成有效浮盈后…防止赢转亏」。"""

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
        r = _run(bars, breakeven_trigger=0.05)  # 浮盈仅 2%
        assert r["reason"] == "stop"
        assert not r.get("breakeven_armed")

    def test_gap_down_can_pierce_breakeven(self):
        """跳空低开会穿过保本位——保本止损**不保证**一定保本，必须如实建模。"""
        bars = BASE + [(10.4, 10.6, 10.3, 10.5), (9.8, 9.9, 9.3, 9.4)]
        r = _run(bars, breakeven_trigger=0.03)
        assert r["ret"] == pytest.approx(9.8 / 10 - 1), (
            "按开盘价 9.8 成交，不是保本位 10.0"
        )
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
        big = BASE + [
            (10 + i * 0.35, 10 + i * 0.35 + 0.15, 10 + i * 0.35 - 0.12, 10 + i * 0.35)
            for i in range(1, 25)
        ]
        for tp in (0.05, 0.08, 0.12):
            r = _run(big, trail_pct=tp)
            assert r["reason"] == "open_end", f"trail {tp} 砍掉了无回撤的趋势单"
            assert r["ret"] == pytest.approx(_run(big)["ret"])

    def test_tighter_trail_exits_earlier(self):
        """上涨中夹一次深回撤：紧的 trail 被震出，松的能扛住。"""
        bars = BASE + [
            (10.5, 10.7, 10.4, 10.6),
            (11.0, 11.2, 10.9, 11.1),  # 峰值 11.2
            (11.0, 11.05, 10.35, 10.4),  # 回撤 7.6%
            (10.5, 11.5, 10.45, 11.4),
            (11.6, 12.0, 11.5, 11.9),
        ]  # 继续上涨
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
        bars = BASE + [
            (10.3, 10.6, 10.2, 10.5),
            (10.4, 10.45, 9.95, 9.98),
            (9.9, 9.95, 9.6, 9.7),
        ]
        r = _run(bars, breakeven_trigger=0.05, trail_pct=0.08)
        # 保本位 10.0 > trail 位 10.6×0.92=9.75 ⇒ 是保本位决定的出场
        assert r["reason"] == "breakeven_stop"

    def test_trail_named_when_it_dominates(self):
        r = _run(WIN_TO_LOSS, breakeven_trigger=0.05, trail_pct=0.08)
        assert r["reason"] == "trail_stop", "trail 位 11.3×0.92=10.40 > 保本位 10.0"


class TestCombinedWithScaleOut:
    def test_scale_out_still_applies(self):
        bars = BASE + [
            (10.4, 10.5, 10.3, 10.45),
            (10.9, 11.0, 10.8, 10.95),
            (11.0, 11.1, 10.2, 10.3),
            (10.2, 10.3, 9.6, 9.7),
        ]
        r = _run(bars, scale_out_frac=0.5, breakeven_trigger=0.05, trail_pct=0.10)
        assert isinstance(r["ret"], float) and np.isfinite(r["ret"])
        if r.get("scale_out_idx") is not None:
            assert "scaled" in r["reason"]


class TestCliWiring:
    def test_flags_reach_evaluate_trades(self):
        import inspect

        from custos.research.backtest_factors import evaluate_trades

        ps = inspect.signature(evaluate_trades).parameters
        assert ps["breakeven_trigger"].default == 0.0
        assert ps["trail_pct"].default == 0.0
        src = inspect.getsource(evaluate_trades)
        assert "breakeven_trigger=breakeven_trigger" in src
        assert "trail_pct=trail_pct" in src

    def test_cli_exposes_flags(self):
        import pathlib

        src = pathlib.Path("src/custos/research/backtest_factors.py").read_text(
            encoding="utf-8"
        )
        assert '"--breakeven"' in src and '"--trail"' in src
        assert (
            "breakeven_trigger=args.breakeven" in src and "trail_pct=args.trail" in src
        )


class TestStopTriggerCloseVsIntraday:
    """止损触发口径（2026-08-04 按 B1_w.pdf 修正）。

    材料反复强调看收盘：
      「设止损…**看上下区间，看收盘价**」
      「破掉止损价格，拍掉！（**收盘时**）」
      「**忽略盘中的冲高回落**」「**不要在下杀中卖出**」
      「不要在意盘中上蹿下跳，给老子他妈的拿住！」

    ⚠️ 但保本止损是**例外**：「赚钱的票有过上涨行为后，马上回到成本价，
    拍掉！（**盘中关注**）」——常规止损位在下方较远、盘中假破常见，等收盘确认；
    保本位就是成本价、属心理防线，立即执行。
    """

    # 盘中最低 9.5 跌破止损 9.9，但收盘 10.2 收回，之后继续上涨
    FAKE_BREAK = BASE + [
        (9.95, 10.3, 9.5, 10.2),
        (10.2, 10.5, 10.15, 10.45),
        (10.5, 10.8, 10.45, 10.75),
    ]

    def test_default_is_close(self):
        import inspect

        d = inspect.signature(simulate_b1_trade).parameters["stop_trigger"].default
        assert d == "close"

    def test_fake_intraday_break_is_ignored(self):
        r = _run(self.FAKE_BREAK)
        assert r["reason"] != "stop", "盘中假破不该止损"
        assert r["ret"] > 0

    def test_intraday_mode_reproduces_old_behavior(self):
        r = _run(self.FAKE_BREAK, stop_trigger="intraday")
        assert r["reason"] == "stop" and r["holding"] == 1

    def test_real_break_still_stops(self):
        bars = BASE + [(9.9, 9.95, 9.6, 9.7), (9.7, 9.75, 9.5, 9.55)]
        assert _run(bars)["reason"] == "stop"

    def test_close_mode_fills_at_close(self):
        """收盘破位按收盘价成交——这是材料的执行方式（收盘时拍掉）。"""
        bars = BASE + [(9.95, 9.98, 9.60, 9.70)]
        r = _run(bars)
        assert r["ret"] == pytest.approx(9.70 / 10 - 1)

    def test_close_mode_can_lose_more_than_intraday(self):
        """必须承认的权衡：等收盘确认会多承受下跌，真破位时亏更多。

        减少假止损与加大真止损幅度是对冲的，净效果只能靠回测判定，
        不能只讲「减少假止损」这一面。
        """
        bars = BASE + [(9.95, 9.98, 9.60, 9.70)]
        assert _run(bars)["ret"] < _run(bars, stop_trigger="intraday")["ret"]

    def test_breakeven_stays_intraday_under_close_mode(self):
        """保本止损在收盘口径下**仍按盘中**触发，且按保本位成交。"""
        bars = BASE + [
            (10.4, 10.5, 10.35, 10.45),
            (10.9, 11.2, 10.85, 11.15),
            (11.0, 11.1, 9.98, 10.3),
            (10.3, 10.4, 10.2, 10.35),
        ]
        r = _run(bars, breakeven_trigger=0.05)
        assert r["reason"] == "breakeven_stop"
        assert r["ret"] == pytest.approx(0.0, abs=1e-9), "按成本价成交，不是收盘价"

    def test_breakeven_not_downgraded_to_close_fill(self):
        """回归：曾因 be_hit 被 other_hit 抢先，成交价从成本价滑到收盘价（差 1.2pp）。"""
        r = _run(WIN_TO_LOSS, breakeven_trigger=0.05)
        assert r["reason"] == "breakeven_stop"
        assert r["ret"] == pytest.approx(0.0, abs=1e-9)


class TestStopTickBuffer:
    """材料：「B1- 买入K线最低点**或向下 3-5 个价位**」——贴着最低点容易被一笔扫掉。"""

    def test_default_zero_keeps_old_behavior(self):
        import inspect

        assert (
            inspect.signature(simulate_b1_trade).parameters["stop_tick_buffer"].default
            == 0
        )

    def test_buffer_widens_stop_and_avoids_marginal_break(self):
        bars = BASE + [
            (9.92, 9.98, 9.88, 9.89),
            (9.9, 10.1, 9.89, 10.05),
            (10.1, 10.3, 10.05, 10.25),
        ]
        tight = _run(bars, stop_tick_buffer=0)
        loose = _run(bars, stop_tick_buffer=3)
        assert tight["reason"] == "stop"
        assert loose["reason"] != "stop", "9.89 > 9.87，留余量后不算破位"
        assert loose["risk_frac"] > tight["risk_frac"], "止损空间变大"

    def test_buffer_not_applied_to_pct_mode(self):
        """pct 模式的止损位是按比例算的，不该再叠加 tick 余量。"""
        a = _run(WIN_TO_LOSS, stop_mode="pct", stop_pct=8.0, stop_tick_buffer=5)
        b = _run(WIN_TO_LOSS, stop_mode="pct", stop_pct=8.0, stop_tick_buffer=0)
        assert a["risk_frac"] == pytest.approx(b["risk_frac"])


class TestCenterRising:
    """收盘价重心（材料持股手册「一等马：收盘价重心上升为主」）。"""

    @pytest.mark.parametrize(
        "seq,expect",
        [
            ([10, 10.2, 10.4, 10.6, 10.8, 11], True),
            ([11, 10.8, 10.6, 10.4, 10.2, 10], False),
            ([10, 9.9, 9.95, 10.3, 10.5, 10.6], True),  # 先跌后涨，重心上移
            ([10, 10.3, 10.6, 10.9, 11.0, 10.85], True),  # 末根小阴但重心仍上升
            ([10, 10.01, 9.99, 10.02, 9.98, 10.0], False),  # 横盘
        ],
    )
    def test_segment_mean_comparison(self, seq, expect):
        from custos.research.backtest_factors import _center_rising

        assert _center_rising(np.array(seq, float)) is expect

    def test_uses_means_not_endpoints(self):
        """**关键**：用前后段均值，不用「末值 > 首值」。

        「重心」是中枢概念——末值比较会被最后一根噪声左右（一根小阴线就把
        「重心上升」判成否），而那正是材料反复告诫的「忽略盘中/单日波动」。
        """
        from custos.research.backtest_factors import _center_rising

        # 末值 9.99 < 首值 10（末值比较会判 False），但前段均值 10.05 < 后段 10.46
        seq = np.array([10, 10.05, 10.1, 10.6, 10.8, 9.99], float)
        assert seq[-1] < seq[0], "构造前提：末值低于首值"
        assert _center_rising(seq) is True, "重心明显上移，不该被末根小阴否掉"

    def test_too_short_returns_false(self):
        from custos.research.backtest_factors import _center_rising

        for seq in ([], [10.0], [10, 10.5], [10, 10.5, 11]):
            assert _center_rising(np.array(seq, float)) is False


class TestCostZoneStop:
    """「不涨就拍」——**三个维度都平淡才砍**（材料持股手册四种马 + 仓位实例）。

        · 未站上 BBI        「不温不火，**没上BBI**，又没到止损。收盘前全拍！」
        · 收盘价重心未上升   「一等马（**收盘价重心上升**为主）⇒ 拿住不动」
        · 未脱离成本区       「三个交易日还没脱离成本区，又没打止损，多等一天」

    ⚠️ 第一版只看「未脱离成本区 3%」一条，实测胜率 38.5%（全场最高）但均盈 9.76%
    （全场最低）——典型「砍掉慢热单」：已站上 BBI、重心上行、只是涨幅还没到 3% 的票
    会被误杀，而这类票里有后来的大赢家。
    """

    FLAT = BASE + [(10.0, 10.04, 9.97, 10.0)] * 8  # 贴着 BBI 横盘
    ABOVE_BBI = BASE + [(10.05, 10.15, 10.0, 10.12)] * 8  # 站上 BBI 但只涨 1.2%
    RISING = BASE + [
        (9.95, 10.0, 9.92, 9.96),
        (9.96, 10.0, 9.93, 9.97),
        (9.97, 10.02, 9.94, 9.99),
        (9.99, 10.03, 9.96, 10.0),
        (10.0, 10.04, 9.97, 10.01),
        (10.01, 10.05, 9.98, 10.02),
    ]
    ESCAPED = BASE + [(10.2, 10.4, 10.15, 10.35)] * 8  # 已涨 3.5%

    def test_default_off(self):
        import inspect

        assert (
            inspect.signature(simulate_b1_trade).parameters["cost_zone_bars"].default
            == 0
        )

    def test_all_three_flat_is_cut(self):
        r = _run(self.FLAT, cost_zone_bars=3)
        assert r["reason"] == "cost_zone_stop"
        assert r["holding"] == 4, "3 个交易日 + 多等一天"

    @pytest.mark.parametrize(
        "attr,why",
        [
            ("ABOVE_BBI", "已站上 BBI"),
            ("RISING", "收盘价重心上升"),
            ("ESCAPED", "已脱离成本区"),
        ],
    )
    def test_any_rising_dimension_keeps_position(self, attr, why):
        """任一维度显示还在涨就留着——旧判据会把前两种也砍掉。"""
        r = _run(getattr(self, attr), cost_zone_bars=3)
        assert r["reason"] != "cost_zone_stop", f"{why} 却被砍了"

    def test_flat_at_bbi_counts_as_not_above(self):
        """贴着 BBI 横盘算「没上 BBI」——用 >= 会把横盘误判成站上而永不触发。"""
        r = _run(self.FLAT, cost_zone_bars=3)
        assert r["reason"] == "cost_zone_stop"

    def test_threshold_configurable(self):
        r = _run(self.ESCAPED, cost_zone_bars=3, cost_zone_pct=10.0)
        # 涨 3.5% 未达 10% 阈值，但重心在上升 ⇒ 仍不该砍
        assert r["reason"] != "cost_zone_stop"

    def test_uses_close_not_intraday_high(self):
        """脱离成本区用收盘价判——盘中冲高又回落不算（材料：忽略盘中冲高回落）。"""
        spike = BASE + [(10.0, 10.9, 9.98, 10.0)] * 8  # 盘中冲 9%，收盘平
        assert _run(spike, cost_zone_bars=3)["reason"] == "cost_zone_stop"

    def test_off_is_byte_identical(self):
        for bars in (self.FLAT, self.ABOVE_BBI, self.RISING, self.ESCAPED):
            assert _run(bars) == _run(bars, cost_zone_bars=0)
