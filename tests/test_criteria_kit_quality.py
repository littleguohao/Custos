# -*- coding: utf-8 -*-
"""criteria_kit 质量门（v0.501）：簇稳健比率界 / 配对 margin 差簇 bootstrap / 近年逐年稳定性。

含零假设校准（成簇世界里 95% 下界越线率接近名义 2.5%）与回归哨兵（把簇拆成逐笔
⇒ 越线率显著偏高——证明簇稳健口径确实在起作用）。
"""

from __future__ import annotations

import numpy as np

from custos.research import criteria_kit as kit


def _clustered_null(rng, k=120, size=8, p=0.2):
    """成簇零假设：每簇一次伯努利、簇内完全相关（信号相邻几天同进同出）。"""
    lens = rng.integers(1, 2 * size, size=k)
    hit = rng.random(k) < p
    return lens.astype(float), (hit * lens).astype(float)


def test_ratio_bounds_basic():
    lift, lo, hi = kit.cluster_ratio_bounds([10, 10, 10], [5, 4, 6], 0.25)
    assert abs(lift - 2.0) < 1e-12 and lo < lift < hi
    assert kit.cluster_ratio_bounds([10], [5], 0.25)[1:] == (None, None)
    assert kit.cluster_ratio_bounds([], [], 0.25) == (None, None, None)
    assert kit.cluster_ratio_bounds([3], [1], None) == (None, None, None)


def test_ratio_bounds_null_calibration_clustered():
    """成簇零假设（真 lift = 1）：下界 > 1 的比例 ≈ 名义 2.5%（容 ≤ 6%）。"""
    rng = np.random.default_rng(7)
    over = 0
    runs = 400
    for _ in range(runs):
        n, h = _clustered_null(rng)
        _l, lo, _h = kit.cluster_ratio_bounds(n, h, 0.2)
        over += int(lo is not None and lo > 1)
    assert over / runs <= 0.06, over


def test_ratio_bounds_sentinel_iid_overrejects():
    """哨兵：同一成簇数据按逐笔（每笔一簇）算界 ⇒ 越线率显著高于名义（簇口径才是对的）。"""
    rng = np.random.default_rng(7)
    over_iid = 0
    runs = 400
    for _ in range(runs):
        n, h = _clustered_null(rng)
        rows_n = np.ones(int(n.sum()))
        rows_h = np.concatenate([np.full(int(ln), hh / ln) for ln, hh in zip(n, h)])
        _l, lo, _h = kit.cluster_ratio_bounds(rows_n, rows_h, 0.2)
        over_iid += int(lo is not None and lo > 1)
    assert over_iid / runs >= 0.10, over_iid


def test_ratio_bounds_detects_planted_edge():
    rng = np.random.default_rng(3)
    n, h = _clustered_null(rng, k=300, p=0.4)  # 真 lift = 2
    _l, lo, _h = kit.cluster_ratio_bounds(n, h, 0.2)
    assert lo is not None and lo > 1.2


def test_cluster_sums():
    a, b = kit.cluster_sums([3, 1, 3, 2], [1, 1, 1, 1], [1, 0, 1, 0])
    assert a.tolist() == [1, 1, 2] and b.tolist() == [0, 0, 2]


def _margin_ref(rets):
    r = np.asarray(rets, dtype=float)
    w, l = r[r > 0], -r[r < 0]
    return (len(w) / len(r)) - 1 / (1 + w.mean() / l.mean())


def test_margin_delta_point_matches_summary_formula():
    ra = [0.1, -0.05, 0.2, -0.02, 0.0]
    rb = [0.05, -0.05, 0.1, -0.08, 0.01]
    out = kit.margin_delta_bounds(ra, [0, 1, 2, 3, 4], rb, [0, 1, 2, 3, 4], seed=1)
    assert abs(out["delta"] - (_margin_ref(ra) - _margin_ref(rb))) < 1e-12
    assert out["n_clusters"] == 5 and out["lo"] <= out["delta"] <= out["hi"]


def test_margin_delta_null_calibration_paired():
    """配对零假设（两组出场同分布、同一批信号簇）：下界 > 0 的比例 ≤ 6%。"""
    rng = np.random.default_rng(11)
    over = 0
    runs = 120
    for t in range(runs):
        k = 150
        lens = rng.integers(1, 6, size=k)
        cid = np.repeat(np.arange(k), lens)
        base = np.repeat(rng.normal(0.0, 0.08, size=k), lens)  # 簇内共因
        ra = base + rng.normal(0, 0.03, size=len(cid))
        rb = base + rng.normal(0, 0.03, size=len(cid))
        out = kit.margin_delta_bounds(ra, cid, rb, cid, seed=t, n_boot=300)
        over += int(out["lo"] is not None and out["lo"] > 0)
    assert over / runs <= 0.06, over


def test_yearly_stability_rules():
    y = lambda pt, lo, hi, n: {"point": pt, "lo": lo, "hi": hi, "n_clusters": n}  # noqa: E731
    # 4 年可判、3 同向 ⇒ 过
    ok = kit.yearly_stability(
        {
            2022: y(1.5, 1.1, 2, 40),
            2023: y(1.3, 0.9, 1.8, 40),
            2024: y(0.9, 0.6, 1.2, 40),
            2025: y(1.4, 1.0, 1.9, 50),
        },
        null=1.0,
    )
    assert ok["ok"] and ok["judged"] == 4 and ok["agree"] == 3
    # 2 同 2 反 ⇒ 不过
    bad = kit.yearly_stability(
        {
            2022: y(1.5, 1.1, 2, 40),
            2023: y(0.8, 0.5, 1.1, 40),
            2024: y(0.9, 0.6, 1.2, 40),
            2025: y(1.4, 1.0, 1.9, 50),
        },
        null=1.0,
    )
    assert not bad["ok"]
    # 稀疏年只禁显著反向：全稀疏且无反向 ⇒ 过（evidence=none）
    sparse = kit.yearly_stability(
        {2022: y(3.0, 0.5, 5, 5), 2024: y(2.0, 0.2, 4, 3)}, null=1.0
    )
    assert sparse["ok"] and sparse["evidence"] == "none"
    # 稀疏年显著反向（上界 < null）⇒ 不过，哪怕可判年全同向
    rev = kit.yearly_stability(
        {
            2022: y(1.5, 1.1, 2, 40),
            2023: y(1.5, 1.1, 2, 40),
            2024: y(1.5, 1.1, 2, 40),
            2025: y(0.2, 0.05, 0.6, 4),
        },
        null=1.0,
    )
    assert not rev["ok"] and rev["reversed_years"] == [2025]
    # str 年份键、2026 不计
    s = kit.yearly_stability(
        {"2022": y(1.5, 1.1, 2, 40), "2026": y(0.1, 0.0, 0.2, 99)}, null=1.0
    )
    assert s["ok"] and "2026" not in s["years"]


def test_recent_years_inside_mining_b():
    from custos.research import load_window as lw

    a, b = lw.SEG_MINING_B
    for yy in kit.RECENT_YEARS:
        assert a <= f"{yy}-01-01" and f"{yy}-12-31" <= b
