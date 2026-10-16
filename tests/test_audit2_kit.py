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


def test_hash_ignores_tag_outdir_timeout():
    a = argparse.Namespace(x=1, tag="r40_b1", out_dir="", timeout=0)
    b = argparse.Namespace(x=1, tag="r40_b1_retry", out_dir="/tmp/x", timeout=900)
    assert jb.config_hash("t", a) == jb.config_hash("t", b)
    c = argparse.Namespace(x=2, tag="r40_b1", out_dir="", timeout=0)
    assert jb.config_hash("t", a) != jb.config_hash("t", c)
