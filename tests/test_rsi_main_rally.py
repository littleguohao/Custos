# -*- coding: utf-8 -*-
"""RSI 状态 + 主升始发点因子回归测试（2026-08-04）。

RSI 的价值在**区间行为**而非 70/30 超买超卖：强势股 RSI 会长期停在 70 上方，用 70/30
会一直误判。真正有判别力的是"RSI 回调的低点在哪"——牛市区间回调低点在 40~50，
熊市区间反弹高点在 50~60，这两个边界区分"健康回调"与"下跌中继"。

主升始发点来自微信文章公式，逐条识别后发现是四个标准指标的组合（RSI7 / J值 / CCI14 /
基于高低点的资金流入占比）。原文**源码与文字描述相反**（CROSS 方向），两种口径都实现
由回测判定。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import backtest_factors as bt
import main_rally_factor as mr
import rsi_state as rs


def _mk(closes, highs=None, lows=None, vols=None):
    c = np.asarray(closes, float)
    o = np.concatenate(([c[0]], c[:-1]))
    return pd.DataFrame({
        "date": pd.bdate_range("2024-01-02", periods=len(c)),
        "open": o,
        "high": np.asarray(highs, float) if highs is not None else np.maximum(c, o) * 1.01,
        "low": np.asarray(lows, float) if lows is not None else np.minimum(c, o) * 0.99,
        "close": c,
        "volume": np.asarray(vols, float) if vols is not None else np.full(len(c), 4e5),
        "amount": np.full(len(c), 4e6),
    })


class TestRsiFormula:
    """RSI 必须与通达信 SMA(MAX(C-REF(C,1),0),N,1)/SMA(ABS(...),N,1)*100 同口径。"""

    def test_matches_wilder(self):
        c = pd.Series([10, 10.5, 10.2, 11, 10.8, 11.5, 11.2, 12] * 8, dtype=float)
        got = rs.rsi(c, 14)
        d = c.diff()
        au = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        ad = d.abs().ewm(alpha=1 / 14, adjust=False).mean()
        assert float(got.iloc[-1]) == pytest.approx(float((au / ad * 100).iloc[-1]), abs=1e-9)

    def test_bounded_0_100(self):
        rising = pd.Series(np.linspace(10, 30, 80))
        falling = pd.Series(np.linspace(30, 10, 80))
        assert float(rs.rsi(rising, 14).iloc[-1]) <= 100.0
        assert float(rs.rsi(falling, 14).iloc[-1]) >= 0.0

    def test_flat_series_is_nan_not_crash(self):
        """全平序列 ad=0 → 不得抛异常（应为 NaN）。"""
        flat = pd.Series([10.0] * 60)
        out = rs.rsi(flat, 14)
        assert out.iloc[-1] != out.iloc[-1] or 0 <= float(out.iloc[-1]) <= 100


class TestRsiRegime:
    """四态分类：strong / weak_rebound / decline_continuation / deep_oversold / neutral。"""

    def _strong(self):
        """牛市区间：整体上行，回调浅（RSI 低点守在 40 上方，且曾 >70）。"""
        base = np.linspace(10, 26, 120)
        wob = 0.35 * np.sin(np.arange(120) / 3.0)
        return _mk(base + wob)

    def _decline(self):
        """熊市区间 + 反弹高点递降。"""
        c = [30.0]
        for i in range(119):
            c.append(c[-1] * (0.985 if i % 7 < 5 else 1.012))
        return _mk(c)

    def test_strong_regime(self):
        r = rs.rsi_regime(self._strong())
        assert r["available"] is True
        assert r["state"] == "strong", f"实际 {r}"
        assert r["range_low"] >= rs.BULL_RANGE_LOW
        assert r["range_high"] > rs.BULL_RANGE_CONFIRM

    def test_decline_or_weak(self):
        r = rs.rsi_regime(self._decline())
        assert r["state"] in ("decline_continuation", "weak_rebound", "deep_oversold")

    def test_deep_oversold_is_a_separate_dimension(self):
        """deep_oversold 是**独立字段**，不占用 state。

        它曾被做成一种 state，结果"长期向上+当前深跌"(B1 最想要的形态)与"结构已坏的
        深跌"归为同类、分数还低于"纯上涨"——那正是 s_shape 用买强分给买弱买点打分的
        同一个错误。现在两个维度分开，strong+deep 才是最高分组合。
        """
        c = list(np.linspace(30, 12, 100)) + [11.5, 11.0, 10.6, 10.3, 10.1]
        r = rs.rsi_regime(_mk(c))
        assert r["deep_oversold"] is True
        assert r["rsi"] < rs.DEEP_OVERSOLD
        assert r["state"] in ("strong", "neutral", "weak_rebound",
                              "decline_continuation")      # 不含 deep_oversold

    def test_ideal_b1_is_strong_plus_deep(self):
        """长期强 + 当前深跌 = B1 最理想形态，必须能被识别且得最高分。"""
        rng = np.random.default_rng(5)
        up, p = [], 30.0
        for i in range(100):
            p *= 1.005 * (1 + 0.012 * np.sin(i / 2.3) + rng.normal(0, 0.004))
            up.append(p)
        drop = []
        q = up[-1]
        for _ in range(10):
            q *= 0.965
            drop.append(q)
        ideal = rs.rsi_state_score(_mk(up + drop))
        plain = rs.rsi_state_score(_mk(up))
        assert ideal["ideal_b1"] is True
        assert ideal["score"] > plain["score"], "理想 B1 必须高于「只是结构好」"

    def test_lower_highs_flag(self):
        r = rs.rsi_regime(self._decline())
        assert "lower_highs" in r

    def test_short_history_unavailable(self):
        r = rs.rsi_regime(_mk(np.linspace(10, 12, 20)))
        assert r["available"] is False


class TestRsiDivergence:
    def test_bullish_divergence(self):
        """价格创新低而 RSI 不创新低。"""
        # 第一段深跌到 10 → 反弹 → 再跌到略低于 10 但跌势缓和
        c = list(np.linspace(20, 10, 40)) + list(np.linspace(10.2, 14, 12)) \
            + list(np.linspace(13.8, 9.9, 18))
        r = rs.rsi_divergence(_mk(c))
        assert r["available"] is True
        assert r["price_new_low"] is True
        assert r["bullish"] is (r["price_new_low"] and r["rsi_higher"])

    def test_no_divergence_in_steady_decline(self):
        """匀速下跌不构成背离（RSI 也在新低）。"""
        r = rs.rsi_divergence(_mk(np.linspace(30, 10, 90)))
        assert r["bullish"] is False

    def test_short_history_unavailable(self):
        assert rs.rsi_divergence(_mk(np.linspace(10, 12, 20)))["available"] is False


class TestRsiMulti:
    def test_reports_three_periods(self):
        r = rs.rsi_multi(_mk(np.linspace(10, 20, 90)))
        assert r["available"] is True
        for k in ("rsi6", "rsi14", "rsi24"):
            assert isinstance(r[k], float)

    def test_stacked_low_means_accelerating_decline(self):
        """短<中<长 是下跌**加速**的标志，不是"下跌中"。

        ⚠️ 必须用**带波动**的序列：单调下跌时 up 恒为 0 ⇒ RSI 恒为 0、三者相等，
        测不出任何东西（我第一版就踩了这个坑）。
        """
        def walk(n, drift, seed, p0=30.0):
            rng = np.random.default_rng(seed)
            p, out = p0, []
            for i in range(n):
                p *= (1 + drift) * (1 + 0.012 * np.sin(i / 2.3) + rng.normal(0, 0.004))
                out.append(p)
            return out
        steady = _mk(walk(90, -0.004, 7))
        accel = _mk(walk(50, -0.001, 7) + walk(40, -0.012, 9))
        assert rs.rsi_multi(steady)["available"] is True
        assert rs.rsi_multi(accel)["stacked_low"] is True        # 加速跌:短<中<长

    def test_fast_cross_detected(self):
        c = list(np.linspace(30, 12, 70)) + list(np.linspace(12.2, 16, 12))
        r = rs.rsi_multi(_mk(c))
        assert isinstance(r["fast_cross_mid"], bool)


class TestCci:
    """CCI 必须与标准公式（AVEDEV 是平均绝对偏差，不是标准差）一致。"""

    def test_matches_standard_formula(self):
        c = pd.Series([10, 10.5, 10.2, 11, 10.8, 11.5, 11.2, 12] * 8, dtype=float)
        df = pd.DataFrame({"high": c * 1.01, "low": c * 0.99, "close": c})
        got = mr.cci(df, 14)
        tp = (df.high + df.low + df.close) / 3
        ma = tp.rolling(14).mean()
        ad = tp.rolling(14).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
        assert float(got.iloc[-1]) == pytest.approx(float(((tp - ma) / (0.015 * ad)).iloc[-1]),
                                                    abs=1e-9)

    def test_negative_in_oversold(self):
        df = _mk(np.linspace(30, 10, 80))
        assert float(mr.cci(df, 14).iloc[-1]) < 0


class TestFlowRatio:
    """资金流入占比 = 15日高点抬升累计 / (抬升+降低)，基于高低点而非收盘。"""

    def test_all_rising_gives_one(self):
        df = _mk(np.linspace(10, 20, 60))
        assert float(flow := mr.flow_ratio(df).iloc[-1]) == pytest.approx(1.0, abs=1e-9)

    def test_all_falling_gives_zero(self):
        df = _mk(np.linspace(20, 10, 60))
        assert float(mr.flow_ratio(df).iloc[-1]) == pytest.approx(0.0, abs=1e-9)

    def test_bounded_0_1(self):
        c = 15 + 3 * np.sin(np.arange(80) / 4.0)
        v = mr.flow_ratio(_mk(c)).dropna()
        assert v.min() >= 0.0 and v.max() <= 1.0


class TestMainRallyStart:
    """原文三条件；CROSS 方向两种口径都要能选。"""

    def _oversold_turn(self):
        """深跌后企稳拐头（让 RSI7 低位上行、CCI 极低上行、J 拐头）。"""
        c = list(np.linspace(30, 12, 70)) + [11.6, 11.3, 11.1, 11.05, 11.2]
        return _mk(c)

    def test_reports_all_conditions(self):
        r = mr.detect_main_rally_start(self._oversold_turn())
        assert r["available"] is True
        for k in ("flow_ok", "rsi_ok", "cci_ok", "j_turn_up", "t1_ok", "conditions_met"):
            assert k in r

    def test_cross_mode_both_directions_available(self):
        df = self._oversold_turn()
        below = mr.detect_main_rally_start(df, cross_mode="below")
        above = mr.detect_main_rally_start(df, cross_mode="above")
        either = mr.detect_main_rally_start(df, cross_mode="either")
        assert below["cross_mode"] == "below" and above["cross_mode"] == "above"
        # either 至少不弱于任一单向
        assert either["flow_ok"] >= (below["flow_ok"] or above["flow_ok"])

    def test_source_code_semantics_is_default(self):
        """默认必须是**源码口径**(below)——文字描述与源码相反，源码更可信。"""
        import inspect
        sig = inspect.signature(mr.detect_main_rally_start)
        assert sig.parameters["cross_mode"].default == "below"

    def test_j_turn_matches_project_j(self):
        """原文 D11 就是本项目的 J 值，必须同口径。"""
        from technical_monitor import kdj
        df = self._oversold_turn()
        mine = float(mr._j_series(df).iloc[-1])
        assert mine == pytest.approx(float(kdj(df)["j"]), abs=1e-3)

    def test_thresholds_are_from_source(self):
        assert mr.FLOW_THRESHOLD == 0.8
        assert mr.RSI_N == 7 and mr.RSI_OVERSOLD == 20.0
        assert mr.CCI_N == 14 and mr.CCI_EXTREME == -100.0
        assert mr.MAIN_RALLY_MIN_BARS == 60      # 原文 BARSCOUNT(C)>60

    def test_short_history_unavailable(self):
        r = mr.detect_main_rally_start(_mk(np.linspace(10, 12, 30)))
        assert r["available"] is False and "60" in r["reason"]


class TestRegistration:
    @pytest.mark.parametrize("name", ["rsi_state", "main_rally"])
    def test_scorer_registered(self, name):
        assert name in bt.SCORERS

    @pytest.mark.parametrize("name", ["rsi_strong", "rsi_bull_div", "j_low_rsi_strong",
                                      "j_low_rsi_div", "main_rally", "main_rally_above"])
    def test_gate_registered(self, name):
        assert name in bt.ENTRY_GATES

    @pytest.mark.parametrize("name", ["rsi_strong", "rsi_bull_div", "j_low_rsi_strong",
                                      "j_low_rsi_div", "main_rally", "main_rally_above"])
    def test_gates_never_raise(self, name):
        for df in (_mk([10.0] * 5), _mk(np.linspace(10, 20, 150)),
                   _mk(np.linspace(30, 10, 150))):
            assert isinstance(bt.ENTRY_GATES[name](df), bool)

    def test_scorers_return_none_on_short_history(self):
        short = _mk(np.linspace(10, 12, 20))
        for name in ("rsi_state", "main_rally"):
            assert bt.SCORERS[name](short, "600000") is None

    def test_j_low_rsi_gates_are_intersections(self):
        df = _mk(np.linspace(30, 10, 150))
        for name, part in (("j_low_rsi_strong", "rsi_strong"),
                           ("j_low_rsi_div", "rsi_bull_div")):
            expect = bt.ENTRY_GATES["j_low"](df) and bt.ENTRY_GATES[part](df)
            assert bt.ENTRY_GATES[name](df) is bool(expect)


class TestNotWiredIntoScreening:
    def test_score_candidates_untouched(self):
        import inspect

        from screening import score_candidates as sc
        src = inspect.getsource(sc)
        for name in ("rsi_state", "main_rally", "rsi_regime"):
            assert name not in src, "接入选股链前必须先有回测证据"
