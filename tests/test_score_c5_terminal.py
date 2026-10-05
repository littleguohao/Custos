# -*- coding: utf-8 -*-
"""R36-C5 终审终端钉测（score_c5_terminal.py）。

锁的契约：冻结基因组自含读取 fail-closed、两窗合并标尺自含读取（γ 分档）、
日簇配对 bootstrap 可复现、CLI 只接受 pre2019 段内窗口（硬拒绝镜像）、
空结果护栏不落盘、判决复用 exit_c5_terminal.apply_c5（CI 三分）。
"""

from __future__ import annotations

import argparse
import json

import pytest

from custos.research import score_c5_terminal as c5
from custos.research.exit_c5_terminal import VERDICT_NOT_VETOED
from custos.research.score_calibration_study import CONTRIB_LEG_KEYS

LEGS = list(CONTRIB_LEG_KEYS)


def _report(dm=0.0942, dj=0.1347, nm=158, nj=101, mult=None, addon=None):
    if mult is None:
        mult = {k: 0.0 for k in LEGS}
        mult["adx_gt_60"] = 1.0
    return {
        "top_genome": {"multipliers": mult, "addon": addon},
        "criteria_readings": {
            "R34-C1": {"top_n_mining": nm, "top_n_judgment": nj},
            "R34-C2": {"delta_mining": dm, "delta_judgment": dj},
        },
    }


def _trade(code, day, ret):
    return {
        "code": code,
        "entry_date": day,
        "exit_date": day,
        "ret": ret,
        "reason": "trail",
        "holding": 5,
        "risk_frac": 0.02,
    }


class TestLoadFrozenGenome:
    def test_valid_single_leg(self):
        mult = c5.load_frozen_genome(_report())
        assert mult["adx_gt_60"] == 1.0
        assert set(mult) == set(LEGS)

    def test_rejects_addon_genome(self):
        with pytest.raises(ValueError, match="addon"):
            c5.load_frozen_genome(_report(addon={"expr": "close", "lambda": 6.0}))

    def test_rejects_missing_multipliers(self):
        with pytest.raises(ValueError, match="multipliers"):
            c5.load_frozen_genome({"top_genome": {}})

    def test_rejects_leg_key_drift(self):
        mult = {k: 0.0 for k in LEGS}
        mult["adx_gt_60"] = 1.0
        mult["ghost_leg"] = 0.5
        with pytest.raises(ValueError, match="CONTRIB_LEG_KEYS"):
            c5.load_frozen_genome(_report(mult=mult))

    def test_rejects_negative_and_all_zero(self):
        mult = {k: 0.0 for k in LEGS}
        mult["adx_gt_60"] = -1.0
        with pytest.raises(ValueError, match="非负有限"):
            c5.load_frozen_genome(_report(mult=mult))
        with pytest.raises(ValueError, match="全零"):
            c5.load_frozen_genome(_report(mult={k: 0.0 for k in LEGS}))


class TestYardstickFromScoreReport:
    def test_r36_real_readings_non_degraded(self):
        """R36-P3 实测双窗（+0.0942/+0.1347，158/101）：保留率 143% ⇒ γ=0.5。"""
        y = c5.yardstick_from_score_report(_report())
        assert y["combined"] == pytest.approx(
            (0.0942 * 158 + 0.1347 * 101) / 259, rel=1e-6
        )
        assert y["retention"] == pytest.approx(0.1347 / 0.0942, rel=1e-3)
        assert y["candidate_degraded"] is False
        assert y["gamma"] == 0.5
        assert y["bar"] == pytest.approx(0.5 * y["combined"])

    def test_degraded_gets_gamma_075(self):
        y = c5.yardstick_from_score_report(_report(dm=0.10, dj=0.03))
        assert y["candidate_degraded"] is True
        assert y["gamma"] == 0.75

    def test_missing_pieces_raise(self):
        with pytest.raises(ValueError):
            c5.yardstick_from_score_report({"criteria_readings": {"R34-C2": {}}})

    def test_nonpositive_mining_margin_raises(self):
        with pytest.raises(ValueError):
            c5.yardstick_from_score_report(_report(dm=0.0))


class TestDayClusterBootstrap:
    def _pools(self, n_days=40, cand_ret=0.08, base_ret=-0.02):
        # 胜负混合（全胜 ⇒ payoff 无定义 ⇒ margin None——真实数据的常态形状）
        cand = [
            _trade(
                f"{i:06d}",
                f"2012-02-{i % 28 + 1:02d}",
                cand_ret if i % 4 else -0.02,
            )
            for i in range(n_days)
        ]
        base = [
            _trade(
                f"{i:06d}",
                f"2012-02-{i % 28 + 1:02d}",
                0.03 if i % 3 == 0 else base_ret,
            )
            for i in range(n_days)
        ]
        return cand, base

    def test_reproducible_and_ordered(self):
        cand, base = self._pools()
        r1 = c5.day_cluster_bootstrap(cand, base, seed=7, n_boot=200)
        r2 = c5.day_cluster_bootstrap(cand, base, seed=7, n_boot=200)
        assert r1 == r2  # 种子复现
        assert r1["se"] is not None and r1["se"] >= 0
        lo, hi = r1["ci95"]
        assert lo <= hi
        assert r1["n_days"] == 28

    def test_clearly_better_candidate_ci_positive(self):
        cand, base = self._pools(cand_ret=0.08, base_ret=-0.02)
        r = c5.day_cluster_bootstrap(cand, base, seed=3, n_boot=300)
        assert r["ci95"][0] > 0, "候选系统性更好 ⇒ CI95 应全正"

    def test_empty_days(self):
        r = c5.day_cluster_bootstrap([], [], seed=1, n_boot=50)
        assert r["se"] is None and r["n_days"] == 0


class TestPre2019Guard:
    def _args(self, start, end):
        ap = c5._build_parser()
        return ap, ap.parse_args(
            [
                "--from-report",
                "r.json",
                "--codes-file",
                "x.txt",
                "--start",
                start,
                "--end",
                end,
            ]
        )

    def test_rejects_window_outside_pre2019(self):
        ap, args = self._args("2017-01-01", "2018-01-01")
        with pytest.raises(SystemExit):
            c5._check_pre2019(args, ap)

    def test_rejects_mining_window(self):
        ap, args = self._args("2022-01-01", "2024-07-31")
        with pytest.raises(SystemExit):
            c5._check_pre2019(args, ap)

    def test_accepts_default_window(self):
        ap = c5._build_parser()
        args = ap.parse_args(["--from-report", "r.json", "--codes-file", "x.txt"])
        c5._check_pre2019(args, ap)  # 不抛即过

    def test_default_count_is_full_history(self):
        """count 是「最新向前 N 根」滚动窗——pre2019 终审必须全历史加载，
        否则 start/end 过滤后窗口被静默剪空（首跑 19 笔碎片教训）。"""
        ap = c5._build_parser()
        args = ap.parse_args(["--from-report", "r.json", "--codes-file", "x.txt"])
        assert args.count == 100000


def _ns(tmp_path, report):
    rp = tmp_path / "rep.json"
    rp.write_text(json.dumps(report), encoding="utf-8")
    cf = tmp_path / "codes.txt"
    cf.write_text("000001\n", encoding="utf-8")
    return argparse.Namespace(
        from_report=str(rp),
        codes_file=str(cf),
        start=c5.PRE2019_START,
        end=c5.PRE2019_END,
        count=2000,
        cost_bps=25.0,
        top_n=20,
        exit_name="",
        n_bootstrap=100,
        seed=7,
        tag="t",
    )


class TestRunC5:
    def _fake_select(self, better=True):
        def _sel(collected, overrides, top_n):
            # 按 j_low 权重键区分两臂（候选格 j_low 关=0；基准=默认 24）
            is_cand = overrides.get("j_low") == 0.0
            # 胜负混合（全胜 ⇒ payoff 无定义 ⇒ margin None）
            taken = [
                _trade(
                    f"{i:06d}",
                    f"2012-03-{i % 28 + 1:02d}",
                    (0.08 if i % 4 else -0.02)
                    if (is_cand and better)
                    else (0.03 if i % 3 == 0 else -0.02),
                )
                for i in range(40)
            ]
            return taken, {"n_taken": len(taken)}

        return _sel

    def test_happy_path_not_vetoed(self, tmp_path):
        args = _ns(tmp_path, _report())
        rep = c5.run_c5(
            args,
            collector=lambda w: [{"trade": {}, "cand": {}, "code": "000001"}],
            select_fn=self._fake_select(better=True),
        )
        assert rep["kill"]["verdict"] == VERDICT_NOT_VETOED
        assert rep["d_margin"] > 0
        assert rep["bootstrap"]["n_days"] == 28
        assert rep["config"]["candidate_multipliers_nonzero"] == {"adx_gt_60": 1.0}

    def test_empty_collection_guard(self, tmp_path):
        args = _ns(tmp_path, _report())
        with pytest.raises(RuntimeError, match="空结果护栏"):
            c5.run_c5(args, collector=lambda w: [])

    def test_empty_selection_guard(self, tmp_path):
        args = _ns(tmp_path, _report())
        with pytest.raises(RuntimeError, match="空结果护栏"):
            c5.run_c5(
                args,
                collector=lambda w: [{"trade": {}, "cand": {}, "code": "000001"}],
                select_fn=lambda c, o, n: ([], {"n_taken": 0}),
            )

    def test_bad_report_fails_before_collect(self, tmp_path):
        args = _ns(tmp_path, _report(addon={"expr": "close", "lambda": 6.0}))
        called = []
        with pytest.raises(ValueError, match="addon"):
            c5.run_c5(args, collector=lambda w: called.append(w) or [])
        assert not called, "基因组非法须在收集前 fail-closed"
