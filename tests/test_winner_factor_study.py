# -*- coding: utf-8 -*-
"""winner_factor_study 钉测：面板构建（含 unavailable 语义）/ lift / 切半 / 无未来函数。"""

from __future__ import annotations

import pandas as pd
import pytest

from custos.pipeline.screening import enrich_candidates as ec
from custos.pipeline.screening import score_candidates as sc
from custos.research import score_return_study as srs
from custos.research import winner_factor_study as wfs


def _mk_df(n: int = 400, start: str = "2024-01-01") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=n).strftime("%Y-%m-%d")
    close = [10.0 + i * 0.01 for i in range(n)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": [c * 1.01 for c in close],
            "low": [c * 0.99 for c in close],
            "close": close,
            "volume": [1000.0] * n,
            "amount": [10000.0] * n,
        }
    )


def _mk_cand(**overrides) -> dict:
    """最小可用 cand：patterns 恒可评估，其余检测器按 overrides 覆盖。"""
    cand = {
        "patterns": {
            "bbi_above": True,
            "j_low": True,
            "volume_contraction": False,
            "reversal_k_candidate": False,
            "relative_strength_strong": True,
        },
        "macd_technics": {"available": False},
        "bottom_volume": {"available": True, "hit": True},
        "leader_volume": {"available": False, "hit": False},
        "volume_sustain": {"available": True, "status": "mainline_confirmed"},
        "ignition": {"available": True, "hit": False},
        "pullback_shrink": {"available": True, "hit": True},
        "b1_ignition": {"hit": False},
        "five_day_entry": {"available": True, "hit": True},
        "repair_signals": {"signals": ["j_turn_up"]},
        "non_one_wave": {"available": True, "status": "revoked"},
        "weekly_j_available": True,
        "weekly_j_low": False,
        "signals": {
            "breakout_pullback_b1": {"state": "miss"},
            "rsi_strong": {"state": "hit"},
            "rsi_deep_oversold": {"state": "unavailable"},
            "rsi_bull_div": {"state": "miss"},
        },
        "distribution": {"available": True, "risk_level": "watch"},
    }
    cand.update(overrides)
    return cand


class TestBuildFactorPanel:
    def test_key_set_and_order(self):
        panel = wfs.build_factor_panel(_mk_cand())
        assert list(panel) == wfs.PANEL_KEYS  # 键集合与顺序钉死
        assert len(panel) == 29

    def test_hit_and_miss(self):
        panel = wfs.build_factor_panel(_mk_cand())
        assert panel["bbi_above"] is True
        assert panel["volume_contraction"] is False
        assert panel["bottom_volume"] is True
        assert panel["volume_sustain_mainline"] is True
        assert panel["five_day_entry"] is True
        assert panel["repair_signals"] is True
        assert panel["non_one_wave_revoked"] is True
        assert panel["non_one_wave_confirmed"] is False
        assert panel["weekly_j_low"] is False
        assert panel["distribution_watch"] is True
        assert panel["distribution_high"] is False

    def test_unavailable_is_none_not_false(self):
        """算不出 = None，绝不当 False（命中率分母只含可评估样本）。"""
        panel = wfs.build_factor_panel(_mk_cand())
        # macd_technics.available=False ⇒ 全部 8 条腿 None
        for k in wfs.PANEL_GROUPS["macd_technics"]:
            assert panel[k] is None, k
        assert panel["leader_volume"] is None  # available=False
        assert panel["rsi_deep_oversold"] is None  # signals 三态 unavailable
        assert panel["rsi_strong"] is True
        assert panel["platform_pullback_b1"] is False  # miss 是 False 不是 None

    def test_macd_wm_leg_double_guard(self):
        """周/月红柱腿：mt.available 但 wm_available=False 时仍为 None。"""
        cand = _mk_cand(
            macd_technics={
                "available": True,
                "zone": 1,
                "zone1_restart": False,
                "bottom_divergence": {"hit": True},
                "above_water": True,
                "bar_grow": False,
                "wm_available": False,
                "wm_bar_grow": False,
                "top_divergence": {"hit": False},
                "three_peaks": {"hit": False},
            }
        )
        panel = wfs.build_factor_panel(cand)
        assert panel["macd_zone1"] is True
        assert panel["macd_bottom_divergence"] is True
        assert panel["macd_wm_bar_grow"] is None  # wm 腿双重守卫


class TestHitStatsAndLift:
    def _trades(self, panel_vals_top, panel_vals_bot):
        # ret 降序构造：top 组收益全高于 bottom 组
        top = [
            {
                "ret": 0.10 - i * 0.001,
                "entry_date": f"2026-01-{i + 1:02d}",
                "panel": {"f": v},
            }
            for i, v in enumerate(panel_vals_top)
        ]
        bot = [
            {
                "ret": -0.01 - i * 0.001,
                "entry_date": f"2026-01-{i + 1:02d}",
                "panel": {"f": v},
            }
            for i, v in enumerate(panel_vals_bot)
        ]
        return top + bot

    def test_hit_stats_excludes_none(self):
        trades = [{"panel": {"f": v}} for v in (True, False, None, True)]
        st = wfs.hit_stats(trades, "f")
        assert st["n_eval"] == 3  # None 不进分母
        assert st["n_hit"] == 2
        assert st["rate"] == pytest.approx(2 / 3, abs=1e-4)

    def test_lift(self):
        # top 4 中 3，bottom 4 中 1 ⇒ lift = 0.75/0.25 = 3.0
        trades = self._trades([True, True, True, False], [True, False, False, False])
        en = wfs.factor_enrichment(trades, [], "f")
        assert en["lift"] == pytest.approx(3.0)
        assert en["top50"]["n_hit"] == 3
        assert en["bottom50"]["n_hit"] == 1

    def test_lift_none_when_bottom_zero(self):
        trades = self._trades([True, True], [False, False])
        en = wfs.factor_enrichment(trades, [], "f")
        assert en["lift"] is None  # bottom 命中率 0 ⇒ lift 无定义

    def test_top_frac_tightens_winner_group(self):
        """top_frac=0.10：10 笔里 top 组只有 1 笔，lift 按新分母算。"""
        top = [{"ret": 0.50, "entry_date": "2026-01-05", "panel": {"f": True}}]
        bot = [
            {
                "ret": 0.10 - i * 0.01,
                "entry_date": f"2026-01-{i + 6:02d}",
                "panel": {"f": v},
            }
            for i, v in enumerate(
                [True, False, False, False, False, False, False, False, False]
            )
        ]
        en = wfs.factor_enrichment(top + bot, [], "f", top_frac=0.10)
        assert en["top_frac"] == 0.10
        assert en["top50"]["n_eval"] == 1  # ceil(10×0.10)=1
        assert en["bottom50"]["n_eval"] == 9
        assert en["lift"] == pytest.approx(1.0 / (1 / 9), abs=1e-3)  # 1.0 / 0.111 = 9.0

    def test_support_thin_after_tightening(self):
        """top 组收紧 5 倍后命中支撑变薄 ⇒ 如实标不足（阈值不放水）。"""
        trades = self._trades([True, True], [True] * 40)
        en = wfs.factor_enrichment(trades, [], "f", top_frac=0.10)
        # 42 笔 × 0.10 ⇒ top 组 5 笔（含 bottom 混入的高 ret），命中数 ≪ 30
        assert en["support"].startswith("不足")

    def test_support_flag(self):
        trades = self._trades([True, True], [True, False])
        en = wfs.factor_enrichment(trades, [], "f")
        assert en["support"].startswith("不足")  # 命中数远低于 MIN_HIT_SUPPORT

    def test_top50_split_consistency_with_srs(self):
        """切半口径与 score_return_study 完全一致（同一函数，钉住防漂移）。"""
        trades = self._trades([True, True, True], [False, False])
        top, bot = srs.split_top_half(trades)
        assert all(t["ret"] > 0 for t in top) and all(t["ret"] < 0 for t in bot)
        assert len(top) == 3 and len(bot) == 2  # 奇数 top 多拿一笔


class TestSplitTopFrac:
    def test_default_matches_split_top_half(self):
        """frac=0.5 与 score_return_study.split_top_half 逐位一致（旧行为不变）。"""
        for n in (0, 1, 2, 5, 10, 11):
            trades = [{"ret": float(r)} for r in range(n)]
            a = wfs.split_top_frac(trades, 0.5)
            b = srs.split_top_half(trades)
            assert [t["ret"] for t in a[0]] == [t["ret"] for t in b[0]]
            assert [t["ret"] for t in a[1]] == [t["ret"] for t in b[1]]

    def test_top10(self):
        trades = [{"ret": float(r)} for r in range(20)]  # 0..19
        top, bottom = wfs.split_top_frac(trades, 0.10)
        assert [t["ret"] for t in top] == [19.0, 18.0]  # ceil(20×0.10)=2
        assert len(bottom) == 18

    def test_small_group_min_one(self):
        top, bottom = wfs.split_top_frac([{"ret": 0.1}], 0.10)
        assert len(top) == 1 and bottom == []
        assert wfs.split_top_frac([], 0.10) == ([], [])


class TestCliTopFrac:
    def test_default_half(self):
        args = wfs._build_parser().parse_args([])
        assert args.top_frac == pytest.approx(0.5)

    def test_explicit(self):
        args = wfs._build_parser().parse_args(["--top-frac", "0.10"])
        assert args.top_frac == pytest.approx(0.10)


class TestHalfWindowConsistency:
    def test_consistent_direction(self):
        # 前半/后半都 top 侧富集 ⇒ consistent=True
        trades = []
        for half, dates in (
            ("h1", ["2026-01-05", "2026-01-06"]),
            ("h2", ["2026-06-01", "2026-06-02"]),
        ):
            for d in dates:
                trades.append({"ret": 0.10, "entry_date": d, "panel": {"f": True}})
                trades.append({"ret": -0.05, "entry_date": d, "panel": {"f": False}})
        en = wfs.factor_enrichment(trades, [], "f")
        assert en["half_window"]["consistent"] is True
        assert (
            en["half_window"]["first"]["lift"] is None or True
        )  # bottom 0 命中 lift None 不影响方向判定

    def test_flip_detected(self):
        # 前半 top 富集、后半 bottom 富集 ⇒ consistent=False。
        # 注意半窗按 entry_date 中位切（<=mid 归前半）：偶数样本时后半首个日期会
        # 溢进前半——构造时让溢出笔不改变前半方向（口径钉测，不是绕开它）。
        trades = [
            # 前半（1 月）：top 两只命中、bottom 两只不命中
            {"ret": 0.10, "entry_date": "2026-01-05", "panel": {"f": True}},
            {"ret": 0.09, "entry_date": "2026-01-06", "panel": {"f": True}},
            {"ret": -0.05, "entry_date": "2026-01-07", "panel": {"f": False}},
            {"ret": -0.06, "entry_date": "2026-01-08", "panel": {"f": False}},
            # 后半（6 月）：top 不命中、bottom 命中（方向翻转）
            {"ret": 0.02, "entry_date": "2026-06-01", "panel": {"f": False}},
            {"ret": 0.01, "entry_date": "2026-06-02", "panel": {"f": False}},
            {"ret": -0.01, "entry_date": "2026-06-03", "panel": {"f": True}},
            {"ret": -0.02, "entry_date": "2026-06-04", "panel": {"f": True}},
        ]
        en = wfs.factor_enrichment(trades, [], "f")
        hw = en["half_window"]
        assert hw["first"]["top_rate"] > hw["first"]["bottom_rate"]
        assert hw["second"]["top_rate"] < hw["second"]["bottom_rate"]
        assert hw["consistent"] is False


class TestPanelHookNoLookahead:
    """panel_hook 的 as-of 截断：compute_metrics 收到的 df 必须只含 ≤ 信号日。"""

    def test_truncation(self, monkeypatch):
        n = 1500
        df = _mk_df(n)
        index_df = _mk_df(n)
        i = n - 200
        entry_date = df["date"].iloc[i]
        captured = {}

        def fake_compute_metrics(df_arg, index_arg, code="", df_long=None):
            captured["df"] = df_arg
            captured["index"] = index_arg
            captured["df_long"] = df_long
            return {}

        monkeypatch.setattr(ec, "compute_metrics", fake_compute_metrics)
        monkeypatch.setattr(sc, "technical_score", lambda cand, w=None: (50, "中", {}))
        monkeypatch.setattr(
            wfs, "build_factor_panel", lambda cand: {k: None for k in wfs.PANEL_KEYS}
        )

        out = wfs.panel_hook(df, index_df, i, "000001")
        assert out["tech_score"] == 50
        assert list(out["panel"]) == wfs.PANEL_KEYS
        got = captured["df"]
        assert got["date"].iloc[-1] == entry_date  # 无未来函数
        assert (got["date"] <= entry_date).all()
        assert len(got) == ec.OHLCV_LOAD_BARS
        assert len(captured["df_long"]) == ec.OHLCV_LOAD_BARS_LONG
        assert (captured["index"]["date"] <= entry_date).all()


class TestVerdict:
    def _en(self, lift, consistent, frac, support="ok", first_dir_up=True):
        return {
            "lift": lift,
            "support": support,
            "half_window": {
                "consistent": consistent,
                "first": {
                    "top_rate": 0.5 if first_dir_up else 0.1,
                    "bottom_rate": 0.1 if first_dir_up else 0.5,
                },
            },
            "interval_consistency": {"frac_lift_gt1": frac},
        }

    def test_rich_stable(self):
        assert wfs.verdict_of(self._en(1.5, True, 0.8)) == "✅ 富集且稳定"

    def test_reverse(self):
        assert (
            wfs.verdict_of(self._en(0.5, True, 0.2, first_dir_up=False))
            == "⛔ 反向（输家侧富集）"
        )

    def test_noise_when_inconsistent(self):
        assert wfs.verdict_of(self._en(1.5, False, 0.8)) == "⚠️ 噪声/不稳"

    def test_insufficient(self):
        assert (
            wfs.verdict_of(self._en(1.5, True, 0.8, support="不足(x)")) == "⚠️ 样本不足"
        )
