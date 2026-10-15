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


def _with_batch(args, tool):
    """v0.374 批次锁（TODO #95）：生产方向（注入件全 None）跑数须先入批——
    登记文件已被 conftest 改道 tmp，这里开批并把批次身份写进 args。"""
    from custos.research import judgment_batch as jb

    h = jb.config_hash(tool, args)
    jb.open_batch("b1", [{"id": "c1", "config_hash": h, "unit": "R", "note": "t"}])
    args.batch_id, args.candidate_id = "b1", "c1"
    return jb


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
        args = _args_v2(
            n_random=2,
            c4_min_pool=2,
            codes="",
            codes_file="",
            universe_local=False,
            universe_sample=0,
            universe_seed=42,
            count=2000,
        )
        _with_batch(args, "factor_exit_study")  # v0.374：生产方向须带批次
        return fes.run_study(
            args,
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
        args = _args(
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
        )
        _with_batch(args, "factor_exit_study")  # v0.374：判定子窗相交 ⇒ 须带批次
        rep = fes.run_study(
            args,
            warm_fn=None,
            replay_fn=None,
        )
        assert len(calls) == 1
        assert calls[0]["unit"] == "R39"
        assert calls[0]["window"] == "judgment_2017_2021"
        assert calls[0]["synthetic"] is False
        assert rep["window_usage"]["window"] == "judgment_2017_2021"

    def test_production_without_batch_rejected(self):
        """v0.374 批次锁（TODO #95）：v2 缺省窗 + 无批次 ⇒ 数据加载之前
        ap.error（exit 2）——门在暖身/加载之前，无需任何数据桩。"""
        with pytest.raises(SystemExit) as exc:
            fes.run_study(
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
        assert exc.value.code == 2

    def test_mark_read_lands_and_auto_closes(self, monkeypatch):
        """带合法批次 ⇒ 过闸，收尾 mark_read 落进 tmp 登记文件；单候选批
        读完自动关批（末位候选读完自动 close）。"""
        _wu_spy(monkeypatch)
        self._production_run(monkeypatch)
        from custos.research import judgment_batch as jb

        rows = [
            json.loads(ln)
            for ln in jb.REGISTRY.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        assert [r["ev"] for r in rows] == ["open", "claim", "read", "close"]
        assert (
            rows[1]["candidate_id"] == "c1" and rows[1]["tool"] == "factor_exit_study"
        )


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
    "Z0QBAA=="
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
