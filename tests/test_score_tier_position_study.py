# -*- coding: utf-8 -*-
"""score_tier_position_study（R42 Phase 1）钉测。

锁的契约（蓝图 §钉测清单）：①切点只估挖掘窗（判定窗分布极端偏移不改
切点——泄漏钉）；②C2 单调三形态（过/倒挂 falsified/相邻打平读法——
打平不算倒挂但 高−低 必须严格 >0）；③C3 MDE 三态（双窗 |ΔexpR|<0.035
⇒ 无法区分/≥0.035 按符号读/W2 过滤器对照注明在报告）；④C1 不过 ⇒
untested 优先于其他判决；⑤C4 池重抽确定性 + 三态（confirmed_pass/
provisional/indeterminate）+ confirmed_fail；⑥空结果护栏（任一窗 0
信号 ⇒ rc=2 不落盘）；⑦pre2019 + 前向 holdout 硬拒绝；⑧报告自含
字段（切点/权重格/MDE/W2 注明/组合层未测注明/cost_sensitivity/
provenance/window_usage/forward_holdout_note）。
"""

from __future__ import annotations

import argparse

import pytest

from custos.research import factor_exit_study as fes
from custos.research import score_tier_position_study as stp
from custos.research.load_window import EXIT_BARS_HOLDOUT_NOTE

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
    70/40 与研究分档**逐位一致**（各档 60 笔 ≥ C1 线 50）。"""
    per_code = {}
    k = 0
    for c_i in range(n_codes):
        code = f"{c_i:06d}"
        signals = []
        scores = {}
        for s_i in range(sigs_per_code):
            day = f"2023-{(k % 12) + 1:02d}-{(k % 28) + 1:02d}"
            signals.append({"i": 10 + s_i * 2, "date": day, "score": 0.0})
            scores[day] = 10.0 + (k % 90) + score_shift
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


# ---------------------------------------------------------------------------
# ⑤ C4：确定性 + 状态机
# ---------------------------------------------------------------------------


class TestC4:
    def test_arm_seeds_reproducible(self):
        a = _run(_mk_per_code(), _replay_monotone)
        b = _run(_mk_per_code(), _replay_monotone)
        assert a["criteria"]["C4"]["pool"] == b["criteria"]["C4"]["pool"]

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
        assert rep["window_usage"]["k"] is None  # 合成运行不入台账（v0.334 守卫）
        assert "未入台账" in rep["window_usage"]["note"]
        assert rep["forward_holdout_note"] == EXIT_BARS_HOLDOUT_NOTE
        assert rep["verdict"] == "candidate"
