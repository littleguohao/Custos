# -*- coding: utf-8 -*-
"""score_tier_position_study（R42 Phase 1）钉测。

锁的契约（蓝图 §钉测清单）：①切点只估挖掘窗（判定窗分布极端偏移不改
切点——泄漏钉）；②C2 单调三形态（过/倒挂 falsified/相邻打平读法——
打平不算倒挂但 高−低 必须严格 >0）；③C3 MDE 三态（双窗 |ΔexpR|<0.035
⇒ 无法区分/≥0.035 按符号读/W2 过滤器对照注明在报告）；④C1 不过 ⇒
untested 优先于其他判决 + 每档每窗簇数入报告（v0.335）；⑤C4 池重抽
确定性 + 三态（confirmed_pass/provisional/indeterminate）+
confirmed_fail + 整簇打乱钉（v0.335：同簇同档逐臂校验）；⑥空结果
护栏（任一窗 0 信号 ⇒ rc=2 不落盘）；⑦pre2019 + 前向 holdout 硬拒绝；
⑧报告自含字段（切点/权重格/MDE/W2 注明/组合层未测注明/
cost_sensitivity/provenance/window_usage/forward_holdout_note）；
⑨台账生产方向钉（v0.343：生产 synthetic=False / 注入 synthetic=True）。

v2 窗口布局钉（TODO #90 owner 定案——窗口四参缺省改 None）：(a) 缺省=
v2 三段（mining_a 2000~2016 / judgment 2017~2021 / mining_b
2022~2026-09-04，逐段 warm）；(b) A∪B 合并池读数=手工合并两窗信号重算
（逐位）；(c) 段末隔离带三刀生效（截止 2016-10-02/2021-10-02/2026-06-06）
且剔除数入 segments 块；(d) legacy R40 参数（均⊆B 互不相交）接受且合成
数据报告与 v2 切换前**逐位一致**（golden 钉；window_usage/provenance/
generated_at 为定案变化/易失件除外）；(e) 越段/相交/半截窗 SystemExit、
判定在挖掘前但不相交合法（旧次序规则作废）；(f) judgment==SEG_JUDGMENT
⇒ 登记新键 judgment_2017_2021，legacy ⇒ 不登记（报告 mining_segment
注明）；(g) 语义合法变化的断言同步改写（TestReportSelfContained 的
window_usage、TestProductionRecordUse 键名）。
"""

from __future__ import annotations

import argparse
import json
import random

import pytest

from custos.research import factor_exit_study as fes
from custos.research import score_tier_position_study as stp
from custos.research.load_window import EXIT_BARS_HOLDOUT_NOTE
from custos.research import load_window as lw

_WINDOWS = (("2022-01-01", "2024-07-31"), ("2024-08-01", "2026-09-04"))


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
        codes="",
        codes_file="",
        count=2000,
        universe_sample=0,
        universe_local=False,
        universe_seed=42,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _mk_per_code(n_codes=12, sigs_per_code=15, score_shift=0.0):
    """N 码 × M 信号（i=10,12,… 逐码唯一）；score=10+(k mod 90)（+shift）——

    默认夹具 180 信号/窗：三档切点稳定 ≈[39.67, 69.33]，整数分阈值
    70/40 与研究分档**逐位一致**（各档 60 笔 ≥ C1 线 50）。

    v0.335：score 改 ``10+(k·37 mod 90)``——gcd(37,90)=1 ⇒ 总体多重集仍是
    10..99 各两次（切点逐位不变），但同码 15 个连续 k 的分数**跨档散开**
    （真实数据同码簇分数相近是另一极端——簇统计归 test_null_calibration
    的成簇夹具管，本套件夹具让单元测试聚焦 C1/C2/C3 机械读数）。"""
    per_code = {}
    k = 0
    for c_i in range(n_codes):
        code = f"{c_i:06d}"
        signals = []
        scores = {}
        for s_i in range(sigs_per_code):
            day = f"2023-{(k % 12) + 1:02d}-{(k % 28) + 1:02d}"
            signals.append({"i": 10 + s_i * 2, "date": day, "score": 0.0})
            scores[day] = 10.0 + ((k * 37) % 90) + score_shift
            k += 1
        for s in signals:
            s["score"] = scores[s["date"]]
        per_code[code] = {"signals": signals, "scores": scores}
    return per_code


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
        "score": r.get("score", 50.0),
    }


def _band_of(score):
    """夹具分的档阈值（与研究分档逐位一致，见 _mk_per_code docstring）。"""
    return "high" if score >= 70 else ("mid" if score >= 40 else "low")


def _banded_replay(cycles):
    """每档一个 ret 循环表：档内计数器取模定胜负（确定性、档内笔数精确）。"""

    def _replay(subset, params):
        counters = {"high": 0, "mid": 0, "low": 0}
        out = []
        for r in subset:
            band = _band_of(r["score"])
            k = counters[band]
            counters[band] += 1
            cyc = cycles[band]
            out.append(_trade_of(r, cyc[k % len(cyc)]))
        return out

    return _replay


#: 单调大胜：margin 0.333/0.167/−0.25（高−低 0.583）；ΔexpR W1/W2/W3 全正可读
_replay_monotone = _banded_replay(
    {"high": [0.10, -0.02], "mid": [0.02, -0.01], "low": [0.005, -0.015]}
)
#: 倒挂：margin −0.25/0.167/0.333 ⇒ C2 False
_replay_inverted = _banded_replay(
    {"high": [0.005, -0.015], "mid": [0.02, -0.01], "low": [0.10, -0.02]}
)
#: 高=中>低（margin 0.167/0.167/−0.25，高−低 0.417>0）⇒ 打平读法过
_replay_tie_high_mid = _banded_replay(
    {"high": [0.05, -0.025], "mid": [0.05, -0.025], "low": [0.005, -0.015]}
)
#: 三档全同（margin 0.167 齐平，高−低=0）⇒ C2 False
_replay_all_equal = _banded_replay(
    {"high": [0.02, -0.01], "mid": [0.02, -0.01], "low": [0.02, -0.01]}
)
#: 微差单调：margin 0.0083/0/−0.0167（相邻档差 <0.086 ⇒ 低置信）；
#: ΔexpR 全组 |Δ|<0.035 ⇒ 与零无法区分
_replay_tiny_delta = _banded_replay(
    {"high": [0.0305, -0.0295], "mid": [0.03, -0.03], "low": [0.029, -0.031]}
)
#: margin 单调（0.5/0.125/0.05）但高档均值 R 最低 ⇒ W1/W2 ΔexpR 可读为负
_replay_neg_delta = _banded_replay(
    {
        "high": [-0.004, 0.012, 0.012, 0.012],
        "mid": [0.05, -0.03],
        "low": [0.4, -0.10, -0.10, -0.10],
    }
)


def _replay_no_r(subset, params):
    """r_multiple 全缺 ⇒ 加权 expR 不可评 ⇒ C4 臂全 None ⇒ 池空。"""
    out = []
    for j, r in enumerate(subset):
        t = _trade_of(r, 0.02 if j % 2 == 0 else -0.01)
        t["r_multiple"] = None
        out.append(t)
    return out


def _run(per_code_or_spec, replay, **kw):
    if isinstance(per_code_or_spec, dict) and all(
        isinstance(v, tuple) for v in per_code_or_spec.keys()
    ):
        spec = per_code_or_spec
    else:
        spec = {w: per_code_or_spec for w in _WINDOWS}
    warm_fn = lambda s, e: spec[(s, e)]  # noqa: E731
    return stp.run_study(_args(**kw), warm_fn=warm_fn, replay_fn=replay)


# ---------------------------------------------------------------------------
# v2 三段布局夹具（TODO #90：四参全 None ⇒ mining_a ∪ mining_b 合并池 +
# judgment=受保护段 2017~2021；跨段同码日期/ i 区段错开 ⇒ 跨段不成簇、
# 簇映射键不撞号）
# ---------------------------------------------------------------------------


def _v2_args(**kw):
    base = dict(
        mining_start=None, mining_end=None, judgment_start=None, judgment_end=None
    )
    base.update(kw)
    return _args(**base)


def _mk_seg_per_code(day_of, n_codes=12, sigs_per_code=15, i0=10):
    """段夹具（_mk_per_code 同骨架）：day_of(k)→date 串；i 从 i0 起按 2 步进
    （段间 i 区段错开 ⇒ 同码跨段不成簇）；分数多重集与 _mk_per_code 逐位
    一致（10..99 各两次 ⇒ 三档切点 ≈[39.67, 69.33]、档阈值 70/40 不变，
    每段 180 信号、各档 60 笔 ≥ C1 线）。"""
    per_code = {}
    k = 0
    for c_i in range(n_codes):
        code = f"{c_i:06d}"
        signals = []
        scores = {}
        for s_i in range(sigs_per_code):
            day = day_of(k)
            signals.append({"i": i0 + s_i * 2, "date": day, "score": 0.0})
            scores[day] = 10.0 + ((k * 37) % 90)
            k += 1
        for s in signals:
            s["score"] = scores[s["date"]]
        per_code[code] = {"signals": signals, "scores": scores}
    return per_code


def _v2_spec():
    """默认 v2 三段 spec：日期都在各段隔离带截止日前（零剔除）。"""
    return {
        lw.SEG_MINING_A: _mk_seg_per_code(
            lambda k: f"2010-{(k % 12) + 1:02d}-{(k % 28) + 1:02d}", i0=10
        ),
        lw.SEG_JUDGMENT: _mk_seg_per_code(
            lambda k: f"2019-{(k % 12) + 1:02d}-{(k % 28) + 1:02d}", i0=5000
        ),
        lw.SEG_MINING_B: _mk_seg_per_code(
            lambda k: f"2023-{(k % 12) + 1:02d}-{(k % 28) + 1:02d}", i0=10000
        ),
    }


def _run_v2(spec, replay, **kw):
    warm_fn = lambda s, e: spec[(s, e)]  # noqa: E731
    return stp.run_study(_v2_args(**kw), warm_fn=warm_fn, replay_fn=replay)


def _stateless_replay(subset, params):
    """无状态 replay：ret 仅由该笔分数决定（与调用切分/次数无关）——v2
    逐段重放后合并 = 合并一次重放，供合并池手工重算逐位对拍。"""
    return [_trade_of(r, (r["score"] - 50.0) / 1000.0) for r in subset]


# ---------------------------------------------------------------------------
# ① 切点只估挖掘窗（泄漏钉）
# ---------------------------------------------------------------------------


class TestMiningOnlyCuts:
    def test_judgment_shift_does_not_move_cuts(self):
        """判定窗分数整体 +100 ⇒ 若误用判定窗估计切点，切点会完全改写。"""
        mining = _mk_per_code()
        judgment = _mk_per_code(score_shift=100.0)
        spec = {_WINDOWS[0]: mining, _WINDOWS[1]: judgment}
        rep = _run(spec, _replay_monotone)
        cuts = rep["cuts"]["values_mining_estimated"]
        mining_scores = [s for pack in mining.values() for s in pack["scores"].values()]
        assert cuts == pytest.approx(
            fes.quantile_cuts(mining_scores, stp.TIER_QS), rel=1e-12
        )
        assert all(c < 100 for c in cuts), "切点必须来自挖掘窗分布"
        # 判定窗全部信号（分 ~110+）落高档——切点没被判定窗拉走
        n_j = rep["accounting"]["judgment"]["n_subset"]
        assert rep["tiers"]["judgment"]["high"]["n"] == n_j
        assert rep["tiers"]["judgment"]["mid"]["n"] == 0
        assert rep["tiers"]["judgment"]["low"]["n"] == 0
        # 与未偏移跑的切点逐位一致
        rep_base = _run(_mk_per_code(), _replay_monotone)
        assert cuts == rep_base["cuts"]["values_mining_estimated"]


# ---------------------------------------------------------------------------
# ② C2 单调三形态
# ---------------------------------------------------------------------------


class TestC2Monotonicity:
    def test_monotone_pass_and_candidate(self):
        rep = _run(_mk_per_code(), _replay_monotone)
        c = rep["criteria"]
        assert c["C1"]["ok"] is True
        assert c["C2"]["ok"] is True, c["C2"]
        for w in ("mining", "judgment"):
            wd = c["C2"]["windows"][w]
            assert wd["monotone"] is True and wd["high_low"] > 0
            assert wd["mde_low_confidence"] is False  # 相邻档差 ≥0.166 ≫ 0.086
        assert c["C2"]["low_confidence"] is False
        assert c["C3"]["ok"] is True
        assert c["C4"]["state"] == "confirmed_pass"
        assert rep["verdict"] == "candidate"

    def test_inverted_falsified(self):
        rep = _run(_mk_per_code(), _replay_inverted)
        c = rep["criteria"]
        assert c["C2"]["ok"] is False
        assert rep["verdict"] == "falsified"  # C2 不过当场收口（预注册四态）

    def test_adjacent_tie_passes_when_high_low_positive(self):
        """打平读法：「高=中>低」按字面 ≥ 算过（高−低 必须 >0）。"""
        rep = _run(_mk_per_code(), _replay_tie_high_mid)
        c2 = rep["criteria"]["C2"]
        assert c2["ok"] is True, c2
        for w in ("mining", "judgment"):
            wd = c2["windows"][w]
            assert wd["adjacent"]["high_mid"] == 0.0  # 高=中 打平不算倒挂
            assert wd["high_low"] > 0

    def test_all_equal_fails_high_low_must_be_strictly_positive(self):
        """三档齐平 ⇒ 高−低=0 ⇒ 不过（打平读法的硬边界）。"""
        rep = _run(_mk_per_code(), _replay_all_equal)
        c2 = rep["criteria"]["C2"]
        assert c2["ok"] is False
        assert rep["verdict"] == "falsified"


# ---------------------------------------------------------------------------
# ③ C3 MDE 三态 + W2 对照注明
# ---------------------------------------------------------------------------


class TestC3Mde:
    def test_tiny_delta_indistinguishable(self):
        """双窗 |ΔexpR| 全 <0.035 ⇒ 全组 indistinguishable；c3 ok=None
        （不读增量也不读证伪——不放行不判死）。"""
        rep = _run(_mk_per_code(), _replay_tiny_delta)
        c = rep["criteria"]
        assert c["C2"]["ok"] is True  # 符号序单调仍成立
        assert c["C2"]["low_confidence"] is True  # 相邻档差 <0.086 ⇒ 低置信
        c3 = c["C3"]
        assert c3["ok"] is None
        for g in ("W1", "W2", "W3"):
            assert c3["groups"][g]["pass"] is False
            for w in ("mining", "judgment"):
                assert c3["groups"][g]["reading"][w] == "indistinguishable"

    def test_readable_positive_passes(self):
        """≥0.035 按符号读：正 ⇒ positive + pass（candidate 全链已锁）。"""
        rep = _run(_mk_per_code(), _replay_monotone)
        c3 = rep["criteria"]["C3"]
        assert c3["ok"] is True
        for g in ("W1", "W2", "W3"):
            for w in ("mining", "judgment"):
                assert c3["groups"][g]["reading"][w] == "positive"
        assert c3["best_group_mining"] == "W2"  # 强倾斜 Δ 最大（0.30）

    def test_readable_negative_recorded_not_falsified_by_c3(self):
        """margin 单调但高档均值 R 最低 ⇒ W1/W2 Δ 可读为负如实记录；
        C3 不过只放行 None（预注册四态：falsified 只来自 C2/C4）。"""
        rep = _run(_mk_per_code(), _replay_neg_delta)
        c = rep["criteria"]
        assert c["C2"]["ok"] is True, c["C2"]  # margin 单调成立
        c3 = c["C3"]
        assert c3["ok"] is None  # 无组双窗正 ⇒ 不过不判死
        for g in ("W1", "W2"):
            for w in ("mining", "judgment"):
                assert c3["groups"][g]["reading"][w] == "negative"
        assert c3["groups"]["W3"]["reading"]["mining"] == "indistinguishable"

    def test_w2_note_in_report(self):
        rep = _run(_mk_per_code(), _replay_monotone)
        assert "过滤器" in rep["criteria"]["C3"]["w2_note"]
        assert any("过滤器" in n for n in rep["notes"])


# ---------------------------------------------------------------------------
# ④ C1 优先
# ---------------------------------------------------------------------------


class TestC1Priority:
    def test_c1_untested_takes_priority(self):
        """每档 ~6 笔 ≪ 50 ⇒ untested（即使 C2 也不过，C1 优先）。"""
        per_code = _mk_per_code(n_codes=3, sigs_per_code=6)
        rep = _run(per_code, _replay_inverted)
        assert rep["criteria"]["C1"]["ok"] is False
        assert rep["verdict"] == "untested"

    def test_c1_reports_cluster_counts(self):
        """v0.335 修订：C1 阈值不变（按笔数），每档每窗簇数入报告供判读
        打折——默认夹具 12 码 × 码内 i 间隔 2 ≤5 ⇒ 每窗 12 簇。"""
        rep = _run(_mk_per_code(), _replay_monotone)
        c1 = rep["criteria"]["C1"]
        for w in ("mining", "judgment"):
            for tier in ("high", "mid", "low"):
                assert c1["n_clusters"][w][tier] == 12
                assert rep["tiers"][w][tier]["n_clusters"] == 12


# ---------------------------------------------------------------------------
# ⑤ C4：确定性 + 状态机 + 整簇打乱（v0.335）
# ---------------------------------------------------------------------------


class TestC4:
    def test_arm_seeds_reproducible(self):
        a = _run(_mk_per_code(), _replay_monotone)
        b = _run(_mk_per_code(), _replay_monotone)
        assert a["criteria"]["C4"]["pool"] == b["criteria"]["C4"]["pool"]

    def test_arm_tier_draw_unit(self):
        """整簇抽签单元钉：同簇同档、确定性、labels 不参与计数对齐。"""
        labels = ["low", "mid", "high", "low", "mid", "high"]
        clusters = [0, 0, 0, 1, 1, 1]
        out = stp.arm_tier_draw(labels, clusters, random.Random(5))
        assert out[0] == out[1] == out[2]
        assert out[3] == out[4] == out[5]
        assert out == stp.arm_tier_draw(labels, clusters, random.Random(5))

    def test_c4_arm_moves_whole_clusters(self, monkeypatch):
        """整簇移动钉（v0.335）：C4 每条臂的档归属在同簇内恒定——默认夹具
        12 码、码内 i 间隔 2 ≤5 ⇒ 每窗 12 簇，逐臂校验。"""
        seen = []
        orig = stp.arm_tier_draw

        def spy(labels, clusters, rng):
            out = orig(labels, clusters, rng)
            seen.append((list(clusters), list(out)))
            return out

        monkeypatch.setattr(stp, "arm_tier_draw", spy)
        rep = _run(_mk_per_code(), _replay_monotone)
        assert len(seen) == rep["criteria"]["C4"]["evaluated"]  # 每臂恰调一次
        for clusters, out in seen:
            assert len(set(clusters)) == 12, "挖掘窗 12 码 ⇒ 12 簇"
            by_cluster: dict[int, str] = {}
            for cid, lab in zip(clusters, out):
                assert by_cluster.setdefault(cid, lab) == lab, "同簇必须同档"

    def test_empty_pool_indeterminate(self):
        """r_multiple 全缺 ⇒ 臂全 None ⇒ 池空=indeterminate（不放行，v0.297 族）。"""
        rep = _run(_mk_per_code(), _replay_no_r, n_random=3)
        c4 = rep["criteria"]["C4"]
        assert c4["pool_size"] == 0
        assert c4["evaluated"] == c4["max_arms"] == 30  # 打满上限（v0.317 族）
        assert c4["state"] == "indeterminate"

    def test_small_pool_provisional(self):
        rep = _run(_mk_per_code(), _replay_monotone, n_random=3, c4_min_pool=50)
        c4 = rep["criteria"]["C4"]
        assert c4["pool_size"] == 3 < c4["min_pool"]
        assert c4["state"] == "provisional"
        assert rep["verdict"] == "provisional"  # 池未满不放行不判死

    def test_confirmed_fail_on_inverted(self):
        rep = _run(_mk_per_code(), _replay_inverted, n_random=4, c4_min_pool=3)
        c4 = rep["criteria"]["C4"]
        assert c4["state"] == "confirmed_fail"
        assert c4["candidate_delta_mining"] <= (c4["q95"] or 0)

    def test_confirmed_pass_on_monotone(self):
        rep = _run(_mk_per_code(), _replay_monotone, n_random=4, c4_min_pool=3)
        c4 = rep["criteria"]["C4"]
        assert c4["state"] == "confirmed_pass"
        assert c4["candidate_group"] == "W2"
        assert c4["candidate_delta_mining"] > c4["q95"]


# ---------------------------------------------------------------------------
# ⑤b 簇映射命中率 fail-closed（v0.336，owner review）
# ---------------------------------------------------------------------------


def _replay_misaligned(subset, params):
    """交易 entry_date 全部改成信号表外的日期 ⇒ 簇映射命中率 0。"""
    return [_trade_of({**r, "date": "1999-01-04"}, 0.01) for r in subset]


class TestClusterMapGuard:
    def test_hit_rate_reported_full(self):
        """正常夹具：命中率=1.0 入报告（双窗 mapped==total==180）。"""
        rep = _run(_mk_per_code(), _replay_monotone)
        cm = rep["cluster_map"]
        assert cm["min_rate"] == stp.CLUSTER_MAP_MIN_RATE == 0.99
        for w in ("mining", "judgment"):
            assert cm[w]["rate"] == 1.0
            assert cm[w]["mapped"] == cm[w]["total"] == 180

    def test_misaligned_keys_fail_closed(self):
        """错位键 ⇒ RuntimeError（口径缺陷不猜——负 id 兜底会把 C4 静默
        退回逐笔打乱，v0.335 修掉的 bug）。"""
        with pytest.raises(RuntimeError, match="簇映射命中率护栏"):
            _run(_mk_per_code(), _replay_misaligned)

    def test_misaligned_cli_rc2_no_artifact(self, tmp_path):
        """CLI 层：护栏 ⇒ rc=2 不落盘（同空结果护栏哲学）。"""
        per_code = _mk_per_code()
        rc = stp.main(
            [
                "--mining-start",
                "2022-01-01",
                "--mining-end",
                "2024-07-31",
                "--judgment-start",
                "2024-08-01",
                "--judgment-end",
                "2026-09-04",
                "--tag",
                "cm_t",
                "--out-dir",
                str(tmp_path),
            ],
            warm_fn=lambda s, e: per_code,
            replay_fn=_replay_misaligned,
        )
        assert rc == 2
        assert not (tmp_path / "cm_t").exists(), "簇映射护栏：不落盘"


# ---------------------------------------------------------------------------
# ⑥⑦ CLI 护栏
# ---------------------------------------------------------------------------


class TestCli:
    def test_pre2019_overlap_rejected(self):
        with pytest.raises(SystemExit):
            stp.main(
                [
                    "--mining-start",
                    "2015-01-01",
                    "--mining-end",
                    "2024-07-31",
                    "--judgment-start",
                    "2024-08-01",
                    "--judgment-end",
                    "2026-09-04",
                    "--tag",
                    "t",
                ]
            )

    def test_forward_holdout_rejected(self):
        with pytest.raises(SystemExit):
            stp.main(
                [
                    "--mining-start",
                    "2022-01-01",
                    "--mining-end",
                    "2024-07-31",
                    "--judgment-start",
                    "2024-08-01",
                    "--judgment-end",
                    "2026-09-05",  # 前向 holdout 冻结段（≥2026-09-05 硬拒绝）
                    "--tag",
                    "t",
                ]
            )

    def test_empty_guard_no_artifact(self, tmp_path):
        per_code = _mk_per_code(n_codes=1, sigs_per_code=3)
        for pack in per_code.values():
            pack["scores"] = {}  # 全缺分 ⇒ 子集空
        rc = stp.main(
            [
                "--mining-start",
                "2022-01-01",
                "--mining-end",
                "2024-07-31",
                "--judgment-start",
                "2024-08-01",
                "--judgment-end",
                "2026-09-04",
                "--tag",
                "empty_t",
                "--out-dir",
                str(tmp_path),
            ],
            warm_fn=lambda s, e: per_code,
            replay_fn=_replay_monotone,
        )
        assert rc == 2
        assert not (tmp_path / "empty_t").exists(), "空结果护栏：不落盘"


# ---------------------------------------------------------------------------
# ⑧ 报告自含字段
# ---------------------------------------------------------------------------


class TestReportSelfContained:
    def test_fields(self):
        rep = _run(_mk_per_code(), _replay_monotone)
        assert rep["schema"] == "score_tier_position/v1"
        # 切点自含（≈[39.67, 69.33]——挖掘窗 1/3、2/3 分位）
        cuts = rep["cuts"]["values_mining_estimated"]
        assert cuts == pytest.approx([39.667, 69.333], abs=0.01)
        # 出场钉死档 = DEFAULT_EXIT_GRID pct5_trail08 参数原样
        assert stp.EXIT_PARAMS == {
            "stop_mode": "pct",
            "stop_pct": 5,
            "trail_pct": 0.08,
        }
        assert rep["params"]["exit"]["name"] == "pct5_trail08"
        assert rep["params"]["exit"]["params"] == stp.EXIT_PARAMS
        # 权重格三组归一化（平均权重=1；W3=9/7,6/7,6/7）
        wg = rep["params"]["weight_grid"]
        assert set(wg) == {"W1", "W2", "W3"}
        for g in wg.values():
            assert sum(g.values()) / 3 == pytest.approx(1.0, rel=1e-9)
        assert wg["W3"]["high"] == pytest.approx(9 / 7, rel=1e-9)
        assert wg["W2"]["low"] == 0.0
        # MDE 冻结值入判据块
        assert rep["criteria"]["C2"]["mde"] == 0.086
        assert rep["criteria"]["C3"]["mde"] == 0.035
        assert rep["criteria"]["C1"]["min_tier_n"] == 50
        # 每档每窗 n + 三组权重逐档读数（margin/加权 expR/Δ）
        assert rep["tiers"]["mining"]["high"]["n"] == 60
        rd = rep["readings"]["mining"]
        assert rd["groups"]["W2"]["weighted_expR"] > rd["equal_expR"]
        assert rd["groups"]["W2"]["delta_expR"] == pytest.approx(0.30, abs=1e-9)
        # W2 对照注明 + 组合层未测注明
        assert any("过滤器" in n for n in rep["notes"])
        assert any("组合层" in n and "未测" in n for n in rep["notes"])
        assert "组合层" in rep["criteria"]["C3"]["portfolio_note"]
        # 成本副读数（v0.329）：高档/低档 × 双窗 50bps 双报 + 高−低 Δ 翻号
        cs = rep["cost_sensitivity"]
        assert cs["base_bps"] == 25.0 and cs["side_bps"] == 50.0
        assert set(cs["arms"]) == {
            "high_mining",
            "low_mining",
            "high_judgment",
            "low_judgment",
        }
        assert set(cs["deltas"]) == {
            "d_margin_high_low_mining",
            "d_margin_high_low_judgment",
        }
        d = cs["deltas"]["d_margin_high_low_mining"]
        assert d["base"] > 0 and d["side"] > 0 and d["flip"] is False
        # provenance / window_usage / holdout 注记
        pv = rep["provenance"]
        assert pv["unit"] == "R42"
        assert pv["criteria_version"] == "v0.312/v0.326"
        assert pv["pre_reg_doc"].endswith("R42_score_tier_position.md")
        # legacy（判定窗 ⊆ 挖掘B）：v2 口径不入台账（TODO #90 语义改写）
        assert rep["window_usage"]["window"] == "mining_segment"
        assert rep["window_usage"]["k"] is None
        assert "不入台账" in rep["window_usage"]["note"]
        assert rep["forward_holdout_note"] == EXIT_BARS_HOLDOUT_NOTE
        assert rep["verdict"] == "candidate"


# ---------------------------------------------------------------------------
# ⑨ 台账生产方向钉（v0.343，owner——r42_probe 漏登根因）
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
    合成 ⇒ record_use 必须以 ``synthetic=False`` 被调且 k 直通报告（生产
    口径）。防回归语义：以后有人在生产分支里又重绑注入参数 ⇒ 入口快照
    ``injected`` 变 True ⇒ 本测试立刻红。

    v2 台账口径（TODO #90）：判定窗==受保护段 SEG_JUDGMENT（2017~2021）才
    登记（新键 judgment_2017_2021）；legacy 窗（⊆挖掘B）读挖掘段不登记。"""

    def _production_run(self, monkeypatch, spec, args):
        import pandas as pd

        from custos.datasource.local_tdx import local_tdx_data
        from custos.research import backtest_factors as bt
        from custos.research import exit_campaign as ec

        monkeypatch.setattr(bt, "_resolve_universe", lambda ns, ap: ["000000"])
        monkeypatch.setattr(bt, "load_amv_regime", lambda since: {"2022-01-03": "做多"})
        monkeypatch.setattr(
            local_tdx_data,
            "get_ohlcv_table",
            lambda code, count: pd.DataFrame({"date": ["2022-01-03"]}),
        )
        monkeypatch.setattr(
            ec,
            "warm_v0_signals",
            lambda codes, regime, index_df, *, count, cost_bps, start, end: spec[
                (start, end)
            ],
        )
        monkeypatch.setattr(
            fes,
            "replay_signals",
            lambda per_code, subset, params, regime, cost_bps: _replay_monotone(
                subset, params
            ),
        )
        return stp.run_study(args, warm_fn=None, replay_fn=None)

    def test_production_records_with_synthetic_false(self, monkeypatch):
        """v2 缺省布局（判定=受保护段）：生产路径登记新键 + synthetic=False。"""
        calls = _wu_spy(monkeypatch)
        rep = self._production_run(
            monkeypatch, _v2_spec(), _v2_args(n_random=2, c4_min_pool=2)
        )
        assert len(calls) == 1
        assert calls[0]["unit"] == "R42"
        assert calls[0]["window"] == "judgment_2017_2021"
        assert calls[0]["synthetic"] is False, "生产路径必须按 synthetic=False 登记"
        assert rep["window_usage"]["window"] == "judgment_2017_2021"
        assert rep["window_usage"]["k"] == 7, "生产口径 k 直通报告"
        assert "第 7 次被读" in rep["window_usage"]["note"]

    def test_legacy_production_not_registered(self, monkeypatch):
        """legacy R40 窗（判定 ⊆ 挖掘B）：生产路径也不登记（v2 口径——读
        挖掘段自由，报告注明 mining_segment）。"""
        calls = _wu_spy(monkeypatch)
        spec = {w: _mk_per_code() for w in _WINDOWS}
        rep = self._production_run(monkeypatch, spec, _args(n_random=2, c4_min_pool=2))
        assert calls == [], "判定窗在挖掘段内——不入台账（v2 口径）"
        assert rep["window_usage"]["window"] == "mining_segment"
        assert rep["window_usage"]["k"] is None
        assert "不入台账" in rep["window_usage"]["note"]

    def test_injected_still_synthetic(self, monkeypatch):
        """对照钉：同一 v2 夹具走注入路径 ⇒ synthetic=True（双向钉死）。"""
        calls = _wu_spy(monkeypatch)
        rep = _run_v2(_v2_spec(), _replay_monotone, n_random=2, c4_min_pool=2)
        assert len(calls) == 1
        assert calls[0]["window"] == "judgment_2017_2021"
        assert calls[0]["synthetic"] is True
        assert rep["window_usage"]["k"] == 7  # 桩固定返回（跳过写台账由本体管）


# ---------------------------------------------------------------------------
# ⑩ v2 窗口布局钉（TODO #90 owner 定案——窗口四参缺省=None ⇒ v2 三段）
# ---------------------------------------------------------------------------


class TestV2Layout:
    """(a) 缺省=v2 三段：区间正确 + 逐段 warm/collect（挖掘 A、B 各一次 +
    判定一次）+ 段末隔离带截止日/剔除数入 segments 块（默认夹具零剔除）。"""

    def test_default_v2_segments(self):
        spec = _v2_spec()
        calls = []

        def warm(s, e):
            calls.append((s, e))
            return spec[(s, e)]

        rep = stp.run_study(_v2_args(), warm_fn=warm, replay_fn=_replay_monotone)
        assert calls == [
            ("2000-01-01", "2016-12-31"),  # 挖掘 A（原 pre2019 并入）
            ("2022-01-01", "2026-09-04"),  # 挖掘 B（原挖掘窗+原判定窗）
            ("2017-01-01", "2021-12-31"),  # 判定（受保护段，单独记台账）
        ]
        assert rep["layout"] == "v2"
        segs = rep["segments"]
        assert (segs["mining_a"]["start"], segs["mining_a"]["end"]) == (
            "2000-01-01",
            "2016-12-31",
        )
        assert (segs["judgment"]["start"], segs["judgment"]["end"]) == (
            "2017-01-01",
            "2021-12-31",
        )
        assert (segs["mining_b"]["start"], segs["mining_b"]["end"]) == (
            "2022-01-01",
            "2026-09-04",
        )
        # 段末隔离带截止日 = 段末 −90 自然日（load_window 单源）
        assert segs["mining_a"]["entry_deadline"] == "2016-10-02"
        assert segs["judgment"]["entry_deadline"] == "2021-10-02"
        assert segs["mining_b"]["entry_deadline"] == "2026-06-06"
        for name in ("mining_a", "judgment", "mining_b"):
            assert segs[name]["n_signals"] == 180
            assert segs[name]["n_clipped"] == 0
        assert segs["mining_total"]["n_signals"] == 360
        assert segs["mining_total"]["n_pooled"] == 360
        assert rep["windows"]["mining"] == {"start": "2000-01-01", "end": "2026-09-04"}
        assert rep["windows"]["judgment"] == {
            "start": "2017-01-01",
            "end": "2021-12-31",
        }
        # 读数键形态不变：mining = A∪B 合并池 / judgment 单独
        assert set(rep["tiers"]) == {"mining", "judgment"}
        assert rep["tiers"]["mining"]["high"]["n"] == 120  # A 60 + B 60
        assert rep["tiers"]["judgment"]["high"]["n"] == 60
        assert rep["verdict"] == "candidate"


def _merged_mining_records(spec):
    """手工合并 A∪B 信号（与 collect_scored 同口径同序：段序 A→B、码序、
    信号序照夹具构造；默认夹具零剔除 ⇒ 即合并入池清单）。"""
    return [
        {
            "code": code,
            "date": s["date"],
            "i": s["i"],
            "score": pack["scores"][s["date"]],
            "sig": s,
        }
        for seg in (lw.SEG_MINING_A, lw.SEG_MINING_B)
        for code, pack in spec[seg].items()
        for s in pack["signals"]
    ]


class TestV2MergedPool:
    """(b) A∪B 合并池读数 = 手工合并两窗信号重算（逐位 ==，非 approx）。"""

    def test_merged_readings_bit_equal_manual(self):
        spec = _v2_spec()
        rep = _run_v2(spec, _stateless_replay)
        merged = _merged_mining_records(spec)
        # 切点：合并池 1/3、2/3 经验分位（单源重算，逐位 ==）
        assert rep["cuts"]["values_mining_estimated"] == fes.quantile_cuts(
            [r["score"] for r in merged], stp.TIER_QS
        )
        exp_trades = _stateless_replay(merged, None)
        for tier in ("high", "mid", "low"):
            exp_tier = [t for t in exp_trades if _band_of(t["score"]) == tier]
            got = rep["tiers"]["mining"][tier]
            assert got["n"] == len(exp_tier)
            assert got["margin"] == stp._margin_of(exp_tier)
        rs = [t["r_multiple"] for t in exp_trades]
        assert rep["readings"]["mining"]["equal_expR"] == sum(rs) / len(rs)
        labels = [_band_of(t["score"]) for t in exp_trades]
        for g, wts in stp.WEIGHT_GRID.items():
            assert rep["readings"]["mining"]["groups"][g][
                "weighted_expR"
            ] == stp.weighted_expr(labels, exp_trades, wts)
        cm = rep["cluster_map"]["mining"]
        assert cm["mapped"] == cm["total"] == len(merged) == 360
        assert cm["rate"] == 1.0


def _v2_spec_with_buffer_signals():
    """默认 v2 夹具 + 三段各添「截止日后」信号一笔（应被剔除）与「截止日
    前贴身」信号一笔（应保留——边界钉，score=80 落高档）。"""
    spec = _v2_spec()
    extra = {
        lw.SEG_MINING_A: ("2016-11-15", "2016-09-30", 40000),  # 截止 2016-10-02
        lw.SEG_MINING_B: ("2026-07-01", "2026-06-05", 50000),  # 截止 2026-06-06
        lw.SEG_JUDGMENT: ("2021-12-01", "2021-09-30", 60000),  # 截止 2021-10-02
    }
    for seg, (after, before, i) in extra.items():
        pack = spec[seg]["000000"]
        for day in (after, before):
            pack["signals"].append({"i": i, "date": day, "score": 80.0})
            pack["scores"][day] = 80.0
    return spec


class TestV2ClipBuffer:
    """(c) 段末隔离带三刀生效：截止日后信号被剔除、剔除数入 segments 块、
    截止日前贴身信号保留在池。"""

    def test_three_cuts_applied_and_counted(self):
        rep = _run_v2(_v2_spec_with_buffer_signals(), _stateless_replay)
        segs = rep["segments"]
        assert segs["mining_a"]["entry_deadline"] == "2016-10-02"
        assert segs["judgment"]["entry_deadline"] == "2021-10-02"
        assert segs["mining_b"]["entry_deadline"] == "2026-06-06"
        for name in ("mining_a", "judgment", "mining_b"):
            assert segs[name]["n_signals"] == 182, name  # 隔离带前收集数（180+2）
            assert segs[name]["n_clipped"] == 1, name  # 截止日后那笔被剔除
        assert segs["mining_total"]["n_signals"] == 364
        assert segs["mining_total"]["n_clipped"] == 2
        assert segs["mining_total"]["n_pooled"] == 362  # 合并入池 = 364−2
        # 剔除落到读数：mining 池 362 笔（未剔则 364）；贴身保留的 2 笔高档在池
        total = sum(rep["tiers"]["mining"][t]["n"] for t in ("high", "mid", "low"))
        assert total == 362
        assert rep["tiers"]["mining"]["high"]["n"] == 122  # 120 + A/B 各一贴身
        total_j = sum(rep["tiers"]["judgment"][t]["n"] for t in ("high", "mid", "low"))
        assert total_j == 181
        assert rep["tiers"]["judgment"]["high"]["n"] == 61


#: golden（v2 切换前 legacy R40 报告，内嵌于本文件）：合成夹具
#: ``_mk_per_code`` 双窗 + ``_replay_monotone`` + ``_args(n_random=4,
#: c4_min_pool=3)``，canonical json.dumps(sort_keys)；捕获时剔除
#: generated_at（时间戳）/ window_usage（v2 台账口径定案变化）/
#: provenance（git 状态易失）。捕获自 v2 切换前提交（8ea048c 工作区干净态）。
_GOLDEN_R40_JSON = """{"accounting": {"judgment": {"n_score_missing": 0, "n_signals": 180, "n_subset": 180}, "mining": {"n_score_missing": 0, "n_signals": 180, "n_subset": 180}}, "cluster_map": {"judgment": {"mapped": 180, "rate": 1.0, "total": 180}, "min_rate": 0.99, "mining": {"mapped": 180, "rate": 1.0, "total": 180}, "note": "簇映射命中率（v0.336）：交易经 (code, entry_date) 回挂信号所属簇——低于 min_rate fail-closed 不落盘（映射缺失 ⇒ C4 静默退回逐笔打乱，口径缺陷不猜）"}, "cost_sensitivity": {"arms": {"high_judgment": {"margin": 0.31248828051753236, "n": 60, "payoff_ratio": 4.333, "win_rate": 0.5}, "high_mining": {"margin": 0.31248828051753236, "n": 60, "payoff_ratio": 4.333, "win_rate": 0.5}, "low_judgment": {"margin": -0.37489063867016625, "n": 60, "payoff_ratio": 0.143, "win_rate": 0.5}, "low_mining": {"margin": -0.37489063867016625, "n": 60, "payoff_ratio": 0.143, "win_rate": 0.5}}, "base_bps": 25.0, "deltas": {"d_margin_high_low_judgment": {"base": 0.5835208802200551, "flip": false, "side": 0.6873789191876987}, "d_margin_high_low_mining": {"base": 0.5835208802200551, "flip": false, "side": 0.6873789191876987}}, "note": "成本副读数（review #6）：side 档=逐笔 ret 平移 (side−base)/1e4 解析重算（不重跑引擎）；flip=True ⇒ 结论成本敏感、判读降权", "side_bps": 50.0}, "criteria": {"C1": {"min_tier_n": 50, "n_clusters": {"judgment": {"high": 12, "low": 12, "mid": 12}, "mining": {"high": 12, "low": 12, "mid": 12}}, "n_taken": {"judgment": {"high": 60, "low": 60, "mid": 60}, "mining": {"high": 60, "low": 60, "mid": 60}}, "ok": true, "rule": "每档每窗 n ≥ 50（collect_all 全候选分档口径；不过=untested 不判 falsified；v0.335 修订：阈值不变、簇数入报告供判读打折）"}, "C2": {"low_confidence": false, "mde": 0.086, "ok": true, "rule": "双窗 margin(高)≥margin(中)≥margin(低) 且 高−低>0（相邻打平不算倒挂，高−低必须严格>0）；MDE 条款：相邻档差 |Δ|<0.086 ⇒ 低置信（形态读数非结论，MDE 不改变判定本身）", "windows": {"judgment": {"adjacent": {"high_mid": 0.16666666666666669, "mid_low": 0.4168542135533884}, "evaluable": true, "high_low": 0.5835208802200551, "margins": {"high": 0.33333333333333337, "low": -0.25018754688672173, "mid": 0.16666666666666669}, "mde_low_confidence": false, "monotone": true}, "mining": {"adjacent": {"high_mid": 0.16666666666666669, "mid_low": 0.4168542135533884}, "evaluable": true, "high_low": 0.5835208802200551, "margins": {"high": 0.33333333333333337, "low": -0.25018754688672173, "mid": 0.16666666666666669}, "mde_low_confidence": false, "monotone": true}}}, "C3": {"best_delta_mining": 0.3000000000000001, "best_group_mining": "W2", "groups": {"W1": {"delta_expR": {"judgment": 0.1499999999999994, "mining": 0.1499999999999994}, "pass": true, "reading": {"judgment": "positive", "mining": "positive"}}, "W2": {"delta_expR": {"judgment": 0.3000000000000001, "mining": 0.3000000000000001}, "pass": true, "reading": {"judgment": "positive", "mining": "positive"}}, "W3": {"delta_expR": {"judgment": 0.07619047619047564, "mining": 0.07619047619047564}, "pass": true, "reading": {"judgment": "positive", "mining": "positive"}}}, "mde": 0.035, "ok": true, "portfolio_note": "组合层加权 rdd + v2.1 相对门（参照=等仓位）需要逐笔仓位权重——simulate_portfolio_topn 是固定 risk_pct 制、不支持逐笔权重；按 owner 拍板不改引擎，本单元 C3 只报交易层加权 expR，组合层标注「未测」（引擎支持后补测属新单元）", "rule": "任一组双窗加权 expR（Σw·R/Σw）> 等权 ⇒ 过（主判据）；MDE 条款：|ΔexpR|<0.035 ⇒ 与零无法区分（不读增量也不读证伪），|Δ|≥MDE 才按符号判读；不过不放行不判死（预注册四态：falsified 只来自 C2/C4）", "w2_note": "W2 低档权重 0 = R36 Phase 4 已证伪的过滤器路线（v0.292 在案）——保留它是做对照：W2 若「过线」而 W1/W3 不过，读作「过滤器效应残存」而非「分层成立」，不与分层结论混读"}, "C4": {"candidate_delta_mining": 0.3000000000000001, "candidate_group": "W2", "evaluated": 4, "max_arms": 40, "min_pool": 3, "note": "整簇打乱「交易→档」归属（v0.335 修订：簇=同 code 信号点相邻 ≤5 根 bar 连续段，kit.cluster_ids/cluster_draw 单源；每簇独立均匀抽一档、同簇同档，各档笔数臂间自然波动——不逐笔对齐笔数）；每臂取同套权重格 max（与 C3 同预算）；C3 最佳组（挖掘窗 ΔexpR）> 池 q95 才 confirmed；池构造/状态机=criteria_kit 单源（v0.322）", "pool": [0.04533333333333334, 0.06296296296296283, 0.0377777777777778, 0.004197530864196886], "pool_size": 4, "q95": 0.0603185185185184, "state": "confirmed_pass", "target_pool": 4}, "rule_note": "R42-C1~C4 跑数前写死（v0.312 落档；v0.326 功效节采 A=保 3 档+MDE 判读条款，C3 升主判据）；C5=pre2019 单独终步只能杀——C2 和 C3 都过线才启动，owner 拍板发令（本工具不建 C5）"}, "cuts": {"note": "切点只在挖掘窗估计——判定窗直接应用（判定窗泄漏=死罪，双窗纪律）", "qs": [0.3333333333333333, 0.6666666666666666], "values_mining_estimated": [39.666666666666664, 69.33333333333333]}, "forward_holdout_note": "判定窗末尾前进场的交易，出场执行会用到前向 holdout 区间的 bar（单侧机械泄漏：holdout 数据不参与选型/判据，只执行既定持仓的出场；截数据会把末段持仓强平失真——owner review #2② 注记在案）", "notes": ["W2 低档权重 0 = R36 Phase 4 已证伪的过滤器路线（v0.292 在案）——保留它是做对照：W2 若「过线」而 W1/W3 不过，读作「过滤器效应残存」而非「分层成立」，不与分层结论混读", "组合层加权 rdd + v2.1 相对门（参照=等仓位）需要逐笔仓位权重——simulate_portfolio_topn 是固定 risk_pct 制、不支持逐笔权重；按 owner 拍板不改引擎，本单元 C3 只报交易层加权 expR，组合层标注「未测」（引擎支持后补测属新单元）"], "objective_version": "v2.1-margin-rddref", "params": {"cost_bps": 25.0, "exit": {"name": "pct5_trail08", "params": {"stop_mode": "pct", "stop_pct": 5, "trail_pct": 0.08}}, "note": "档内交易=collect_all 全候选分档（非 top_n——top_n 截断下判定窗每档 ~49 笔会贴 C1 线，v0.301 在案）；--top-n 不参与任何读数（组合层未测，见 notes）；出场=pct5_trail08 参数原样取自 strategy_grid.DEFAULT_EXIT_GRID", "tier_qs": [0.3333333333333333, 0.6666666666666666], "top_n": 20, "weight_grid": {"W1": {"high": 1.5, "low": 0.5, "mid": 1.0}, "W2": {"high": 2.0, "low": 0.0, "mid": 1.0}, "W3": {"high": 1.2857142857142856, "low": 0.8571428571428571, "mid": 0.8571428571428571}}, "weight_grid_raw": {"W1": {"high": 1.5, "low": 0.5, "mid": 1.0}, "W2": {"high": 2.0, "low": 0.0, "mid": 1.0}, "W3": {"high": 1.5, "low": 1.0, "mid": 1.0}}}, "readings": {"judgment": {"equal_expR": 0.2666666666666668, "groups": {"W1": {"delta_expR": 0.1499999999999994, "weighted_expR": 0.4166666666666662}, "W2": {"delta_expR": 0.3000000000000001, "weighted_expR": 0.5666666666666669}, "W3": {"delta_expR": 0.07619047619047564, "weighted_expR": 0.3428571428571424}}}, "mining": {"equal_expR": 0.2666666666666668, "groups": {"W1": {"delta_expR": 0.1499999999999994, "weighted_expR": 0.4166666666666662}, "W2": {"delta_expR": 0.3000000000000001, "weighted_expR": 0.5666666666666669}, "W3": {"delta_expR": 0.07619047619047564, "weighted_expR": 0.3428571428571424}}}}, "schema": "score_tier_position/v1", "tag": "t", "tiers": {"judgment": {"high": {"margin": 0.33333333333333337, "n": 60, "n_clusters": 12}, "low": {"margin": -0.25018754688672173, "n": 60, "n_clusters": 12}, "mid": {"margin": 0.16666666666666669, "n": 60, "n_clusters": 12}}, "mining": {"high": {"margin": 0.33333333333333337, "n": 60, "n_clusters": 12}, "low": {"margin": -0.25018754688672173, "n": 60, "n_clusters": 12}, "mid": {"margin": 0.16666666666666669, "n": 60, "n_clusters": 12}}}, "verdict": "candidate", "windows": {"judgment": {"end": "2026-09-04", "start": "2024-08-01"}, "mining": {"end": "2024-07-31", "start": "2022-01-01"}}}"""


class TestLegacyR40BitIdentical:
    """(d) legacy R40 参数（2022-01-01~2024-07-31 / 2024-08-01~2026-09-04，
    均 ⊆ 挖掘B 且互不相交）：接受且合成数据报告与 v2 切换前**逐位一致**
    （golden 硬钉——判据/读数/成本副读数/笔记文本全字段同一）。"""

    def test_report_bit_identical_to_pre_v2(self):
        spec = {w: _mk_per_code() for w in _WINDOWS}
        rep = stp.run_study(
            _args(n_random=4, c4_min_pool=3),
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=_replay_monotone,
        )
        for k in ("generated_at", "window_usage", "provenance"):
            rep.pop(k, None)
        canon = json.dumps(rep, ensure_ascii=False, sort_keys=True, allow_nan=True)
        assert canon == _GOLDEN_R40_JSON

    def test_legacy_r40_cli_accepted(self, tmp_path):
        per_code = _mk_per_code()
        rc = stp.main(
            [
                "--mining-start",
                "2022-01-01",
                "--mining-end",
                "2024-07-31",
                "--judgment-start",
                "2024-08-01",
                "--judgment-end",
                "2026-09-04",
                "--tag",
                "r40_t",
                "--out-dir",
                str(tmp_path),
            ],
            warm_fn=lambda s, e: per_code,
            replay_fn=_replay_monotone,
        )
        assert rc == 0
        assert (tmp_path / "r40_t" / "_score_tier_position__r40_t.json").exists()


class TestCliWindowGuardsV2:
    """(e) v2 口径窗口护栏：越段/相交/半截 ⇒ SystemExit；判定在挖掘前但
    不相交 ⇒ 合法（旧「挖掘终 < 判定始」次序规则作废）。"""

    def test_cross_segment_rejected(self):
        """越段（2016-01-01~2022-06-30 跨 挖掘A/判定/挖掘B）⇒ 硬拒绝。"""
        with pytest.raises(SystemExit):
            stp.main(
                [
                    "--mining-start",
                    "2016-01-01",
                    "--mining-end",
                    "2022-06-30",
                    "--judgment-start",
                    "2023-01-01",
                    "--judgment-end",
                    "2024-01-01",
                    "--tag",
                    "t",
                ]
            )

    def test_overlapping_rejected(self):
        """两窗各自 ⊆ 挖掘B 但相交 ⇒ 硬拒绝（不相交规则）。"""
        with pytest.raises(SystemExit):
            stp.main(
                [
                    "--mining-start",
                    "2022-01-01",
                    "--mining-end",
                    "2024-07-31",
                    "--judgment-start",
                    "2024-06-01",
                    "--judgment-end",
                    "2026-09-04",
                    "--tag",
                    "t",
                ]
            )

    def test_partial_windows_rejected(self):
        """半截布局（只给 --mining-start）⇒ 硬拒绝（不许半截布局）。"""
        with pytest.raises(SystemExit):
            stp.main(["--mining-start", "2022-01-01", "--tag", "t"])

    def test_disjoint_judgment_before_mining_accepted(self, tmp_path):
        """旧次序规则作废钉：判定=受保护段 2017~2021、挖掘 ⊆B（在其后）——
        不相交即合法（v2 判定段天然夹在挖掘 A/B 之间），且按受保护段登记。"""
        per_code = _mk_per_code()
        spec = {("2023-01-01", "2024-01-01"): per_code, lw.SEG_JUDGMENT: per_code}
        rc = stp.main(
            [
                "--mining-start",
                "2023-01-01",
                "--mining-end",
                "2024-01-01",
                "--judgment-start",
                "2017-01-01",
                "--judgment-end",
                "2021-12-31",
                "--tag",
                "rev_t",
                "--out-dir",
                str(tmp_path),
            ],
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=_replay_monotone,
        )
        assert rc == 0
        rep = json.loads(
            (tmp_path / "rev_t" / "_score_tier_position__rev_t.json").read_text(
                encoding="utf-8"
            )
        )
        assert rep["window_usage"]["window"] == "judgment_2017_2021"
        assert "layout" not in rep and "segments" not in rep  # legacy 单段无 v2 块


class TestRecordUseKeys:
    """(f) 台账键 v2 口径：judgment==SEG_JUDGMENT ⇒ 登记新键
    judgment_2017_2021；legacy ⇒ 完全不登记（报告 mining_segment 注明）。"""

    def test_v2_registers_new_key(self, monkeypatch):
        calls = _wu_spy(monkeypatch)
        rep = _run_v2(_v2_spec(), _replay_monotone, n_random=2, c4_min_pool=2)
        assert len(calls) == 1
        assert calls[0]["unit"] == "R42"
        assert calls[0]["window"] == "judgment_2017_2021"
        assert rep["window_usage"]["window"] == "judgment_2017_2021"

    def test_v2_synthetic_run_k_none_real_path(self):
        """不挂桩走真 record_use：合成守卫 ⇒ k=None + 未入台账注明（v0.334）。"""
        rep = _run_v2(_v2_spec(), _replay_monotone, n_random=2, c4_min_pool=2)
        assert rep["window_usage"]["window"] == "judgment_2017_2021"
        assert rep["window_usage"]["k"] is None
        assert "未入台账" in rep["window_usage"]["note"]

    def test_legacy_not_registered(self, monkeypatch):
        calls = _wu_spy(monkeypatch)
        rep = _run(_mk_per_code(), _replay_monotone, n_random=2, c4_min_pool=2)
        assert calls == [], "判定窗在挖掘段内——不入台账（v2 口径）"
        assert rep["window_usage"]["window"] == "mining_segment"
        assert rep["window_usage"]["k"] is None
        assert "不入台账" in rep["window_usage"]["note"]
