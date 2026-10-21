"""R48 tail_feature_study（v0.439）：特征派生、统计口径、端到端、零假设校准两连测 + 逐笔置换哨兵。"""

import hashlib
import math

import numpy as np
import pandas as pd
import pytest

import helpers_null as hn
from custos.research import tail_feature_study as tf

SPANS = {
    "mining_a": ("2000-01-01", "2016-12-31"),
    "mining_b": ("2022-01-01", "2026-09-04"),
}


def _u(*key) -> float:
    return (
        int.from_bytes(
            hashlib.md5("|".join(map(str, key)).encode()).digest()[:4], "little"
        )
        / 2**32
    )


def _world(n_codes=40, burst=4, gap_in=3, gap_out=25):
    """多票、成簇信号：每簇 burst 个信号相隔 gap_in 根（同簇），簇间 gap_out 根。"""
    spec = {}
    for (s, e), (d0, d1) in (
        (SPANS["mining_a"], ("2012-01-02", "2016-12-30")),
        (SPANS["mining_b"], ("2022-01-03", "2026-09-04")),
    ):
        days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(d0, d1)]
        n = len(days)
        close = [10.0 + 0.001 * i for i in range(n)]
        df = pd.DataFrame(
            {
                "date": days,
                "close": close,
                "low": [c - 0.2 for c in close],
                "volume": 1e6,
            }
        )
        packs = {}
        for k in range(n_codes):
            sigs, scores = [], {}
            i = 300 + (k * 7) % gap_out
            b = 0
            while i < n - 2:
                for j in range(burst):
                    ii = i + j * gap_in
                    if ii < n - 2:
                        sigs.append({"i": ii, "date": days[ii], "_burst": b})
                        scores[days[ii]] = 100 * _u("score", k, s, b)
                i += burst * gap_in + gap_out
                b += 1
            packs[f"{k:06d}"] = {"df": df, "signals": sigs, "scores": scores}
        spec[(s, e)] = packs
    return spec


def _burst_of(code, date, spec):
    for packs in spec.values():
        for sg in packs[code]["signals"]:
            if sg["date"] == date:
                return sg["_burst"]
    return 0


def _feature_fn(spec):
    def fn(code, df):
        rows = []
        for packs in spec.values():
            for sg in packs[code]["signals"]:
                b = sg["_burst"]
                u = _u("feat", code, b)  # 簇内恒定（成簇）
                rows.append(
                    {
                        "date": sg["date"],
                        "mcap_float": 1e9 * (1 + 9 * u),
                        "dist_high250": -u,
                        "limitup60": 1.0 if _u("lu", code, b) < 0.3 else 0.0,
                        "pe_ttm": 1.0 if _u("pe", code, b) < 0.7 else -1.0,
                        "cfo_to_np": 1.0 if _u("cfo", code, b) < 0.6 else -1.0,
                        "roe_ttm": 1.0 if _u("roe", code, b) < 0.7 else -1.0,
                    }
                )
        return pd.DataFrame(rows).drop_duplicates("date")

    return fn


def _replay(spec, seed, edge=False, clustered=False):
    def rp(subset, params):
        import test_plan_rules_replay as tp

        out = []
        for r in subset:
            b = _burst_of(r["code"], r["date"], spec)
            key = (r["code"], b) if clustered else (r["code"], r["date"])
            h = _u("ret", *key, seed, r["date"] if clustered else "")
            hb = _u("retb", *key, seed)  # clustered：簇级高/低质量（与特征独立）
            p_big = 0.06
            if clustered:
                p_big = 0.14 if hb < 0.4 else 0.007
            if edge and _u("lu", r["code"], b) < 0.3:
                p_big = 0.16
            ret = 0.25 if h < p_big else (-0.12 if h > 0.7 else 0.01)
            out.append(tp._trade_of(r, ret))
        return out

    return rp


def _args(**kw):
    import argparse

    base = dict(
        tag="t",
        cost_bps=25.0,
        top_n=20,
        seed=3,
        n_random=25,
        c4_min_pool=20,
        codes="",
        codes_file="",
        count=2000,
        universe_sample=0,
        universe_local=False,
        universe_seed=42,
    )
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture(autouse=True)
def _small_c1(monkeypatch):
    monkeypatch.setattr(tf, "MIN_N_HI", 50)
    monkeypatch.setattr(tf, "YEAR_MIN_HI", 10)


def _run(seed, edge=False, clustered=False, **kw):
    spec = _world()
    return tf.run_study(
        _args(seed=seed, **kw),
        warm_fn=lambda s, e: spec[(s, e)],
        replay_fn=_replay(spec, seed, edge, clustered),
        feature_fn=_feature_fn(spec),
        sector_fn=lambda c, d: _u("sec", c, d[:7]) < 0.5,
    )


def test_derive_rules():
    d = tf.derive(
        {
            "pe_ttm": 5.0,
            "cfo_to_np": 1.2,
            "roe_ttm": 0.1,
            "limitup60": 2,
            "mcap_float": 1e9,
            "dist_high250": -0.3,
        },
        65.0,
        False,
    )
    assert (
        d["fund_good"] == 1
        and d["tech_high"] == 1
        and d["resonance3"] == 1
        and d["limitup_active"] == 1
    )
    assert d["legs_aligned"] == 1  # 基本面中 + 技术强 = 2 腿
    d2 = tf.derive({"pe_ttm": float("nan")}, 65.0, True)
    assert (
        math.isnan(d2["fund_good"])
        and math.isnan(d2["resonance3"])
        and math.isnan(d2["legs_aligned"])
    )
    d3 = tf.derive({"pe_ttm": -1.0, "cfo_to_np": 1, "roe_ttm": 1}, None, True)
    assert d3["fund_good"] == 0 and math.isnan(d3["tech_high"])


def test_feature_stats_lift_and_quintile():
    rng = np.random.RandomState(0)
    v = rng.uniform(0, 1, 5000)
    big = rng.uniform(0, 1, 5000) < (0.20 - 0.18 * v)  # 单调：值越低大单越多
    s = tf.feature_stats(v, big, np.zeros(5000, bool), np.full(5000, 2020), "quintile")
    assert s["n_hi"] == pytest.approx(1000, abs=5) and s["lift_big"] > 1.5
    assert s["spearman"] is not None and s["spearman"] > 0.5


def test_end_to_end_and_edge_feature():
    rep = _run(1, edge=True)
    assert rep["unit"] == "R48" and set(rep["features"]) == set(tf.FEATURES)
    lu = rep["features"]["limitup_active"]
    assert (
        lu["criteria"]["C2"]["ok"] and lu["criteria"]["C4"]["state"] == "confirmed_pass"
    )
    assert rep["window_usage"]["window"] == "not_read"
    from custos.research import result_digest as rd

    import json
    import tempfile
    from pathlib import Path

    p = Path(tempfile.mkdtemp()) / "r48.json"
    p.write_text(json.dumps(rep, default=str), encoding="utf-8")
    assert rd.build_digest("r48", p, "t")["missing"] == []


def _unit_c4(rep):
    """单元级 C4：任一特征 confirmed_pass 记为 pass（校准口径 = 7 特征里挑最好的误报率）。"""
    st = [v["criteria"]["C4"]["state"] for v in rep["features"].values()]
    return {
        "criteria": {
            "C4": {
                "state": "confirmed_pass"
                if "confirmed_pass" in st
                else "confirmed_fail"
            }
        }
    }


class TestTailFeatureCalibration:
    def test_noise(self):
        hn.assert_noise_calibration("tail_feature", lambda s: _unit_c4(_run(100 + s)))

    def test_edge_detected(self):
        hn.assert_edge_detected(
            "tail_feature",
            lambda s: {
                "criteria": {
                    "C4": {
                        "state": _run(100 + s, edge=True)["features"]["limitup_active"][
                            "criteria"
                        ]["C4"]["state"]
                    }
                }
            },
        )

    def test_clustered_noise(self):
        hn.assert_noise_calibration(
            "tail_feature[成簇]", lambda s: _unit_c4(_run(200 + s, clustered=True))
        )


class TestSentinelTailIidPermutation:
    """回归哨兵：置换退化为逐笔（拆散簇）⇒ 成簇零假设下校准必须失败。"""

    def test_calibration_catches_iid(self, monkeypatch):
        def iid(items, rng):
            idx = list(range(len(items)))
            rng.shuffle(idx)
            return np.array(idx)

        monkeypatch.setattr(tf, "cluster_perm", iid)
        with pytest.raises(AssertionError, match="零假设校准失败"):
            hn.assert_noise_calibration(
                "tail_feature[逐笔置换哨兵]",
                lambda s: _unit_c4(_run(200 + s, clustered=True)),
            )
