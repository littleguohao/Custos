# -*- coding: utf-8 -*-
"""score_variants_study 钉测：变体确定性 / V0 重建 / V1 取反 / 篮子选择 / 判据 / 无未来函数。"""

from __future__ import annotations

import pytest

from custos.research import score_variants_study as svs


def _trade(contrib=None, panel=None, ret=0.0, entry_date="2026-01-05"):
    """最小 trade：变体只许读 factor_contrib/panel（无未来函数的结构保证）。"""
    t = {"ret": ret, "entry_date": entry_date}
    if contrib is not None:
        t["factor_contrib"] = contrib
    if panel is not None:
        t["panel"] = panel
    return t


class TestV0Reconstruction:
    def test_matches_live_technical_score(self):
        """V0 = contrib 求和（排除证据键）clamp——与 live technical_score 逐位一致。"""
        from custos.pipeline.screening import score_candidates as sc

        cand = {
            "patterns": {"bbi_above": True, "j_low": True, "volume_contraction": True},
            "macd_technics": {"available": True, "above_water": True},
            "zhixing": {"available": True, "qsx_gt_dks": True, "close_above_qsx": True},
        }
        live_score, _, contrib = sc.technical_score(cand, None)
        assert svs.v0_score(_trade(contrib=contrib)) == live_score

    def test_evidence_key_excluded(self):
        """perfect_b1_fit 是证据键（不计分）——混进 contrib 不得改变 V0。"""
        c1 = {"j_low": 24, "bbi_above": 5}
        c2 = {"j_low": 24, "bbi_above": 5, "perfect_b1_fit": 7}
        assert (
            svs.v0_score(_trade(contrib=c1)) == svs.v0_score(_trade(contrib=c2)) == 29
        )

    def test_clamp(self):
        assert (
            svs.v0_score(_trade(contrib={"j_low": 24, "macd_top_divergence": -50})) == 0
        )
        assert svs.v0_score(_trade(contrib={"j_low": 200})) == 100


class TestV1SignFlip:
    def test_hand_computed(self):
        """反向腿变号：base=24+5−8+7=28；reverse=5+(−8)+7=4；V1=28−2×4=20。"""
        contrib = {
            "j_low": 24,
            "bbi_above": 5,  # 反向腿 +5 → −5
            "macd_top_divergence": -8,  # 反向腿 −8 → +8
            "macd_above_water": 7,  # 反向腿 +7 → −7
        }
        assert svs.v0_score(_trade(contrib=contrib)) == 28
        assert svs.v1_score(_trade(contrib=contrib)) == 20

    def test_no_reverse_legs_equals_v0(self):
        contrib = {"j_low": 24, "weekly_j_low": 5}
        t = _trade(contrib=contrib)
        assert svs.v1_score(t) == svs.v0_score(t) == 29


class TestV2V3:
    def test_v2_weights(self):
        panel = {
            "rsi_deep_oversold": True,
            "weekly_j_low": True,
            "rsi_bull_div": False,
            "macd_bottom_divergence": True,
        }
        assert svs.v2_score(_trade(panel=panel)) == 40 + 20 + 20

    def test_v2_unavailable_counts_zero(self):
        """unavailable(None) 按不命中=0 分，不惩罚数据缺失。"""
        panel = {"rsi_deep_oversold": None, "weekly_j_low": True}
        assert svs.v2_score(_trade(panel=panel)) == 20

    def test_v3_penalty(self):
        panel = {
            "rsi_deep_oversold": True,  # +40
            "macd_above_water": True,  # −5
            "rsi_strong": True,  # −5
            "platform_pullback_b1": False,
        }
        assert svs.v3_score(_trade(panel=panel)) == 40 - 10
        assert svs.v2_score(_trade(panel=panel)) == 40

    def test_v3_clamps_at_zero(self):
        panel = {k: True for k in svs._REVERSE_PANEL_KEYS}  # 11 条反向全中 = −55
        assert svs.v3_score(_trade(panel=panel)) == 0


class TestDeterminismAndNoLookahead:
    def test_deterministic(self):
        t = _trade(
            contrib={"j_low": 24, "bbi_above": 5},
            panel={"rsi_deep_oversold": True, "rsi_strong": True},
        )
        for fn in svs.VARIANTS.values():
            assert fn(t) == fn(t) == fn(dict(t))

    def test_variants_need_only_contrib_and_panel(self):
        """无未来函数的结构保证：变体只读 factor_contrib/panel 两个键——
        给一个不含行情/日期以外任何字段的 trade 也能算出分。"""
        t = {"factor_contrib": {"j_low": 24}, "panel": {"weekly_j_low": True}}
        assert svs.v0_score(t) == 24
        assert svs.v2_score(t) == 20


class TestBasketSelection:
    def test_basket_picks_highest_scores(self):
        # 分数与收益刻意反着放：高分低收、低分高收
        trades = [
            _trade(contrib={"j_low": s}, ret=r, entry_date=f"2026-01-{i + 1:02d}")
            for i, (s, r) in enumerate(
                [
                    (90, -0.05),
                    (80, -0.04),
                    (70, -0.03),
                    (60, -0.02),
                    (50, -0.01),
                    (10, 0.10),
                    (20, 0.20),
                    (30, 0.30),
                    (40, 0.40),
                    (45, 0.50),
                ]
            )
        ]
        b = svs.basket_stats(trades, svs.v0_score, 0.20)
        assert b["n"] == 2  # ceil(10×0.20)
        assert b["avg_ret"] == pytest.approx((-0.05 + -0.04) / 2)
        assert b["win_rate"] == pytest.approx(0.0)
        assert b["payoff_ratio"] == pytest.approx(0.0)  # 全亏：均盈 0/均亏>0 = 0


class TestJudge:
    def _rep(self, sp, hw1, hw2, w_mean, b_mean, wr, payoff):
        return {
            "corr": {"spearman": sp},
            "half_window": {
                "consistent": (hw1 > 0) == (hw2 > 0),
                "first_half": {"spearman": hw1},
            },
            "winner_top20_dist": {"mean": w_mean},
            "bottom80_dist": {"mean": b_mean},
            "basket_top20_by_variant": {"win_rate": wr, "payoff_ratio": payoff},
        }

    def test_all_pass(self):
        rep = self._rep(0.1, 0.08, 0.05, 50, 40, 0.50, 2.0)
        v0 = {"win_rate": 0.45, "payoff_ratio": 1.8}
        v = svs.judge(rep, v0)
        assert v["candidate"] is True

    def test_fail_each_leg(self):
        v0 = {"win_rate": 0.45, "payoff_ratio": 1.8}
        # C1 负
        assert not svs.judge(self._rep(-0.1, -0.08, -0.05, 50, 40, 0.5, 2.0), v0)[
            "candidate"
        ]
        # C1 半窗翻转
        assert not svs.judge(self._rep(0.1, 0.08, -0.05, 50, 40, 0.5, 2.0), v0)[
            "candidate"
        ]
        # C2 赢家分不高
        assert not svs.judge(self._rep(0.1, 0.08, 0.05, 40, 50, 0.5, 2.0), v0)[
            "candidate"
        ]
        # C3 胜率没升
        assert not svs.judge(self._rep(0.1, 0.08, 0.05, 50, 40, 0.40, 2.0), v0)[
            "candidate"
        ]
        # C3 盈亏比塌了
        assert not svs.judge(self._rep(0.1, 0.08, 0.05, 50, 40, 0.50, 1.5), v0)[
            "candidate"
        ]


class TestRelaxedC3AndFromTrades:
    """v0.121（owner 2026-08-26 拍板放宽线）：C3_relaxed = 胜率 > V0 且盈亏比 ≥ 2.4
    绝对下限；预注册 C3 保留不变（并列展示，不 retroactive 改写）。"""

    def _rep(self, wr, payoff, v0_wr=0.27, v0_payoff=2.81, sp=0.1):
        return {
            "corr": {"spearman": sp},
            "half_window": {
                "consistent": True,
                "first_half": {"spearman": sp},
                "second_half": {"spearman": sp},
            },
            "winner_top20_dist": {"mean": 20.0},
            "bottom80_dist": {"mean": 10.0},
            "basket_top20_by_variant": {"win_rate": wr, "payoff_ratio": payoff},
        }

    def test_relaxed_passes_when_payoff_above_floor(self):
        from custos.research import score_variants_study as svs

        v0 = {"win_rate": 0.27, "payoff_ratio": 2.81}
        vd = svs.judge(self._rep(0.47, 2.41), v0)
        assert vd["C3_basket_wr_up_payoff_kept"] is False  # 预注册仍判不过
        assert vd["C3_relaxed_wr_up_payoff_floor"] is True
        assert vd["candidate"] is False and vd["candidate_relaxed"] is True

    def test_relaxed_fails_below_floor(self):
        from custos.research import score_variants_study as svs

        v0 = {"win_rate": 0.27, "payoff_ratio": 2.81}
        vd = svs.judge(self._rep(0.47, 2.39), v0)
        assert vd["candidate_relaxed"] is False

    def test_relaxed_fails_when_wr_not_up(self):
        from custos.research import score_variants_study as svs

        v0 = {"win_rate": 0.47, "payoff_ratio": 2.0}
        vd = svs.judge(self._rep(0.46, 3.0), v0)
        assert vd["candidate_relaxed"] is False

    def test_from_trades_offline_rejudge(self, tmp_path):
        """--from-trades 离线重判：不重跑回测也能出 verdicts。"""
        import json as _json

        from custos.research import score_variants_study as svs

        trades = [
            {
                "ret": 0.1,
                "interval_idx": 0,
                "tech_score": 10,
                "reason": "bbi_exit",
                "factor_contrib": {},
                "panel": {"rsi_deep_oversold": True},
            },
            {
                "ret": -0.1,
                "interval_idx": 0,
                "tech_score": 90,
                "reason": "bbi_exit",
                "factor_contrib": {},
                "panel": {},
            },
        ]
        f = tmp_path / "study.json"
        f.write_text(
            _json.dumps({"trades": trades, "config": {"tag": "t"}}),
            encoding="utf-8",
        )
        out = tmp_path / "rejudged.json"
        assert svs.main(["--from-trades", str(f), "--out", str(out)]) == 0
        rep = _json.loads(out.read_text(encoding="utf-8"))
        assert "candidate_relaxed" in rep["verdicts"]["V2"]
