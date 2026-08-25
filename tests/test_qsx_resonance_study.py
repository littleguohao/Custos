# -*- coding: utf-8 -*-
"""qsx_resonance_study 钉测：共振检测器（合成 K 线：有共振/无共振/贴线假摔/
站回时序因果/双线去重）+ QSX 跌破清仓出场（引擎新通道）+ 三臂 gate + 统计函数。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from custos.core import indicators as ind
from custos.research import backtest_factors as bf
from custos.research import qsx_resonance_study as qrs


# ---------------------------------------------------------------------------
# 合成数据工具
# ---------------------------------------------------------------------------


def _mk_df(closes, opens=None, lows=None, highs=None) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n),
            "open": opens if opens is not None else list(closes),
            "high": highs if highs is not None else [c * 1.01 for c in closes],
            "low": lows if lows is not None else [c * 0.99 for c in closes],
            "close": list(closes),
            "volume": [1e6] * n,
            "amount": [1e9] * n,
        }
    )


def _flat(n: int, v: float = 10.0) -> np.ndarray:
    return np.full(n, v, dtype=float)


# ---------------------------------------------------------------------------
# 共振检测器
# ---------------------------------------------------------------------------


class TestResonance:
    """线恒 10；tol=1% ⇒ 贴线带 [9.9, 10.1]；reclaim_bars=3；min_events=2。"""

    KW = {"lookback": 30, "tol": 0.01, "reclaim_bars": 3, "min_events": 2}

    def _series(self, close, low, line=None):
        n = len(close)
        line = _flat(n) if line is None else np.asarray(line, dtype=float)
        s = lambda a: pd.Series(np.asarray(a, dtype=float))  # noqa: E731
        return qrs.qsx_dks_resonance(s(close), s(low), s(line), s(line), **self.KW)

    def test_no_touch_no_resonance(self):
        """无共振：股价全程远高于线，从不贴线 ⇒ 全 False。"""
        close = [11.0] * 40
        low = [10.8] * 40
        res = self._series(close, low)
        assert not res.any()

    def test_two_bounces_resonance(self):
        """有共振：两次「贴线→当日收回线上」⇒ 第二次 confirm 起为 True。"""
        n = 40
        close = [11.0] * n
        low = [10.8] * n
        # 第一次回踩：bar 5 贴线、当日收 10.5（>线，confirm=5）
        low[5] = 10.02
        close[5] = 10.5
        # 第二次回踩：bar 12 贴线、当日收 10.4（confirm=12）
        low[12] = 10.05
        close[12] = 10.4
        res = self._series(close, low)
        assert not res[:12].any()  # 第二次 confirm 之前最多 1 次事件 ⇒ False
        assert res[12]  # 第二次 confirm 当根起 True
        assert res[12 + 5]  # 窗口内持续 True

    def test_touch_fakeout_not_counted(self):
        """贴线假摔：贴线后收盘破线且窗口内站不回 ⇒ 不算事件（仅 1 次有效 ⇒ False）。"""
        n = 40
        close = [11.0] * n
        low = [10.8] * n
        # 有效事件：bar 5 贴线收回
        low[5] = 10.02
        close[5] = 10.5
        # 假摔：bar 12 贴线但收 9.5 破线，之后 9.4/9.3 一路走低，3 根内站不回
        low[12] = 10.0
        close[12] = 9.5
        close[13] = 9.4
        close[14] = 9.3
        close[15] = 9.2
        low[13] = 9.3
        low[14] = 9.2
        low[15] = 9.1
        res = self._series(close, low)
        assert not res.any()  # 只有 1 次有效事件 < min_events=2

    def test_delayed_reclaim_causal(self):
        """站回滞后：贴线当日收破线、第 2 根才站回 ⇒ confirm 之前不可见（as-of 因果）。"""
        n = 40
        close = [11.0] * n
        low = [10.8] * n
        # 事件 1：bar 5 confirm
        low[5] = 10.02
        close[5] = 10.5
        # 事件 2：bar 12 贴线收破（9.8），bar 13 仍线下（9.85），bar 14 站回（10.3）
        low[12] = 10.0
        close[12] = 9.8
        low[13] = 9.7
        close[13] = 9.85
        close[14] = 10.3
        low[14] = 10.1
        res = self._series(close, low)
        assert not res[13]  # 事件 2 尚未确认 ⇒ 仍只有 1 次
        assert res[14]  # confirm=14 起第二次可见 ⇒ True

    def test_same_dip_two_lines_dedup(self):
        """双线去重：QSX 与 DKS 同值时同一次下跌只在一条线上各计一次——
        dedup 后 2 次（不是 4 次）⇒ min_events=3 不成立、=2 成立。"""
        n = 40
        close = [11.0] * n
        low = [10.8] * n
        low[5] = 10.02
        close[5] = 10.5
        low[12] = 10.05
        close[12] = 10.4
        s = lambda a: pd.Series(np.asarray(a, dtype=float))  # noqa: E731
        line = s(_flat(n))
        res3 = qrs.qsx_dks_resonance(
            s(close),
            s(low),
            line,
            line,
            lookback=30,
            tol=0.01,
            reclaim_bars=3,
            min_events=3,
        )
        assert not res3.any()  # 若不去重会有 4 次 ⇒ ≥3 成立；去重后只有 2 次
        res2 = qrs.qsx_dks_resonance(
            s(close),
            s(low),
            line,
            line,
            lookback=30,
            tol=0.01,
            reclaim_bars=3,
            min_events=2,
        )
        assert res2[12]

    def test_below_line_hug_not_counted(self):
        """线下贴线不算回踩：段前收盘已在线下（不是从上往下）⇒ 不计事件。"""
        n = 30
        # 前 5 根在线上，第 5 根破线，之后贴着线下方走（low 贴线但 close 一直在线下）
        close = [11.0] * 5 + [9.6] * (n - 5)
        low = [10.8] * 5 + [9.95] * (n - 5)  # 9.95 在 ±1% 带内
        res = self._series(close, low)
        assert not res.any()

    def test_window_expiry(self):
        """事件滑出 lookback 窗后不再计入。"""
        n = 80
        close = [11.0] * n
        low = [10.8] * n
        low[5] = 10.02
        close[5] = 10.5
        low[12] = 10.05
        close[12] = 10.4
        res = self._series(close, low)
        assert res[12]
        # 事件 1 可见窗 [5, 5+30-1=34]，事件 2 [12, 41]；两事件同见 ⇒ [12,34]。
        # 35 起事件 1 出窗只剩事件 2（<min_events=2）⇒ False。
        assert res[34]
        assert not res[35]


# ---------------------------------------------------------------------------
# QSX 跌破清仓（simulate_b1_trade 新通道）
# ---------------------------------------------------------------------------


class TestQsxExit:
    def test_qsx_exit_next_open(self):
        """收盘 < QSX ⇒ 次日**开盘**清仓（reason=qsx_exit，exit_idx=j+1）。"""
        closes = [10.0] * 6 + [11.0, 10.5, 9.0, 9.1, 9.2]
        opens = list(closes)
        opens[9] = 8.5  # 次日低开：证明按 open[j+1] 而非 close[j] 成交
        df = _mk_df(closes, opens=opens)
        bbi = pd.Series(_flat(len(closes), 5.0))  # BBI 远低于价，不干扰
        qsx = pd.Series(_flat(len(closes), 9.5))
        tr = bf.simulate_b1_trade(
            df,
            5,
            bbi,
            bbi_exit_consec=0,
            stop_mode="pct",
            stop_pct=50,
            qsx=qsx,
            qsx_exit_consec=1,
        )
        assert tr["reason"] == "qsx_exit"
        assert tr["exit_idx"] == 9  # 触发根 j=8（close 9.0<9.5）的次日
        assert tr["ret"] == pytest.approx(8.5 / 10.0 - 1, abs=1e-9)

    def test_qsx_exit_last_bar_settles_close(self):
        """触发根已是最后一根 ⇒ 按最后收盘结算（无次日）。"""
        closes = [10.0] * 6 + [11.0, 10.5, 9.0]
        df = _mk_df(closes)
        bbi = pd.Series(_flat(len(closes), 5.0))
        qsx = pd.Series(_flat(len(closes), 9.5))
        tr = bf.simulate_b1_trade(
            df,
            5,
            bbi,
            bbi_exit_consec=0,
            stop_mode="pct",
            stop_pct=50,
            qsx=qsx,
            qsx_exit_consec=1,
        )
        assert tr["reason"] == "qsx_exit"
        assert tr["exit_idx"] == len(closes) - 1
        assert tr["ret"] == pytest.approx(9.0 / 10.0 - 1, abs=1e-9)

    def test_qsx_exit_consec2(self):
        """consec=2：单日破线不触发，连破 2 根才在第二根的次日开盘清。"""
        closes = [10.0] * 6 + [11.0, 9.0, 10.0, 9.2, 9.1, 9.3]
        df = _mk_df(closes)
        bbi = pd.Series(_flat(len(closes), 5.0))
        qsx = pd.Series(_flat(len(closes), 9.5))
        tr = bf.simulate_b1_trade(
            df,
            5,
            bbi,
            bbi_exit_consec=0,
            stop_mode="pct",
            stop_pct=50,
            qsx=qsx,
            qsx_exit_consec=2,
        )
        # j=7(9.0<9.5) 单日破、j=8(10.0) 收复 ⇒ 不触发；j=9(9.2)、j=10(9.1) 连破 ⇒ 次日 11 开盘清
        assert tr["reason"] == "qsx_exit"
        assert tr["exit_idx"] == 11

    def test_qsx_nan_guard(self):
        """QSX NaN 根不触发（NaN 守卫），恢复有效后正常判定。"""
        closes = [10.0] * 6 + [11.0, 9.0, 9.0, 9.2]
        df = _mk_df(closes)
        bbi = pd.Series(_flat(len(closes), 5.0))
        qsx_v = _flat(len(closes), 9.5)
        qsx_v[7] = np.nan  # 第 7 根线值缺失 ⇒ 该根不判
        tr = bf.simulate_b1_trade(
            df,
            5,
            bbi,
            bbi_exit_consec=0,
            stop_mode="pct",
            stop_pct=50,
            qsx=pd.Series(qsx_v),
            qsx_exit_consec=1,
        )
        assert tr["reason"] == "qsx_exit"
        assert tr["exit_idx"] == 9  # 第 8 根才触发（7 被 NaN 跳过）

    def test_bbi_exit_disabled_by_zero_consec(self):
        """bbi_exit_consec=0 ⇒ BBI 连破清仓整体关闭（本轮 owner 口径）。"""
        closes = [10.0, 11.0, 11.0, 9.8, 9.7, 9.6, 9.5]
        df = _mk_df(closes)
        bbi = pd.Series(_flat(len(closes), 10.0))
        on = bf.simulate_b1_trade(
            df, 0, bbi, bbi_exit_consec=2, stop_mode="pct", stop_pct=50
        )
        assert on["reason"] == "bbi_exit"  # 默认通道仍在（回归钉）
        off = bf.simulate_b1_trade(
            df, 0, bbi, bbi_exit_consec=0, stop_mode="pct", stop_pct=50
        )
        assert off["reason"] == "open_end"  # 0 ⇒ 关闭，不再 bbi_exit

    def test_stop_priority_over_qsx(self):
        """同根既破止损又破 QSX ⇒ 止损先判（优先级 ①>②c）。"""
        closes = [10.0, 8.0, 8.1, 8.2]
        df = _mk_df(closes)
        bbi = pd.Series(_flat(len(closes), 5.0))
        qsx = pd.Series(_flat(len(closes), 9.5))
        tr = bf.simulate_b1_trade(
            df,
            0,
            bbi,
            bbi_exit_consec=0,
            stop_mode="pct",
            stop_pct=12,
            qsx=qsx,
            qsx_exit_consec=1,
        )
        assert tr["reason"] != "qsx_exit"
        assert "stop" in tr["reason"]

    def test_evaluate_trades_pass_through(self):
        """evaluate_trades 透传：qsx_exit_consec=1 + bbi_exit_consec=0 ⇒ 出 qsx_exit 记录。"""
        n = 40
        closes = [10.0 + 0.1 * i for i in range(30)] + [12.0, 11.0, 10.0] + [9.5] * 7
        closes = closes[:n]
        df = _mk_df(closes)
        qsx = ind.qsx_series(df["close"])
        entry_i = 25
        gate = lambda s, p=None: len(s) - 1 == entry_i  # noqa: E731
        trades = bf.evaluate_trades(
            {"600000": df},
            scorer=bf.SCORERS["baseline"],
            entry_gate=gate,
            tradability=False,
            min_bars=5,
            bbi_exit_consec=0,
            stop_mode="pct",
            stop_pct=50,
            qsx_exit_consec=1,
        )
        assert len(trades) == 1
        rec = trades[0]
        assert rec["reason"] == "qsx_exit"
        # 与手工口径对拍：entry 后首个 close<qsx 根 j ⇒ exit 于 j+1（其日期=exit_date）
        j = next(
            k
            for k in range(entry_i + 1, n)
            if float(df["close"].iloc[k]) < float(qsx.iloc[k])
        )
        assert rec["exit_date"] == str(df["date"].iloc[j + 1])[:10]


# ---------------------------------------------------------------------------
# 三臂 gate
# ---------------------------------------------------------------------------


class TestGates:
    """j_low 用预计算通道钉住（kdj_j 恒 5<13）；切片是前缀 ⇒ i=末根下标。"""

    N = 20
    PRE = {"kdj_j": np.full(N, 5.0)}

    def _slice(self, i: int) -> pd.DataFrame:
        return _mk_df([10.0] * (i + 1))  # 长度 i+1 ⇒ 末根下标 i

    def test_arm_a_only_j_low(self):
        gate = qrs._make_gate("A", np.zeros(self.N, bool), np.zeros(self.N, bool))
        assert gate(self._slice(15), self.PRE)  # J<13 即放行
        bad_pre = {"kdj_j": np.full(self.N, 50.0)}
        assert not gate(self._slice(15), bad_pre)  # J 不低 ⇒ 拒

    def test_arm_b_requires_qsx_gt_dks(self):
        gt = np.zeros(self.N, bool)
        gt[15] = True
        gate = qrs._make_gate("B", gt, np.zeros(self.N, bool))
        assert gate(self._slice(15), self.PRE)
        assert not gate(self._slice(14), self.PRE)  # gt[14]=False ⇒ 拒

    def test_arm_c_requires_resonance(self):
        gt = np.ones(self.N, bool)
        res = np.zeros(self.N, bool)
        res[15] = True
        gate = qrs._make_gate("C", gt, res)
        assert gate(self._slice(15), self.PRE)
        assert not gate(self._slice(14), self.PRE)  # 共振不满足 ⇒ 拒


# ---------------------------------------------------------------------------
# 统计函数
# ---------------------------------------------------------------------------


def _trade(
    ret, code="600000", entry="2024-01-01", reason="qsx_exit", r=1.0, score=40.0
):
    return {
        "code": code,
        "entry_date": entry,
        "exit_date": "2024-02-01",
        "ret": ret,
        "r_multiple": r,
        "reason": reason,
        "tech_score": score,
    }


class TestStats:
    def test_wilson_ci_basics(self):
        assert qrs.wilson_ci(0, 0) == (None, None)
        lo, hi = qrs.wilson_ci(50, 100)
        assert lo == pytest.approx(0.4038, abs=1e-3)
        assert hi == pytest.approx(0.5962, abs=1e-3)
        lo2, hi2 = qrs.wilson_ci(1, 10)  # 小样本区间明显宽
        assert hi2 - lo2 > hi - lo

    def test_arm_stats(self):
        trades = [_trade(0.1, r=2.0), _trade(-0.05, r=-1.0), _trade(0.02, r=0.5)]
        st = qrs.arm_stats(trades)
        assert st["n"] == 3
        assert st["win_rate"] == pytest.approx(2 / 3, abs=1e-4)
        assert st["expectancy_R"] == pytest.approx(0.5, abs=1e-3)
        assert st["win_rate_wilson95"][0] < st["win_rate"] < st["win_rate_wilson95"][1]
        assert st["n_codes"] == 1

    def test_half_window_ret(self):
        trades = [_trade(0.1, entry=f"2024-01-0{i}") for i in range(1, 5)] + [
            _trade(-0.1, entry=f"2024-02-0{i}") for i in range(1, 5)
        ]
        hw = qrs.half_window_ret(trades)
        assert hw["consistent"] is False  # 前半正后半负 ⇒ 翻转
        assert qrs.half_window_ret(trades[:4])["skipped"]  # 样本不足如实标

    def test_compare_arms_marginal(self):
        def rep(arm, n_win, n_lose, code):
            trades = [_trade(0.1, code=code) for _ in range(n_win)] + [
                _trade(-0.1, code=code) for _ in range(n_lose)
            ]
            return qrs.build_arm_report(trades, arm)

        reps = {
            "A": rep("A", 40, 60, "a"),
            "B": rep("B", 50, 50, "b"),
            "C": rep("C", 30, 20, "c"),
        }
        cmp = qrs.compare_arms(reps)
        m1 = cmp["marginal_filter1_B_minus_A"]
        assert m1["win_rate"] == pytest.approx(0.5 - 0.4, abs=1e-4)
        m2 = cmp["marginal_filter2_C_minus_B"]
        assert m2["win_rate"] == pytest.approx(0.6 - 0.5, abs=1e-4)
        hit = cmp["resonance_hit"]
        assert hit["signal_rate_C_over_B"] == pytest.approx(50 / 100, abs=1e-4)
        assert hit["code_rate_C_over_B"] == pytest.approx(1.0, abs=1e-4)

    def test_build_arm_report_top_frac(self):
        trades = [
            _trade(0.5 - i * 0.1, entry=f"2024-01-{i + 1:02d}", score=30 + i)
            for i in range(10)
        ]
        rep = qrs.build_arm_report(trades, "C", top_frac=0.2)
        assert rep["n_trades"] == 10
        assert rep["top20_score_dist"]["n"] == 2  # ceil(10×0.2)
        assert rep["rest_score_dist"]["n"] == 8
        assert "qsx_exit" in rep["exit_reasons"]
