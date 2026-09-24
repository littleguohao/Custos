# -*- coding: utf-8 -*-
"""R37-C5 终审终端钉测（exit_c5_terminal.py）。

锁的契约：基因组键解析 fail-closed、两窗合并标尺自含读取、v0.273 否决
条件三条款（含 n<200 量级停用前置）、配对 bootstrap 可复现、CLI 只接受
pre2019 段内窗口（硬拒绝镜像）。
"""

from __future__ import annotations

import argparse

import pytest

from custos.research import exit_c5_terminal as c5

CAND_KEY = (
    "sp8|breakeven=off|cost_zone=3x2|qsx=off|scale_out=off|time_stop=20|trail=0.08"
)


def _trade(code, day, ret):
    return {
        "code": code,
        "entry_date": day,
        "ret": ret,
        "reason": "stop",
        "holding": 5,
    }


class TestParseGenomeKey:
    def test_roundtrip_candidate_key(self):
        from custos.research.evolution import exit_genome as eg

        g = c5.parse_genome_key(CAND_KEY)
        assert eg.genome_key(g) == CAND_KEY  # 归一后键复原
        assert g["stop_pct"] == 8.0
        assert g["cost_zone_bars"] == 3.0 and g["cost_zone_pct"] == 2.0
        assert g["time_stop_bars"] == 20.0 and g["trail_pct"] == 0.08
        assert g["breakeven_trigger"] == 0.0  # 关闭家族归零

    def test_rejects_bad_inputs(self):
        for bad in (
            "cost_zone=3x2",  # 无 sp 起手
            "sp8|unknown=off",  # 未知家族
            "sp8|cost_zone=3",  # 双参家族缺一档
            "sp7|trail=0.08",  # stop_pct 不在档位
            "sp8|trail=0.07",  # 档位外取值
        ):
            with pytest.raises(ValueError):
                c5.parse_genome_key(bad)


class TestCombinedYardstick:
    def test_n_weighted_merge(self):
        rep = {
            "best_candidate": {
                "d_margin_mining": 0.0336,
                "d_margin_judgment": 0.0034,
                "mining": {"n_taken": 243},
                "judgment": {"n_taken": 146},
            }
        }
        y = c5.combined_yardstick(rep)
        assert y["combined"] == pytest.approx(
            (0.0336 * 243 + 0.0034 * 146) / 389, rel=1e-6
        )
        # 保留率 0.0034/0.0336 ≈ 10% < 0.5 ⇒ degraded ⇒ γ=0.75（v0.276 分档）
        assert y["retention"] == pytest.approx(0.0034 / 0.0336, rel=1e-3)
        assert y["candidate_degraded"] is True
        assert y["gamma"] == 0.75
        assert y["bar"] == pytest.approx(0.75 * y["combined"])

    def test_non_degraded_gets_gamma_half(self):
        rep = {
            "best_candidate": {
                "d_margin_mining": 0.0942,
                "d_margin_judgment": 0.1347,  # 保留率 143% ⇒ 非 degraded
                "mining": {"n_taken": 158},
                "judgment": {"n_taken": 101},
            }
        }
        y = c5.combined_yardstick(rep)
        assert y["candidate_degraded"] is False
        assert y["gamma"] == 0.5

    def test_missing_pieces_raise(self):
        with pytest.raises(ValueError):
            c5.combined_yardstick({"best_candidate": {"d_margin_mining": 0.01}})

    def test_nonpositive_mining_margin_raises(self):
        rep = {
            "best_candidate": {
                "d_margin_mining": 0.0,
                "d_margin_judgment": 0.01,
                "mining": {"n_taken": 100},
                "judgment": {"n_taken": 100},
            }
        }
        with pytest.raises(ValueError):
            c5.combined_yardstick(rep)


class TestApplyC5:
    YARD = {"combined": 0.0222, "bar": 0.0111, "gamma": 0.5}

    def test_n_below_100_kills(self):
        v = c5.apply_c5(99, 0.05, self.YARD)
        assert v["verdict"] == c5.VERDICT_KILLED
        assert [f["clause"] for f in v["fired"]] == ["a_sample"]

    def test_nonpositive_margin_kills(self):
        v = c5.apply_c5(150, 0.0, self.YARD)
        assert v["verdict"] == c5.VERDICT_KILLED
        assert [f["clause"] for f in v["fired"]] == ["b_sign"]

    def test_magnitude_clause_disabled_below_200(self):
        # 100≤n<200：Δ<bar 也不启用量级条款（v0.273 前置）
        v = c5.apply_c5(150, 0.005, self.YARD)
        assert v["verdict"] == c5.VERDICT_NOT_VETOED
        assert v["magnitude_clause_active"] is False

    def test_magnitude_clause_kills_at_200(self):
        v = c5.apply_c5(250, 0.005, self.YARD)
        assert v["verdict"] == c5.VERDICT_KILLED
        assert [f["clause"] for f in v["fired"]] == ["c_magnitude"]

    def test_all_pass_not_vetoed(self):
        v = c5.apply_c5(250, 0.02, self.YARD)
        assert v["verdict"] == c5.VERDICT_NOT_VETOED
        assert v["fired"] == []

    def test_missing_margin_kills_conservatively(self):
        v = c5.apply_c5(150, None, self.YARD)
        assert v["verdict"] == c5.VERDICT_KILLED


class TestPairBootstrap:
    def test_pairs_on_intersection(self):
        cand = [
            _trade("000001", "2012-01-04", 0.05),
            _trade("000002", "2012-01-05", -0.02),
        ]
        base = [_trade("000001", "2012-01-04", 0.03)]
        pairs = c5.pair_trades(cand, base)
        assert len(pairs) == 1 and pairs[0][1]["ret"] == 0.03

    def test_bootstrap_reproducible_and_positive_se(self):
        cand = [
            _trade(
                f"{i:06d}",
                f"2012-03-{i % 28 + 1:02d}",
                0.02 + (i % 5) * 0.01 * (1 if i % 2 else -1),
            )
            for i in range(60)
        ]
        base = [
            _trade(
                f"{i:06d}",
                f"2012-03-{i % 28 + 1:02d}",
                0.01 + (i % 3) * 0.008 * (1 if i % 2 else -1),
            )
            for i in range(60)
        ]
        pairs = c5.pair_trades(cand, base)
        r1 = c5.paired_bootstrap(pairs, seed=7, n_boot=200)
        r2 = c5.paired_bootstrap(pairs, seed=7, n_boot=200)
        assert r1 == r2  # 种子复现
        assert r1["se"] is not None and r1["se"] > 0
        lo, hi = r1["ci95"]
        assert lo <= hi


class TestPre2019Guard:
    def test_rejects_window_outside_pre2019(self):
        ap = c5._build_parser()
        args = ap.parse_args(
            [
                "--genome",
                CAND_KEY,
                "--codes-file",
                "x.txt",
                "--campaign-report",
                "y.json",
                "--start",
                "2017-01-01",
            ]
        )
        with pytest.raises(SystemExit):
            c5._check_pre2019(args, ap)

    def test_accepts_default_window(self):
        ap = c5._build_parser()
        args = ap.parse_args(
            [
                "--genome",
                CAND_KEY,
                "--codes-file",
                "x.txt",
                "--campaign-report",
                "y.json",
            ]
        )
        c5._check_pre2019(args, ap)  # 不抛即过


class TestRunC5Guards:
    def test_campaign_key_mismatch_fails(self, tmp_path):
        rep = tmp_path / "rep.json"
        rep.write_text(
            '{"best_candidate": {"key": "sp5|breakeven=off|cost_zone=off|qsx=off|scale_out=off|time_stop=off|trail=0.08"}}',
            encoding="utf-8",
        )
        args = argparse.Namespace(
            genome=CAND_KEY,
            campaign_report=str(rep),
            codes_file="x",
            cost_bps=25.0,
            top_n=20,
            seed=1,
            n_bootstrap=10,
            tag="t",
            start=c5.PRE2019_START,
            end=c5.PRE2019_END,
        )
        with pytest.raises(RuntimeError, match="对账不符"):
            c5.run_c5(args, per_code={})
