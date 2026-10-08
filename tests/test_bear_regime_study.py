# -*- coding: utf-8 -*-
"""bear_regime_study.py（R40 空头区间做多全栈研究终端）钉测。

锁的契约：regime 反转语义（仅空头日放行/无映射不放行）、230 格枚举、
随机臂**对等纪律**（挖掘窗选型冻结 ⇒ 判定窗读数由冻结配置产生——用
「判定窗池全负」结果级证明：若臂在判定窗重选，池不可能全负）、
C1 不过=untested、C2 三态（池未建=None⇒provisional 不 falsified）、
CLI 护栏、产物自含。全合成注入，不发真实加载。
"""

from __future__ import annotations

import argparse

import pytest

from custos.research import backtest_factors as bt
from custos.research import bear_regime_study as brs
from custos.research import factor_exit_study as fes


def _args(**kw):
    base = dict(
        mining_start="2022-01-01",
        mining_end="2024-07-31",
        judgment_start="2024-08-01",
        judgment_end="2026-09-04",
        cost_bps=25.0,
        top_n=20,
        seed=7,
        n_random=4,
        c4_min_pool=3,
        tag="t",
        gates="all",
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _mk_per_code(
    gate_counts, windows=(("2022-01-01", "2024-07-31"), ("2024-08-01", "2026-09-04"))
):
    """{gate: count} → 双窗 per_code（df 占位 None——注入 replay 时不用）。"""
    out = {}
    for w_i, (s, e) in enumerate(windows):
        per_code = {}
        k = 0
        for gate, n in gate_counts.items():
            for _ in range(n):
                code = f"{(k % 30):06d}"
                day = f"2023-{(k % 12) + 1:02d}-{(k % 28) + 1:02d}"
                i = w_i * 10000 + k
                pack = per_code.setdefault(
                    code, {"df": None, "gate_signals": {}, "scores": {}}
                )
                pack["gate_signals"].setdefault(gate, []).append({"i": i, "date": day})
                pack["scores"][day] = 50.0
                k += 1
        out[(s, e)] = per_code
    return out


def _trade_of(r, ret):
    return {
        "code": r["code"],
        "entry_date": r["date"],
        "exit_date": r["date"],
        "ret": ret,
        "reason": "stop",
        "holding": 5,
        "risk_frac": 0.05,
        "r_multiple": ret / 0.05,
        "score": r["score"],
    }


def _replay_winner_gate(subset, params):
    """j_low 门两窗稳赢（margin≈0.3），其余门负（≈−0.167）；出场档中性
    （C3 扰动不改结论）。随机臂（__random__）贴零。"""
    out = []
    for r in subset:
        if r["gate"] == "j_low":
            ret = 0.08 if r["i"] % 2 == 0 else -0.02
        elif r["gate"] == "__random__":
            ret = 0.01 if r["i"] % 2 == 0 else -0.005
        else:
            ret = -0.01 if r["i"] % 2 == 0 else 0.005
        out.append(_trade_of(r, ret))
    return out


def _replay_flat(subset, params):
    """全部同分布（margin≈0.167）——top 打不过随机臂 q95 ⇒ falsified。"""
    return [_trade_of(r, 0.02 if r["i"] % 2 == 0 else -0.01) for r in subset]


def _replay_frozen_probe(subset, params):
    """冻结语义探针：trail_pct>0 的档挖掘窗（i<10000）赢、判定窗大亏；
    其余档两窗小赚。若臂在判定窗**重选**，判定池不可能全负。"""
    out = []
    for r in subset:
        if params.get("trail_pct"):
            if r["i"] < 10000:
                ret = 0.08 if r["i"] % 2 == 0 else -0.02
            else:
                ret = -0.05 if r["i"] % 2 == 0 else 0.01
        else:
            ret = 0.008 if r["i"] % 2 == 0 else -0.004
        out.append(_trade_of(r, ret))
    return out


def _rand_flat(n, w):
    """随机臂合成：margin≈0.167 且权益正漂移（rdd 门要过）——候选剧本里
    top=0.3 打得过；falsified 剧本里 top 也是 0.167 ⇒ 打不过池。"""
    return [
        {
            "code": f"{i % 7:06d}",
            "date": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
            "i": (0 if w == "mining" else 10000) + i,
            "score": 50.0,
            "sig": {"i": i, "date": "2024-01-02"},
            "gate": "__random__",
        }
        for i in range(n)
    ]


def _run(spec, replay, n_random=4, c4_min_pool=3, **kw):
    warm_fn = lambda s, e: spec[(s, e)]  # noqa: E731
    return brs.run_study(
        _args(n_random=n_random, c4_min_pool=c4_min_pool, **kw),
        warm_fn=warm_fn,
        replay_fn=replay,
        random_entry_fn=_rand_flat,
    )


class TestInvertRegime:
    def test_mapping(self):
        got = brs.invert_regime_bearish(
            {"2022-01-04": "空头", "2022-01-05": "做多", "2022-01-06": "中性"}
        )
        assert got == {
            "2022-01-04": "做多",
            "2022-01-05": "空头",
            "2022-01-06": "空头",
        }

    def test_checker_semantics(self):
        """仅空头日放行；做多/中性不放行；无映射日（首条记录前）不放行。"""
        regime = brs.invert_regime_bearish({"2022-01-04": "空头", "2022-02-01": "做多"})
        chk = bt._amv_checker(regime)
        assert chk("2022-01-04") is True  # 真空头日
        assert chk("2022-01-20") is True  # 空头粘滞延续
        assert chk("2022-02-01") is False  # 真做多日
        assert chk("2021-12-31") is False  # 无映射日（as-of 无前驱）


class TestGridAndVerdicts:
    def test_grid_230_and_candidate(self):
        spec = _mk_per_code({"j_low": 120, "j_low_adx25": 4, "qn_three_red": 4})
        rep = _run(spec, _replay_winner_gate)
        assert len(rep["configs"]) == 46 * 5
        assert rep["gates"] == sorted(bt.ENTRY_GATES)
        assert rep["top"]["gate"] == "j_low"
        c = rep["criteria"]
        assert c["C1"]["ok"] is True, c["C1"]
        assert c["C2"]["ok"] is True, c["C2"]
        assert c["C2"]["pool_ok_mining"] and c["C2"]["pool_ok_judgment"]
        assert c["C3"]["ok"] is True and len(c["C3"]["draws"]) == brs.C3_DRAWS
        assert c["C4"]["state"] == "confirmed_pass"
        assert rep["verdict"] == "candidate"

    def test_falsified_when_top_cannot_beat_random(self):
        spec = _mk_per_code({"j_low": 120, "j_low_adx25": 4})
        rep = _run(spec, _replay_flat)
        assert rep["criteria"]["C2"]["ok"] is False  # 池 q95 打不过了
        assert rep["verdict"] == "falsified"

    def test_c1_shortfall_is_untested(self):
        spec = _mk_per_code({"j_low": 60})  # 60 < 100
        rep = _run(spec, _replay_winner_gate)
        assert rep["criteria"]["C1"]["ok"] is False
        assert rep["verdict"] == "untested", "C1 不过=untested 不判 falsified（v0.301）"

    def test_provisional_when_pool_small(self):
        spec = _mk_per_code({"j_low": 120, "j_low_adx25": 4})
        rep = _run(spec, _replay_winner_gate, n_random=2, c4_min_pool=50)
        assert rep["criteria"]["C4"]["state"] == "provisional"
        assert rep["verdict"] == "provisional"

    def test_empty_signals_guard(self):
        warm_fn = lambda s, e: {}  # noqa: E731
        with pytest.raises(RuntimeError, match="空结果护栏"):
            brs.run_study(_args(), warm_fn=warm_fn, replay_fn=_replay_winner_gate)


class TestArmFrozenSemantics:
    def test_judgment_pool_from_frozen_config(self):
        """对等纪律（v0.302）结果级证明：判定窗池全负 ⇒ 臂没有当窗重选
        （重选会挑两窗小赚的档，池不可能为负）。"""
        spec = _mk_per_code({"j_low": 120})
        rep = _run(spec, _replay_frozen_probe, n_random=4, c4_min_pool=2)
        pool_j = rep["pools"]["judgment"]
        assert len(pool_j) == 4
        assert all(m < 0 for m in pool_j), pool_j
        assert all(m > 0 for m in rep["pools"]["mining"])
        assert "冻结" in rep["pools"]["arm_construction"]


class TestC3Perturb:
    def test_levels_params_perturbed_others_kept(self):
        import random as _r

        rng = _r.Random(1)
        out, moved = brs.perturb_exit_params(
            {"stop_mode": "pct", "stop_pct": 5, "trail_pct": 0.08}, rng
        )
        assert moved is True
        assert out["stop_mode"] == "pct", "档位外键原样保留"
        assert out["stop_pct"] in (4.0, 5.0, 6.0), "吸附档位空间"
        assert out["trail_pct"] in (0.06, 0.08, 0.10)

    def test_base_low_no_perturbable(self):
        import random as _r

        out, moved = brs.perturb_exit_params({}, _r.Random(1))
        assert moved is False and out == {}, "base_low 原样留痕"


class TestCliAndProduct:
    def test_pre2019_overlap_rejected(self):
        ap = brs._build_parser()
        args = ap.parse_args(["--tag", "t", "--mining-start", "2015-01-01"])
        with pytest.raises(SystemExit):
            fes._check_windows(args, ap)

    def test_self_contained(self):
        spec = _mk_per_code({"j_low": 120, "j_low_adx25": 4})
        rep = _run(spec, _replay_winner_gate)
        for k in (
            "schema",
            "verdict",
            "gates",
            "exit_grid",
            "windows",
            "amv_mode",
            "n_signals_by_gate",
            "top",
            "configs",
            "pools",
            "criteria",
            "objective_version",
        ):
            assert k in rep, k
        assert rep["objective_version"] == "v2-margin"
        for cfg in rep["configs"]:
            for w in ("mining", "judgment"):
                assert "taken" not in (cfg[w] or {}), "报告不落交易明细"
        assert set(rep["criteria"]) == {"C1", "C2", "C3", "C4", "rule_note"}
