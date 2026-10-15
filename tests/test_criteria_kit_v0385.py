"""判据件 v0.385（审计 CK-2/3/5/6）：置换判定线、批次判定线、跨段簇键、NaN、MDE k。"""

import math
import random

import pytest

from custos.research import criteria_kit as kit
from custos.research import power_mde as pm
from custos.research import strategy_grid as sg


def test_perm_threshold_order_stat():
    pool = list(range(1, 51))  # 50 臂
    assert kit.q95(pool) == 49  # 第 2 大
    assert kit.perm_threshold(list(range(200)), 0.05) == 190  # 第 10 大
    assert kit.q95([0.3]) == 0.3 and kit.q95([]) is None
    assert kit.q95([1.0, float("nan"), 2.0]) == 2.0


def test_null_false_pass_at_most_alpha():
    rng = random.Random(0)
    for n in (20, 50, 100):
        hit = 0
        trials = 4000
        for _ in range(trials):
            pool = [rng.gauss(0, 1) for _ in range(n)]
            hit += rng.gauss(0, 1) > kit.q95(pool)
        rate = hit / trials
        # k=⌊0.05(n+1)⌋：n=20→1/21≈4.8%，n=50→2/51≈3.9%，n=100→5/101≈5.0%
        assert rate <= 0.05 + 3 * math.sqrt(0.05 * 0.95 / trials), (n, rate)


def test_batch_threshold():
    pool = list(range(1, 201))
    assert kit.batch_threshold(pool, 1) == kit.q95(pool)
    assert kit.batch_threshold(pool, 4) == 199  # α=0.0125 ⇒ k=⌊2.51⌋=2
    with pytest.raises(ValueError):
        kit.batch_threshold(pool, 0)


def test_cluster_key_includes_segment():
    recs = [
        {"code": "000001", "i": 700, "date": "2008-03-03", "_seg": "mining_a"},
        {"code": "000001", "i": 702, "date": "2023-05-05", "_seg": "mining_b"},
    ]
    a, b = kit.cluster_ids(recs)
    assert a != b
    same = [dict(r, _seg="x") for r in recs]
    assert len(set(kit.cluster_ids(same))) == 1  # 同段仍按 i 判邻（旧行为）


def test_nan_objective_is_none():
    row = {"margin": 0.1, "expectancy_R": float("nan"), "ret_over_dd": 1.0}
    assert sg.objective_of(row, (1.0, 0.0, 0.0)) is None


def test_mde_default_80pct_power():
    assert pm.MDE_K == 2.8
    se = pm.se_margin(0.4, 500, 0.5, 1.0)
    assert pm.mde(0.4, 500, 0.5) == pytest.approx(2.8 * se)
