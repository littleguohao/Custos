"""组合层平分破序与准入（v0.384，审计 E1/E2/E5）。"""

import random

from custos.research import backtest_factors as bt


def _c(code, day, ret, score=0.0, rf=0.05):
    return {
        "code": code,
        "entry_date": f"2023-01-{day:02d}",
        "exit_date": f"2023-01-{day + 3:02d}",
        "ret": ret,
        "risk_frac": rf,
        "score": score,
    }


def _pool():
    rng = random.Random(1)
    return [
        _c(f"{k:06d}", d, rng.uniform(-0.1, 0.1))
        for d in range(2, 20, 4)
        for k in range(30)
    ]


def test_topn_order_independent():
    pool = _pool()
    a = bt.simulate_portfolio_topn(pool, top_n=3)
    rev = bt.simulate_portfolio_topn(list(reversed(pool)), top_n=3)
    sh = pool[:]
    random.Random(7).shuffle(sh)
    b = bt.simulate_portfolio_topn(sh, top_n=3)
    assert a["final_equity"] == rev["final_equity"] == b["final_equity"]


def test_topn_not_code_ordered_and_seed_matters():
    pool = _pool()
    taken: list = []
    bt.simulate_portfolio_topn(pool, top_n=3, taken_out=taken)
    first_day = sorted(t["code"] for t in taken if t["entry_date"] == "2023-01-02")
    assert first_day != ["000000", "000001", "000002"], "平分仍按代码号取前"
    eq = {
        bt.simulate_portfolio_topn(pool, top_n=3, tie_seed=s)["final_equity"]
        for s in range(8)
    }
    assert len(eq) > 1


def test_portfolio_same_day_order_independent():
    pool = _pool()
    a = bt.simulate_portfolio(pool, max_concurrent=3)
    b = bt.simulate_portfolio(list(reversed(pool)), max_concurrent=3)
    assert a["final_equity"] == b["final_equity"]


def test_zero_risk_frac_kept_and_none_counted():
    pool = [
        _c("000001", 2, -0.3, rf=0.0),
        _c("000002", 10, 0.1),
        _c("000003", 10, 0.1, rf=None),
    ]
    out = bt.simulate_portfolio(pool, max_concurrent=5)
    assert out["n_taken"] == 2 and out["n_no_risk"] == 1


def test_none_score_ranked_last():
    day = [_c("000001", 2, -0.2, score=None), _c("000002", 2, 0.3, score=-5.0)]
    taken: list = []
    bt.simulate_portfolio_topn(day, top_n=1, taken_out=taken)
    assert [t["code"] for t in taken] == ["000002"]
