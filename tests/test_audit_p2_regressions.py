# -*- coding: utf-8 -*-
"""P2 回测偏差回归测试。

这一组守的是**结论的可信度**,而不是程序不崩。回测里的偏差不会报错——它安静地
把策略结果做得更好看,然后错误的结论被写进治理文档、变成上线的入场门槛。
本文件把审计发现的四类偏差固化成永久防线:

  E1 半程一致性按股票顺序切分(防过拟合门槛形同虚设)
  E2 前向窗口静默截断(3 日收益混进 ret20)
  E3 打分器缺数据被填 0 分并参与排名
  E5 可成交性缺失(涨停照买、跌停照卖、停牌照止损)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from custos.research import backtest_factors as bt
from custos.research import launch_point_study as lp


def _bars(closes, highs=None, lows=None, vols=None, start="2026-01-05"):
    n = len(closes)
    return pd.DataFrame(
        {
            "date": pd.bdate_range(start=start, periods=n).strftime("%Y-%m-%d"),
            "open": closes,
            "high": highs if highs is not None else [c * 1.01 for c in closes],
            "low": lows if lows is not None else [c * 0.99 for c in closes],
            "close": closes,
            "volume": vols if vols is not None else [1e6] * n,
            "amount": [1e7] * n,
        }
    )


class TestForwardWindowCensoring:
    """E2: 窗口不足必须删失,不能把短窗收益写进长窗字段。"""

    def test_insufficient_window_is_censored(self):
        df = _bars([10.0 + i * 0.1 for i in range(10)])
        fm = bt.forward_metrics(df, 5, horizon=20)  # 只剩 4 根
        assert fm["available"] is False
        assert fm["censored"] is True
        assert fm["bars"] == 4 and fm["need"] == 20

    def test_full_window_still_works(self):
        df = _bars([10.0 + i * 0.1 for i in range(40)])
        fm = bt.forward_metrics(df, 5, horizon=20)
        assert fm["available"] is True
        assert fm["bars"] == 20 and fm["truncated"] is False

    def test_legacy_truncation_is_opt_in_and_flagged(self):
        df = _bars([10.0 + i * 0.1 for i in range(10)])
        fm = bt.forward_metrics(df, 5, horizon=20, require_full=False)
        assert fm["available"] is True
        assert fm["truncated"] is True, "旧口径必须自报截断"

    def test_evaluate_does_not_mix_short_windows_into_ret20(self):
        """端到端:靠近数据末端的信号不得给出 ret20。"""
        df = _bars([10.0 + (i % 7) * 0.3 for i in range(70)])
        recs = bt.evaluate(
            {"600000": df},
            horizons=(20,),
            min_bars=60,
            scorer=lambda d, c: {"score": 1.0, "suggestion": "可买"},
        )
        assert recs, "应有信号记录"
        for r in recs:
            if r["ret20"] is not None:
                assert r["ret20_bars"] == 20, "有 ret20 就必须是满 20 根"


class TestScorerMissingDataExcluded:
    """E3: 打分器返回 None 表示没数据,不能变成 0 分参与排名。"""

    def test_none_scorer_yields_no_records(self):
        df = _bars([10.0 + (i % 5) * 0.2 for i in range(80)])
        assert (
            bt.evaluate(
                {"600000": df}, horizons=(5,), min_bars=60, scorer=lambda d, c: None
            )
            == []
        )

    def test_zero_score_is_kept_as_real_score(self):
        """真实的 0 分与"没有分"必须区分开。"""
        df = _bars([10.0 + (i % 5) * 0.2 for i in range(80)])
        recs = bt.evaluate(
            {"600000": df},
            horizons=(5,),
            min_bars=60,
            scorer=lambda d, c: {"score": 0.0, "suggestion": "可买"},
        )
        assert recs and all(r["s_star"] == 0.0 for r in recs)

    def test_scorer_value_helper_distinguishes(self):
        df = _bars([10.0] * 10)
        assert lp._scorer_value(None, df, "600000") == (0.0, True)
        assert lp._scorer_value(lambda d, c: None, df, "600000") == (None, False)
        assert lp._scorer_value(lambda d, c: {}, df, "600000") == (None, False)
        assert lp._scorer_value(lambda d, c: {"score": 0.0}, df, "600000") == (
            0.0,
            True,
        )
        assert lp._scorer_value(lambda d, c: {"score": -3.5}, df, "600000") == (
            -3.5,
            True,
        )

    def test_negative_domain_scorer_not_outranked_by_missing(self):
        """mcap 类打分器是负值域:缺数据填 0 会排到所有真实分数前面。"""
        df = _bars([10.0] * 10)
        real, ok_real = lp._scorer_value(lambda d, c: {"score": -2.0}, df, "600000")
        missing, ok_missing = lp._scorer_value(lambda d, c: None, df, "600001")
        assert ok_real and not ok_missing
        assert missing is None, "缺数据必须是 None 而非 0.0——0.0 > -2.0 会篡改排名"


class TestTradability:
    """E5: 涨停买不到、跌停卖不掉、停牌不成交。"""

    def test_limit_up_bar_is_not_buyable(self):
        closes = [10.0, 11.0]  # +10% 涨停
        df = _bars(closes, highs=[10.1, 11.0], lows=[9.9, 11.0])
        buy, _ = bt.tradable_flags(df, "600000")
        assert buy[1] is np.False_ or not buy[1]

    def test_limit_down_bar_is_not_sellable(self):
        closes = [10.0, 9.0]  # -10% 跌停
        df = _bars(closes, highs=[10.1, 9.0], lows=[9.9, 9.0])
        _, sell = bt.tradable_flags(df, "600000")
        assert not sell[1]

    def test_halted_bar_is_neither(self):
        df = _bars([10.0, 10.05], vols=[1e6, 0])
        buy, sell = bt.tradable_flags(df, "600000")
        assert not buy[1] and not sell[1]

    def test_normal_bar_is_both(self):
        df = _bars([10.0, 10.2])
        buy, sell = bt.tradable_flags(df, "600000")
        assert buy[1] and sell[1]

    def test_chinext_uses_twenty_percent_limit(self):
        """创业板 20% 才算涨停,10% 仍可买。"""
        df = _bars([10.0, 11.0])
        buy_main, _ = bt.tradable_flags(df, "600000")  # 主板:+10% 已涨停
        buy_gem, _ = bt.tradable_flags(df, "300750")  # 创业板:+10% 未涨停
        assert not buy_main[1] and buy_gem[1]

    def test_bj_uses_thirty_percent_limit(self):
        df = _bars([10.0, 12.0])  # +20%
        buy_gem, _ = bt.tradable_flags(df, "300750")  # 创业板 20% → 涨停
        buy_bj, _ = bt.tradable_flags(df, "920819")  # 北交所 30% → 未涨停
        assert not buy_gem[1] and buy_bj[1]

    def test_limit_pct_by_prefix(self):
        assert bt._limit_pct("600000") == 10.0
        assert bt._limit_pct("000001") == 10.0
        assert bt._limit_pct("300750") == 20.0
        assert bt._limit_pct("688111") == 20.0
        assert bt._limit_pct("920819") == 30.0

    def test_stop_on_limit_down_day_defers_fill(self):
        """止损触发日跌停 → 不得在该日成交,顺延到下一可卖日。"""
        # 第3根跌停且击穿止损,第4根正常
        closes = [10.0, 10.0, 9.0, 8.5]
        df = _bars(closes, highs=[10.1, 10.1, 9.0, 8.7], lows=[9.9, 9.9, 9.0, 8.4])
        bbi = pd.Series([float("nan")] * len(closes))
        _, sell = bt.tradable_flags(df, "600000")
        tr = bt.simulate_b1_trade(
            df, 1, bbi, stop_mode="pct", stop_pct=5.0, can_sell=sell, max_exit_delay=5
        )
        assert tr["reason"].endswith("_delayed"), f"应顺延成交, got {tr['reason']}"
        assert tr["exit_idx"] == 3

    def test_stop_fills_normally_when_sellable(self):
        closes = [10.0, 10.0, 9.4, 9.3]
        df = _bars(closes, highs=[10.1, 10.1, 9.6, 9.5], lows=[9.9, 9.9, 9.3, 9.2])
        bbi = pd.Series([float("nan")] * len(closes))
        _, sell = bt.tradable_flags(df, "600000")
        tr = bt.simulate_b1_trade(
            df, 1, bbi, stop_mode="pct", stop_pct=5.0, can_sell=sell, max_exit_delay=5
        )
        assert tr["reason"] == "stop"

    def test_permanently_unsellable_is_marked(self):
        """连续跌停卖不掉:必须标 unfillable,不能假装按止损价出掉了。"""
        closes = [10.0, 10.0] + [10.0 * (0.9**k) for k in range(1, 8)]
        highs = [c for c in closes]
        lows = [c for c in closes]  # 全一字跌停
        df = _bars(closes, highs=highs, lows=lows)
        bbi = pd.Series([float("nan")] * len(closes))
        _, sell = bt.tradable_flags(df, "600000")
        tr = bt.simulate_b1_trade(
            df, 1, bbi, stop_mode="pct", stop_pct=5.0, can_sell=sell, max_exit_delay=3
        )
        assert tr["reason"].endswith("_unfillable")

    def test_tradability_can_be_disabled_for_comparison(self):
        """开关存在,便于量化护栏带来的差异;但默认必须是开。"""
        import inspect

        sig = inspect.signature(bt.evaluate_trades)
        assert sig.parameters["tradability"].default is True


class TestSplitConsistencyByDate:
    """E1: 半程一致性必须按日期切,且各半程独立定阈值。"""

    def test_cache_path_keyed_by_parameters(self):
        from custos.research import analyze_winner_features as awf

        p1 = awf._cache_path()
        orig = awf.FWD
        try:
            awf.FWD = orig + 5
            p2 = awf._cache_path()
        finally:
            awf.FWD = orig
        assert p1 != p2, "换 FWD 必须换缓存文件,否则静默复用旧样本"

    def test_split_uses_date_not_insertion_order(self):
        """留证:rows 是按股票 append 的,按位置切分得到的是股票集合而非时间段。"""
        rows = [
            {"date": "2026-06-01", "y": 0.1},
            {"date": "2026-01-05", "y": 0.2},
            {"date": "2026-06-02", "y": 0.3},
            {"date": "2026-01-06", "y": 0.4},
        ]
        by_position = rows[:2]
        assert {r["date"][:7] for r in by_position} == {"2026-06", "2026-01"}, (
            "按位置切分会把两个时间段混在同一半程"
        )
        rows.sort(key=lambda x: x["date"])
        dates = sorted({r["date"] for r in rows})
        split = dates[len(dates) // 2]
        first = [r for r in rows if r["date"] < split]
        assert {r["date"][:7] for r in first} == {"2026-01"}, (
            "按日期切分才是真正的时间前后段"
        )

    def test_source_sorts_rows_by_date(self):
        import inspect
        from custos.research import analyze_winner_features as awf

        src = inspect.getsource(awf.main)
        assert 'rows.sort(key=lambda x: x["date"])' in src
        assert "win_half" in src, "半程须用各自独立的标签阈值"
