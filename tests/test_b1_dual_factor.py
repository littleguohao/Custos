# -*- coding: utf-8 -*-
"""B1 双轴因子回归测试（good_b1.pptx 形态提炼，2026-08-03）。

设计前提（owner 裁定）：B1 是**单纯的回调买入**，不吃突破。所以技术轴 = 长期结构
（底子好，软加权）× 短期回调（买点到），s_shape 的突破式分项不参与。

本文件钉住两件事：
  ① 四种典型形态的**判别方向**（买点型 > 好票但非买点 > 差票）；
  ② 旧 s_shape 主轴在同样样本上**方向相反**——它把"突破未回调"排第一、把 good_b1
     型排倒数第二。这是把它移出 B1 技术轴的量化依据，不能日后被"顺手改回去"。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import backtest_factors as bt
import b1_dual_factor as bd
from s_shape import compute_s_reversal, compute_s_shape
from technical_monitor import kdj, zhixing_state


def _mk(rows):
    c = np.array([r[0] for r in rows], float)
    v = np.array([r[1] for r in rows], float)
    o = np.concatenate(([c[0]], c[:-1]))
    return pd.DataFrame({
        "date": pd.bdate_range("2024-06-03", periods=len(c)).strftime("%Y-%m-%d"),
        "open": o, "high": np.maximum(c, o) * 1.012, "low": np.minimum(c, o) * 0.988,
        "close": c, "volume": v, "amount": v * c,
    })


def _platform(n, base, v, amp=0.035, period=14):
    return [(base * (1 + amp * np.sin(2 * np.pi * i / period)), v) for i in range(n)]


def _launch(start, v, bars=(0.07, 0.06, 0.03, 0.02, 0.02)):
    out, p = [], start
    for i, g in enumerate(bars):
        p *= (1 + g)
        out.append((p, v * (3.2 if i < 2 else 1.8)))
    return out


def _drift(start, n, per, v):
    out, p = [], start
    for _ in range(n):
        p *= (1 + per)
        out.append((p, v))
    return out


@pytest.fixture(scope="module")
def shapes():
    """四种形态：A good_b1型 / B 阴跌超卖 / C 突破未回调 / D 突破回踩型。"""
    a = _platform(110, 10.0, 4.0e5)
    a += _launch(a[-1][0], 4.0e5)
    a += _drift(a[-1][0], 12, 0.012, 7.0e5)
    a += _drift(a[-1][0], 10, -0.022, 2.6e5)          # 缩量回调创近9日新低

    b = _drift(22.0, 150, -0.006, 3.0e5)              # 长期无量阴跌

    c = _platform(120, 10.0, 4.0e5)
    c += _launch(c[-1][0], 4.0e5)
    c += _drift(c[-1][0], 8, 0.015, 8.0e5)            # 突破后仍在高位

    plat = _platform(130, 10.0, 4.0e5)
    ph = max(p for p, _ in plat[-60:])
    d = list(plat) + _launch(ph * 0.995, 4.0e5, bars=(0.06, 0.05, 0.03))
    d += _drift(d[-1][0], 6, 0.012, 7.0e5)
    d += [(float(x), 2.8e5) for x in np.linspace(d[-1][0], ph * 1.005, 9)]

    return {k: _mk(v) for k, v in
            (("A", a), ("B", b), ("C", c), ("D", d))}


class TestQsxDksMatchesZhixing:
    """QSX/DKS 必须与 technical_monitor.zhixing_state 同口径（图上参数 14,28,57,114）。"""

    def test_dks_matches(self, shapes):
        df = shapes["A"]
        zx = zhixing_state(df)
        assert zx.get("available")
        mine = float(bd._dks_series(df["close"]).iloc[-1])
        # zhixing_state 落盘时 round(4)，故按其精度比对而非 1e-9
        assert round(mine, 4) == pytest.approx(float(zx["dks"]), abs=1e-4)

    def test_qsx_matches(self, shapes):
        df = shapes["A"]
        zx = zhixing_state(df)
        mine = float(bd._qsx_series(df["close"]).iloc[-1])
        assert round(mine, 4) == pytest.approx(float(zx["qsx"]), abs=1e-4)

    def test_windows_are_the_chart_ones(self):
        """断言**常量真值**，不是源码文本。

        DKS 已收敛到 `indicators`（2026-08-06，第 3 份重复指标），
        窗口从字面量变成了 `DKS_MA_WINDOWS` 常量 ⇒ 原来的 `"(14, 28, 57, 114)" in src`
        立刻假失败（值是对的、文本读不到）。**能读真值就别读源码。**
        """
        import indicators
        assert indicators.DKS_MA_WINDOWS == (14, 28, 57, 114), \
            "参数必须与 good_b1 图上的知行趋势线一致"
        import inspect
        sig = inspect.signature(indicators.dks_series)
        assert sig.parameters["windows"].default == (14, 28, 57, 114)


class TestLaunchSegment:
    """放量启动段：good_b1 九例全部有，是"主力进过场"的证据。"""

    def test_detects_launch(self, shapes):
        assert bd.detect_launch_segment(shapes["A"])["hit"] is True

    def test_no_launch_in_quiet_decline(self, shapes):
        """长期无量阴跌没有启动段——这正是它与"启动后健康回调"的区别。"""
        assert bd.detect_launch_segment(shapes["B"])["hit"] is False

    def test_requires_both_gain_and_volume(self):
        """只涨不放量、只放量不涨都不算启动。"""
        rise_only = _mk(_platform(30, 10.0, 4e5) + _launch(10.0, 4e5 / 3.2, bars=(0.07,)))
        assert bd.detect_launch_segment(rise_only)["hit"] is False

    def test_short_history_unavailable(self):
        assert bd.detect_launch_segment(_mk(_platform(10, 10.0, 4e5)))["available"] is False


class TestLongStructure:
    def test_good_shape_scores_higher_than_decline(self, shapes):
        a = bd.compute_long_structure(shapes["A"])
        b = bd.compute_long_structure(shapes["B"])
        assert a["score"] > b["score"]

    def test_qsx_gt_dks_is_top_weighted_component(self, shapes):
        a = bd.compute_long_structure(shapes["A"])
        assert a["qsx_gt_dks"] is True
        assert a["components"]["qsx_gt_dks"] == bd.STRUCT_QSX_DKS_PTS

    def test_decline_has_no_bull_stack(self, shapes):
        assert bd.compute_long_structure(shapes["B"])["qsx_gt_dks"] is False

    def test_needs_120_bars_for_dks(self):
        r = bd.compute_long_structure(_mk(_platform(100, 10.0, 4e5)))
        assert r["available"] is False and "114" in r["reason"]


class TestDualAxisOrdering:
    """**核心断言**：双轴排序必须把买点型排在"好票但非买点"与"差票"之前。"""

    def _scores(self, shapes):
        return {k: bd.compute_b1_dual(v, "600000")["score"] for k, v in shapes.items()}

    def test_pullback_buy_points_rank_first(self, shapes):
        s = self._scores(shapes)
        assert s["D"] > s["C"], "突破回踩型(买点)应高于突破未回调(非买点)"
        assert s["A"] > s["C"], "good_b1型(买点)应高于突破未回调(非买点)"

    def test_bad_stock_ranks_last(self, shapes):
        s = self._scores(shapes)
        assert s["B"] == min(s.values()), "长期无量阴跌应垫底"

    def test_old_s_shape_ordering_is_inverted(self, shapes):
        """留证：旧 s_shape 主轴在同一批样本上方向相反。

        它把"突破未回调"(C)排第一、good_b1 型(A)排倒数第二——因为它的 pivot/
        pocket_pivot/compression 三项(占 50 分)奖励的是突破而非回调。这是把它移出
        B1 技术轴的量化依据。
        """
        ss = {k: compute_s_shape(v, "600000")["s_star"] for k, v in shapes.items()}
        assert ss["C"] == max(ss.values()), "旧口径把'突破未回调'排第一"
        assert ss["A"] < ss["C"], "旧口径把 good_b1 型排在'突破未回调'之后"

    def test_axes_are_softly_weighted(self):
        """轴1 是软加权而非硬门槛：QSX<DKS 仍可得分（owner 裁定）。"""
        assert bd.W_STRUCT + bd.W_REVERSAL == pytest.approx(1.0)
        assert 0 < bd.W_STRUCT < 1 and 0 < bd.W_REVERSAL < 1
        assert bd.W_REVERSAL > bd.W_STRUCT, "B1 是回调买入，买点轴权重应更高"

    def test_unavailable_on_short_history(self):
        r = bd.compute_b1_dual(_mk(_platform(50, 10.0, 4e5)), "600000")
        assert r["available"] is False


class TestBreakoutPullbackB1:
    """突破回踩型 B1 = platform_pullback ∩ J<13（结论#15 留下的未测组合）。"""

    def test_hits_on_breakout_pullback(self, shapes):
        r = bd.detect_breakout_pullback_b1(shapes["D"], "600000")
        assert r["hit"] is True
        assert r["in_b1_zone"] is True and r["close_ge_platform_high"] is True

    def test_requires_b1_zone(self, shapes):
        """有平台突破回踩但 J 不低 → 不算 B1（它不是买点）。"""
        r = bd.detect_breakout_pullback_b1(shapes["D"], "600000", j_threshold=-99.0)
        assert r["hit"] is False and r["in_b1_zone"] is False

    def test_strict_vs_tolerant_ph(self, shapes):
        """两种"不低于前高"口径都要报出来，供回测对比（platform_high 基于最高价）。"""
        r = bd.detect_breakout_pullback_b1(shapes["D"], "600000")
        assert r["close_ge_platform_high"] is True      # 默认 0.98 容差
        assert r["close_ge_ph_strict"] is False         # 严格口径要求收盘超历史最高价
        assert r["ph_tol"] == 0.98

    def test_no_platform_no_hit(self, shapes):
        r = bd.detect_breakout_pullback_b1(shapes["B"], "600000")
        assert r["hit"] is False and r["reason"] == "no_platform_pullback"


class TestWeeklyResonance:
    """日周线 B1 共振（owner 2026-08-03 提出的加分项）。

    周线 J<13 意味着更大周期的回调也到位——日线可能只是短暂杀跌，周线同时超卖才说明
    整段回调走完。
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def resonance_shapes():
        # 仅日线 B1：短促急跌 4 根，周线还没跌下来
        a = _platform(110, 10.0, 4.0e5) + _launch(10.0, 4.0e5)
        a += _drift(a[-1][0], 20, 0.010, 7.0e5)
        a += _drift(a[-1][0], 4, -0.030, 2.6e5)
        # 日+周共振：45 根(9周)缓跌 + 末段加速
        b = _platform(110, 10.0, 4.0e5) + _launch(10.0, 4.0e5)
        b += _drift(b[-1][0], 12, 0.012, 7.0e5)
        b += _drift(b[-1][0], 45, -0.008, 3.0e5)
        b += _drift(b[-1][0], 5, -0.022, 2.4e5)
        return {"daily_only": _mk(a), "resonance": _mk(b)}

    def test_matches_weekly_j_state(self, resonance_shapes):
        """口径必须与 enrich_candidates.weekly_j_state 一致（同一 resample + KDJ）。

        注意 ``weekly_j_state`` **没有**字符串日期兜底（生产 df 的 date 是 datetime，
        所以它不暴露），而 detect_weekly_b1_resonance 加了兜底。这里把 date 转成
        datetime 再比，避免比的是"谁更能容错"而不是口径。
        """
        from screening.enrich_candidates import weekly_j_state
        for df in resonance_shapes.values():
            dt = df.copy()
            dt["date"] = pd.to_datetime(dt["date"])
            mine = bd.detect_weekly_b1_resonance(df)
            theirs = weekly_j_state(dt)
            assert mine["weekly_j"] == pytest.approx(float(theirs["weekly_j"]), abs=1e-2)
            assert mine["weekly_j_low"] is theirs["weekly_j_low"]

    def test_hits_only_when_both_periods_low(self, resonance_shapes):
        a = bd.detect_weekly_b1_resonance(resonance_shapes["daily_only"])
        b = bd.detect_weekly_b1_resonance(resonance_shapes["resonance"])
        assert a["daily_j_low"] is True and a["weekly_j_low"] is False
        assert a["hit"] is False, "只有日线低不算共振"
        assert b["daily_j_low"] is True and b["weekly_j_low"] is True
        assert b["hit"] is True

    def test_bonus_applied_only_on_resonance(self, resonance_shapes):
        a = bd.compute_b1_dual(resonance_shapes["daily_only"], "600000")
        b = bd.compute_b1_dual(resonance_shapes["resonance"], "600000")
        assert a["resonance_bonus"] == 0.0
        assert a["score"] == a["score_without_resonance"]
        assert b["resonance_bonus"] == bd.RESONANCE_BONUS_PTS
        assert b["score"] == pytest.approx(
            min(100.0, b["score_without_resonance"] + bd.RESONANCE_BONUS_PTS), abs=0.11)

    def test_string_dates_are_handled(self, resonance_shapes):
        """生产 df 的 date 是 datetime，但测试/中间产物常是字符串——resample 需要兜底。"""
        df = resonance_shapes["resonance"].copy()
        df["date"] = df["date"].astype(str)
        r = bd.detect_weekly_b1_resonance(df)
        assert r["available"] is True and r["hit"] is True

    def test_does_not_mutate_input(self, resonance_shapes):
        df = resonance_shapes["resonance"].copy()
        df["date"] = df["date"].astype(str)
        before = df["date"].iloc[0]
        bd.detect_weekly_b1_resonance(df)
        assert df["date"].iloc[0] == before, "不得就地改调用方的 df"

    def test_nan_j_does_not_count_as_low(self):
        """J 算不出时不能当"满足 B1"（与 j_below_threshold 同一不变量）。"""
        r = bd.detect_weekly_b1_resonance(_mk(_platform(8, 10.0, 4e5)))
        assert r["hit"] is False

    def test_long_pullback_tension_is_real(self, resonance_shapes):
        """留证:**日周共振与长期结构完好有内在张力**。

        周线 J<13 需要约 9 周下跌，而那么长的回调会破坏 QSX>DKS。实测共振用例的
        双轴总分反而低于"仅日线 B1"用例——共振加分 +12 补不回轴1 的下降。
        回测须专门比较这两组，而不是假定共振一定更好。
        """
        a = bd.compute_b1_dual(resonance_shapes["daily_only"], "600000")
        b = bd.compute_b1_dual(resonance_shapes["resonance"], "600000")
        assert a["qsx_gt_dks"] is True
        assert b["qsx_gt_dks"] is False, "长回调破坏了长期多头结构"
        assert b["score"] < a["score"], "共振加分补不回轴1 的下降——这正是待回测的张力"


class TestBacktestRegistration:
    """因子必须注册进回测入口，否则无法验证。"""

    @pytest.mark.parametrize("name", ["b1_dual", "b1_dual_no_res", "long_structure"])
    def test_scorer_registered(self, name):
        assert name in bt.SCORERS

    @pytest.mark.parametrize("name", ["qsx_gt_dks", "j_low_qsx_gt_dks",
                                      "breakout_pullback_b1", "weekly_j_low",
                                      "j_low_weekly_resonance", "j_low_qsx_weekly"])
    def test_gate_registered(self, name):
        assert name in bt.ENTRY_GATES

    def test_scorer_returns_none_on_short_history(self):
        """缺数据返回 None → evaluate 跳过而非填 0 分参与排名（审计 E3）。"""
        assert bt.SCORERS["b1_dual"](_mk(_platform(30, 10.0, 4e5)), "600000") is None

    def test_gates_never_raise(self, shapes):
        for name in ("qsx_gt_dks", "j_low_qsx_gt_dks", "breakout_pullback_b1",
                     "weekly_j_low", "j_low_weekly_resonance", "j_low_qsx_weekly"):
            for df in list(shapes.values()) + [_mk(_platform(5, 10.0, 4e5))]:
                assert isinstance(bt.ENTRY_GATES[name](df), bool)

    def test_j_low_qsx_is_intersection(self, shapes):
        """j_low_qsx_gt_dks 必须是两者交集——"长期向上的票上买短期回调点"。"""
        for df in shapes.values():
            expect = bt.ENTRY_GATES["j_low"](df) and bt.ENTRY_GATES["qsx_gt_dks"](df)
            assert bt.ENTRY_GATES["j_low_qsx_gt_dks"](df) is bool(expect)


class TestNotYetWiredIntoScreening:
    """本批只做可回测因子，**不接入选股链**——先验证赢过无条件基准（结论#15 教训）。"""

    def test_score_candidates_untouched(self):
        import inspect

        from screening import score_candidates as sc
        src = inspect.getsource(sc)
        assert "b1_dual" not in src, "接入选股链前必须先有回测证据"
