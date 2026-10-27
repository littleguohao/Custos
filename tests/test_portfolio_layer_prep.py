"""v0.460（TODO #117 组合层准备）：simulate_portfolio_topn 的 exposure_fn 与 scan_portfolio_grid。"""

import pandas as pd
import pytest

from custos.research import backtest_factors as bt


def _cands(n_days=60, per_day=6):
    days = pd.bdate_range("2023-01-02", periods=n_days).strftime("%Y-%m-%d").tolist()
    out = []
    for k, d in enumerate(days):
        for j in range(per_day):
            ex = days[min(k + 3 + j % 4, n_days - 1)]
            out.append(
                {
                    "code": f"{j:06d}",
                    "entry_date": d,
                    "exit_date": ex,
                    "ret": ((k * 7 + j * 3) % 11 - 4) / 100.0,
                    "risk_frac": 0.05 + 0.01 * (j % 3),
                    "score": float((k + j) % 5),
                }
            )
    return out


def test_exposure_none_bit_identical():
    c = _cands()
    a = bt.simulate_portfolio_topn(c, top_n=3, max_concurrent=6)
    b = bt.simulate_portfolio_topn(c, top_n=3, max_concurrent=6, exposure_fn=None)
    assert a == b
    one = bt.simulate_portfolio_topn(
        c, top_n=3, max_concurrent=6, exposure_fn=lambda d: 1.0
    )
    assert {k: v for k, v in one.items()} == a


def test_zero_exposure_opens_nothing_and_half_caps():
    c = _cands()
    z = bt.simulate_portfolio_topn(
        c, top_n=3, max_concurrent=6, exposure_fn=lambda d: 0.0
    )
    assert z["n_taken"] == 0
    full = bt.simulate_portfolio_topn(
        c, top_n=6, max_concurrent=20, max_pos_frac=0.2, risk_pct=1.0
    )
    half = bt.simulate_portfolio_topn(
        c,
        top_n=6,
        max_concurrent=20,
        max_pos_frac=0.2,
        risk_pct=1.0,
        exposure_fn=lambda d: 0.5,
    )
    assert 0 < half["n_taken"] < full["n_taken"]


def test_regime_dependent_exposure():
    c = _cands()
    first_half = sorted({t["entry_date"] for t in c})[30]
    rg = bt.simulate_portfolio_topn(
        c,
        top_n=3,
        max_concurrent=6,
        exposure_fn=lambda d: 0.0 if d < first_half else 1.0,
    )
    taken: list = []
    bt.simulate_portfolio_topn(
        c,
        top_n=3,
        max_concurrent=6,
        taken_out=taken,
        exposure_fn=lambda d: 0.0 if d < first_half else 1.0,
    )
    assert rg["n_taken"] > 0 and all(t["entry_date"] >= first_half for t in taken)


def test_equal_weight_recipe():
    """等权 = risk_pct 取大 + max_pos_frac=1/N：每笔名义仓位恒为 1/N（不随 risk_frac 变）。"""
    c = _cands()
    taken: list = []
    bt.simulate_portfolio_topn(
        c, top_n=2, max_concurrent=4, risk_pct=1.0, max_pos_frac=0.25, taken_out=taken
    )
    assert taken and min(1.0 / max(t["risk_frac"], 0.02) for t in taken) > 0.25


def test_scan_grid_seed_band():
    c = [dict(t, score=0.0) for t in _cands()]  # 全平分 ⇒ 结果只由平分种子决定
    rows = bt.scan_portfolio_grid(
        c,
        [
            {"name": "narrow", "top_n": 1, "max_concurrent": 2},
            {"name": "wide", "top_n": 6, "max_concurrent": 20, "max_pos_frac": 0.05},
            {
                "name": "half",
                "top_n": 6,
                "max_concurrent": 20,
                "exposure_fn": lambda d: 0.5,
            },
        ],
        tie_seeds=tuple(range(8)),
    )
    assert [r["name"] for r in rows] == ["narrow", "wide", "half"]
    nar = rows[0]["total_return"]
    assert (
        nar["min"] <= nar["p50"] <= nar["max"] and nar["min"] < nar["max"]
    )  # 平分噪声可见
    assert rows[2]["exposure_fn"] is True and "exposure_fn" not in rows[2]["params"]
    with pytest.raises(TypeError):
        bt.scan_portfolio_grid(c, [{"bogus": 1}])
