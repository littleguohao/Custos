# -*- coding: utf-8 -*-
"""score_calibration_study 钉测：池内命中率 / add-one / LOO 机械正确性 / CLI 冒烟。

R24 Phase 0（预注册）：逐腿边际分析是离线纯函数——只读 factor_contrib/panel，
不重跑回测；evidence-only 键（perfect_b1_fit）不进 V0 重建。
"""

from __future__ import annotations

import json

import pytest

from custos.research import score_calibration_study as scs
from custos.research import score_variants_study as svs


def _trade(contrib=None, panel=None, ret=0.0, entry_date="2026-01-05"):
    t = {"ret": ret, "entry_date": entry_date}
    if contrib is not None:
        t["factor_contrib"] = contrib
    if panel is not None:
        t["panel"] = panel
    return t


class TestLegRegistry:
    def test_leg_lists_cover_weights_and_panel(self):
        """contrib 腿 = 权重表计分键（阈值/合成参数不是腿）；panel-only 腿补齐证据腿。"""
        from custos.pipeline.screening import score_candidates as sc
        from custos.research import winner_factor_study as wfs

        assert "j_low" in scs.CONTRIB_LEG_KEYS
        assert "repair_signals" in scs.CONTRIB_LEG_KEYS  # 合并后的单一 contrib 键
        for non_leg in (
            "tech_strong_fallback",
            "tech_mid_fallback",
            "repair_signals_each",
            "repair_signals_cap",
        ):
            assert non_leg not in scs.CONTRIB_LEG_KEYS
        assert len(scs.CONTRIB_LEG_KEYS) == len(sc.DEFAULT_TECH_WEIGHTS) - 3
        # panel 里 contrib 没有的腿（rsi_strong/platform_pullback_b1/深水RSI 等）
        assert set(scs.PANEL_ONLY_LEG_KEYS) == set(wfs.PANEL_KEYS) - set(
            scs.CONTRIB_LEG_KEYS
        )
        assert "rsi_strong" in scs.PANEL_ONLY_LEG_KEYS
        assert "platform_pullback_b1" in scs.PANEL_ONLY_LEG_KEYS
        # 证据键不是腿
        assert "perfect_b1_fit" not in scs.CONTRIB_LEG_KEYS


class TestPoolHitRates:
    def test_hit_rate_and_no_discrimination(self):
        """j_low 全命中 ⇒ 命中率 100% 且标「无区分度」（地板效应主因）。"""
        trades = [
            _trade(contrib={"j_low": 24, "bbi_above": 5}, ret=0.1),
            _trade(contrib={"j_low": 24}, ret=-0.1),
            _trade(contrib={"j_low": 24, "bbi_above": 5}, ret=0.05),
            _trade(contrib={"j_low": 24}, ret=-0.05),
        ]
        rates = scs.pool_hit_rates(trades)
        assert rates["j_low"]["hit_rate"] == 1.0
        assert rates["j_low"]["no_discrimination"] is True
        assert rates["bbi_above"]["hit_rate"] == 0.5
        assert rates["bbi_above"]["no_discrimination"] is False
        assert rates["bbi_above"]["n_hit"] == 2

    def test_negative_contrib_leg_counts_as_hit(self):
        """负腿（macd_top_divergence −8）以负值记录——contrib 键出现即命中。"""
        trades = [
            _trade(contrib={"j_low": 24, "macd_top_divergence": -8}, ret=0.1),
            _trade(contrib={"j_low": 24}, ret=-0.1),
        ]
        rates = scs.pool_hit_rates(trades)
        assert rates["macd_top_divergence"]["hit_rate"] == 0.5

    def test_panel_unavailable_excluded_from_denominator(self):
        """panel 三态：None=unavailable 不进分母（wfs 口径）。"""
        trades = [
            _trade(contrib={"j_low": 24}, panel={"rsi_deep_oversold": True}, ret=0.1),
            _trade(contrib={"j_low": 24}, panel={"rsi_deep_oversold": False}, ret=0.1),
            _trade(contrib={"j_low": 24}, panel={"rsi_deep_oversold": None}, ret=0.1),
            _trade(contrib={"j_low": 24}, panel={}, ret=0.1),  # 键缺失同 unavailable
        ]
        rates = scs.pool_hit_rates(trades)
        assert rates["rsi_deep_oversold"]["source"] == "panel"
        assert rates["rsi_deep_oversold"]["n_eval"] == 2
        assert rates["rsi_deep_oversold"]["hit_rate"] == 0.5


class TestAddOneMargins:
    """手算案例：6 笔，腿 bbi_above 命中前 3 笔。

    全样本：胜 3/6=0.5，均盈 (0.20+0.10+0.05)/3=0.1167，均亏 (0.10+0.05+0.20)/3
    =0.1167 ⇒ 盈亏比 1.0 ⇒ margin = 0.5 − 1/2 = 0。
    命中子集 {0,1,2}：胜 2/3=0.6667，均盈 0.15，均亏 0.10 ⇒ 盈亏比 1.5
    ⇒ margin = 0.6667 − 1/2.5 = 0.2667 ⇒ vs 全样本 +0.2667。
    """

    def _trades(self):
        rets = [0.20, -0.10, 0.10, 0.05, -0.05, -0.20]
        return [
            _trade(
                contrib={"j_low": 24, **({"bbi_above": 5} if i < 3 else {})},
                ret=r,
                entry_date=f"2026-01-{i + 1:02d}",
            )
            for i, r in enumerate(rets)
        ]

    def test_hand_computed(self):
        add1 = scs.add_one_margins(self._trades())
        row = add1["bbi_above"]
        assert row["n"] == 3
        assert row["margin"] == pytest.approx(2 / 3 - 1 / 2.5, abs=1e-3)
        assert row["margin_vs_universe"] == pytest.approx(0.2667, abs=1e-3)
        # 全体命中的腿：子集=全样本 ⇒ delta = 0
        assert add1["j_low"]["margin_vs_universe"] == pytest.approx(0.0, abs=1e-9)
        # 零命中的腿：子集为空 ⇒ 如实 None（不编数）
        assert add1["macd_above_water"]["n"] == 0
        assert add1["macd_above_water"]["margin"] is None

    def test_subset_without_losses_margin_none(self):
        """子集全赢（无亏单 ⇒ 盈亏比无定义）⇒ margin 如实 None，不编数。"""
        trades = [
            _trade(contrib={"j_low": 24, "bbi_above": 5}, ret=0.10),
            _trade(contrib={"j_low": 24}, ret=-0.10),
        ]
        add1 = scs.add_one_margins(trades)
        assert add1["bbi_above"]["margin"] is None
        assert add1["bbi_above"]["margin_vs_universe"] is None


class TestLeaveOneOutMargins:
    """10 笔：t1/t2 带 macd_above_water(+7) 得分 31，其余 j_low-only 24。

    V0 篮子（top-20% ⇒ 2 笔）= {t1,t2}：胜 1/2，均盈 0.20，均亏 0.10
    ⇒ margin = 0.5 − 1/3 ≈ 0.1667。
    去掉 macd_above_water ⇒ 全员 24 同分，稳定排序取原序前 2 = {t0,t1}：
    胜 1/2，均盈 0.20，均亏 0.05 ⇒ 盈亏比 4.0 ⇒ margin = 0.5 − 1/5 = 0.3
    ⇒ LOO delta = 0.3 − 0.1667 ≈ +0.1333（该腿是负贡献）。
    """

    def _trades(self):
        rets = [-0.05, 0.20, -0.10, 0.03, -0.03, 0.02, -0.02, 0.01, -0.01, 0.04]
        trades = []
        for i, r in enumerate(rets):
            contrib = {"j_low": 24}
            if i in (1, 2):
                contrib["macd_above_water"] = 7
            if i == 1:
                contrib["perfect_b1_fit"] = 8  # 证据键：混进 contrib 不得进重建分
            trades.append(
                _trade(contrib=contrib, ret=r, entry_date=f"2026-01-{i + 1:02d}")
            )
        return trades

    def test_hand_computed(self):
        loo = scs.leave_one_out_margins(self._trades())
        row = loo["macd_above_water"]
        assert row["margin"] == pytest.approx(0.5 - 1 / 5, abs=1e-4)
        assert row["margin_vs_v0"] == pytest.approx(
            (0.5 - 1 / 5) - (0.5 - 1 / 3), abs=1e-3
        )

    def test_uniform_leg_removal_keeps_basket(self):
        """j_low 每票同值 24 ⇒ 去掉后相对排序不变 ⇒ 篮子 margin 变化 = 0。"""
        loo = scs.leave_one_out_margins(self._trades())
        assert loo["j_low"]["margin_vs_v0"] == pytest.approx(0.0, abs=1e-9)

    def test_evidence_only_key_never_enters_rebuild(self):
        """perfect_b1_fit 在 t1 的 contrib 里：V0 重建与 LOO 都不得计它的 8 分——
        若计入，t1 会高出 8 分、LOO macd_above_water 篮子仍是 {t1,t2} 而非 {t0,t1}。"""
        trades = self._trades()
        assert svs.v0_score(trades[1]) == 31  # 24+7，不含 perfect_b1_fit 的 8
        loo = scs.leave_one_out_margins(trades)
        assert loo["macd_above_water"]["margin"] == pytest.approx(0.3, abs=1e-4)
        # 对照：把证据键删掉，结果必须逐位相同
        for t in trades:
            t["factor_contrib"].pop("perfect_b1_fit", None)
        assert loo == scs.leave_one_out_margins(trades)

    def test_panel_only_legs_have_no_loo(self):
        """panel-only 腿不进 V0 打分 ⇒ ablation 报告里 LOO 为 None（如实标注）。"""
        rep = scs.ablation_report(self._trades(), label="t")
        by_leg = {row["leg"]: row for row in rep["legs"]}
        assert by_leg["rsi_strong"]["loo"] is None
        assert by_leg["j_low"]["loo"] is not None
        assert rep["label"] == "t"


class TestCliAblation:
    def _write(self, path, trades):
        path.write_text(
            json.dumps({"trades": trades, "config": {"tag": "t"}}), encoding="utf-8"
        )

    def test_smoke_two_windows(self, tmp_path):
        """--ablation --from-trades 两文件：逐窗口各落一份 .ablation.json。"""
        rets = [0.20, -0.10, 0.10, -0.05, 0.05, -0.20]
        trades = [
            _trade(
                contrib={"j_low": 24, **({"bbi_above": 5} if i < 3 else {})},
                panel={"rsi_deep_oversold": i % 2 == 0},
                ret=r,
                entry_date=f"2026-01-{i + 1:02d}",
            )
            for i, r in enumerate(rets)
        ]
        f1 = tmp_path / "main.rejudged.json"
        f2 = tmp_path / "cw.rejudged.json"
        self._write(f1, trades)
        self._write(f2, trades)
        assert scs.main(["--ablation", "--from-trades", str(f1), str(f2)]) == 0
        for f, stem in ((f1, "main.rejudged"), (f2, "cw.rejudged")):
            out = f.with_suffix(".ablation.json")
            assert out.exists()
            rep = json.loads(out.read_text(encoding="utf-8"))
            assert rep["label"] == stem  # 窗口标签默认取文件名
            by_leg = {row["leg"]: row for row in rep["legs"]}
            assert by_leg["j_low"]["no_discrimination"] is True
            assert by_leg["bbi_above"]["add_one"]["n"] == 3
            assert by_leg["j_low"]["loo"]["margin_vs_v0"] == pytest.approx(0.0)

    def test_tag_overrides_label(self, tmp_path):
        f = tmp_path / "x.json"
        self._write(f, [_trade(contrib={"j_low": 24}, ret=0.1)])
        assert scs.main(["--ablation", "--from-trades", str(f), "--tag", "主窗"]) == 0
        rep = json.loads(f.with_suffix(".ablation.json").read_text(encoding="utf-8"))
        assert rep["label"] == "主窗"

    def test_empty_trades_returns_error(self, tmp_path):
        f = tmp_path / "empty.json"
        self._write(f, [])
        assert scs.main(["--ablation", "--from-trades", str(f)]) == 1
        assert not f.with_suffix(".ablation.json").exists()
