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


# ---------------------------------------------------------------------------
# Phase 2：候选方案 / 灵敏度 / C5 / pre2019 硬拒绝
# ---------------------------------------------------------------------------


class TestPhase2Candidates:
    def test_p0_is_v0_minus_floor_and_neg_legs(self):
        """P0 手算：V0_raw − j_low(24) − 5 条 contrib 负腿贡献（含 macd_top_div −8 变 +8）。"""
        contrib = {
            "j_low": 24,
            "bbi_above": 5,  # 保留腿
            "volume_contraction": 15,  # 负腿归零
            "relative_strength_strong": 15,  # 负腿归零
            "b1_ignition": 8,  # 负腿归零
            "ignition": 4,  # 负腿归零
            "macd_top_divergence": -8,  # 负腿归零（移除 −8 ⇒ +8 回加）
            "macd_bottom_divergence": 8,  # 保留腿
        }
        t = _trade(contrib=contrib, panel={})
        v0 = svs.v0_score(t)  # 24+5+15+15+8+4−8+8 = 71
        assert v0 == 71
        p0 = scs.make_candidate_score(
            scs.PHASE2_CANDIDATES["P0_min_change"]["contrib_mult"],
            scs.PHASE2_CANDIDATES["P0_min_change"]["panel_weights"],
        )
        # P0 = 5+8 = 13（只剩 bbi_above + macd_bottom_divergence）
        assert p0(t) == 13

    def test_p1_rebuild_ignores_contrib(self):
        """P1：contrib 全归零（即使 V0 分项很高），只算 panel 正向腿。"""
        t = _trade(
            contrib={"j_low": 24, "leader_volume": 6},
            panel={"rsi_deep_oversold": True, "weekly_j_low": True},
        )
        p1 = scs.make_candidate_score(
            scs.PHASE2_CANDIDATES["P1_rebuild"]["contrib_mult"],
            scs.PHASE2_CANDIDATES["P1_rebuild"]["panel_weights"],
        )
        assert p1(t) == 60  # 40 + 20，contrib 不计

    def test_p2_negative_legs(self):
        t = _trade(
            panel={
                "rsi_deep_oversold": True,  # +40
                "rsi_strong": True,  # −5
                "ignition": True,  # −5
                "b1_ignition": None,  # unavailable = 0
            }
        )
        p2 = scs.make_candidate_score(
            scs.PHASE2_CANDIDATES["P2_rebuild_neg"]["contrib_mult"],
            scs.PHASE2_CANDIDATES["P2_rebuild_neg"]["panel_weights"],
        )
        assert p2(t) == 30

    def test_p3_adds_leader_volume(self):
        p3 = scs.make_candidate_score(
            scs.PHASE2_CANDIDATES["P3_rebuild_leader"]["contrib_mult"],
            scs.PHASE2_CANDIDATES["P3_rebuild_leader"]["panel_weights"],
        )
        assert p3(_trade(panel={"leader_volume": True})) == 20
        assert (
            p3(_trade(panel={"rsi_deep_oversold": True, "leader_volume": True})) == 60
        )

    def test_candidate_count_and_simple_int_weights(self):
        """纪律：候选 ≤4；panel 权重全是简单整数。"""
        assert len(scs.PHASE2_CANDIDATES) <= 4
        for spec in scs.PHASE2_CANDIDATES.values():
            for w in spec["panel_weights"].values():
                assert isinstance(w, int)


class TestPhase2C5:
    def test_strong_frac(self):
        band = {">=60": {"n": 100}, "<30": {"n": 900}}
        c5 = scs.c5_strong_frac(band, 1000)
        assert c5["strong_frac"] == pytest.approx(0.10)
        assert c5["pass"] is True
        assert c5["a_bucket"]  # A 桶不可算如实标注
        band2 = {">=60": {"n": 200}, "<30": {"n": 800}}
        assert scs.c5_strong_frac(band2, 1000)["pass"] is False


class TestPhase2Sensitivity:
    def _trending_trades(self, n=80):
        """合成样本：深水 RSI 命中的票整体更强，但两组都带亏单
        （margin 可定义——篮子无亏单时 payoff=None ⇒ margin=None，m2 口径）。"""
        trades = []
        for i in range(n):
            hit = i % 2 == 0
            if hit:
                ret = 0.05 if i % 4 == 0 else -0.01  # 命中组 3 胜 1 亏
            else:
                ret = 0.02 if i % 8 == 1 else -0.02  # 未中组 1 胜 3 亏
            trades.append(
                _trade(
                    panel={"rsi_deep_oversold": hit},
                    ret=ret,
                    entry_date=f"2026-01-{i % 28 + 1:02d}",
                )
            )
        return trades

    def test_stable_candidate_not_flagged(self):
        trades = self._trending_trades()
        windows = {"w1": trades}
        spec = scs.PHASE2_CANDIDATES["P1_rebuild"]
        s = scs.sensitivity_scan(windows, "P1_rebuild", spec)
        # 强信号下 ±50% 扰动不应翻转 C3★
        assert s["n_perturbations"] == 8  # 4 腿 × 2 向
        assert s["parameter_sensitive"] is False

    def test_zero_mult_leg_restored_one_sided(self):
        """P0 的归零腿扰动 = 单侧恢复 0.5（×0.5 of 0 还是 0，无意义）。"""
        windows = {"w1": self._trending_trades()}
        spec = scs.PHASE2_CANDIDATES["P0_min_change"]
        s = scs.sensitivity_scan(windows, "P0_min_change", spec)
        # 6 条归零腿 × 单侧恢复 = 6 次扰动 × 1 窗 = 6 次检查
        assert s["n_perturbations"] == 6
        assert s["n_checks"] == 6


class TestPhase2Pre2019Guard:
    def test_pre2019_rejected(self, tmp_path):
        f = tmp_path / "x_pre2019.json"
        f.write_text(json.dumps({"trades": [{"ret": 0.1}]}), encoding="utf-8")
        assert scs._phase2_main([str(f)]) == 2

    def test_empty_trades_rejected(self, tmp_path):
        f = tmp_path / "x_n400.json"
        f.write_text(json.dumps({"trades": []}), encoding="utf-8")
        assert scs._phase2_main([str(f)]) == 1


# ---------------------------------------------------------------------------
# Phase 3：pre2019 untouched 终审
# ---------------------------------------------------------------------------


class TestPhase3:
    def test_finalist_names(self):
        """终审名单 = Phase 2 推荐的三方案（P0 已淘汰，不在列）。"""
        assert scs.PHASE3_FINALIST_NAMES == (
            "P1_rebuild",
            "P2_rebuild_neg",
            "P3_rebuild_leader",
        )
        assert "P0_min_change" not in scs.PHASE3_FINALIST_NAMES

    def test_terminal_verdict_requires_c1_and_c3star(self, monkeypatch):
        """终审判定 = C1 且 C3★（一票否决；C2/C5 不进终审线，按预注册）。"""
        fake_eval = {
            "C1": True,
            "C2": False,  # C2 不过也不影响终审线
            "C3_star": True,
            "C5": {"pass": False, "strong_frac": 0.99},
            "corr": {"spearman": 0.1},
            "half_window": {"consistent": True, "first_half": {"spearman": 0.1}},
            "winner_top20_mean": 50,
            "bottom80_mean": 40,
            "basket": {"win_rate": 0.5, "payoff_ratio": 2.0},
            "basket_margin": 0.2,
            "universe_margin": 0.1,
        }
        monkeypatch.setattr(
            scs, "eval_candidate", lambda trades, name, fn: dict(fake_eval)
        )
        rep = scs.phase3_report([{"ret": 0.1}], "pre2019")
        assert rep["verdict"] == "通过"
        assert rep["passed"] == list(scs.PHASE3_FINALIST_NAMES)
        # C1 翻转 ⇒ 一票否决
        monkeypatch.setattr(
            scs,
            "eval_candidate",
            lambda trades, name, fn: {**fake_eval, "C1": False},
        )
        rep2 = scs.phase3_report([{"ret": 0.1}], "pre2019")
        assert rep2["verdict"] == "证伪"
        assert rep2["passed"] == []
        assert "分位数分层" in rep2["fallback"]
        # C3★ 失线 ⇒ 同样一票否决
        monkeypatch.setattr(
            scs,
            "eval_candidate",
            lambda trades, name, fn: {**fake_eval, "C3_star": False},
        )
        assert scs.phase3_report([{"ret": 0.1}], "pre2019")["verdict"] == "证伪"

    def test_phase3_guard_only_accepts_pre2019(self, tmp_path):
        good = tmp_path / "x_pre2019.json"
        good.write_text(json.dumps({"trades": []}), encoding="utf-8")
        # 空 trades 报 1（数据问题），但不是守卫的 2
        assert scs._phase3_main([str(good)]) == 1
        bad = tmp_path / "x_n400.json"
        bad.write_text(json.dumps({"trades": [{"ret": 0.1}]}), encoding="utf-8")
        assert scs._phase3_main([str(bad)]) == 2  # 非终审窗 ⇒ 硬拒绝
        assert scs._phase3_main([str(good), str(good)]) == 2  # 多文件 ⇒ 拒绝

    def test_phase3_real_eval_smoke(self):
        """真实 eval 链路冒烟：合成强信号样本，三方案终审应通过且 verdict 字段齐全。

        效应量要够大（n=200、命中组 75% 胜率）：样本小/效应弱时 C3★ 的
        Wilson 不重叠条件不过——这不是 bug，是判据设计（显著性守门）。
        """
        trades = []
        for i in range(200):
            hit = i % 2 == 0
            if hit:
                ret = 0.05 if i % 8 in (0, 2, 4) else -0.01  # 命中组 3 胜 1 亏
            else:
                ret = 0.02 if i % 8 == 1 else -0.02  # 未中组 1 胜 3 亏
            trades.append(
                _trade(
                    panel={"rsi_deep_oversold": hit},
                    ret=ret,
                    # 两段时间 101/99（C1 要求前后半窗都可评估且同正；
                    # 中位切分点须落在前段日期上，偶数对半会被后段首日吞掉）
                    entry_date="2026-01-05" if i < 101 else "2026-06-01",
                )
            )
        rep = scs.phase3_report(trades, "pre2019")
        assert rep["verdict"] == "通过"
        for name in scs.PHASE3_FINALIST_NAMES:
            c = rep["candidates"][name]
            assert c["terminal_pass"] is True
            assert c["eval"]["C5"]["strong_frac"] is not None
