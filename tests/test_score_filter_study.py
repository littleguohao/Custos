# -*- coding: utf-8 -*-
"""R36 Phase 4 过滤器研究钉测（score_filter_study.py）。

锁的契约：X 网格与 4 对象写死、前置剔除的 fail-open/floor 口径、随机对照
种子复现、位移数/缺值率必报、pre2019 硬拒绝、空结果护栏、v0_self 空对照
语义（小 X 下选择逐位不变）。
"""

from __future__ import annotations

import argparse

import pytest

from custos.research import score_filter_study as sf


def _item(code, day, ret=0.05, fval=None, addon=None):
    it = {
        "code": code,
        "trade": {
            "code": code,
            "entry_date": day,
            "exit_date": day,
            "ret": ret,
            "reason": "trail",
            "holding": 5,
            "risk_frac": 0.02,
        },
        "cand": {"_score": 50.0},
    }
    if fval is not None:
        it["fval"] = fval
    if addon is not None:
        it["addon"] = addon
    return it


class TestFrozenConfig:
    def test_x_grid_frozen(self):
        assert sf.X_GRID == (0.10, 0.20, 0.30, 0.50)

    def test_filter_set_frozen(self):
        assert set(sf.FILTER_KEYS) == {
            "v0_self",
            "neg4",
            "p2_sole",
            "reversal_quality",
        }

    def test_neg4_legs_frozen(self):
        assert sf.NEG4_LEGS == (
            "distribution_high",
            "distribution_watch",
            "macd_top_divergence",
            "volume_yy_bear",
        )


class TestFilterDay:
    def test_drops_bottom_floor(self):
        items = [_item(f"{i:06d}", "2023-01-04", fval=float(i)) for i in range(10)]
        kept = sf._filter_day(items, 0.30)
        assert len(kept) == 7
        assert {it["code"] for it in kept} == {f"{i:06d}" for i in range(3, 10)}

    def test_missing_value_fail_open(self):
        items = [_item(f"{i:06d}", "2023-01-04", fval=float(i)) for i in range(5)]
        items.append(_item("999999", "2023-01-04"))  # 无 fval
        items[-1]["fval"] = None
        kept = sf._filter_day(items, 0.50)
        # 有值 5 只 floor(5×0.5)=2 剔 2；缺值 fail-open 留下
        assert "999999" in {it["code"] for it in kept}
        assert len(kept) == 4

    def test_x_zero_passthrough(self):
        items = [_item("000001", "2023-01-04", fval=1.0)]
        assert sf._filter_day(items, 0.0) == items

    def test_deterministic_tie_break_by_code(self):
        items = [_item(c, "2023-01-04", fval=1.0) for c in ("b", "a", "c")]
        kept = sf._filter_day(items, 0.50)  # floor(3×0.5)=1 剔并列中 code 最小者
        assert [it["code"] for it in kept] == ["b", "c"]


class TestRandomFilterDay:
    def test_seed_reproducible(self):
        import random

        items = [_item(f"{i:06d}", "2023-01-04") for i in range(20)]
        a = sf._random_filter_day(items, 0.30, random.Random(7))
        b = sf._random_filter_day(items, 0.30, random.Random(7))
        assert [i["code"] for i in a] == [i["code"] for i in b]
        assert len(a) == 14


class TestDisplacement:
    def test_counts_removed_baseline_picks(self):
        base = [_item("000001", "2023-01-04"), _item("000002", "2023-01-04")]
        filt = [_item("000001", "2023-01-04")]
        assert (
            sf._displacement([i["trade"] for i in base], [i["trade"] for i in filt])
            == 1
        )


class TestQ95:
    def test_small_sample_none(self):
        assert sf._q95([0.01] * 10) is None

    def test_value(self):
        import statistics

        xs = list(range(100))
        assert sf._q95([float(x) for x in xs]) == pytest.approx(
            statistics.quantiles(xs, n=100, method="inclusive")[94]
        )


def _fake_score(cand, weights=None):
    return cand.get("_score", 50.0), "中", {}


def _run_args(tmp_path):
    cf = tmp_path / "codes.txt"
    cf.write_text("000001\n", encoding="utf-8")
    return argparse.Namespace(
        codes_file=str(cf),
        mining_start="2022-01-01",
        mining_end="2024-07-31",
        judgment_start="2024-08-01",
        judgment_end="2026-09-04",
        count=2000,
        cost_bps=25.0,
        top_n=20,
        n_random=50,
        seed=7,
        tag="t",
    )


def _synth_pool(n_days=30, per_day=8, start="2023-01-"):
    """合成候选池：两窗同构（day 序号区分窗）。"""
    pool = []
    for d in range(1, n_days + 1):
        day = f"{start}{d:02d}" if d <= 28 else f"{start}28"
        for j in range(per_day):
            pool.append(
                _item(
                    f"{(d * 100 + j) % 1000:06d}",
                    day,
                    ret=0.04 if (d + j) % 3 else -0.02,
                    addon={sf.P2_SOLE_EXPR: 0.5},
                )
            )
    return pool


class TestRunStudy:
    def test_happy_path(self, tmp_path, monkeypatch):
        from custos.pipeline.screening import score_candidates as sc

        monkeypatch.setattr(sc, "technical_score", _fake_score)
        args = _run_args(tmp_path)
        rep = sf.run_study(
            args,
            collector=lambda w: _synth_pool(),
            rq_fetcher=lambda code, its: [0.6] * len(its),
        )
        assert set(rep["filters"]) == set(sf.FILTER_KEYS)
        # 键接线钉（v0_self 富化键 ≠ 过滤器键 曾致缺值率=1 全 fail-open）
        v0row = rep["filters"]["v0_self"]["per_x"]["0.20"]["mining"]
        assert v0row["missing_rate"] == 0.0
        assert rep["filters"]["neg4"]["per_x"]["0.20"]["mining"]["missing_rate"] == 0.0
        assert (
            rep["filters"]["p2_sole"]["per_x"]["0.20"]["mining"]["missing_rate"] == 0.0
        )
        assert (
            rep["filters"]["reversal_quality"]["per_x"]["0.20"]["mining"][
                "missing_rate"
            ]
            == 0.0
        )
        for fkey, blk in rep["filters"].items():
            assert set(blk["per_x"]) == {"0.10", "0.20", "0.30", "0.50"}
            row = blk["per_x"]["0.20"]
            assert row["mining"]["missing_rate"] is not None
            assert row["mining"]["displacement"] is not None
            assert row["random_n"] == 100  # 50 臂 × 双窗合并
        assert rep["baseline"]["mining"]["reading"]["n_taken"]

    def test_empty_collection_guard(self, tmp_path, monkeypatch):
        from custos.pipeline.screening import score_candidates as sc

        monkeypatch.setattr(sc, "technical_score", _fake_score)
        args = _run_args(tmp_path)
        with pytest.raises(RuntimeError, match="空结果护栏"):
            sf.run_study(args, collector=lambda w: [])

    def test_pre2019_hard_reject(self, tmp_path):
        with pytest.raises(SystemExit):
            sf.main(
                [
                    "--codes-file",
                    "x.txt",
                    "--mining-start",
                    "2015-01-01",
                    "--mining-end",
                    "2017-01-01",
                    "--judgment-start",
                    "2018-01-01",
                    "--judgment-end",
                    "2019-01-01",
                ]
            )


class TestVerdictHint:
    def test_no_valid_x(self):
        assert "no_valid_x" in sf._verdict_hint({}, {"best_x": None})
