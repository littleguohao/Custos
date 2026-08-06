"""J 值与 BBI 的**唯一实现**。

2026-08-06 清点：`_j_series` 有 3 份、BBI 公式在 4 处各写一遍。
这两个恰好是 B1 最核心的两个指标：

    J < 13   —— 入场触发（B1 候选的唯一硬条件）
    BBI      —— 移动止盈与持仓状态（bbi_above / 连破 N 日清仓）

它们分散在 **live 选股链 / 研究回测器 / 持仓状态机** 三处。
⚠️ 只要有一处被单独修改，回测与 live 就会对同一根 K 线算出不同的 J/BBI，
**两边的结论再也无法互相印证** —— 而 R1 的框架、M2 的整套止损研究全建立在 BBI 上。

实测当时**尚未发散**（BBI 四处一致；b2 与 main_rally 的 J 逐点相同；
enrich 因多一步 fillna(50) 最大差 1.44 但 J<13 触发面 0 根不一致）。
**趁还没发散就合并**，而不是等某次改动之后再去比对。
"""
from __future__ import annotations

import pathlib
import re
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("07_tools", "07_tools/screening", "07_tools/market_timing"):
    sys.path.insert(0, str(ROOT / _p))

import indicators as I  # noqa: E402


def _bars(n=80, seed=11, flat_slice=None):
    rng = np.random.default_rng(seed)
    close = 10 + np.cumsum(rng.normal(0, 0.2, n))
    high = close + abs(rng.normal(0, 0.15, n))
    low = close - abs(rng.normal(0, 0.15, n))
    if flat_slice:
        s = flat_slice
        high[s], low[s] = close[s], close[s]      # 一字板
    return pd.DataFrame({"close": close, "high": high, "low": low})


class TestJSeries:
    def test_formula_matches_kdj_definition(self):
        """J = 3K − 2D，K/D 是 RSV 的两次 EWM（com = m−1）。"""
        df = _bars(40)
        c, lo, hi = df["close"], df["low"].rolling(9).min(), df["high"].rolling(9).max()
        rsv = ((c - lo) / (hi - lo).replace(0, np.nan) * 100).replace([np.inf, -np.inf], np.nan)
        k = rsv.ewm(com=2, adjust=False).mean()
        d = k.ewm(com=2, adjust=False).mean()
        assert np.allclose(I.j_series(df).to_numpy(), (3 * k - 2 * d).to_numpy(),
                           equal_nan=True)

    def test_flat_bar_does_not_poison_whole_series(self):
        """⚠️ **一根一字板不得毁掉整条 J 序列。**

        `high == low` 时 `(close-low)/(high-low)` 是 0/0；不先把 0 换成 NaN 就会
        产生 inf，而 **inf 进了 EWM 会把之后所有值污染成 NaN**。
        """
        j = I.j_series(_bars(40, flat_slice=slice(10, 13)))
        tail = j.to_numpy()[20:]
        assert np.isfinite(tail).all(), "一字板之后的 J 被污染成 NaN"

    def test_fill_na_is_explicit_not_default(self):
        """NaN 策略必须显式传参 —— 默认保持 NaN（数据不足就是不足）。"""
        df = _bars(6)                     # 短于 rolling(9) ⇒ 全 NaN
        assert I.j_series(df).isna().all()
        assert (I.j_series(df, fill_na=50.0) == 50.0).all()

    def test_short_series_never_fabricates_a_low_j(self):
        """数据不足时不得产生「J 很低」的假信号 —— 那会凭空造出 B1 候选。"""
        for n in (1, 3, 8):
            j = I.j_series(_bars(max(n, 2)))
            assert not (j.dropna() < 13).any(), f"n={n} 时凭空出现 J<13"


class TestBbiSeries:
    def test_formula(self):
        c = _bars(60)["close"]
        want = sum(c.rolling(k).mean() for k in (3, 6, 12, 24)) / 4
        assert np.allclose(I.bbi_series(c).to_numpy(), want.to_numpy(), equal_nan=True)

    def test_needs_24_bars(self):
        """不足 24 根时 BBI 必须是 NaN —— 不能用短均线凑一个数出来。"""
        assert I.bbi_series(_bars(20)["close"]).isna().all()


class TestNoLocalReimplementation:
    """收敛后不许再有本地实现 —— 那是最常见的回退方式。"""

    CASES = [
        ("screening/enrich_candidates.py", "_j_canonical"),
        ("screening/main_rally_factor.py", "j_series as _j_series"),
        ("screening/b2_surge_factor.py", "_j_canonical"),
        ("screening/backtest_factors.py", "bbi_series as _bbi_series"),
        ("market_timing/technical_monitor.py", "kdj_series"),
    ]

    @pytest.mark.parametrize("rel,marker", CASES)
    def test_delegates_to_indicators(self, rel, marker):
        s = (ROOT / "07_tools" / rel).read_text(encoding="utf-8")
        assert "from indicators import" in s, f"{rel} 未导入共享指标"
        assert marker in s

    @pytest.mark.parametrize("rel", [c[0] for c in CASES])
    def test_no_inline_kdj_or_bbi_formula(self, rel):
        """源码里不许再出现 `3*k-2*d` 或 `(MA3+..+MA24)/4` 的内联算式。"""
        s = (ROOT / "07_tools" / rel).read_text(encoding="utf-8")
        assert not re.search(r"3 ?\* ?k ?- ?2 ?\* ?d", s), f"{rel} 又内联了 J 公式"
        assert not re.search(r"rolling\(n\)\.mean\(\) for n in \(3, 6, 12, 24\)", s), \
            f"{rel} 又内联了 BBI 公式"


class TestBehaviorPreserved:
    """合并必须**零行为变化**：三个调用方的语义各自保留。"""

    def test_enrich_keeps_fill_50(self):
        import enrich_candidates as E
        df = _bars(6)
        assert (np.asarray(E._j_series(df), dtype=float) == 50.0).all(), \
            "enrich 的 fillna(50) 行为丢了"

    def test_b2_keeps_short_series_guard(self):
        """b2 的 n<12 守卫要留着：返回 None 让调用方知道「数据不足」而非「没信号」。"""
        import b2_surge_factor as B
        assert B._j_series(_bars(11)) is None
        assert B._j_series(_bars(30)) is not None

    def test_main_rally_keeps_nan(self):
        import main_rally_factor as M
        assert pd.Series(M._j_series(_bars(6))).isna().all()

    def test_backtest_bbi_entrypoints_agree(self):
        import backtest_factors as BT
        df = _bars(60)
        assert np.allclose(BT._bbi_series(df["close"]).to_numpy(),
                           BT._bbi_series_from(df), equal_nan=True)


class TestHoldingStateSharesJWithSelection:
    """持仓状态机与 live 选股链必须用**同一个 J**。

    `technical_monitor.kdj` 是第 4 份 J 实现（持仓状态机用它出 `daily_j`/`kdj_death_cross`）。
    ⚠️ 同一只票可能同时被 `enrich_candidates`（选股）与本模块（持仓）评估 ——
    两边算出不同的 J，就会出现「**选股说 J<13 可进、持仓说 J 不低**」这类无法解释的矛盾，
    而 J<13 是 B1 唯一的硬入场条件。
    """

    def test_same_j_as_selection_chain(self):
        import enrich_candidates as E
        import technical_monitor as TM
        df = _bars(50, seed=3)
        r = TM.kdj(df)
        assert r.get("available") is not False
        je = float(np.asarray(E._j_series(df), dtype=float)[-1])
        assert abs(r["j"] - je) < 1e-9, "持仓状态机与选股链的 J 不一致"

    def test_kdj_series_exposes_k_and_d(self):
        """需要 K/D 的调用方用 `kdj_series`，不必自己再算一遍 —— 那是重复的起点。"""
        k, d, j = I.kdj_series(_bars(40))
        assert np.allclose((3 * k - 2 * d).to_numpy(), j.to_numpy(), equal_nan=True)

    def test_j_series_is_thin_wrapper(self):
        """`j_series` 必须复用 `kdj_series`，不许两套算法并存。"""
        src = (ROOT / "07_tools" / "indicators.py").read_text(encoding="utf-8")
        body = src[src.rindex("def j_series"):]
        assert "kdj_series(" in body, "j_series 应委托给 kdj_series"
        assert "ewm(" not in body, "j_series 又自己算了一遍 EWM"

    def test_short_series_guard_preserved(self):
        import technical_monitor as TM
        assert TM.kdj(_bars(10)).get("available") is False
