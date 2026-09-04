# -*- coding: utf-8 -*-
"""TODO #58 效率优化的**逐位等价**钉测：ENTRY_GATES 预计算旁路。

背景：evaluate_trades 原来每根 bar 都把 ``df.iloc[:i+1]`` 传给 entry_gate，
gate 内的 KDJ/MACD/ADX 在全前缀上重算（每股 O(n²)，实测占回测绝大部分时间）。
这些指标（EWM adjust=False / Wilder 平滑 / rolling 窗口）都从第 0 根开始递归
⇒ prefix 上算出的末点与全序列第 i 点是**同一串浮点运算**，数学上逐位相同。
`_precompute_gate_series` 逐股算一次，gate 走 ``(df_slice, precomputed=None)``
双形态取对应点。

本文件钉住这条等价（防未来有人"优化"出口径漂移）：

  ① 每个 gate × 每个采样 bar：``gate(slice) == gate(slice, precomputed)``
     —— 黑盒 gate（platform_pullback/b2/rsi/main_rally 等）不接受预计算，
        两路必须同样一致（它们应**忽略** precomputed）。
  ② evaluate_trades 的逐笔输出在 开/关预计算 下**完全一致**（trades list ==，
     含 round 后字段；用 monkeypatch 关掉预计算模拟旧路径）。
"""

import numpy as np
import pandas as pd
import pytest

from custos.research import backtest_factors as bt


def _bars(n: int = 160, seed: int = 7) -> pd.DataFrame:
    """确定性合成日线：掺入一字板(high==low)、零量停牌、近涨停，覆盖 gate 边界路径。"""
    rng = np.random.default_rng(seed)
    close = np.maximum(10 + np.cumsum(rng.normal(0, 0.15, n)), 1.0)
    high = close + np.abs(rng.normal(0, 0.08, n))
    low = close - np.abs(rng.normal(0, 0.08, n))
    open_ = low + (high - low) * rng.random(n)
    vol = np.abs(rng.normal(1e6, 2e5, n))
    for k in range(5, n, 17):  # 一字板：RSV 0/0 → kdj fill_na=50 路径
        high[k] = low[k] = open_[k] = close[k]
    for k in range(9, n, 23):  # 停牌：量 0
        vol[k] = 0.0
    for k in range(13, n, 29):  # 近涨停
        close[k] = close[k - 1] * 1.098
        high[k] = max(high[k], close[k])
        low[k] = min(low[k], close[k])
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


def _always_buy(df: pd.DataFrame, code: str) -> dict:
    """每根 bar 都判「可买」的 scorer —— 让 entry_gate 成为唯一过滤项，充分走 gate 路径。"""
    return {"score": 50.0, "suggestion": "可买", "aux": {}, "components": {}}


# 消费预计算序列的 gate（KDJ/MACD/ADX/RSI/QSX-DKS 系及其组合）；其余为黑盒 detector gate。
_FAST_GATES = {
    "j_low",
    "reversal_k",
    "j_macd_turn",
    "j_low_dif_pos",
    "j_low_adx25",
    "j_low_adx60",
    "j_low_qsx_gt_dks",
    "qsx_gt_dks",
    "rsi_strong",
    "rsi_bull_div",
    "j_low_rsi_strong",
    "j_low_rsi_div",
    "rsi_deep",
    "j_low_rsi_deep",
}

_ALL_GATES = sorted(k for k, g in bt.ENTRY_GATES.items() if g is not None)


@pytest.mark.parametrize("gate_name", _ALL_GATES)
def test_gate_precomputed_matches_slice_per_bar(gate_name):
    """① 逐 bar 等价：gate(slice) 与 gate(slice, precomputed) 每个采样点都相同。"""
    df = _bars()
    pre = bt._precompute_gate_series(df)
    assert pre is not None, "预计算在依赖齐全时不该退 None"
    gate = bt.ENTRY_GATES[gate_name]
    n = len(df)
    for i in list(range(0, n, 8)) + [n - 2, n - 1]:
        sl = df.iloc[: i + 1]
        assert gate(sl) == gate(sl, pre), f"{gate_name} 在 i={i} 两路不一致"


# 变体：默认 / 强制每根过 gate(collect_all) / 动态止损 / 成本区+分批止盈 / 周线。
# 黑盒 gate 每根都跑 detector 较慢，只跑默认 + collect_all 两个变体。
_VARIANTS_FAST = [
    {},
    {"collect_all": True},
    {"collect_all": True, "breakeven_trigger": 0.05, "trail_pct": 0.08},
    {"collect_all": True, "cost_zone_bars": 3, "scale_out_frac": 0.5},
    {"collect_all": True, "weekly": True, "min_bars": 8},
]
_VARIANTS_SLOW = [{}, {"collect_all": True}]

_CASES = [
    (g, vi, v)
    for g in _ALL_GATES
    for vi, v in enumerate(_VARIANTS_FAST if g in _FAST_GATES else _VARIANTS_SLOW)
]


@pytest.mark.parametrize(
    "gate_name,variant",
    [(g, v) for g, _, v in _CASES],
    ids=[f"{g}#{vi}" for g, vi, _ in _CASES],
)
def test_evaluate_trades_bitwise_equal_with_precompute(monkeypatch, gate_name, variant):
    """② 逐笔等价：开/关预计算，evaluate_trades 的 trades 列表完全一致。"""
    bars = {
        "600000": _bars(seed=7),
        "000001": _bars(seed=11),
        "300750": _bars(seed=13),
    }
    gate = bt.ENTRY_GATES[gate_name]
    kw = {"min_bars": 30, **variant}
    t_on = bt.evaluate_trades(bars, scorer=_always_buy, entry_gate=gate, **kw)
    # 关掉预计算 = 旧的逐切片路径（gate 收到 precomputed=None 时现算）
    monkeypatch.setattr(bt, "_precompute_gate_series", lambda df, kdj=None: None)
    t_off = bt.evaluate_trades(bars, scorer=_always_buy, entry_gate=gate, **kw)
    assert t_on == t_off, f"{gate_name} {variant} 逐笔输出不一致"


def test_precompute_equivalence_not_vacuous():
    """防"空==空"假绿：j_low 两路都必须真的出交易。"""
    bars = {
        "600000": _bars(seed=7),
        "000001": _bars(seed=11),
        "300750": _bars(seed=13),
    }
    t = bt.evaluate_trades(
        bars,
        scorer=_always_buy,
        entry_gate=bt.ENTRY_GATES["j_low"],
        collect_all=True,
    )
    assert len(t) > 0, "合成数据上 j_low 一笔都没出，等价测试形同空转"


# ---- TODO #59②：rsi_regime/rsi_divergence 双形态（rsi_series=）逐点等价 ----


def _shaped_bars(kind: str, n: int = 120, seed: int = 3) -> pd.DataFrame:
    """不同形态的合成日线，覆盖 RSI gate 的边界路径。"""
    rng = np.random.default_rng(seed)
    if kind == "strong_trend":  # 强趋势：单调上行 → 牛市区间
        close = 10 + np.cumsum(np.abs(rng.normal(0.12, 0.05, n)))
    elif kind == "oscillating":  # 震荡：正弦 + 噪声
        close = 10 + 1.5 * np.sin(np.arange(n) / 6.0) + rng.normal(0, 0.05, n)
    elif kind == "nan_prefix":  # 含 NaN 前缀（停牌段）
        close = np.maximum(10 + np.cumsum(rng.normal(0, 0.15, n)), 1.0)
        close[:5] = np.nan
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


@pytest.mark.parametrize(
    "kind,n",
    [
        ("random_walk", 120),
        ("strong_trend", 120),
        ("oscillating", 120),
        ("nan_prefix", 120),
        ("random_walk", 40),  # 刚好 RSI_MIN_BARS
        ("random_walk", 41),  # RSI_MIN_BARS + 1
    ],
)
def test_rsi_state_dual_form_pointwise_equal(kind, n):
    """rsi_regime/rsi_divergence：全序列预计算 vs 逐前缀现算，每个采样点 dict 完全相等。

    等价依据：indicators.rsi 是 ewm(alpha=1/n, adjust=False)，从第 0 根递归，
    prefix 末点与全序列同位点是同一串浮点运算（含 NaN 处理、min_bars 守卫、
    四态分类边界、摆动点 idxmin 取值，全部作用在同值同 index 的序列上）。
    """
    from custos.core.factors.rsi_state import rsi_divergence, rsi_regime
    from custos.core.indicators import rsi

    df = _shaped_bars(kind, n=n)
    full = rsi(df["close"], 14)
    for i in list(range(0, n, 3)) + [n - 2, n - 1]:
        sl = df.iloc[: i + 1]
        assert rsi_regime(sl, rsi_series=full) == rsi_regime(sl), (
            f"rsi_regime {kind} n={n} 在 i={i} 两路不一致"
        )
        assert rsi_divergence(sl, rsi_series=full) == rsi_divergence(sl), (
            f"rsi_divergence {kind} n={n} 在 i={i} 两路不一致"
        )


def test_rsi_state_dual_form_not_vacuous():
    """防"空==空"假绿：两路都必须真的判出 strong / 底背离至少一次。"""
    from custos.core.factors.rsi_state import rsi_divergence, rsi_regime
    from custos.core.indicators import rsi

    df = _shaped_bars("strong_trend", n=160, seed=5)
    full = rsi(df["close"], 14)
    strong_hits = sum(
        rsi_regime(df.iloc[: i + 1], rsi_series=full).get("state") == "strong"
        for i in range(len(df))
    )
    assert strong_hits > 0, "强趋势合成数据上 strong 一次都没判出，等价测试形同空转"
    # 底背离：先跌出两个低点再企稳回升的 V 形
    n = 120
    down = 20 - np.cumsum(np.abs(np.random.default_rng(9).normal(0.3, 0.05, n // 2)))
    up = down[-1] + np.cumsum(
        np.abs(np.random.default_rng(10).normal(0.1, 0.03, n - n // 2))
    )
    close = np.concatenate([down, up])
    v = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.05,
            "low": close - 0.05,
            "close": close,
            "volume": np.full(n, 1e6),
        }
    )
    full_v = rsi(v["close"], 14)
    div_out = rsi_divergence(v, rsi_series=full_v)
    assert div_out == rsi_divergence(v) and div_out.get("available"), (
        "V 形合成数据上 divergence 两路不一致或不可用"
    )


# ---- QSX/DKS 与周线 gate 的预计算旁路（2026-09-03）----
# `_precompute_gate_series` 新增 qsx/dks 日线全序列与 weekly_j/weekly_qsx/weekly_dks/
# weekly_bars（「截至当日前缀 resample("W-FRI")」口径，含进行中部分周，见
# `_weekly_gate_arrays` docstring）。这里钉住这批 gate 的快/慢两路逐 bar 一致。
# ⚠️ 合成数据必须带 ``amount`` 列 —— indicators.resample 的聚合字典引用它，
#    缺列时慢路径 resample 直接 KeyError（gate 恒 False），等价性形同空转。


def _bars_amount(n: int = 600, seed: int = 7) -> pd.DataFrame:
    """带 amount 列的随机游走（600 根 ≈ 120 周，越过周 DKS 的 ≥114 周守卫）。"""
    df = _bars(n=n, seed=seed)
    df["amount"] = df["volume"] * df["close"]
    return df


def _shaped_bars_amount(kind: str) -> pd.DataFrame:
    """三种形态：上行趋势（周 QSX>DKS 真命中）/ 下跌（日周 J 双低真命中）/
    趋势后急回（复合 gate 真命中）。"""
    if kind == "uptrend":
        rng = np.random.default_rng(5)
        close = 10 + np.cumsum(np.abs(rng.normal(0.08, 0.1, 800)))
    elif kind == "decline":
        rng = np.random.default_rng(3)
        close = np.maximum(30 - np.cumsum(np.abs(rng.normal(0.06, 0.05, 620))), 1.0)
    else:  # pullback
        rng = np.random.default_rng(5)
        up = 10 + np.cumsum(np.abs(rng.normal(0.08, 0.08, 700)))
        down = up[-1] - np.cumsum(np.abs(rng.normal(0.12, 0.1, 40)))
        close = np.maximum(np.concatenate([up, down]), 1.0)
    n = len(close)
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
            "amount": vol * close,
        }
    )


_QSX_WEEKLY_GATES = [
    "qsx_gt_dks",
    "j_low_qsx_gt_dks",
    "weekly_j_low",
    "j_low_weekly_resonance",
    "weekly_qsx_gt_dks",
    "j_low_qsx_weekly",
    "j_low_weekly_qsx_weekly",
]


@pytest.mark.parametrize("gate_name", _QSX_WEEKLY_GATES)
@pytest.mark.parametrize("kind", ["random_walk", "uptrend", "decline", "pullback"])
def test_qsx_weekly_gate_precomputed_matches_slice_per_bar(gate_name, kind):
    """③ QSX/DKS 与周线 gate 逐 bar 等价：gate(slice) == gate(slice, precomputed)。"""
    df = _bars_amount() if kind == "random_walk" else _shaped_bars_amount(kind)
    pre = bt._precompute_gate_series(df)
    assert pre is not None and "weekly_j" in pre and "qsx" in pre, (
        "带 amount 的合成数据上预计算应含周线与 QSX/DKS 键"
    )
    gate = bt.ENTRY_GATES[gate_name]
    n = len(df)
    for i in list(range(0, n, 3)) + [n - 2, n - 1]:
        sl = df.iloc[: i + 1]
        assert gate(sl) == gate(sl, pre), f"{gate_name} {kind} 在 i={i} 两路不一致"


def test_qsx_weekly_gate_precompute_not_vacuous():
    """防"空==空"假绿：每个 gate 至少在一个形态上真的判出 True（两路同时）。"""
    expectations = {
        "qsx_gt_dks": "random_walk",
        "j_low_qsx_gt_dks": "random_walk",
        "weekly_j_low": "decline",
        "j_low_weekly_resonance": "decline",
        "weekly_qsx_gt_dks": "uptrend",
        "j_low_qsx_weekly": "pullback",
        "j_low_weekly_qsx_weekly": "pullback",
    }
    for gate_name, kind in expectations.items():
        df = _bars_amount() if kind == "random_walk" else _shaped_bars_amount(kind)
        pre = bt._precompute_gate_series(df)
        gate = bt.ENTRY_GATES[gate_name]
        hits = sum(gate(df.iloc[: i + 1], pre) for i in range(0, len(df), 3))
        assert hits > 0, f"{gate_name} 在 {kind} 形态上一次都没判出，等价测试形同空转"


def test_prepare_stock_shared_kdj_bitwise_equal(monkeypatch):
    """④ gate_pre 与 scorer_pre(_sc_kdj_j) 共享 KDJ：逐笔输出与旧路径完全一致。"""
    bars = {"600000": _bars(seed=7), "000001": _bars(seed=11)}
    kw = {"min_bars": 30, "collect_all": True}
    sc = bt.SCORERS["kdj_j"]
    gate = bt.ENTRY_GATES["j_low"]
    t_on = bt.evaluate_trades(bars, scorer=sc, entry_gate=gate, **kw)
    assert t_on, "合成数据上一笔都没出，等价测试形同空转"
    monkeypatch.setattr(bt, "_precompute_gate_series", lambda df, kdj=None: None)
    monkeypatch.setattr(bt, "_precompute_kdj_j_series", lambda df, kdj=None: None)
    t_off = bt.evaluate_trades(bars, scorer=sc, entry_gate=gate, **kw)
    assert t_on == t_off, "共享 KDJ 后逐笔输出与旧路径不一致"


# ---- v0.173 无切片快速路径（_PrefixLen 占位 / df=None+n 点查询）----
# 白名单 gate 的 precomputed 分支只读 pre + len(df_slice) ⇒ 传 _PrefixLen(i+1)
# 占位与传真切片必须逐 bar 一致；evaluate_trades 开/关快速路径逐笔一致。


def _whitelist_gate_names() -> list[str]:
    """_SLICE_FREE_GATES 里函数对应的 ENTRY_GATES 名（身份反查）。"""
    return sorted(
        name for name, g in bt.ENTRY_GATES.items() if g in bt._SLICE_FREE_GATES
    )


@pytest.mark.parametrize("gate_name", _whitelist_gate_names())
@pytest.mark.parametrize("kind", ["random_walk", "uptrend", "decline", "pullback"])
def test_slice_free_gate_matches_slice_per_bar(gate_name, kind):
    """⑤ 无切片占位逐 bar 等价：gate(_PrefixLen(i+1), pre) == gate(slice, pre)。

    slice vs precomputed 的等价由上文 ③ 逐 3 根钉住，这里只钉占位维度（每 7 根
    + 末尾，控制耗时——黑盒慢路径不在此测试范围内）。
    """
    df = _bars_amount() if kind == "random_walk" else _shaped_bars_amount(kind)
    pre = bt._precompute_gate_series(df)
    gate = bt.ENTRY_GATES[gate_name]
    assert bt._slice_free_ok(gate, pre), (
        f"{gate_name} 在白名单内但必需键不齐（kind={kind}），快速路径判定有漏洞"
    )
    n = len(df)
    for i in list(range(0, n, 7)) + [n - 2, n - 1]:
        sl = df.iloc[: i + 1]
        fast = gate(bt._PrefixLen(i + 1), pre)
        slow = gate(sl, pre)
        assert fast == slow, f"{gate_name} {kind} 在 i={i} 占位路径不一致"


def test_slice_free_gate_not_vacuous():
    """防空转：白名单里几个代表 gate 在占位路径下真的判出 True。"""
    expectations = {
        "j_low": "decline",
        "j_low_adx25": "decline",
        "qsx_gt_dks": "random_walk",
        "weekly_qsx_gt_dks": "uptrend",
        "j_low_weekly_qsx_weekly": "pullback",
    }
    for gate_name, kind in expectations.items():
        df = _bars_amount() if kind == "random_walk" else _shaped_bars_amount(kind)
        pre = bt._precompute_gate_series(df)
        gate = bt.ENTRY_GATES[gate_name]
        hits = sum(gate(bt._PrefixLen(i + 1), pre) for i in range(0, len(df), 3))
        assert hits > 0, f"{gate_name} 占位路径在 {kind} 上一次都没判出"


def test_slice_free_whitelist_audit():
    """⑥ 白名单卫生：还读 df 列的 gate 不得入内；必需键缺失时不准走快速路径。"""
    # 读列/黑盒 gate 一律不在白名单
    for name in (
        "reversal_k",
        "platform_pullback",
        "breakout_pullback_b1",
        "rsi_bull_div",
        "j_low_rsi_div",
    ):
        g = bt.ENTRY_GATES.get(name)
        if g is not None:
            assert g not in bt._SLICE_FREE_GATES, f"{name} 读 df 列，不得进白名单"
    # 必需键缺失（周线序列算不出 ⇒ gate 会回退读真 df 的慢路径）⇒ 判定必须否决
    df = _bars(seed=7)  # 无 amount 列 ⇒ indicators.resample KeyError ⇒ 无周线键
    pre = bt._precompute_gate_series(df)
    assert pre is not None and "weekly_j" not in pre, "该数据应缺周线键"
    wk_gate = bt.ENTRY_GATES["j_low_weekly_qsx_weekly"]
    assert wk_gate in bt._SLICE_FREE_GATES and not bt._slice_free_ok(wk_gate, pre)
    # gate_pre 整体缺失 / 外部单参 gate ⇒ 否决
    assert not bt._slice_free_ok(wk_gate, None)
    assert not bt._slice_free_ok(lambda df: True, None)
    # gate=None（ENTRY_GATES["none"]）⇒ gate 侧恒放行
    assert bt._slice_free_ok(None, None)


# 注：周线复合 gate（j_low_weekly_qsx_weekly 等）在合成数据上叠加默认 scorer 后
# 0 交易（逐 bar 占位等价由 ⑤ 钉住、真命中由 test_slice_free_gate_not_vacuous 钉住），
# 这里只放「默认 scorer 下真的出交易」的 gate，防"空==空"假绿。
@pytest.mark.parametrize(
    "gate_name", ["j_low", "j_low_adx25", "rsi_strong", "qsx_gt_dks"]
)
@pytest.mark.parametrize("use_signals", [False, True], ids=["single_pass", "signals"])
def test_slice_free_evaluate_trades_bitwise_equal(monkeypatch, gate_name, use_signals):
    """⑦ 逐笔等价：快速路径开 vs 关（清空两个白名单 ⇒ 强制旧路径），trades 完全一致。

    覆盖单遍循环与信号两阶段两条扫描路径（默认 scorer=_sc_b1_pullback 在白名单内）。
    """
    bars = {
        "600000": _bars_amount(seed=7),
        "000001": _shaped_bars_amount("decline"),
    }
    gate = bt.ENTRY_GATES[gate_name]
    kw: dict = {"min_bars": 30, "collect_all": True, "entry_gate": gate}
    if use_signals:
        kw["signals_out"] = []
    t_fast = bt.evaluate_trades(bars, **kw)
    assert t_fast, f"{gate_name} 合成数据上一笔都没出，等价测试形同空转"
    monkeypatch.setattr(bt, "_SLICE_FREE_GATES", {})
    monkeypatch.setattr(bt, "_SLICE_FREE_SCORERS", frozenset())
    t_slow = bt.evaluate_trades(bars, **kw)
    assert t_fast == t_slow, f"{gate_name} use_signals={use_signals} 两路逐笔不一致"


def test_slice_free_dates_prefetch_bitwise():
    """⑧ 日期预提取口径：_prepare_stock 的 dates 数组逐位 == str(df['date'].iloc[i])[:10]。"""
    for df in (_bars(seed=7), _bars_amount(seed=11)):
        prep = bt._prepare_stock(
            df,
            False,
            30,
            "600000",
            True,
            0.0,
            "tick",
            bt.ENTRY_GATES["j_low"],
            scorer=None,
        )
        assert prep is not None
        df_sorted = df.sort_values("date").reset_index(drop=True)
        expect = [str(df_sorted["date"].iloc[i])[:10] for i in range(len(df_sorted))]
        assert prep["dates"] == expect
