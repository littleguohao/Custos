# -*- coding: utf-8 -*-
"""amv_formula_check 钉测：SMA 递归语义 / REF-MA 无未来函数 / 对齐 / 比对数学。"""

from __future__ import annotations

import numpy as np
import pytest

from custos.research import amv_formula_check as af


class TestSmaTdx:
    def test_recursive_semantics(self):
        """Y = (X×M + Y′×(N−M))/N，Y₀=X₀——TDX 口径（不是简单均线）。"""
        x = np.array([10.0, 20.0, 30.0, 40.0])
        y = af.sma_tdx(x, 10, 1)
        assert y[0] == 10.0
        assert y[1] == pytest.approx((20 + 9 * 10.0) / 10)  # 11.0
        assert y[2] == pytest.approx((30 + 9 * 11.0) / 10)  # 12.9
        assert y[3] == pytest.approx((40 + 9 * 12.9) / 10)

    def test_not_simple_ma(self):
        """与 10 日简单均线不同（语义区分钉住，防误用 rolling mean）。"""
        x = np.arange(1.0, 30.0)
        y = af.sma_tdx(x, 10, 1)
        ma10 = np.convolve(x, np.ones(10) / 10, mode="full")[9:29]
        assert not np.allclose(y[9:29], ma10)

    def test_prefix_sensitive_documented(self):
        """递归起点敏感（口径事实，钉住防有人改成窗口调用）。"""
        x = np.arange(1.0, 30.0)
        full = af.sma_tdx(x, 10, 1)
        tail = af.sma_tdx(x[10:], 10, 1)
        assert not np.allclose(full[10:], tail)  # 前缀起点不同 ⇒ 轨迹不同


class TestFormulaNoLookahead:
    def test_ma_ref_uses_only_past(self):
        """MA(REF(CLOSE,1),5)：T 日的分母只用 close[T-5..T-1]（shift(1) 后 rolling）。"""
        close = np.arange(1.0, 12.0)  # close[i] = i+1
        amount = np.full(11, 1e7)  # SMA 恒 1e7 ⇒ 公式值 = momentum × 0.835
        f = af.formula_series(amount, close)
        # i=5：分母 = mean(close[0..4]) = mean(1..5) = 3 ⇒ f = 6/3×0.835
        assert f[5] == pytest.approx(6.0 / 3.0 * 0.835, rel=1e-9)
        # i=6：分母 = mean(close[1..5]) = mean(2..6) = 4 ⇒ f = 7/4×0.835
        assert f[6] == pytest.approx(7.0 / 4.0 * 0.835, rel=1e-9)
        # 前 5 根分母数据不足（shift 后不足 5 个有效值）⇒ NaN
        assert np.isnan(f[:5]).all()
        # close=None ⇒ 动量项 = 1
        f1 = af.formula_series(amount, None)
        assert f1[10] == pytest.approx(0.835)


class TestCompareSeries:
    def test_perfect_match(self):
        """公式=真值 ⇒ 相关 1、残差 0、隐含系数=SCALE。"""
        n = 50
        dates = np.array([f"2026-01-{i % 28 + 1:02d}" for i in range(n)])
        amount = 1e12 + np.arange(n) * 1e9
        truth = af.formula_series(amount, None)  # 纯量能项当真值
        blk = af.compare_series(dates, af.formula_series(amount, None), truth)
        a = blk["all"]
        assert a["level_corr"] == pytest.approx(1.0)
        assert a["resid_median_pct"] == pytest.approx(0.0)
        assert a["implied_scale"] == pytest.approx(af.SCALE)

    def test_warmup_skipped(self):
        """前 WARMUP_BARS 根不参评（SMA 递归起点敏感段）。"""
        n = 60
        dates = np.array([f"2026-01-{i % 28 + 1:02d}" for i in range(n)])
        truth = np.arange(1.0, n + 1)
        formula = truth.copy()
        formula[: af.WARMUP_BARS] = 9999.0  # 预热段严重失真——跳过 ⇒ 不影响指标
        blk = af.compare_series(dates, formula, truth)
        assert blk["all"]["n"] == n - af.WARMUP_BARS
        assert blk["all"]["resid_median_pct"] == pytest.approx(0.0)

    def test_era_split(self):
        """分时段：区间内样本 <30 ⇒ None（如实不评）；全体不足 30 也 None。"""
        n = 40
        dates = np.array(["1995-06-01"] * 20 + ["2020-06-01"] * 20)
        v = np.arange(1.0, n + 1)
        blk = af.compare_series(dates, v, v)
        assert blk["1993-2006"] is None  # 20−15（预热落在该段）< 30
        assert blk["2016+"] is None  # 20 < 30
        assert blk["all"] is None  # 40−15=25 < 30 ⇒ 如实不评

    def test_nan_alignment_dropped(self):
        """CLOSE 变体缺日期（NaN 对齐）⇒ 该日剔除，不硬算。"""
        n = 60
        dates = np.array([f"2026-01-{i % 28 + 1:02d}" for i in range(n)])
        truth = np.arange(1.0, n + 1)
        formula = truth.copy()
        formula[25] = np.nan  # 变体该日缺数据
        blk = af.compare_series(dates, formula, truth)
        assert blk["all"]["n"] == n - af.WARMUP_BARS - 1
