"""格子选中子集读数（v0.407 审计 2 P0-1）：打分比较只能读 top-N 选中子集。"""

import json
import random

from custos.research import backtest_factors as bt
from custos.research import strategy_grid as sg


def _cell_file(tmp_path, name, score_fn, top_n=3):
    rng = random.Random(0)
    trades = []
    for d in range(1, 25):
        for k in range(10):
            ret = rng.uniform(-0.06, 0.08)
            trades.append(
                {
                    "code": f"{k:06d}",
                    "entry_date": f"2023-02-{d:02d}" if d <= 28 else "",
                    "exit_date": f"2023-03-{d:02d}",
                    "ret": round(ret, 4),
                    "reason": "stop",
                    "risk_frac": 0.05,
                    "r_multiple": ret / 0.05,
                    "holding": 3,
                    "score": score_fn(k, ret),
                }
            )
    pf = bt.simulate_portfolio_topn(trades, top_n=top_n)
    payload = {
        "trade_summary": bt.summarize_trades(trades),
        "trades": trades,
        "portfolio": pf,
    }
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_full_pool_invariant_selected_not(tmp_path):
    cell = {"scorer": "x", "gate": "j_low", "exit": "e", "params": {}}
    good = _cell_file(
        tmp_path, "good", lambda k, r: r + random.Random(k).gauss(0, 0.04)
    )  # 带噪正序
    bad = _cell_file(
        tmp_path, "bad", lambda k, r: -r + random.Random(k).gauss(0, 0.04)
    )  # 带噪反序
    fg, fb = sg.load_cell_row(cell, good, False), sg.load_cell_row(cell, bad, False)
    assert fg["margin"] == fb["margin"]  # 全池与 scorer 无关（审计 P0-1 实锤）
    sgd, sbd = (
        sg.load_cell_selected_row(cell, good, False),
        sg.load_cell_selected_row(cell, bad, False),
    )
    assert sgd["reading_basis"] == "selected_topn" and sgd["margin"] > sbd["margin"]
    assert sgd["n"] < fg["n"]


def test_selected_unavailable_fail_closed(tmp_path):
    cell = {"scorer": "x", "gate": "j_low", "exit": "e", "params": {}}
    p = tmp_path / "s.json"
    p.write_text(
        json.dumps(
            {
                "trade_summary": {"n": 10, "win_rate": 0.5, "payoff_ratio": 2.0},
                "portfolio": {"mode": "topn", "top_n": 3},
            }
        ),
        encoding="utf-8",
    )
    r = sg.load_cell_selected_row(cell, p, False)
    assert r["margin"] is None and r["reading_basis"] == "selected_unavailable"


def test_daily_selected_keys_v0416(tmp_path):
    """v0.416：cell 行附带每日 top-N 选中集读数（R36 frozen-read 池用），不受并发约束。"""
    cell = {"scorer": "x", "gate": "j_low", "exit": "e", "params": {}}
    good = _cell_file(tmp_path, "g", lambda k, r: r + random.Random(k).gauss(0, 0.04))
    bad = _cell_file(tmp_path, "b", lambda k, r: -r + random.Random(k).gauss(0, 0.04))
    rg, rb = (
        sg.load_cell_selected_row(cell, good, False),
        sg.load_cell_selected_row(cell, bad, False),
    )
    assert (
        rg["n_selected"] == 24 * 3 and rg["n_selected"] > rg["n"]
    )  # 24 日 × top3 > 组合成交
    assert rg["sel_margin"] > rb["sel_margin"]
    p = tmp_path / "s.json"
    p.write_text(
        json.dumps({"trade_summary": {"n": 1}, "portfolio": {"mode": "topn"}}),
        encoding="utf-8",
    )
    assert sg.load_cell_selected_row(cell, p, False)["sel_margin"] is None
