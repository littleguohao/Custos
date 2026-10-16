"""审计 2（v0.406）判据件修复钉测。"""

import argparse

from custos.research import criteria_kit as kit
from custos.research import judgment_batch as jb
from custos.research.m2_stop_sweep import _realized_view


def _t(ret, reason):
    return {
        "code": "000001",
        "entry_date": "2023-01-03",
        "exit_date": "2023-01-09",
        "ret": ret,
        "reason": reason,
        "risk_frac": 0.05,
        "r_multiple": ret / 0.05,
        "holding": 4,
    }


def test_realized_view_all_unrealized_nulls_stats():
    s = {
        "n": 2,
        "win_rate": 0.5,
        "payoff_ratio": 6.0,
        "expectancy_R": 2.5,
        "_trades": [_t(0.3, "open_end"), _t(-0.05, "open_end")],
    }
    v = _realized_view(s)
    assert v["n"] == 0 and v["win_rate"] is None and v["payoff_ratio"] is None
    assert v["expectancy_R"] is None


def test_min_pool_counts_valid_only():
    nan = float("nan")
    assert (
        kit.c4_state_of([nan] * 58 + [10.0, 9.0], min_pool=20, plan_delta=0.5)
        == "provisional"
    )
    assert kit.c4_state_of([nan] * 60, min_pool=20, plan_delta=0.5) == "indeterminate"


def test_hash_ignores_tag_outdir_but_not_timeout():
    a = argparse.Namespace(x=1, tag="r40_b1", out_dir="", timeout=0)
    b = argparse.Namespace(x=1, tag="r40_b1_retry", out_dir="/tmp/x", timeout=0)
    assert jb.config_hash("t", a) == jb.config_hash("t", b)
    # 审计 3 B-1：timeout 决定格子 ran/failed ⇒ 影响读数，必须进哈希
    t = argparse.Namespace(x=1, tag="r40_b1", out_dir="", timeout=900)
    assert jb.config_hash("t", a) != jb.config_hash("t", t)
    c = argparse.Namespace(x=2, tag="r40_b1", out_dir="", timeout=0)
    assert jb.config_hash("t", a) != jb.config_hash("t", c)


def test_pick_practice_loader_today_does_not_raise():
    from custos.core.paths import cn_now
    from custos.research import pick_practice as pp

    loader = pp._make_loader(
        [{"date": cn_now().strftime("%Y-%m-%d"), "code": "600000"}]
    )
    assert callable(loader)  # 审计 3 A-1：原 count_for_start(今天) 抛 ValueError


def test_base_equals_live_bidirectional(monkeypatch):
    from custos.pipeline.screening import score_candidates as sc
    from custos.research import family_lattice_study as fl

    assert fl._base_equals_live() is True
    extra = {**sc.resolve_tech_weights(None), "__new_leg__": 3}
    monkeypatch.setattr(sc, "resolve_tech_weights", lambda w=None: extra)
    assert fl._base_equals_live() is False


def test_selected_row_replays_recorded_max_pos(tmp_path):
    import json
    import random

    from custos.research import backtest_factors as bt
    from custos.research import strategy_grid as sg

    rng = random.Random(1)
    trades = [
        {
            "code": f"{k:06d}",
            "entry_date": f"2023-01-{d:02d}",
            "exit_date": f"2023-01-{d:02d}",
            "ret": round(rng.uniform(-0.05, 0.07), 4),
            "reason": "stop",
            "risk_frac": 0.01,
            "r_multiple": 1.0,
            "holding": 1,
            "score": rng.random(),
        }
        for d in range(1, 25)
        for k in range(8)
    ]
    cell = {"scorer": "x", "gate": "j_low", "exit": "e", "params": {}}
    outs: list = []
    for mpf in (0.05, 0.20):
        pf = bt.simulate_portfolio_topn(
            trades, top_n=3, max_pos_frac=mpf, max_concurrent=3
        )
        assert pf["max_pos_frac"] == mpf
        p = tmp_path / f"c{mpf}.json"
        p.write_text(
            json.dumps(
                {
                    "trade_summary": bt.summarize_trades(trades),
                    "trades": trades,
                    "portfolio": pf,
                }
            ),
            encoding="utf-8",
        )
        r = sg.load_cell_selected_row(cell, p, False)
        assert r["max_pos_frac_assumed"] is False
        outs.append(r["n"])
