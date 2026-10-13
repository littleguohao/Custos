# -*- coding: utf-8 -*-
"""factor_exit_study.py（R39 因子×出场交互研究终端）钉测。

锁的契约：档集合法性、枚举 80 格、分位切点只用挖掘窗（双窗硬隔离）、
top/uniform-best 选择语义（v2 rdd 门 + 只在挖掘窗）、C1 桶级计数、
C4 随机分桶臂同预算、产物自含；v2 窗口布局（TODO #90）：四参全缺省 =
三段（挖掘 A/B + 判定 2017~2021，段末 90 日隔离带裁剪、A∪B 合并池）、
四参全显式 = legacy 单段双窗（R40 报告与切换前**逐位同一**金色快照）、
越段/相交/半截布局即拒、判定窗=受保护段才登记 judgment_2017_2021。
全流程用合成数据注入（warm_fn/replay_fn + monkeypatched adx14_series），
不发真实加载。
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json

import pandas as pd
import pytest

from helpers_golden import canon_json
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


def _args_v2(**kw):
    """v2 布局参数（窗口四参全 None ⇒ 三段：挖掘 A/B + 判定 2017~2021）。"""
    return _args(
        mining_start=None, mining_end=None, judgment_start=None, judgment_end=None, **kw
    )


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


#: v2 三段窗口（= load_window SEG_MINING_A/SEG_JUDGMENT/SEG_MINING_B 的字面值）
_V2_SEGS = (
    ("2000-01-01", "2016-12-31"),
    ("2017-01-01", "2021-12-31"),
    ("2022-01-01", "2026-09-04"),
)


def _three_level_spec_v2(n_per_seg):
    """v2 三段三水平因子夹具（与 _three_level_spec 同构；段内年份远离
    段末隔离带——2005/2019/2023，天然零裁剪）。"""

    def _mk(window_offset, year):
        spec = {}
        for k in range(n_per_seg):
            code = f"{k % 30:06d}"
            day = f"{year}-{(k % 12) + 1:02d}-{(k % 28) + 1:02d}"
            i = window_offset + k
            spec.setdefault(code, []).append((day, i, 50.0, k % 3))
        return spec

    return {
        _V2_SEGS[0]: _per_code(_mk(0, 2005)),
        _V2_SEGS[1]: _per_code(_mk(100000, 2019)),
        _V2_SEGS[2]: _per_code(_mk(200000, 2023)),
    }


def _run(monkeypatch, spec_by_window, replay_fn, **kw):
    _patch_adx(monkeypatch)
    warm_fn = lambda s, e: spec_by_window[(s, e)]  # noqa: E731
    return fes.run_study(_args(**kw), warm_fn=warm_fn, replay_fn=replay_fn)


def _run_v2(monkeypatch, spec_by_seg, replay_fn, **kw):
    _patch_adx(monkeypatch)
    warm_fn = lambda s, e: spec_by_seg[(s, e)]  # noqa: E731
    return fes.run_study(_args_v2(**kw), warm_fn=warm_fn, replay_fn=replay_fn)


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
        # legacy 判定窗（⊆挖掘 B）不登记（v2 口径，TODO #90）
        assert rep["window_usage"]["window"] == "mining_segment"
        assert rep["window_usage"]["k"] is None
        assert "判定窗在挖掘段内" in rep["window_usage"]["note"]

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


class TestClusteredC4Arms:
    """v0.335（owner review——跑数前修订）：C4 随机分桶臂改**整簇**赋桶
    （kit.cluster_ids/cluster_draw 单源；逐信号打乱把簇拆散、q95 偏低，
    与 R42 同源同修）。"""

    @staticmethod
    def _clustered_spec(n=180):
        """同码信号 i 连续（间隔 1 ≤5 ⇒ 每码一簇；30 码 ⇒ 30 簇）。"""

        def _mk(window_offset):
            spec = {}
            for kk in range(n):
                code = f"{kk % 30:06d}"
                day = f"2023-{(kk % 12) + 1:02d}-{(kk % 28) + 1:02d}"
                i = window_offset + kk // 30  # 同码 i 连续 ⇒ 一簇
                spec.setdefault(code, []).append((day, i, 50.0, kk % 3))
            return spec

        return {
            ("2022-01-01", "2024-07-31"): _per_code(_mk(0)),
            ("2024-08-01", "2026-09-04"): _per_code(_mk(10000)),
        }

    def test_c4_arm_same_cluster_same_bucket(self, monkeypatch):
        """同簇同桶钉：spy kit.cluster_draw——C4 臂每次调用返回的分桶在
        同簇内恒定；B2/B3 两分桶方案各调一次/臂。"""
        calls = []
        orig = fes.kit.cluster_draw

        def spy(clusters, choices, rng):
            out = orig(clusters, choices, rng)
            calls.append((list(clusters), tuple(choices), list(out)))
            return out

        monkeypatch.setattr(fes.kit, "cluster_draw", spy)
        rep = _run(
            monkeypatch,
            self._clustered_spec(),
            _scripted_replay,
            n_random=3,
            c4_min_pool=2,
        )
        evaluated = rep["criteria"]["C4"]["evaluated"]
        # 每臂 × 2 分桶方案（主研究不走 cluster_draw——真实分桶按因子值）
        assert len(calls) == evaluated * 2, calls
        assert {c[1] for c in calls} == {(0, 1), (0, 1, 2)}  # B2 / B3
        for clusters, _choices, out in calls:
            assert len(set(clusters)) == 30, "30 码 × 同码 i 连续 ⇒ 30 簇"
            by_cluster: dict[int, int] = {}
            for cid, b in zip(clusters, out):
                assert by_cluster.setdefault(cid, b) == b, "同簇必须同桶"


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
    """v2 布局护栏（TODO #90）：越段/相交/半截布局即拒；次序规则作废。"""

    @staticmethod
    def _parse(ap, ms, me, js, je):
        return ap.parse_args(
            [
                "--tag",
                "t",
                "--mining-start",
                ms,
                "--mining-end",
                me,
                "--judgment-start",
                js,
                "--judgment-end",
                je,
            ]
        )

    def test_cross_segment_rejected(self):
        """越段即拒（v2 段检查单源）：mining 2015~2024 跨挖掘 A/判定/挖掘 B
        三段，不在任何单段内。"""
        ap = fes._build_parser()
        args = self._parse(ap, "2015-01-01", "2024-07-31", "2024-08-01", "2026-09-04")
        with pytest.raises(SystemExit):
            fes._check_windows(args, ap)

    def test_windows_overlap_rejected(self):
        """相交即拒：两窗均 ⊆ 挖掘 B（段检查各过）但区间相交——v2 起只查
        不相交（取代旧 mining_end < judgment_start 次序规则）。"""
        ap = fes._build_parser()
        args = self._parse(ap, "2022-01-01", "2025-01-01", "2024-08-01", "2026-09-04")
        with pytest.raises(SystemExit):
            fes._check_windows(args, ap)

    def test_judgment_before_mining_accepted(self):
        """次序规则作废钉：判定窗整段早于挖掘窗（判定段 2017~2021 vs 挖掘 B
        子窗）⇒ 不相交即合法，不再要求 mining 在前。"""
        ap = fes._build_parser()
        args = self._parse(ap, "2022-01-01", "2024-07-31", "2017-01-01", "2021-12-31")
        fes._check_windows(args, ap)  # 不炸即过

    def test_r40_legacy_windows_accepted(self):
        """R40 旧窗（均 ⊆ 挖掘 B 且互不相交）= 合法 legacy 兼容通道。"""
        ap = fes._build_parser()
        args = self._parse(ap, "2022-01-01", "2024-07-31", "2024-08-01", "2026-09-04")
        fes._check_windows(args, ap)
        segs, jwin = fes._resolve_layout(args)
        assert segs == [("2022-01-01", "2024-07-31")]
        assert jwin == ("2024-08-01", "2026-09-04")

    @pytest.mark.parametrize(
        "argv_tail",
        [
            ["--mining-start", "2022-01-01"],  # 只给一个
            ["--mining-start", "2022-01-01", "--mining-end", "2024-07-31"],  # 只给挖掘
            [  # 给三个
                "--mining-start",
                "2022-01-01",
                "--mining-end",
                "2024-07-31",
                "--judgment-start",
                "2024-08-01",
            ],
        ],
    )
    def test_partial_layout_rejected(self, argv_tail):
        """半截布局即拒：四参须全给（legacy）或全不给（v2）。"""
        ap = fes._build_parser()
        args = ap.parse_args(["--tag", "t", *argv_tail])
        with pytest.raises(SystemExit):
            fes._check_windows(args, ap)

    def test_defaults_accepted(self):
        ap = fes._build_parser()
        fes._check_windows(ap.parse_args(["--tag", "t"]), ap)


# ---------------------------------------------------------------------------
# 台账生产方向钉（v0.343，owner——r42_probe 漏登根因）
# ---------------------------------------------------------------------------


def _wu_spy(monkeypatch):
    """record_use 捕获桩：返回固定 k=7（不碰真台账），返回调用清单。"""
    from custos.research import window_usage as wu

    calls = []

    def spy(unit, window, tag, purpose, *, synthetic=False, path=None):
        calls.append({"unit": unit, "window": window, "synthetic": synthetic})
        return 7

    monkeypatch.setattr(wu, "record_use", spy)
    return calls


class TestProductionRecordUse:
    """run_study 传 warm_fn=None（**生产路径**）+ 生产依赖全 monkeypatch 成
    合成 ⇒ record_use 必须以 ``synthetic=False`` 被调且 k 直通报告。防回归：
    生产分支重绑注入参数 ⇒ 入口快照 ``injected`` 变 True ⇒ 立刻红。
    v2 台账口径（TODO #90）：判定窗=受保护段（2017~2021）才登记，键
    ``judgment_2017_2021``；legacy 判定窗（⊆挖掘 B）不登记。"""

    def _production_run(self, monkeypatch):
        import pandas as pd

        from custos.datasource.local_tdx import local_tdx_data
        from custos.research import backtest_factors as bt
        from custos.research import exit_campaign as ec

        _patch_adx(monkeypatch)  # df={"fmap"} 替身（与注入路径同夹具口径）
        spec = _three_level_spec_v2(60)

        def _resolve_fake(ns, ap):
            # 宇宙解析副作用同 universe.resolve：三件套塞 ns.universe_meta
            ns.universe_meta = {
                "universe_rule": "test_rule",
                "n_codes": 1,
                "universe_sha256": "deadbeef01234567",
            }
            return ["000000"]

        monkeypatch.setattr(bt, "_resolve_universe", _resolve_fake)
        monkeypatch.setattr(bt, "load_amv_regime", lambda since: {"2019-01-03": "做多"})
        monkeypatch.setattr(
            local_tdx_data,
            "get_ohlcv_table",
            lambda code, count: pd.DataFrame({"date": ["2019-01-03"]}),
        )
        monkeypatch.setattr(
            ec,
            "warm_v0_signals",
            lambda codes, regime, index_df, *, count, cost_bps, start, end, warmup_bars=0: (
                spec[(start, end)]
            ),
        )
        monkeypatch.setattr(
            fes,
            "replay_signals",
            lambda per_code, subset, params, regime, cost_bps: _scripted_replay(
                subset, params
            ),
        )
        # fxt._args_v2 无宇宙/count 字段（注入路径用不到）——生产分支要读
        return fes.run_study(
            _args_v2(
                n_random=2,
                c4_min_pool=2,
                codes="",
                codes_file="",
                universe_local=False,
                universe_sample=0,
                universe_seed=42,
                count=2000,
            ),
            warm_fn=None,
            replay_fn=None,
        )

    def test_production_records_with_synthetic_false(self, monkeypatch):
        calls = _wu_spy(monkeypatch)
        rep = self._production_run(monkeypatch)
        assert len(calls) == 1
        assert calls[0]["unit"] == "R39"
        assert calls[0]["window"] == "judgment_2017_2021"
        assert calls[0]["synthetic"] is False, "生产路径必须按 synthetic=False 登记"
        assert rep["window_usage"]["k"] == 7, "生产口径 k 直通报告"
        assert "第 7 次被读" in rep["window_usage"]["note"]

    def test_production_universe_meta_wired(self, monkeypatch):
        """宇宙三件套回塞（TODO #89 接线确认）：_resolve_universe 的
        ns.universe_meta ⇒ args.universe_meta ⇒ provenance 三件套。"""
        _wu_spy(monkeypatch)
        rep = self._production_run(monkeypatch)
        pv = rep["provenance"]
        assert pv["universe_rule"] == "test_rule"
        assert pv["n_codes"] == 1
        assert pv["universe_sha256"] == "deadbeef01234567"

    def test_injected_still_synthetic(self, monkeypatch):
        """对照钉：同一夹具走注入路径 ⇒ synthetic=True（双向钉死）。"""
        calls = _wu_spy(monkeypatch)
        rep = _run_v2(
            monkeypatch,
            _three_level_spec_v2(60),
            _scripted_replay,
            n_random=2,
            c4_min_pool=2,
        )
        assert len(calls) == 1
        assert calls[0]["window"] == "judgment_2017_2021"
        assert calls[0]["synthetic"] is True
        assert rep["window_usage"]["k"] == 7

    def test_legacy_judgment_not_registered(self, monkeypatch):
        """legacy 判定窗（2024~2026，已并入挖掘 B）**不登记**——读挖掘段
        不入台账（v2 口径）；报告 window_usage 写定案注记。"""
        calls = _wu_spy(monkeypatch)
        rep = _run(
            monkeypatch,
            _three_level_spec(60),
            _scripted_replay,
            n_random=2,
            c4_min_pool=2,
        )
        assert calls == [], "legacy 判定窗在挖掘段内——record_use 不得被调"
        assert rep["window_usage"] == {
            "window": "mining_segment",
            "k": None,
            "note": "判定窗在挖掘段内（非受保护段 2017~2021）——读挖掘段不入台账（v2 口径）",
        }

    # ---------------------------------------------------------------------------
    # v2 窗口布局钉测（TODO #90，owner 2026-10-13 拍板）
    # ---------------------------------------------------------------------------

    def test_legacy_judgment_subwindow_registered(self, monkeypatch):
        """判定**子窗**（2018~2020 ⊆ 判定段）⇒ 相交即登记（v0.361 owner
        review——精确相等会把判定子窗放成漏网，7 入口统一 touches_judgment）。"""
        import pandas as pd

        from custos.datasource.local_tdx import local_tdx_data
        from custos.research import backtest_factors as bt
        from custos.research import exit_campaign as ec

        _patch_adx(monkeypatch)
        calls = _wu_spy(monkeypatch)
        def _mk_pc(year, off):
            d = {}
            for k in range(60):
                code = f"{k % 30:06d}"
                day = f"{year}-{(k % 12) + 1:02d}-{(k % 28) + 1:02d}"
                d.setdefault(code, []).append((day, off + k, 50.0, k % 3))
            return _per_code(d)

        spec = {
            ("2022-01-01", "2024-07-31"): _mk_pc(2023, 0),
            ("2018-01-01", "2020-12-31"): _mk_pc(2019, 10000),
        }

        def _resolve_fake(ns, ap):
            ns.universe_meta = {
                "universe_rule": "test_rule",
                "n_codes": 1,
                "universe_sha256": "deadbeef01234567",
            }
            return ["000000"]

        monkeypatch.setattr(bt, "_resolve_universe", _resolve_fake)
        monkeypatch.setattr(bt, "load_amv_regime", lambda since: {"2019-01-03": "做多"})
        monkeypatch.setattr(
            local_tdx_data,
            "get_ohlcv_table",
            lambda code, count: pd.DataFrame({"date": ["2019-01-03"]}),
        )
        monkeypatch.setattr(
            ec,
            "warm_v0_signals",
            lambda codes, regime, index_df, *, count, cost_bps, start, end, warmup_bars=0: (
                spec[(start, end)]
            ),
        )
        monkeypatch.setattr(
            fes,
            "replay_signals",
            lambda per_code, subset, params, regime, cost_bps: _scripted_replay(
                subset, params
            ),
        )
        rep = fes.run_study(
            _args(
                judgment_start="2018-01-01",
                judgment_end="2020-12-31",
                n_random=2,
                c4_min_pool=2,
                codes="",
                codes_file="",
                universe_local=False,
                universe_sample=0,
                universe_seed=42,
                count=2000,
            ),
            warm_fn=None,
            replay_fn=None,
        )
        assert len(calls) == 1
        assert calls[0]["unit"] == "R39"
        assert calls[0]["window"] == "judgment_2017_2021"
        assert calls[0]["synthetic"] is False
        assert rep["window_usage"]["window"] == "judgment_2017_2021"


class TestV2Layout:
    """钉（a）：窗口四参全缺省 ⇒ v2 三段布局（段常量 = load_window 单源）。"""

    def test_v2_defaults_resolve_three_segments(self):
        from custos.research import load_window as lw

        ap = fes._build_parser()
        args = ap.parse_args(["--tag", "t"])
        assert (
            args.mining_start,
            args.mining_end,
            args.judgment_start,
            args.judgment_end,
        ) == (None, None, None, None), "窗口四参缺省全 None（v2 触发条件）"
        fes._check_windows(args, ap)  # 不炸即过
        segs, jwin = fes._resolve_layout(args)
        assert segs == [lw.SEG_MINING_A, lw.SEG_MINING_B]
        assert jwin == lw.SEG_JUDGMENT

    def test_v2_report_windows_and_segments(self, monkeypatch):
        rep = _run_v2(
            monkeypatch,
            _three_level_spec_v2(60),
            _scripted_replay,
            n_random=2,
            c4_min_pool=2,
        )
        assert rep["windows"] == {
            "mining": {"start": "2000-01-01", "end": "2026-09-04"},
            "judgment": {"start": "2017-01-01", "end": "2021-12-31"},
        }
        segs = rep["segments"]
        assert segs["mining_a"] == {
            "start": "2000-01-01",
            "end": "2016-12-31",
            "n_signals": 60,
            "n_clipped": 0,
        }
        assert segs["judgment"] == {
            "start": "2017-01-01",
            "end": "2021-12-31",
            "n_signals": 60,
            "n_clipped": 0,
        }
        assert segs["mining_b"] == {
            "start": "2022-01-01",
            "end": "2026-09-04",
            "n_signals": 60,
            "n_clipped": 0,
        }
        assert segs["mining_merged"] == {"n_signals": 120}
        assert rep["n_signals"] == {"mining": 120, "judgment": 60}
        # v2 判定窗=受保护段 ⇒ 台账窗名换代（合成运行 k=None 未入台账）
        assert rep["window_usage"]["window"] == "judgment_2017_2021"
        assert rep["window_usage"]["k"] is None


class TestMergedPoolReadings:
    """钉（b）：v2 A∪B 合并池读数 = 手工合并两段信号重算（逐位）。"""

    def test_merged_pool_matches_manual_recompute(self, monkeypatch):
        _patch_adx(monkeypatch)
        spec = _three_level_spec_v2(60)
        rep = _run_v2(monkeypatch, spec, _scripted_replay, n_random=2, c4_min_pool=2)

        from custos.research.load_window import clip_entries

        # 手工合并：逐段 collect → 段末隔离带裁剪 → A+B 拼接（信号池层合并）
        merged: list[dict] = []
        for _name, w in (("mining_a", _V2_SEGS[0]), ("mining_b", _V2_SEGS[2])):
            merged.extend(clip_entries(fes.collect_signals(spec[w]), w[1], key="date"))
        assert len(merged) == rep["n_signals"]["mining"]

        for name, qs in fes.BUCKETINGS.items():
            cuts = fes.quantile_cuts([r["factor"] for r in merged], qs)
            assert cuts == rep["bucketings"][name]["cuts_mining_estimated"]
            buckets = fes.buckets_of(merged, cuts)
            stud = fes.study_window(
                {}, merged, buckets, {}, 25.0, 20, replay_fn=_scripted_replay
            )
            for c in stud["configs"]:
                want = dict(c["readings"])
                want.pop("taken")  # 报告 _slim 去掉交易明细
                got = next(
                    x["mining"]
                    for x in rep["configs"]
                    if x["bucketing"] == name and x["mapping"] == c["mapping"]
                )
                assert got == want, f"{name} {c['mapping']}"


class TestSegmentBuffer:
    """钉（c）：v2 三段末隔离带三刀生效（裁剪在 replay/判据之前），剔除数入报告。

    段末入场截止日（load_window.entry_deadline 字面值）：A→2016-10-02、
    判定→2021-10-02、B→2026-06-06。"""

    @staticmethod
    def _buffer_spec():
        """三段各放 截止日前/当日（=截止，保留）/之后（剔除）信号。"""

        def mk(entries):
            spec: dict[str, list] = {}
            for idx, (code, day) in enumerate(entries):
                spec.setdefault(code, []).append((day, idx, 50.0, idx % 3))
            return _per_code(spec)

        return {
            _V2_SEGS[0]: mk(
                [
                    ("000001", "2016-09-30"),
                    ("000002", "2016-10-02"),  # =截止日，保留
                    ("000003", "2016-10-03"),  # 剔除
                    ("000004", "2016-12-30"),  # 剔除
                ]
            ),
            _V2_SEGS[1]: mk(
                [
                    ("000001", "2021-09-01"),
                    ("000002", "2021-10-02"),
                    ("000003", "2021-10-03"),
                    ("000004", "2021-12-31"),
                ]
            ),
            _V2_SEGS[2]: mk(
                [
                    ("000001", "2023-05-01"),
                    ("000002", "2026-06-06"),
                    ("000003", "2026-06-07"),
                    ("000004", "2026-09-04"),
                ]
            ),
        }

    def test_buffer_clips_three_segments(self, monkeypatch):
        seen: list[str] = []

        def spy_replay(subset, params):
            seen.extend(r["date"] for r in subset)
            return _scripted_replay(subset, params)

        rep = _run_v2(
            monkeypatch, self._buffer_spec(), spy_replay, n_random=2, c4_min_pool=2
        )
        segs = rep["segments"]
        assert segs["mining_a"]["n_signals"] == 2
        assert segs["mining_a"]["n_clipped"] == 2
        assert segs["judgment"]["n_signals"] == 2
        assert segs["judgment"]["n_clipped"] == 2
        assert segs["mining_b"]["n_signals"] == 2
        assert segs["mining_b"]["n_clipped"] == 2
        assert segs["mining_merged"] == {"n_signals": 4}
        assert rep["n_signals"] == {"mining": 4, "judgment": 2}
        # 裁剪在 replay 之前：隔离带日期从未进重放
        assert not (
            set(seen)
            & {
                "2016-10-03",
                "2016-12-30",
                "2021-10-03",
                "2021-12-31",
                "2026-06-07",
                "2026-09-04",
            }
        )
        # 截止日当日保留（入场日 ≤ entry_deadline）
        assert {"2016-10-02", "2021-10-02", "2026-06-06"} <= set(seen)

    def test_legacy_never_clips(self, monkeypatch):
        """对照钉：legacy 单段不裁剪（R40 行为逐位不变）——判定窗尾段
        （>挖掘 B 截止日 2026-06-06）信号照常进池、报告无 segments 块。"""

        def mk(year, extra):
            spec: dict[str, list] = {}
            for k in range(12):
                code = f"{k % 6:06d}"
                day = f"{year}-{(k % 12) + 1:02d}-{(k % 28) + 1:02d}"
                spec.setdefault(code, []).append((day, k, 50.0, k % 3))
            for j, (code, day) in enumerate(extra):
                spec.setdefault(code, []).append((day, 100 + j, 50.0, j % 3))
            return _per_code(spec)

        spec = {
            ("2022-01-01", "2024-07-31"): mk(2023, []),
            ("2024-08-01", "2026-09-04"): mk(
                2025,
                [
                    ("000000", "2026-08-10"),  # >2026-06-06（v2 会剔，legacy 不剔）
                    ("000001", "2026-09-04"),  # 窗末日
                ],
            ),
        }
        rep = _run(monkeypatch, spec, _scripted_replay, n_random=2, c4_min_pool=2)
        assert rep["n_signals"] == {"mining": 12, "judgment": 14}
        assert "segments" not in rep


#: legacy R40 金色快照（gzip+base64）：v2 切换**前**代码以同夹具
#: （_three_level_spec(180) + _scripted_replay + _args(n_random=5)）跑出的
#: 报告规范化 JSON（sort_keys）。window_usage 块按定案改 v2 口径、
#: provenance 含环境相关 git 字段——两块比对前剔除，其余逐位同一。
_GOLDEN_R40_B64 = (
    "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"
)


def _golden_r40() -> str:
    return gzip.decompress(base64.b64decode(_GOLDEN_R40_B64)).decode("utf-8")


class TestLegacyR40BitIdentical:
    """钉（d）：legacy R40 参数 ⇒ 接受且合成数据报告与 v2 切换前逐位同一。"""

    def test_legacy_r40_report_bit_identical_to_pre_v2(self, monkeypatch):
        # R40 参数（2022-01-01~2024-07-31 / 2024-08-01~2026-09-04，
        # 均 ⊆ 挖掘 B 且互不相交）⇒ 合法 legacy 兼容通道
        ap = fes._build_parser()
        args = ap.parse_args(
            [
                "--tag",
                "t",
                "--mining-start",
                "2022-01-01",
                "--mining-end",
                "2024-07-31",
                "--judgment-start",
                "2024-08-01",
                "--judgment-end",
                "2026-09-04",
            ]
        )
        fes._check_windows(args, ap)  # 不炸即过（合法）

        rep = _run(monkeypatch, _three_level_spec(180), _scripted_replay, n_random=5)
        got = dict(rep)
        wu_blk = got.pop("window_usage")
        got.pop("provenance")
        # 浮点归一化比对（helpers_golden——CI 异构 CPU 浮点末位差，v0.359）
        assert canon_json(got) == canon_json(json.loads(_golden_r40())), (
            "legacy R40 报告必须与 v2 切换前同一（浮点归一 1e-12）"
        )
        # 唯二定案变更：window_usage 块改 v2 口径（判定窗在挖掘段内不登记）
        assert wu_blk == {
            "window": "mining_segment",
            "k": None,
            "note": "判定窗在挖掘段内（非受保护段 2017~2021）——读挖掘段不入台账（v2 口径）",
        }


class TestMiningInJudgmentSegmentRejected:
    """挖掘窗 ∩ 判定段 ⇒ 拒绝（v0.361 owner review——绕开「只读一次」的后门）。"""

    def test_mining_inside_judgment_segment(self):
        ap = fes._build_parser()
        args = ap.parse_args(
            [
                "--tag",
                "t",
                "--mining-start",
                "2017-01-01",
                "--mining-end",
                "2019-06-30",
                "--judgment-start",
                "2022-01-01",
                "--judgment-end",
                "2024-07-31",
            ]
        )
        with pytest.raises(SystemExit):
            fes._check_windows(args, ap)
