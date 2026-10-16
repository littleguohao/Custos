"""全扫描单格提速逐位等价钉测（v0.402）：表达式 scorer 进 slice-free + gate_pre 周/月惰性。"""

import numpy as np
import pandas as pd
import pytest

from custos.research import backtest_factors as bt
from custos.research.evolution.score_genome import compile_composite


def _bars(seed=0, n_codes=5, n_bars=700):
    rng = np.random.RandomState(seed)
    dates = [
        d.strftime("%Y-%m-%d") for d in pd.bdate_range("2019-01-02", periods=n_bars)
    ]
    out = {}
    for k in range(n_codes):
        close = 10 * np.exp(np.cumsum(rng.normal(0.0003, 0.028, n_bars)))
        op = close * (1 + rng.normal(0, 0.006, n_bars))
        out[f"6000{k:02d}"] = pd.DataFrame(
            {
                "date": dates,
                "open": op,
                "high": np.maximum(op, close) * 1.012,
                "low": np.minimum(op, close) * 0.988,
                "close": close,
                "volume": rng.uniform(1e6, 2e6, n_bars),
                "amount": rng.uniform(1e7, 2e7, n_bars),
            }
        )
    return out


EXPR = compile_composite(
    ["TS_RANK(close,20)", "MA(close,5)/MA(close,10)"], [2, 1], rank_window=250
)
GATES = ["j_low", "weekly_j_low", "j_low_weekly_resonance", "j_low_adx25"]
MONTHLY = [
    g for g in ("qn_three_red", "qn_weekly180_setup") if g in bt.ENTRY_GATES
]  # 读周+月 / 180 周线键


def _eager(monkeypatch):
    """关掉两项优化：slice-free 白名单只留原成员；gate_pre 急切物化。"""
    monkeypatch.setattr(bt, "_SLICE_FREE_SCORERS", frozenset({bt._sc_b1_pullback}))
    orig = bt._precompute_gate_series
    monkeypatch.setattr(
        bt,
        "_precompute_gate_series",
        lambda df, kdj=None: (
            lambda r: dict(r.materialize()) if r is not None else None
        )(orig(df, kdj)),
    )


@pytest.mark.parametrize("gate", GATES + MONTHLY)
def test_bit_identical(monkeypatch, gate):
    key = bt._preregister_scorer_expr(["--scorer-expr", EXPR])
    scorer = bt.SCORERS[key]
    assert scorer in bt._SLICE_FREE_SCORERS
    bars = _bars()
    kw = dict(
        scorer=scorer,
        entry_gate=bt.ENTRY_GATES[gate],
        collect_all=True,
        cost_bps=25,
        stop_mode="pct",
        stop_pct=5,
        trail_pct=0.08,
        amv_regime=None,
    )
    fast = bt.evaluate_trades(bars, **kw)
    with monkeypatch.context() as m:
        _eager(m)
        slow = bt.evaluate_trades(bars, **kw)
    assert fast == slow


def test_lazy_gate_pre_semantics():
    df = _bars(1, n_codes=1)["600000"]
    pre = bt._precompute_gate_series(df)
    assert isinstance(pre, bt._LazyGatePre)
    assert dict.__contains__(pre, "weekly_j") is False  # 未访问不算
    assert pre.get("weekly_j") is not None  # 访问即算
    eager = dict(bt._precompute_gate_series(df).materialize())
    assert set(pre.keys()) == set(eager)
    for k in eager:
        a, b = pre[k], eager[k]
        if isinstance(a, np.ndarray):
            assert np.array_equal(a, b, equal_nan=True)


def test_expr_scorer_point_query_matches_slice():
    key = bt._preregister_scorer_expr(["--scorer-expr", EXPR])
    sc = bt.SCORERS[key]
    df = _bars(2, n_codes=1)["600000"]
    pre = bt._SCORER_PRECOMPUTE[sc](df)
    for i in (260, 400, 699):
        assert sc(None, "600000", pre, i + 1) == sc(df.iloc[: i + 1], "600000", pre)
    assert sc(None, "600000", None, 10) is None  # 无 pre 不能点查询
