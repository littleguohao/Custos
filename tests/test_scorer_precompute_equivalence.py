# -*- coding: utf-8 -*-
"""TODO #59①：scorer 双形态框架 + 默认 scorer（_sc_b1_pullback）预计算旁路的**逐位等价**钉测。

背景：evaluate_trades 原来每根 bar 都把 ``df.iloc[:i+1]`` 传给 scorer，
``compute_b1_pullback_fit`` 在全前缀上重算 MA5/10/60 rolling 与 J 递归序列
（每股 O(n²)，v0.73 实测占 evaluate_trades 总时间 50%+）。rolling 窗口与 J 序列
（RSV→EWM→EWM，fill_na=50）都从第 0 根开始算 ⇒ prefix 上算出的末点与全序列
第 i 点是**同一串浮点运算**，逐位相同；尾部 ≤45 根的窗口统计与前缀长度无关。
`_precompute_b1_pullback_series` 逐股算一次，scorer 走
``(df_slice, code, precomputed=None)`` 三参双形态取对应点。

本文件钉住这条等价（防未来有人"优化"出口径漂移）：

  ① 多种数据形态 × 每个采样 bar：``compute_b1_pullback_fit(slice) ==
     compute_b1_pullback_fit(slice, precomputed)``（dict ==，含 detail 的 round 值）；
     ``_sc_b1_pullback`` 两路同样一致。
  ② evaluate_trades 的逐笔输出在「注册表正常」vs「monkeypatch 清空注册表
     （= 强制旧逐切片路径）」下**完全一致**（trades list ==）。
  ③ 防"空==空"假绿：至少一形态真的判出 hit=True、evaluate_trades 真的出交易。
"""

import numpy as np
import pandas as pd
import pytest

from custos.core.factors.b1_pullback_fit import compute_b1_pullback_fit
from custos.core.factors.rsi_state import rsi_multi, rsi_state_score
from custos.core.indicators import rsi as _rsi_impl
from custos.research import backtest_factors as bt


def _shaped_bars(kind: str, n: int, seed: int = 7) -> pd.DataFrame:
    """多种形态的确定性合成日线，覆盖 compute_b1_pullback_fit 的边界路径。"""
    rng = np.random.default_rng(seed)
    if kind == "strong_trend":  # 强趋势：单调上行 → trend_intact/prior_gain 路径
        close = 10 + np.cumsum(np.abs(rng.normal(0.12, 0.05, n)))
    elif kind == "oscillating":  # 震荡：正弦 + 噪声 → 回踩/反弹交替
        close = 10 + 1.5 * np.sin(np.arange(n) / 6.0) + rng.normal(0, 0.05, n)
    elif kind == "nan_prefix":  # 含 NaN 前缀（停牌段）→ rolling/J 的 NaN 分支
        close = np.maximum(10 + np.cumsum(rng.normal(0, 0.15, n)), 1.0)
        close[:5] = np.nan
    elif kind == "pullback_hit":  # 强趋势后缩量温和回踩 → 真的判出 hit=True
        up = 10 + np.cumsum(np.abs(rng.normal(0.115, 0.02, n * 2 // 3)))
        down = up[-1] * (
            1 - np.cumsum(np.abs(rng.normal(0.0035, 0.001, n - n * 2 // 3)))
        )
        close = np.concatenate([up, down])
        vol = np.concatenate(
            [np.full(n * 2 // 3, 2e6), np.full(n - n * 2 // 3, 5e5)]
        )  # 回踩段缩量
        high = close * 1.004
        low = close * 0.996
        open_ = close * (1 + rng.normal(0, 0.001, n))
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
    else:  # random_walk
        close = np.maximum(10 + np.cumsum(rng.normal(0, 0.15, n)), 1.0)
    high = close + np.abs(rng.normal(0, 0.08, n))
    low = close - np.abs(rng.normal(0, 0.08, n))
    open_ = low + (high - low) * rng.random(n)
    vol = np.abs(rng.normal(1e6, 2e5, n))
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


# (形态, 根数)：刚好 20 根=n<20 守卫边界；41/59/60/61 覆盖 ma60 窗口 min(60,n) 分支；
# 300=长序列（采样而非逐 bar，控制钉测耗时）。
_SHAPES = [
    ("random_walk", 120),
    ("strong_trend", 120),
    ("oscillating", 120),
    ("nan_prefix", 120),
    ("pullback_hit", 120),
    ("random_walk", 20),  # 刚好 20 根：n<20 守卫边界
    ("random_walk", 41),
    ("random_walk", 59),  # n<60：ma60 走 min(60,n) 现算分支
    ("random_walk", 60),  # n>=60 边界
    ("random_walk", 61),
    ("random_walk", 300),  # 长序列
]


def _sample_bars(n: int) -> list[int]:
    """采样 bar：短序列逐根全测；长序列每 3 根 + 关键边界（19/20/58/59/60、末尾）。"""
    if n <= 130:
        return list(range(n))
    pts = set(range(0, n, 3)) | {19, 20, 58, 59, 60, n - 2, n - 1}
    return sorted(p for p in pts if 0 <= p < n)


def _dict_eq(a, b) -> bool:
    """nan 感知的 dict ==：detail 里 round(nan,1)=nan 在**两路逐位相同**，
    但 nan != nan 会让裸 == 误判不一致。其余字段严格 ==。"""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_dict_eq(a[k], b[k]) for k in a)
    if isinstance(a, float) and isinstance(b, float) and np.isnan(a) and np.isnan(b):
        return True
    return bool(a == b)


@pytest.mark.parametrize("kind,n", _SHAPES)
def test_fit_dual_form_per_bar_equal(kind, n):
    """① 逐 bar 等价：compute_b1_pullback_fit 带/不带 precomputed，dict 完全相等。"""
    df = _shaped_bars(kind, n)
    pre = bt._precompute_b1_pullback_series(df)
    assert pre is not None, "预计算在正常数据上不该退 None"
    for i in _sample_bars(n):
        sl = df.iloc[: i + 1]
        a = compute_b1_pullback_fit(sl)
        b = compute_b1_pullback_fit(sl, pre)
        assert _dict_eq(a, b), f"{kind} n={n} 在 i={i} 两路不一致:\n{a}\n{b}"


@pytest.mark.parametrize("kind,n", _SHAPES)
def test_scorer_dual_form_per_bar_equal(kind, n):
    """①b 逐 bar 等价：_sc_b1_pullback 带/不带 precomputed，返回完全相等。"""
    df = _shaped_bars(kind, n)
    pre = bt._precompute_b1_pullback_series(df)
    assert pre is not None
    for i in _sample_bars(n):
        sl = df.iloc[: i + 1]
        assert bt._sc_b1_pullback(sl, "600000") == bt._sc_b1_pullback(
            sl, "600000", pre
        ), f"{kind} n={n} 在 i={i} 两路不一致"


# 变体：默认 / collect_all / 带 entry_gate / 周线。两参外部 scorer 变体单独测（包一层路径）。
_VARIANTS = [
    {},
    {"collect_all": True},
    {"collect_all": True, "entry_gate": bt.ENTRY_GATES["j_low"]},
    {"collect_all": True, "weekly": True, "min_bars": 8},
]


@pytest.mark.parametrize("variant", _VARIANTS, ids=lambda v: str(sorted(v)))
def test_evaluate_trades_bitwise_equal_with_scorer_precompute(monkeypatch, variant):
    """② 逐笔等价：注册表正常 vs 清空注册表（强制旧路径），trades 列表完全一致。"""
    bars = {
        "600000": _shaped_bars("random_walk", 160, seed=7),
        "000001": _shaped_bars("pullback_hit", 160, seed=11),
        "300750": _shaped_bars("oscillating", 160, seed=13),
    }
    kw = {"min_bars": 30, **variant}
    t_on = bt.evaluate_trades(bars, **kw)  # 默认 scorer=_sc_b1_pullback，注册表正常
    monkeypatch.setattr(bt, "_SCORER_PRECOMPUTE", {})  # 查不到 ⇒ 传 None ⇒ 旧路径
    t_off = bt.evaluate_trades(bars, **kw)
    assert t_on == t_off, f"{variant} 逐笔输出不一致"


def test_two_arg_scorer_unaffected():
    """两参外部 scorer 走 _dual_form_scorer 包装：不查注册表、不受预计算影响。"""
    bars = {"600000": _shaped_bars("random_walk", 120, seed=7)}

    def _always_buy(df: pd.DataFrame, code: str) -> dict:
        return {"score": 50.0, "suggestion": "可买", "aux": {}, "components": {}}

    t = bt.evaluate_trades(bars, scorer=_always_buy, collect_all=True, min_bars=30)
    assert len(t) > 0, "两参 scorer 经包装后应正常工作并出交易"


def test_precompute_equivalence_not_vacuous(monkeypatch):
    """③ 防"空==空"假绿：pullback_hit 形态真的判出 hit=True，且两路都出交易。"""
    df = _shaped_bars("pullback_hit", 120, seed=42)
    pre = bt._precompute_b1_pullback_series(df)
    hits_on = sum(
        bool(compute_b1_pullback_fit(df.iloc[: i + 1], pre).get("hit"))
        for i in range(20, len(df))
    )
    assert hits_on > 0, (
        "pullback_hit 合成数据上一次 hit=True 都没判出，等价测试形同空转"
    )
    bars = {"000001": df}
    t_on = bt.evaluate_trades(bars, collect_all=True, min_bars=30)
    monkeypatch.setattr(bt, "_SCORER_PRECOMPUTE", {})
    t_off = bt.evaluate_trades(bars, collect_all=True, min_bars=30)
    assert len(t_on) > 0, "pullback_hit 上一笔交易都没出，逐笔等价形同空转"
    assert t_on == t_off


# ================= TODO #59① 收尾：_sc_kdj_j / _sc_rsi_state 双形态等价 =================
# 同一框架（_SCORER_PRECOMPUTE 按身份查表 → 第三参喂全序列）接另外两个 scorer：
#   - _sc_kdj_j：KDJ（RSV→EWM→EWM，fill_na=50）从第 0 根递归，前缀末点==全序列同位点；
#   - _sc_rsi_state：Wilder RSI（ewm adjust=False，indicators.rsi）同样从第 0 根递归，
#     rsi_state_score 一次触发 5 次 RSI（regime/divergence 的 14 + multi 的 6/14/24），
#     现由 _precompute_rsi_state_series 逐股算一次、rsi_series_map 透传。

# 11/12 = kdj_j 的 len<12 守卫边界；39/40/45 = rsi_state 的 RSI_MIN_BARS=40 守卫边界。
_KDJ_SHAPES = [
    ("random_walk", 120),
    ("strong_trend", 120),
    ("oscillating", 120),
    ("nan_prefix", 120),
    ("random_walk", 11),  # len<12：守卫 → None（两路一致）
    ("random_walk", 12),  # 刚好 12 根边界
    ("random_walk", 300),  # 长序列
]

_RSI_SHAPES = [
    ("random_walk", 120),
    ("strong_trend", 120),
    ("oscillating", 120),
    ("nan_prefix", 120),
    ("random_walk", 39),  # <40：available=False（两路一致）
    ("random_walk", 40),  # 刚好 40 根边界
    ("random_walk", 45),
    ("random_walk", 300),  # 长序列
]


@pytest.mark.parametrize("kind,n", _KDJ_SHAPES)
def test_kdj_j_scorer_dual_form_per_bar_equal(kind, n):
    """① 逐 bar 等价：_sc_kdj_j 带/不带 precomputed，返回 dict 完全相等（含 None/NaN 分支）。"""
    df = _shaped_bars(kind, n)
    pre = bt._precompute_kdj_j_series(df)
    assert pre is not None, "预计算在正常数据上不该退 None"
    for i in _sample_bars(n):
        sl = df.iloc[: i + 1]
        a = bt._sc_kdj_j(sl, "600000")
        b = bt._sc_kdj_j(sl, "600000", pre)
        assert _dict_eq(a, b), f"{kind} n={n} 在 i={i} 两路不一致:\n{a}\n{b}"


@pytest.mark.parametrize("kind,n", _RSI_SHAPES)
def test_rsi_state_scorer_dual_form_per_bar_equal(kind, n):
    """① 逐 bar 等价：_sc_rsi_state 带/不带 precomputed，返回 dict 完全相等。"""
    df = _shaped_bars(kind, n)
    pre = bt._precompute_rsi_state_series(df)
    assert pre is not None, "预计算在正常数据上不该退 None"
    for i in _sample_bars(n):
        sl = df.iloc[: i + 1]
        a = bt._sc_rsi_state(sl, "600000")
        b = bt._sc_rsi_state(sl, "600000", pre)
        assert _dict_eq(a, b), f"{kind} n={n} 在 i={i} 两路不一致:\n{a}\n{b}"


@pytest.mark.parametrize("kind,n", _RSI_SHAPES)
def test_rsi_state_score_and_multi_dual_form_equal(kind, n):
    """①b 因子层逐点等价：rsi_state_score / rsi_multi 带/不带 rsi_series_map（多周期）。"""
    df = _shaped_bars(kind, n)
    smap = {p: _rsi_impl(df["close"], p) for p in (6, 14, 24)}
    for i in _sample_bars(n):
        sl = df.iloc[: i + 1]
        a = rsi_state_score(sl)
        b = rsi_state_score(sl, rsi_series_map=smap)
        assert _dict_eq(a, b), f"rsi_state_score {kind} n={n} 在 i={i} 两路不一致"
        a2 = rsi_multi(sl)
        b2 = rsi_multi(sl, rsi_series_map=smap)
        assert _dict_eq(a2, b2), f"rsi_multi {kind} n={n} 在 i={i} 两路不一致"


@pytest.mark.parametrize("scorer_name", ["kdj_j", "rsi_state"])
def test_evaluate_trades_bitwise_equal_new_scorers(monkeypatch, scorer_name):
    """② 逐笔等价：注册表正常 vs 清空注册表（强制旧路径），trades 列表完全一致。"""
    bars = {
        "600000": _shaped_bars("random_walk", 160, seed=7),
        "000001": _shaped_bars("strong_trend", 160, seed=11),
        "300750": _shaped_bars("oscillating", 160, seed=13),
    }
    kw = {"scorer": bt.SCORERS[scorer_name], "collect_all": True, "min_bars": 30}
    t_on = bt.evaluate_trades(bars, **kw)
    monkeypatch.setattr(bt, "_SCORER_PRECOMPUTE", {})  # 查不到 ⇒ 传 None ⇒ 旧路径
    t_off = bt.evaluate_trades(bars, **kw)
    assert t_on == t_off, f"scorer={scorer_name} 逐笔输出不一致"


def test_new_scorers_equivalence_not_vacuous():
    """③ 防"空==空"假绿：_sc_kdj_j 真的产出记录、_sc_rsi_state 真的判出 available。"""
    df = _shaped_bars("random_walk", 120, seed=42)
    kpre = bt._precompute_kdj_j_series(df)
    kdj_recs = sum(
        bt._sc_kdj_j(df.iloc[: i + 1], "600000", kpre) is not None
        for i in range(12, len(df))
    )
    assert kdj_recs > 0, "_sc_kdj_j 一条记录都没产出，等价测试形同空转"
    rpre = bt._precompute_rsi_state_series(df)
    rsi_avail = sum(
        bt._sc_rsi_state(df.iloc[: i + 1], "600000", rpre) is not None
        for i in range(40, len(df))
    )
    assert rsi_avail > 0, "_sc_rsi_state 一次 available 都没判出，等价测试形同空转"


# ================= v0.173 无切片快速路径：df=None + 显式 n 点查询 =================
# 热循环（_scan_entry_signals / 单遍循环）不再逐 bar 构造 df.iloc[:i+1]：
# compute_b1_pullback_fit(None, pre, n=i+1) 必须与 compute_b1_pullback_fit(slice, pre)
# 逐位一致（df 在 precomputed 分支本就只用于 len(df)）。


@pytest.mark.parametrize("kind,n", _SHAPES)
def test_fit_slice_free_point_query_per_bar_equal(kind, n):
    """④ 逐 bar 等价：df=None+n 点查询 vs 真切片 precomputed 路径，dict 完全相等。"""
    df = _shaped_bars(kind, n)
    pre = bt._precompute_b1_pullback_series(df)
    assert pre is not None, "预计算在正常数据上不该退 None"
    for i in _sample_bars(n):
        sl = df.iloc[: i + 1]
        a = compute_b1_pullback_fit(sl, pre)
        b = compute_b1_pullback_fit(None, pre, n=i + 1)
        assert _dict_eq(a, b), f"{kind} n={n} 在 i={i} 无切片路径不一致:\n{a}\n{b}"


@pytest.mark.parametrize("kind,n", _SHAPES)
def test_scorer_slice_free_per_bar_equal(kind, n):
    """④b 逐 bar 等价：_sc_b1_pullback(None, code, pre, n) == _sc_b1_pullback(slice, code, pre)。"""
    df = _shaped_bars(kind, n)
    pre = bt._precompute_b1_pullback_series(df)
    assert pre is not None
    for i in _sample_bars(n):
        sl = df.iloc[: i + 1]
        a = bt._sc_b1_pullback(sl, "600000", pre)
        b = bt._sc_b1_pullback(None, "600000", pre, n=i + 1)
        assert _dict_eq(a, b), f"{kind} n={n} 在 i={i} 无切片路径不一致:\n{a}\n{b}"


def test_fit_slice_free_requires_precomputed_and_n():
    """④c 守卫：df=None 但缺 precomputed / 缺 n ⇒ available=False（不猜、不 raise）。"""
    r1 = compute_b1_pullback_fit(None)
    r2 = compute_b1_pullback_fit(None, {"c": None}, n=None)
    r3 = compute_b1_pullback_fit(None, None, n=100)
    for r in (r1, r2, r3):
        assert r == {"available": False, "score": 0, "max_score": 7, "hit": False}


def test_scorer_whitelist_audit():
    """⑤ 白名单卫生：_sc_rsi_state（内层 rsi_divergence 必读 df["low"]）等不得入内。"""
    assert bt._sc_b1_pullback in bt._SLICE_FREE_SCORERS
    assert bt._sc_rsi_state not in bt._SLICE_FREE_SCORERS
    assert bt._sc_kdj_j not in bt._SLICE_FREE_SCORERS  # 无 df=None+n 入口，本次不动


# ================= TODO #59②：5 个研究侧 selector scorer 双形态等价 =================
# alpha_pvcorr / low_vol / momentum / reversal_quality / reversal_quality_inv 接
# _SCORER_PRECOMPUTE：pre 只携带逐点/逐位可复现的全序列（float 数组 / pct_change
# 序列），scorer 按 len(slice) 截前缀后走**同一段**判定体——截断视图上的值与前缀
# 切片逐位相同 ⇒ 结构性逐位一致（rolling corr/std 是另一套在线算法，不保证逐位，
# 刻意不向量化，末 ≤20 根窗口统计仍每 bar 现算，总复杂度 O(n)）。本文件再逐 bar 钉一遍。
# reversal_quality_inv 是 4.0 − 正向分，与 reversal_quality 共用同一份 pre（透传）。

_SELECTOR5_PRE = {
    "alpha_pvcorr": bt._precompute_alpha_pvcorr_series,
    "low_vol": bt._precompute_low_vol_series,
    "momentum": bt._precompute_momentum_series,
    "reversal_quality": bt._precompute_reversal_quality_series,
    # inv 与正向共用同一份 OHLCV pre（scorer 内透传给正向 score 再取 4−分）
    "reversal_quality_inv": bt._precompute_reversal_quality_series,
}

# 每因子：4 形态×120 + 长序列 300 + min_bars 守卫边界；
# momentum 另加 120/121/122 = lb=min(100, n-21) 截断边界两侧。
_SELECTOR5_SHAPES = {
    "alpha_pvcorr": [
        ("random_walk", 120),
        ("strong_trend", 120),
        ("oscillating", 120),
        ("nan_prefix", 120),
        ("random_walk", 9),  # <10：守卫 → None（两路一致）
        ("random_walk", 10),  # 刚好 10 根边界
        ("random_walk", 11),
        ("random_walk", 300),  # 长序列
    ],
    "low_vol": [
        ("random_walk", 120),
        ("strong_trend", 120),
        ("oscillating", 120),
        ("nan_prefix", 120),
        ("random_walk", 20),  # <21：守卫 → None（两路一致）
        ("random_walk", 21),  # 刚好 21 根边界
        ("random_walk", 22),
        ("random_walk", 300),
    ],
    "momentum": [
        ("random_walk", 120),
        ("strong_trend", 120),
        ("oscillating", 120),
        ("nan_prefix", 120),
        ("random_walk", 39),  # <40：守卫 → None（两路一致）
        ("random_walk", 40),  # 刚好 40 根边界
        ("random_walk", 41),
        ("random_walk", 121),  # lb=min(100, n-21)：n-21=100 恰好不截断边界
        ("random_walk", 122),  # n-21>100：lb 截断到 100 分支
        ("random_walk", 300),
    ],
    "reversal_quality": [
        ("random_walk", 120),
        ("strong_trend", 120),
        ("oscillating", 120),
        ("nan_prefix", 120),
        ("random_walk", 20),  # <21：守卫 → None（两路一致）
        ("random_walk", 21),  # 刚好 21 根边界
        ("random_walk", 22),
        ("random_walk", 300),
    ],
    "reversal_quality_inv": [
        ("random_walk", 120),
        ("strong_trend", 120),
        ("oscillating", 120),
        ("nan_prefix", 120),
        ("random_walk", 20),
        ("random_walk", 21),
        ("random_walk", 22),
        ("random_walk", 300),
    ],
}

_SELECTOR5_CASES = [
    (name, kind, n) for name, shapes in _SELECTOR5_SHAPES.items() for kind, n in shapes
]


@pytest.mark.parametrize("scorer_name,kind,n", _SELECTOR5_CASES)
def test_selector5_scorer_dual_form_per_bar_equal(scorer_name, kind, n):
    """① 逐 bar 等价：5 个 selector 带/不带 precomputed，返回完全相等（含 None/NaN 分支）。"""
    df = _shaped_bars(kind, n)
    pre = _SELECTOR5_PRE[scorer_name](df)
    assert pre is not None, "预计算在正常数据上不该退 None"
    sc = bt.SCORERS[scorer_name]
    for i in _sample_bars(n):
        sl = df.iloc[: i + 1]
        a = sc(sl, "600000")
        b = sc(sl, "600000", pre)
        assert _dict_eq(a, b), (
            f"{scorer_name} {kind} n={n} 在 i={i} 两路不一致:\n{a}\n{b}"
        )


def test_selector5_registry_identity():
    """①c 身份接线：_SCORER_PRECOMPUTE 的键必须与 SCORERS 注册同一函数对象
    （注册表直通取到的与文件顶部别名是同一对象，身份查表才命中）。"""
    aliases = {
        "alpha_pvcorr": bt._sc_alpha_pvcorr,
        "low_vol": bt._sc_low_vol,
        "momentum": bt._sc_momentum,
        "reversal_quality": bt._sc_reversal_quality,
        "reversal_quality_inv": bt._sc_reversal_quality_inv,
    }
    for name, pre_fn in _SELECTOR5_PRE.items():
        assert bt.SCORERS[name] is aliases[name], f"{name} 注册表直通对象变了"
        assert bt._SCORER_PRECOMPUTE.get(bt.SCORERS[name]) is pre_fn, (
            f"{name} 未按身份命中 _SCORER_PRECOMPUTE"
        )


@pytest.mark.parametrize("scorer_name", list(_SELECTOR5_PRE))
def test_evaluate_trades_bitwise_equal_selector5(monkeypatch, scorer_name):
    """② 逐笔等价：注册表正常 vs 清空注册表（强制旧路径），trades 列表完全一致。"""
    bars = {
        "600000": _shaped_bars("random_walk", 160, seed=7),
        "000001": _shaped_bars("strong_trend", 160, seed=11),
        "300750": _shaped_bars("oscillating", 160, seed=13),
    }
    kw = {"scorer": bt.SCORERS[scorer_name], "collect_all": True, "min_bars": 30}
    t_on = bt.evaluate_trades(bars, **kw)
    monkeypatch.setattr(bt, "_SCORER_PRECOMPUTE", {})  # 查不到 ⇒ 传 None ⇒ 旧路径
    t_off = bt.evaluate_trades(bars, **kw)
    assert t_on == t_off, f"scorer={scorer_name} 逐笔输出不一致"


def test_selector5_equivalence_not_vacuous():
    """③ 防"空==空"假绿：5 个 scorer 在合成数据上都真的产出记录（非全 None），
    且 evaluate_trades 真的出交易。"""
    df = _shaped_bars("random_walk", 160, seed=42)
    for name, pre_fn in _SELECTOR5_PRE.items():
        pre = pre_fn(df)
        recs = sum(
            bt.SCORERS[name](df.iloc[: i + 1], "600000", pre) is not None
            for i in range(40, len(df))
        )
        assert recs > 0, f"{name} 一条记录都没产出，等价测试形同空转"
    bars = {"000001": _shaped_bars("random_walk", 160, seed=42)}
    for name in _SELECTOR5_PRE:
        t = bt.evaluate_trades(
            bars, scorer=bt.SCORERS[name], collect_all=True, min_bars=30
        )
        assert len(t) > 0, f"{name} 一笔交易都没出，逐笔等价形同空转"
