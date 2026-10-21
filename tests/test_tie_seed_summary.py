# -*- coding: utf-8 -*-
"""tie_seed_summary（TODO #107，v0.441）钉测：逐种子重放、一致性校验、配对对照。"""

from __future__ import annotations

import argparse

import pytest

from custos.research import backtest_factors as bt
from custos.research import tie_seed_summary as tss


def _trades(n_days: int = 10, per_day: int = 8, score: float = 0.0) -> list[dict]:
    """合成交易：逐日 per_day 笔同分（全平分，baseline 形态），收益按序号确定。"""
    out = []
    for d in range(n_days):
        for k in range(per_day):
            i = d * per_day + k
            out.append(
                {
                    "code": f"{i:06d}",
                    "entry_date": f"2022-01-{d + 1:02d}",
                    "exit_date": f"2022-01-{d + 2:02d}",
                    "ret": 0.02 if i % 3 else -0.01,
                    "risk_frac": 0.05,
                    "score": score,
                    "reason": "trail",
                }
            )
    return out


def _args(**kw) -> argparse.Namespace:
    base = dict(top_n=3, risk_pct=0.01, max_concurrent=2, max_pos_frac=0.5)
    base.update(kw)
    return argparse.Namespace(**base)


def _product(trades, scorer="baseline", seed0=True) -> dict:
    p = {
        "scorer": scorer,
        "start": "2022-01-01",
        "end": "2022-01-31",
        "trades": trades,
        "trade_summary": {"expectancy": 0.01},
    }
    if seed0:
        p["portfolio"] = bt.simulate_portfolio_topn(
            trades,
            top_n=3,
            risk_pct=0.01,
            max_concurrent=2,
            max_pos_frac=0.5,
            tie_seed=0,
        )
    return p


def test_per_seed_matches_engine_direct():
    tr = _trades()
    per = tss._portfolio_per_seed(tr, range(20), _args())
    direct = bt.simulate_portfolio_topn(
        tr, top_n=3, risk_pct=0.01, max_concurrent=2, max_pos_frac=0.5, tie_seed=7
    )
    assert per[7]["total_return"] == direct["total_return"]
    assert per[7]["n_taken"] == direct["n_taken"]


def test_tied_scores_vary_across_seeds():
    """全平分（baseline 恒 0 分）⇒ 换种子选中集应变——这正是 #107 要量的噪声。"""
    per = tss._portfolio_per_seed(_trades(), range(20), _args())
    rets = {s["total_return"] for s in per.values()}
    assert len(rets) > 1


def test_summarize_rows_and_verify():
    tr = _trades()
    rep = tss.summarize([_product(tr)], range(20), _args())
    assert rep["warns"] == []  # seed0 重放 == 产物 portfolio 块（一致性校验过）
    row = rep["rows"][0]
    assert row["scorer"] == "baseline" and row["n_trades"] == len(tr)
    assert row["cagr"]["min"] <= row["cagr"]["p50"] <= row["cagr"]["max"]
    assert not rep["pairs"]  # 单产物无配对


def test_verify_warns_on_mismatch():
    p = _product(_trades())
    p["portfolio"]["total_return"] = 999.0  # 篡改 ⇒ 校验必须报警
    rep = tss.summarize([p], range(3), _args())
    assert len(rep["warns"]) == 1 and "不一致" in rep["warns"][0]


def test_paired_comparison_math():
    a = _product(_trades(score=0.0), scorer="baseline")
    b = _product(_trades(score=1.0), scorer="reversal_quality")  # 同分但恒 1
    rep = tss.summarize([a, b], range(20), _args())
    assert len(rep["pairs"]) == 1
    pair = rep["pairs"][0]
    assert pair["a"] == "baseline" and pair["b"] == "reversal_quality"
    assert pair["n_seeds"] == 20
    assert 0.0 <= pair["b_win_share"] <= 1.0
    assert (
        pair["delta_total_return_min"]
        <= pair["delta_total_return_p50"]
        <= pair["delta_total_return_max"]
    )


def test_missing_score_fails():
    p = _product(_trades())
    for t in p["trades"]:
        del t["score"]
    with pytest.raises(ValueError, match="score"):
        tss.summarize([p], range(3), _args())


def test_render_md_shape():
    a = _product(_trades(score=0.0), scorer="baseline")
    b = _product(_trades(score=1.0), scorer="reversal_quality")
    rep = tss.summarize([a, b], range(5), _args())
    md = tss.render_md(rep)
    assert "baseline" in md and "逐种子配对对照" in md and "reversal_quality" in md
