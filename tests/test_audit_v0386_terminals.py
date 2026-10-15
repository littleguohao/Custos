"""审计 v0.386 单项修复钉测：随机剔除对等、单窗挖掘拒判定段、regime 预热、阈值严格。"""

import random

import pytest

from custos.research import backtest_0amv_bear_regime as b0
from custos.research import backtest_factors as bt
from custos.research import score_evolution_study as ses
from custos.research import score_filter_study as sfs


def test_random_filter_drops_same_count_as_factor_arm():
    items = [
        {"code": f"{k:06d}", "fval": (k if k % 3 == 0 else None)} for k in range(300)
    ]
    for x in (0.1, 0.3, 0.5):
        n_f = len(items) - len(sfs._filter_day(items, x))
        n_r = len(items) - len(sfs._random_filter_day(items, x, random.Random(1)))
        assert n_f == n_r
    kept = sfs._random_filter_day(items, 0.5, random.Random(2))
    assert all(it in kept for it in items if it["fval"] is None)  # 缺值票全保留


def test_single_window_rejects_judgment_segment():
    with pytest.raises(ValueError):
        ses._validate_single_window(ses.Window("2018-01-01", "2019-12-31"))
    ses._validate_single_window(ses.Window("2022-01-01", "2023-12-31"))


def test_regime_warm_from_ledger_start(monkeypatch):
    recs = [
        {"date": "2016-01-04", "change_pct": -3.0},
        {"date": "2016-01-15", "change_pct": 1.0},
    ]
    monkeypatch.setattr(
        bt,
        "_amv_ledger_records",
        lambda since, after, *a, **k: [r for r in recs if r["date"] >= since],
    )
    reg = bt.load_amv_regime(since="2016-01-10")
    assert reg == {"2016-01-15": "空头"}  # 原实现截断重放 ⇒ 「中性」
