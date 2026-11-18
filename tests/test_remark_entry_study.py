# -*- coding: utf-8 -*-
"""R52 remark_entry_study：零假设校准两连测 + 回归哨兵 + 臂口径 + 护栏。"""

from __future__ import annotations

import argparse
import random

import pandas as pd

from custos.research import remark_entry_study as res


def _world(seed: int, *, boost_w1: float = 0.0, n_codes: int = 120, per: int = 12):
    rng = random.Random(seed)
    out = {}
    for seg in ("mining_a", "mining_b"):
        recs = []
        years = (2005, 2010, 2015) if seg == "mining_a" else (2022, 2023, 2024, 2025)
        for c in range(n_codes):
            for k in range(per):
                waits = {
                    cond: (rng.randint(1, 8) if rng.random() < 0.8 else None)
                    for kind, cond in res.ARMS.values()
                    if kind == "wait"
                }
                o = {d: rng.gauss(0.5, 8.0) for d in range(res.MAX_DELAY + 1)}
                w1 = waits["w_green_shrink"]
                if boost_w1 and w1 is not None:
                    o[w1] += boost_w1
                recs.append(
                    {
                        "code": f"{c:06d}",
                        "i": 100 + 12 * k,
                        "date": f"{years[k % len(years)]}-06-{1 + k % 28:02d}",
                        "_seg": seg,
                        "year": years[k % len(years)],
                        "out": o,
                        "wait": waits,
                        "flag": {
                            cond: rng.random() < 0.3
                            for kind, cond in res.ARMS.values()
                            if kind == "filter"
                        },
                    }
                )
        out[seg] = recs
    return out


def test_null_calibration_two_runs_no_candidate():
    """零假设（条件与收益无关）：两次独立种子 8 臂都不应出 candidate。"""
    for seed in (1, 2):
        arms = res.judge(_world(seed), n_random=60, seed=seed)
        assert not [k for k, v in arms.items() if v["verdict"] == "candidate"], {
            k: v["verdict"] for k, v in arms.items()
        }


def test_sentinel_true_effect_detected():
    """回归哨兵：W1 等到条件后进场的收益系统性 +6pp ⇒ W1 两窗 C2 过、C4 过。"""
    arms = res.judge(_world(3, boost_w1=6.0), n_random=60, seed=3)
    w1 = arms["W1"]
    assert w1["C1"] and w1["C2"] and w1["c4"]["state"] == "confirmed_pass", w1
    assert arms["F1"]["verdict"] != "candidate"


def test_arm_delay_semantics():
    rec = {
        "wait": {"w_fractal": 3, "w_green_shrink": None, "w_zero": None, "w_stable": 2},
        "flag": {
            "s_vol_down": True,
            "s_green_grow": False,
            "s_dead_cross": False,
            "s_far_zero": False,
        },
    }
    assert res.arm_delay(rec, "B0") == 0
    assert res.arm_delay(rec, "W2") == 3 and res.arm_delay(rec, "W1") is None
    assert res.arm_delay(rec, "F1") is None and res.arm_delay(rec, "F2") == 0


def test_code_records_never_reads_past_segment_end(monkeypatch):
    """K 线截到段末：段外剧变不改变任何延后档读数。"""
    import numpy as np

    rng = np.random.default_rng(0)
    n = 700
    c = 10 * np.cumprod(1 + rng.normal(0, 0.025, n))
    df = pd.DataFrame(
        {
            "date": [str(d)[:10] for d in pd.bdate_range("2014-01-01", periods=n)],
            "open": c * 1.002,
            "high": c * 1.02,
            "low": c * 0.98,
            "close": c,
            "volume": rng.lognormal(13, 0.4, n),
        }
    )
    regime = {d: "做多" for d in df["date"]}
    a = res.code_records("600001", df, regime, "mining_a")
    df2 = df.copy()
    cut = df2.index[df2["date"] > "2016-12-31"]
    df2.loc[cut, ["open", "high", "low", "close"]] *= 3
    b = res.code_records("600001", df2, regime, "mining_a")
    assert a and a == b
    assert all(x["date"] <= "2016-12-31" for x in a)


def test_empty_guard(tmp_path):
    rc = res.main(
        ["--tag", "e", "--out-dir", str(tmp_path)],
        recs_fn=lambda a: {"mining_a": [], "mining_b": []},
    )
    assert rc == 2 and not list(tmp_path.iterdir())


def world_report(tmp_path):
    """result_digest 夹具。"""
    args = res._build_parser().parse_args(
        ["--tag", "t", "--n-random", "20", "--out-dir", str(tmp_path)]
    )
    args.cmdline = "remark_entry_study test"
    _ = argparse
    return res.run_study(args, recs_fn=lambda a: _world(5, n_codes=40))


def _flat(w):
    recs = [x for s in res.SEGS for x in w[s]]
    return recs


def test_c4_random_pool_draws_by_cluster():
    """v0.545：随机臂整簇抽签——簇结构变了，随机池随之变化（逐笔独立时两者相同）。"""
    recs = _flat(_world(5, n_codes=60, per=8))
    same = [0] * len(recs)
    diff = list(range(len(recs)))
    a = res.random_pool(recs, diff, "W1", 20, 9)
    b = res.random_pool(recs, [i // 4 for i in range(len(recs))], "W1", 20, 9)
    assert a["pool"] != b["pool"]
    # 全部同簇 ⇒ 每臂只有 1 簇，过不了门（簇数 ≥30），池为空且评估到上限 10×N
    c = res.random_pool(recs, same, "W1", 20, 9)
    assert c["pool"] == [] and c["evaluated"] == 200


def test_c4_pool_fills_to_n_gate_passers():
    recs = _flat(_world(6, n_codes=60, per=8))
    p = res.random_pool(recs, list(range(len(recs))), "F1", 30, 1)
    assert len(p["pool"]) == 30 and p["evaluated"] >= 30


def test_edge_signal_dropped_from_both_arms():
    """段末：等待臂延后档越界的信号整笔剔除，B0 同剔（保配对）。"""
    w = _world(7, n_codes=40, per=6)
    x = w["mining_b"][0]
    x["edge"] = True
    x["out"][0] = 99.0
    seen = {}

    def fake_judge(recs, **kw):
        seen["recs"] = recs
        return {a: {"verdict": "untested"} for a in res.ARMS}

    import custos.research.remark_entry_study as m

    orig = m.judge
    m.judge = fake_judge
    try:
        rep = m.run_study(_args(), recs_fn=lambda _a: w)
    finally:
        m.judge = orig
    assert rep["n_edge_dropped"]["mining_b"] == 1
    assert all(99.0 not in r["out"].values() for r in seen["recs"]["mining_b"])


def _args():
    a = res._build_parser().parse_args(["--tag", "t", "--n-random", "5"])
    a.cmdline = "remark_entry_study test"
    return a
