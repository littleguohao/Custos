"""成簇随机入场臂（v0.399，owner 2026-10-16 拍板 3b）：零假设校准 + 形状钉测。

候选信号成簇（同票连续多日触发），逐 bar 独立抽样的随机臂不成簇 ⇒ 零假设方差低估、
q95 偏低、假过线。这里用「候选本身也是随机簇」的零假设场景校准：成簇臂的假过线率应
≈名义 5%，逐 bar 臂应显著更高（回归哨兵——把成簇换回逐 bar 必须被抓住）。
"""

import random
import statistics

from custos.research import bear_regime_study as brs
from custos.research import criteria_kit as kit

N_CODES, N_BARS, BLOCK = 40, 300, 10


def _world(seed):
    rng = random.Random(seed)
    pool, per_code, ret = [], {}, {}
    for c in range(N_CODES):
        code = f"{c:06d}"
        per_code[code] = {"scores": {}}
        mus = [rng.gauss(0, 0.03) for _ in range(N_BARS // BLOCK + 1)]
        for i in range(N_BARS):
            d = f"d{i:04d}"
            pool.append((code, i, d))
            per_code[code]["scores"][d] = 1.0
            ret[(code, i)] = mus[i // BLOCK] + rng.gauss(0, 0.005)
    return pool, per_code, ret


def _mean_ret(entries, ret):
    return statistics.mean(ret[(e["code"], e["i"])] for e in entries)


def _false_pass_rate(clustered: bool, trials=120, n_arms=60, seed=0):
    rng = random.Random(seed)
    shapes = [list(range(BLOCK))] * 6  # 6 簇 × 10 bar = 60 笔
    n = sum(len(x) for x in shapes)
    hits = 0
    for t in range(trials):
        pool, per_code, ret = _world(seed * 1000 + t)
        cand = brs.random_entries(per_code, {}, n, rng, pool=pool, shapes=shapes)
        arms = [
            _mean_ret(
                brs.random_entries(
                    per_code,
                    {},
                    n,
                    rng,
                    pool=pool,
                    shapes=shapes if clustered else None,
                ),
                ret,
            )
            for _ in range(n_arms)
        ]
        hits += _mean_ret(cand, ret) > kit.q95(arms)
    return hits / trials


def test_clustered_arms_calibrated_and_iid_caught():
    fp_cluster = _false_pass_rate(True)
    fp_iid = _false_pass_rate(False)
    # v0.409（审计 2 T4 整簇放置后）实测：成簇臂 7.5%（名义 ≈4.9%，120 次 SE≈2.4%）/ 逐 bar 23.3%
    assert fp_cluster <= 0.12, fp_cluster
    assert fp_iid >= 0.18, fp_iid  # 回归哨兵：逐 bar 臂在成簇世界里必假过线
    assert fp_iid > 2 * fp_cluster


def test_cluster_shapes_and_draw():
    sigs = [{"code": "000001", "i": i, "date": f"d{i}"} for i in (10, 11, 13, 40)]
    sigs += [{"code": "000002", "i": 5, "date": "d5"}]
    shapes = brs.cluster_shapes(sigs)
    assert sorted(map(tuple, shapes)) == [(0,), (0,), (0, 1, 3)]
    pool, _pc, _r = _world(1)
    got = brs.clustered_draw(pool, shapes, random.Random(3))
    assert (
        len(got) == len(set(got)) and len(got) == 5
    )  # 无放回；整簇放得下才收（v0.409 T4）


def test_iid_path_unchanged():
    pool, per_code, _r = _world(2)
    a = brs.random_entries(per_code, {}, 30, random.Random(9), pool=pool)
    b = [t for t in random.Random(9).sample(pool, 30)]
    assert [(e["code"], e["i"]) for e in a] == [(c, i) for c, i, _d in b]


def test_long_cluster_fits_whole():
    """T4：母体由每段 12 根连续 bar 组成时，长 8 的簇应整簇放进（不截短）。"""
    pool = [
        (f"{c:06d}", i, f"d{i}")
        for c in range(10)
        for i in list(range(0, 12)) + list(range(50, 62))
    ]
    got = brs.clustered_draw(pool, [list(range(8))] * 6, random.Random(0))
    by = {}
    for c, i, _d in got:
        by.setdefault((c, i // 50), []).append(i)
    assert len(got) == 48 and all(len(v) == 8 for v in by.values())
