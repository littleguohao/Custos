"""R49 tail_explore_study（v0.443）：面板构建、A 发现 → B 复现流程、端到端、流动性块、零假设校准 + 逐笔置换哨兵。"""

import numpy as np
import pytest

import helpers_null as hn
import test_tail_feature_study as base
from custos.research import tail_explore_study as te
from custos.research import tail_feature_study as tf


@pytest.fixture(autouse=True)
def _small(monkeypatch):
    monkeypatch.setattr(tf, "MIN_N_HI", 50)
    monkeypatch.setattr(tf, "YEAR_MIN_HI", 10)


def _panel_fn(spec, edge_seed=None):
    bmap = {}
    for packs in spec.values():
        for code, pk in packs.items():
            for sg in pk["signals"]:
                bmap[(code, sg["date"])] = sg["_burst"]

    def fn(code, df, i):
        b = bmap.get((code, str(df["date"].iloc[i])[:10]), 0)
        out = {f"f_x{k}": base._u("px", k, code, b) < 0.3 for k in range(8)}
        out["ss_compression"] = 20 * base._u("ss", code, b)
        return out

    return fn


def _run(seed, *, edge=False, clustered=False, n_random=25, **kw):
    spec = base._world()

    def rp(subset, params):
        import test_plan_rules_replay as tp

        out = []
        for r in subset:
            b = base._burst_of(r["code"], r["date"], spec)
            key = (r["code"], b) if clustered else (r["code"], r["date"])
            h = base._u("ret", *key, seed, r["date"] if clustered else "")
            p_big = 0.06
            if clustered:
                p_big = 0.85 if base._u("retb", *key, seed) < 0.07 else 0.005
            if edge and base._u("px", 3, r["code"], b) < 0.3:  # 植入 f_x3
                p_big = 0.16
            ret = 0.25 if h < p_big else (-0.12 if h > 0.7 else 0.01)
            out.append(tp._trade_of(r, ret))
        return out

    args = base._args(
        seed=seed, n_random=n_random, c4_min_pool=min(40, n_random), min_n=50, **kw
    )
    return te.run_study(
        args,
        panel_fn=_panel_fn(spec),
        warm_fn=lambda s, e: spec[(s, e)],
        replay_fn=rp,
        feature_fn=base._feature_fn(spec),
        sector_fn=lambda c, d: base._u("sec", c, d[:7]) < 0.5,
        osc_fn=base._osc_fn(spec),
    )


def test_cell_stats_and_rows_perm_equivalence():
    rng = np.random.RandomState(1)
    n = 3000
    hi = rng.uniform(size=(n, 3)) < 0.3
    valid = np.ones((n, 3), bool)
    big = rng.uniform(size=n) < 0.1
    loss = rng.uniform(size=n) < 0.2
    p = {"big": big, "loss": loss}
    perm = rng.permutation(n)
    a = te.cell_stats(hi[perm], valid[perm], *te._rows(p, None))
    b = te.cell_stats(hi, valid, *te._rows(p, perm))
    assert np.allclose(a["lift_big"], b["lift_big"]) and np.allclose(
        a["tail"], b["tail"]
    )


def test_end_to_end_edge_lead_robust_and_blocks():
    rep = _run(7, edge=True)
    assert rep["unit"] == "R49" and rep["window_usage"]["window"] == "not_read"
    assert "f_x3" in rep["robust_leads"], rep["robust_leads"][:5]
    assert rep["n_cells"]["mining_a"] > 30
    liq = rep["liquidity"]["mining_a"]
    assert [r["floor_yi"] for r in liq] == [0.0, 1.0, 2.0, 3.0, 5.0]
    from custos.research import result_digest as rd

    import json
    import tempfile
    from pathlib import Path

    p = Path(tempfile.mkdtemp()) / "r49.json"
    p.write_text(json.dumps(rep, default=str), encoding="utf-8")
    assert rd.build_digest("r49", p, "t")["missing"] == []


def _unit(rep):
    st = "confirmed_pass" if rep["robust_leads"] else "confirmed_fail"
    return {"criteria": {"C4": {"state": st}}}


CAL_SEEDS = tuple(range(300, 310))


class TestTailExploreCalibration:
    def test_noise(self):
        hn.assert_noise_calibration(
            "tail_explore", lambda s: _unit(_run(s, n_random=50)), seeds=CAL_SEEDS
        )

    def test_edge_detected(self):
        hn.assert_edge_detected("tail_explore", lambda s: _unit(_run(s, edge=True)))

    def test_clustered_noise(self):
        hn.assert_noise_calibration(
            "tail_explore[成簇]",
            lambda s: _unit(_run(s, clustered=True, n_random=50)),
            seeds=CAL_SEEDS,
        )


class TestSentinelExploreIid:
    """回归哨兵：面板置换退化为逐笔 ⇒ 成簇零假设下校准必须失败。"""

    def test_calibration_catches_iid(self, monkeypatch):
        def iid(items, rng):
            idx = list(range(len(items)))
            rng.shuffle(idx)
            return np.array(idx)

        monkeypatch.setattr(tf, "cluster_perm", iid)
        with pytest.raises(AssertionError, match="零假设校准失败"):
            hn.assert_noise_calibration(
                "tail_explore[逐笔置换哨兵]",
                lambda s: _unit(_run(s, clustered=True, n_random=50)),
                seeds=CAL_SEEDS,
            )


def test_main_writes_both_products(tmp_path):
    """一条命令出两面：R49 同次落 R48 产物，且 R48 读数与单独跑 tail_feature_study 逐字段一致。"""
    import json

    spec = base._world()
    inj = dict(
        warm_fn=lambda s, e: spec[(s, e)],
        replay_fn=base._replay(spec, 9),
        feature_fn=base._feature_fn(spec),
        sector_fn=lambda c, d: base._u("sec", c, d[:7]) < 0.5,
        osc_fn=base._osc_fn(spec),
    )
    common = [
        "--n-random",
        "20",
        "--c4-min-pool",
        "20",
        "--seed",
        "9",
        "--out-dir",
        str(tmp_path),
    ]
    assert (
        te.main(
            [
                *common,
                "--min-n",
                "50",
                "--no-panel",
                "--tag",
                "x49",
                "--r48-tag",
                "x48",
            ],
            **inj,
        )
        == 0
    )
    both = json.loads(
        (tmp_path / "_tail_feature__x48.json").read_text(encoding="utf-8")
    )
    assert (tmp_path / "_tail_explore__x49.json").exists()
    assert tf.main([*common, "--tag", "solo"], **inj) == 0
    solo = json.loads(
        (tmp_path / "_tail_feature__solo.json").read_text(encoding="utf-8")
    )
    for k in ("features", "c4_pool", "descriptive", "n_trades", "verdict"):
        assert both[k] == solo[k], k
