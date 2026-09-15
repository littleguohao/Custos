# -*- coding: utf-8 -*-
"""s_shape 家族预计算旁路钉测（v0.236，TODO #59 性能项）。

铁律：**行为逐位不变**——旁路输出必须与旧路径（逐 bar 前缀切片调
compute_s_shape/compute_s_reversal）逐位一致：

- 全位置对拍（含 warmup 段 None/NaN 位置一致），多 code 前缀（10%/20%/30%
  涨跌幅制度）× 多 seed × 有无放量大阴（penalty 腿命中面）；
- ``evaluate_trades`` 端到端 trades 逐位一致（引擎自动走旁路 vs 摘掉注册表）；
- 旁路异常自动回退旧路径（precompute 返 None / scorer 三参不 raise）；
- 性能：预热 + best-of-3，旁路 < 旧路径 1/20（写法参照 v0.212 的 perf 钉测）。
"""

import time

import numpy as np
import pandas as pd
import pytest

from custos.core.factors import s_shape as ss
from custos.research import backtest_factors as bf


def _mk(seed=11, n=400, crash=False, start="2022-01-01"):
    """随机游走 + 可选末段放量大阴（penalty 腿命中面）。"""
    rng = np.random.default_rng(seed)
    close = 20 + np.cumsum(rng.normal(0, 0.6, n))
    close = np.maximum(close, 1.0)
    df = pd.DataFrame(
        {
            "date": pd.date_range(start, periods=n),
            "open": close + rng.normal(0, 0.15, n),
            "high": close + abs(rng.normal(0.4, 0.15, n)),
            "low": close - abs(rng.normal(0.4, 0.15, n)),
            "close": close,
            "volume": abs(rng.normal(1e6, 4e5, n)),
        }
    )
    if crash:
        df.loc[n - 3, "open"] = close[-4] * 1.005  # 阴线
        df.loc[n - 3, "high"] = close[-4] * 1.005
        df.loc[n - 3, "low"] = close[-3] * 0.995
        df.loc[n - 3, "volume"] = 4e6  # 放量大阴
    return df


class TestPointQueryBitwise:
    """全位置对拍：旁路（precomputed 点查询）vs 旧路径（逐前缀切片直算）。"""

    @pytest.mark.parametrize("code", ["600000", "300750", "688981", "920001"])
    @pytest.mark.parametrize("seed", [7, 11])
    @pytest.mark.parametrize("crash", [False, True])
    def test_all_positions_bitwise(self, code, seed, crash):
        df = _mk(seed=seed, crash=crash)
        pre = ss.score_series(df)
        assert pre is not None
        for key in ("s_shape", "s_reversal", "invert_s_shape"):
            scorer = bf.SCORERS[key]
            for i in range(len(df)):
                sl = df.iloc[: i + 1]
                a = scorer(sl, code)
                b = scorer(sl, code, pre)
                assert (a is None) == (b is None), f"{key}@{i}: None 位置不一致"
                if a is None:
                    continue
                assert a == b, f"{key}@{i}: {a} != {b}"

    def test_warmup_positions_nan_consistent(self):
        """warmup（前缀 < 60 根）：两路都不可用（scorer 返回 None）。"""
        df = _mk(n=80)
        pre = ss.score_series(df)
        for i in range(59):
            sl = df.iloc[: i + 1]
            assert bf.SCORERS["s_shape"](sl, "600000") is None
            assert bf.SCORERS["s_shape"](sl, "600000", pre) is None

    def test_series_keys_and_nan_semantics(self):
        """序列载体的键与 NaN 语义（warmup NaN / 起算后有效）。"""
        df = _mk(n=100)
        pre = ss.score_series(df)
        assert set(pre) == {
            "s_shape",
            "delta",
            "legs",
            "s_rev",
            "s_rev_parts",
            "abs_chg",
            "raw",
        }
        assert set(pre["legs"]) == set(ss._SS_LEGS)
        assert np.isnan(pre["s_shape"][30])  # warmup
        assert not np.isnan(pre["s_shape"][99])  # 起算后
        assert np.isnan(pre["s_rev"][30]) and not np.isnan(pre["s_rev"][99])

    def test_empty_or_missing_kdj_returns_none(self):
        assert ss.score_series(pd.DataFrame()) is None  # 空帧 → None（回退旧路径）


def _mk_strong_shape(n=120, seed=23):
    """S**≥70 会在若干 bar 命中的强势形态（前段宽幅上行→近段收敛→末端放量突破）。

    实证（compute_s_shape）：末根 s_star=53.5 不买，但中段窗口多次 ≥70
    （collect_all 下 15 笔候选）——端到端对拍非空转。
    """
    c = [20.0]
    v = []
    band = []
    for i in range(1, n):
        amp = 0.012 if i < 95 else 0.002
        step = 0.004 + amp * (1 if i % 3 else -0.6)
        c.append(c[-1] * (1 + step))
    for i in range(n):
        band.append(0.010 if i < 95 else 0.002)
        if i < 80:
            v.append(8e5 + i * 5e3)
        elif i < 95:
            v.append(2.0e6)
        elif i < n - 2:
            v.append(8.5e5)
        else:
            v.append(2.4e6)
    c = np.array(c)
    band = np.array(band)
    return pd.DataFrame(
        {
            "date": pd.date_range("2022-01-01", periods=n),
            "open": c * (1 - band / 2),
            "high": c * (1 + band),
            "low": c * (1 - band),
            "close": c,
            "volume": np.array(v, dtype=float),
        }
    )


class TestEvaluateTradesEndToEnd:
    """端到端：引擎自动走旁路 vs 摘掉注册表（旧路径），trades 逐位一致。

    ⚠️ 非空转断言按 scorer 的 suggestion 语义分别处理：s_shape（可买=S**≥70，
    强势形态数据 15 笔）/ invert_s_shape（可买=100−S**≥70，随机走弱数据有命中）
    非空；s_reversal 的 suggestion 词汇表是「强反转候选/观察/弱」——**永远不
    等于「可买」**，trade-sim 进场过滤恒空（它的消费面在 evaluate()/画像层），
    两端恒等的断言照样钉（空=空也是逐位一致）。
    """

    @pytest.mark.parametrize(
        "key,mk",
        [
            ("s_shape", _mk_strong_shape),
            ("invert_s_shape", lambda: _mk(seed=5, n=300)),
            ("s_reversal", lambda: _mk(seed=5, n=300)),
        ],
    )
    def test_trades_bitwise(self, key, mk, monkeypatch):
        df = mk()
        kw = dict(scorer=bf.SCORERS[key], collect_all=True)
        with_pre = bf.evaluate_trades({"600000": df}, **kw)
        monkeypatch.setattr(bf, "_SCORER_PRECOMPUTE", {})  # 摘掉旁路 = 旧路径
        without_pre = bf.evaluate_trades({"600000": df}, **kw)
        assert with_pre == without_pre
        if key != "s_reversal":  # 见类 docstring：s_reversal 恒空属既定语义
            assert with_pre, f"{key} 对拍不能空转"

    def test_precompute_exception_falls_back(self, monkeypatch):
        """旁路预计算异常 → None → scorer 走旧路径，结果逐位不变。

        ⚠️ 契约口径（与 _precompute_kdj_j_series 等先例一致）：预计算函数
        **内部** try/except 返 None——``_prepare_stock`` 的调用点不捕异常
        （raise 会直接炸 evaluate_trades）。所以回退测试注入「返 None」的
        预计算，而不是「抛异常」的。
        """
        df = _mk_strong_shape()
        key = "s_shape"
        monkeypatch.setitem(bf._SCORER_PRECOMPUTE, bf.SCORERS[key], lambda _df: None)
        fell_back = bf.evaluate_trades(
            {"600000": df}, scorer=bf.SCORERS[key], collect_all=True
        )
        monkeypatch.setattr(bf, "_SCORER_PRECOMPUTE", {})
        old = bf.evaluate_trades(
            {"600000": df}, scorer=bf.SCORERS[key], collect_all=True
        )
        assert fell_back == old
        assert fell_back, "回退对拍不能空转"


class TestPerf:
    def test_bypass_faster_than_slice_path(self):
        """性能钉测：预热 + best-of-3，旁路 < 旧路径 1/20（v0.212 perf 钉测写法）。

        负载：1 股 2000 根（合成生产尺度——s3000 单股窗长）逐 bar 点查询。
        """
        df = _mk(seed=17, n=2000)
        n = len(df)

        def old_path():
            for i in range(60, n):  # 起算后才有点（与真实扫描同域）
                bf.SCORERS["s_shape"](df.iloc[: i + 1], "600000")

        pre = ss.score_series(df)

        def new_path():
            for i in range(60, n):
                bf.SCORERS["s_shape"](df.iloc[: i + 1], "600000", pre)

        old_path()  # 预热（与 v0.212 钉测同法：先跑一遍消除冷启动）
        new_path()
        t_old = min(_time(old_path) for _ in range(3))
        t_new = min(_time(new_path) for _ in range(3))
        ratio = t_new / t_old
        assert ratio < 1 / 20, (
            f"旁路提速不足：旧 {t_old:.2f}s 新 {t_new:.2f}s（{ratio:.3f}）"
        )


def _time(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0
