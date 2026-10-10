# -*- coding: utf-8 -*-
"""factor_exit_study.py（R39 因子×出场交互研究终端）钉测。

锁的契约：档集合法性、枚举 80 格、分位切点只用挖掘窗（双窗硬隔离）、
top/uniform-best 选择语义（v2 rdd 门 + 只在挖掘窗）、C1 桶级计数、
C4 随机分桶臂同预算、CLI 窗口护栏（pre2019 硬拒绝镜像）、产物自含。
全流程用合成数据注入（warm_fn/replay_fn + monkeypatched adx14_series），
不发真实加载。
"""

from __future__ import annotations

import argparse

import pandas as pd
import pytest

from custos.research import factor_exit_study as fes
from custos.research.evolution import exit_genome as eg


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
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _per_code(spec):
    """{code: [(date, i, score, factor), ...]} → per_code（df 用 fmap 替身）。"""
    per_code = {}
    for code, sigs in spec.items():
        fmap, signals, scores = {}, [], {}
        for date, i, score, factor in sigs:
            signals.append({"i": i, "date": date})
            scores[date] = score
            fmap[i] = factor
        per_code[code] = {"df": {"fmap": fmap}, "signals": signals, "scores": scores}
    return per_code


def _patch_adx(monkeypatch):
    monkeypatch.setattr(fes, "adx14_series", lambda df: df["fmap"])


def _profile_of(params):
    if params["time_stop_bars"] > 0:
        return "P2_fast"
    if params["trail_pct"] == 0.15:
        return "P3_slow"
    if params["breakeven_trigger"] > 0:
        return "P4_breakeven"
    return "P1_base"


def _scripted_replay(subset, params):
    """机制剧本（margin 标定过）：P3 慢档在高因子信号大赚（wr1/3 payoff4
    ⇒ margin 0.133 = uniform-best），P2 快档在低因子信号（margin 0.083），
    P1 基准零边际、P4 保本微负——真条件映射（b0→P2,b1→P1,b2→P3）合并
    margin ≈ 0.59 ≫ uniform-best；随机分桶臂 ≈ uniform ⇒ C4 可过。"""
    pk = _profile_of(params)
    out = []
    for r in subset:
        f = r["factor"]
        if pk == "P3_slow":
            ret = 0.09 if f == 2 else -0.0225
        elif pk == "P2_fast":
            ret = 0.06 if f == 0 else -0.02
        elif pk == "P1_base":
            ret = 0.01 if r["i"] % 2 == 0 else -0.01
        else:
            ret = 0.008 if r["i"] % 2 == 0 else -0.009
        out.append(
            {
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
        )
    return out


def _flat_replay(subset, params):
    """无交互剧本：所有档同分布（wr 1/2 / payoff 2 ⇒ margin 0.167>0 但
    条件化无增量 ⇒ Δmargin≈0 ⇒ C2 不过 ⇒ falsified）。"""
    out = []
    for r in subset:
        ret = 0.02 if r["i"] % 2 == 0 else -0.01
        out.append(
            {
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
        )
    return out


def _three_level_spec(n_per_window, per_bucket_factor=True):
    """三水平因子（0/1/2 均匀）， mining/judgment 两窗各 n_per_window 信号。"""

    def _mk(window_offset):
        spec = {}
        for k in range(n_per_window):
            code = f"{k % 30:06d}"
            day = f"2023-{(k % 12) + 1:02d}-{(k % 28) + 1:02d}"
            i = window_offset + k
            spec.setdefault(code, []).append((day, i, 50.0, k % 3))
        return spec

    return {
        ("2022-01-01", "2024-07-31"): _per_code(_mk(0)),
        ("2024-08-01", "2026-09-04"): _per_code(_mk(10000)),
    }


def _run(monkeypatch, spec_by_window, replay_fn, **kw):
    _patch_adx(monkeypatch)
    warm_fn = lambda s, e: spec_by_window[(s, e)]  # noqa: E731
    return fes.run_study(_args(**kw), warm_fn=warm_fn, replay_fn=replay_fn)


class TestProfilesAndEnumeration:
    def test_profiles_valid(self):
        for pk, g in fes.PROFILES.items():
            assert eg.validate(g) == [], pk
        assert eg.genome_key(fes.PROFILES["P1_base"]) == eg.genome_key(
            eg.baseline_genome()
        ), "P1 须=pct5_trail08 基准档"

    def test_enumerate_80(self):
        b2 = fes.enumerate_mappings(2)
        b3 = fes.enumerate_mappings(3)
        assert len(b2) == 16 and len(b3) == 64 and len(b2 + b3) == 80
        assert len(set(b2 + b3)) == 80  # 全唯一
        for m in b2 + b3:
            assert all(pk in fes.PROFILES for pk in m)

    def test_quantile_cuts_and_bucket_of(self):
        assert fes.quantile_cuts([1, 2, 3, 4, 5, 6], (0.5,)) == [3.5]
        cuts = fes.quantile_cuts(list(range(1, 13)), (1 / 3, 2 / 3))
        assert len(cuts) == 2 and cuts[0] < cuts[1]
        assert [fes.bucket_of(v, [3.5]) for v in (1, 3, 4, 6)] == [0, 0, 1, 1]


class TestAdxOffset:
    def test_series_matches_adx_last(self):
        """dmi 数组短 1：bar i ↔ adx[i-1]——末 bar 因子值须=_adx_last。"""
        from custos.research import backtest_factors as bt

        n = 60
        close = pd.Series([10 + (i % 7) * 0.3 + i * 0.05 for i in range(n)])
        df = pd.DataFrame(
            {
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
            }
        )
        fmap = fes.adx14_series(df)
        assert fmap[n - 1] == pytest.approx(bt._adx_last(df), rel=1e-9)


class TestMiningOnlyCuts:
    def test_judgment_buckets_use_mining_cuts(self, monkeypatch):
        """判定窗因子分布整体平移 ⇒ 若误用判定窗估计切点，分桶会完全不同。"""
        spec = _three_level_spec(60)
        # 判定窗因子整体 +100
        for pc in spec[("2024-08-01", "2026-09-04")].values():
            for i in pc["df"]["fmap"]:
                pc["df"]["fmap"][i] += 100
        rep = _run(monkeypatch, spec, _scripted_replay)
        cuts = rep["bucketings"]["B2"]["cuts_mining_estimated"]
        assert len(cuts) == 1 and cuts[0] < 10, "切点必须来自挖掘窗分布"
        # 判定窗全部信号落在最右桶（因子值 ~100+ ⇒ bucket=1 for B2）
        assert rep["n_signals"]["judgment"] == 60


class TestScriptedScenarios:
    def test_candidate_scenario(self, monkeypatch):
        rep = _run(monkeypatch, _three_level_spec(180), _scripted_replay, n_random=5)
        assert rep["top"] is not None and rep["uniform_best"] is not None
        c = rep["criteria"]
        assert c["C1"]["ok"] is True, c["C1"]
        assert c["C2"]["ok"] is True, c["C2"]
        assert c["C2"]["d_margin_mining"] > 0 and c["C2"]["d_margin_judgment"] > 0
        assert c["C3"]["ok"] is True, c["C3"]
        assert len(c["C3"]["draws"]) == fes.C3_DRAWS
        # C3 加性扰动钉死（v0.301）：|q_perturbed − q| ≤ 0.2/n_buckets
        # （B3 ±6.7pp / B2 ±10pp——与预注册文字对齐，乘性 ±20% 已废）
        n_b = len(rep["top"]["mapping"])
        delta = fes.C3_PERTURB / n_b
        qs0 = fes.BUCKETINGS[rep["top"]["bucketing"]]
        for d in c["C3"]["draws"]:
            for qp, q in zip(d["qs_perturbed"], qs0):
                assert abs(qp - q) <= delta + 1e-9, d
        assert c["C4"]["state"] == "confirmed_pass", c["C4"]
        assert rep["verdict"] == "candidate"
        assert rep["window_usage"]["k"] == 1  # 判定窗台账（v0.321）

    def test_cost_sensitivity_block_wired(self, monkeypatch):
        """成本副读数（owner review #6，v0.329）：top/uniform-best 交易集
        定向重建（mapping_trades 同引擎路径）50bps 双报 + Δ 双窗翻号标记。"""
        rep = _run(monkeypatch, _three_level_spec(180), _scripted_replay, n_random=5)
        cs = rep["cost_sensitivity"]
        assert cs is not None and cs["base_bps"] == 25.0 and cs["side_bps"] == 50.0
        for arm in ("top_mining", "uniform_mining", "top_judgment", "uniform_judgment"):
            assert cs["arms"][arm]["n"] > 0, arm
        assert set(cs["deltas"]) == {"d_margin_mining", "d_margin_judgment"}
        # 候选大胜场景：50bps 翻不动 Δ 符号
        assert cs["deltas"]["d_margin_mining"]["flip"] is False

    def test_falsified_when_no_conditional_edge(self, monkeypatch):
        rep = _run(monkeypatch, _three_level_spec(180), _flat_replay, n_random=5)
        assert rep["criteria"]["C2"]["ok"] is False
        assert rep["verdict"] == "falsified"

    def test_c1_below_floor_is_untested_not_falsified(self, monkeypatch):
        """v0.301（owner review）：C1 不过 ⇒ **untested（不可判）**——
        样本不足≠否定证据（稀疏桶被 max-of-80 挑中再判死 = 重演 v0.299
        纠正过的错）。"""
        rep = _run(monkeypatch, _three_level_spec(60), _scripted_replay, n_random=5)
        c1 = rep["criteria"]["C1"]
        assert c1["ok"] is False
        assert c1["mining"]["below_floor"], "每桶 20 笔 < 50 须逐桶点名"
        assert rep["verdict"] == "untested"

    def test_c1_counts_empty_buckets(self):
        """0 笔空桶不在 n_taken_by_bucket 里，但必须照判（缺 key=0 笔）。"""
        rd = {"n_taken": 115, "n_taken_by_bucket": {0: 60, 1: 55}}  # 桶 2 零笔
        c1 = fes.judge_c1(rd, rd, 3)
        assert c1["ok"] is False
        assert c1["mining"]["below_floor"] == {2: 0}

    def test_empty_signals_guard(self, monkeypatch):
        _patch_adx(monkeypatch)
        warm_fn = lambda s, e: {}  # noqa: E731
        with pytest.raises(RuntimeError, match="空结果护栏"):
            fes.run_study(_args(), warm_fn=warm_fn, replay_fn=_scripted_replay)

    def test_product_self_contained(self, monkeypatch):
        rep = _run(monkeypatch, _three_level_spec(180), _scripted_replay, n_random=5)
        assert rep["factor"]["name"] == "adx14"
        assert set(rep["profiles"]) == set(fes.PROFILES)
        for pk, blk in rep["profiles"].items():
            assert blk["key"].startswith("sp"), "档集基因组键自含（供 C5 读取）"
        assert rep["bucketings"]["B2"]["cuts_mining_estimated"]
        assert len(rep["configs"]) == 80
        assert set(rep["criteria"]) == {"C1", "C2", "C3", "C4", "rule_note"}
        from custos.research import strategy_grid as sg  # noqa: PLC0415

        assert rep["objective_version"] == sg.OBJECTIVE_VERSION
        for cfg in rep["configs"]:
            assert "taken" not in (cfg["mining"] or {}), "报告不落地交易明细"
        assert rep["top"]["mapping"] in [c["mapping"] for c in rep["configs"]]


class TestC4SameBudget:
    def test_arms_run_full_80(self, monkeypatch):
        """每条随机臂：两种分桶各扰一次、同 80 格取 max——#71 同待遇。"""
        calls = []

        real = fes.study_window

        def spy(
            per_code, signals, buckets, regime, cost_bps, top_n, replay_fn=None, **kw
        ):
            calls.append(max(buckets) + 1 if buckets else 0)
            return real(
                per_code, signals, buckets, regime, cost_bps, top_n, replay_fn, **kw
            )

        monkeypatch.setattr(fes, "study_window", spy)
        n_random = 3
        rep = _run(
            monkeypatch,
            _three_level_spec(180),
            _scripted_replay,
            n_random=n_random,
            c4_min_pool=2,
        )
        # C4 臂调用数 = n_random × 2 分桶（另有 C3 的 4×2 次 + 首轮 2×2 次）
        c4 = rep["criteria"]["C4"]
        assert c4["evaluated"] == n_random
        assert len(c4["pool"]) <= n_random
        # 每桶臂都跑了 B2 和 B3 两种枚举
        assert calls.count(2) >= n_random and calls.count(3) >= n_random


class TestJudges:
    def test_pick_top_respects_rdd_gate(self):
        cfgs = [
            {"readings": {"objective": None, "margin": 0.99}},  # 门不过垫底
            {"readings": {"objective": 0.05, "margin": 0.05}},
        ]
        assert fes.pick_top(cfgs)["readings"]["margin"] == 0.05
        assert fes.pick_top([{"readings": None}]) is None

    def test_pick_uniform_best(self):
        u = {
            "P1_base": {"objective": 0.01, "margin": 0.01},
            "P3_slow": {"objective": 0.03, "margin": 0.03},
            "P2_fast": None,
        }
        assert fes.pick_uniform_best(u)["profile"] == "P3_slow"

    def test_judge_c4_states(self):
        p = fes.judge_c4(0.05, [0.01] * 10, 10, 10, min_pool=50)
        assert p["state"] == "provisional" and p["ok"] is None
        p = fes.judge_c4(0.05, [0.01] * 60, 60, 60, min_pool=50)
        assert p["state"] == "confirmed_pass" and p["ok"] is True
        p = fes.judge_c4(-0.01, [0.01] * 60, 60, 60, min_pool=50)
        assert p["state"] == "confirmed_fail" and p["ok"] is False


class TestCliGuards:
    def test_pre2019_overlap_rejected(self):
        ap = fes._build_parser()
        args = ap.parse_args(["--tag", "t", "--mining-start", "2015-01-01"])
        with pytest.raises(SystemExit):
            fes._check_windows(args, ap)

    def test_window_order_rejected(self):
        ap = fes._build_parser()
        args = ap.parse_args(["--tag", "t", "--mining-end", "2025-01-01"])
        with pytest.raises(SystemExit):
            fes._check_windows(args, ap)

    def test_defaults_accepted(self):
        ap = fes._build_parser()
        fes._check_windows(ap.parse_args(["--tag", "t"]), ap)
