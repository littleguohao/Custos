"""`b1_holding_state.evaluate` 的**每一条风控信号** —— `B1-holding-v1` 契约的核心。

覆盖率清点（2026-08-07）：75%、37 语句未覆盖，而未覆盖的**正好是信号分支本身**。

为什么每条都要单独测：信号带优先级，而优先级会一路传下去 ——

    b1_holding_state.final_priority
      → generate_risk_and_sectors：P0 ⇒ 归一为「清仓」、P1 ⇒「减仓」
        → chief_decision_report：priority=='高' ⇒ **覆盖 B1 动作**、写进持仓处理优先级表

⇒ **一条信号的优先级标错，最终交易计划里的动作就错**。
既有测试（`test_b1_holding_state.py`）覆盖了整体契约形状与部分分支，
这里补的是逐条信号的**触发条件与优先级**。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("07_tools", "07_tools/market_timing"):
    sys.path.insert(0, str(ROOT / _p))

from holdings import b1_holding_state as bh  # noqa: E402


def _row(**kw):
    """一份「什么信号都不触发」的干净持仓行，测试只覆盖需要的字段。"""
    base = {
        "code": "600000", "name": "浦发", "close": 10.0, "holding_pnl_pct": 0.05,
        "trend_state": "横盘震荡", "box20_position": "箱体上半区",
        "latest_date": "2026-08-07", "above_bbi": True,
        "consecutive_closes_below_bbi": 0,
        "n_structure": {"available": True, "prior_low": 8.0, "pullback_low": 9.0},
        "descending_n_structure": {"available": True, "structural_low": 7.0},
        "price_volume": {"available": True},
        "daily_j": 50.0,
    }
    base.update(kw)
    return base


def _sig(state, name):
    return next((s for s in state["signals"] if s["signal"] == name), None)


def _names(state):
    return {s["signal"] for s in state["signals"]}


class TestLossThresholds:
    """浮亏阈值 —— 与趋势/结构无关的**硬风控**。"""

    def test_hard_loss_p0_at_minus_10(self):
        s = bh.evaluate(_row(holding_pnl_pct=-0.10), "", price_date="2026-08-07")
        sig = _sig(s, "hard_loss")
        assert sig and sig["priority"] == "P0" and "-10%硬风控" in sig["reason"]

    def test_loss_reduction_p1_at_minus_7(self):
        s = bh.evaluate(_row(holding_pnl_pct=-0.07), "", price_date="2026-08-07")
        sig = _sig(s, "loss_reduction")
        assert sig and sig["priority"] == "P1"

    def test_thresholds_are_exclusive(self):
        """两档**互斥**：−10% 只出 hard_loss，不该同时出 loss_reduction
        （两条都在会让同一原因在优先级表里出现两次）。"""
        s = bh.evaluate(_row(holding_pnl_pct=-0.12), "", price_date="2026-08-07")
        assert "hard_loss" in _names(s) and "loss_reduction" not in _names(s)

    def test_just_above_threshold_no_signal(self):
        s = bh.evaluate(_row(holding_pnl_pct=-0.0699), "", price_date="2026-08-07")
        assert not ({"hard_loss", "loss_reduction"} & _names(s))

    def test_missing_pnl_does_not_fabricate_a_signal(self):
        """盈亏缺失时**不得**凭空出风控信号（也不得当成 0）。"""
        s = bh.evaluate(_row(holding_pnl_pct=None), "", price_date="2026-08-07")
        assert not ({"hard_loss", "loss_reduction"} & _names(s))


class TestNStructure:
    def test_l1_breach_is_p0(self):
        s = bh.evaluate(_row(close=7.9), "", price_date="2026-08-07")
        assert _sig(s, "n_l1_breach")["priority"] == "P0"

    def test_l2_breach_is_p1_when_l1_holds(self):
        s = bh.evaluate(_row(close=8.5), "", price_date="2026-08-07")
        sig = _sig(s, "n_l2_breach")
        assert sig["priority"] == "P1" and "L1尚未失守" in sig["reason"]

    def test_stale_structure_is_not_a_p0(self):
        """⚠️ 结构**陈旧**（破位过久/顶部旧 N）不得当作当前 P0 ——
        否则一个几个月前就破的结构会每天重复报最高优先级。
        它进 `unavailable`，由趋势/箱体/亏损信号覆盖。
        """
        s = bh.evaluate(_row(close=7.0, n_structure={
            "available": True, "prior_low": 8.0, "stale": True}), "", price_date="2026-08-07")
        assert "n_l1_breach" not in _names(s)
        assert "n_structure_stale" in s["unavailable"]

    def test_unavailable_structure_recorded(self):
        s = bh.evaluate(_row(n_structure={}), "", price_date="2026-08-07")
        assert "n_structure" in s["unavailable"]

    def test_descending_n_confirmed_is_p0(self):
        s = bh.evaluate(_row(close=6.9), "", price_date="2026-08-07")
        assert _sig(s, "desc_n_confirmed")["priority"] == "P0"

    def test_descending_n_stale_not_p0(self):
        s = bh.evaluate(_row(close=6.0, descending_n_structure={
            "available": True, "structural_low": 7.0, "stale": True}), "", price_date="2026-08-07")
        assert "desc_n_confirmed" not in _names(s)
        assert "descending_n_structure_stale" in s["unavailable"]


class TestBbiBreach:
    def test_two_closes_below_is_p1(self):
        s = bh.evaluate(_row(above_bbi=False, consecutive_closes_below_bbi=2), "",
                        price_date="2026-08-07")
        assert _sig(s, "bbi_two_close_breach")["priority"] == "P1"

    def test_first_close_below_is_only_p2_observation(self):
        """⚠️ **首日跌破只给 P2「次日收复观察」** —— B1 的 BBI 是预警而非最终权威，
        首日就清仓会被单日洗盘打掉。"""
        s = bh.evaluate(_row(above_bbi=False, consecutive_closes_below_bbi=1), "",
                        price_date="2026-08-07")
        sig = _sig(s, "bbi_first_breach")
        assert sig["priority"] == "P2" and "次日收复" in sig["action"]

    def test_above_bbi_gives_no_breach_signal(self):
        s = bh.evaluate(_row(above_bbi=True, consecutive_closes_below_bbi=5), "",
                        price_date="2026-08-07")
        assert not ({"bbi_first_breach", "bbi_two_close_breach"} & _names(s))


class TestTrendAndBox:
    def test_downtrend_with_box_break_is_p0(self):
        s = bh.evaluate(_row(trend_state="下跌", box20_position="下沿/破位区"), "",
                        price_date="2026-08-07")
        assert _sig(s, "trend_box_break")["priority"] == "P0"

    def test_downtrend_alone_is_p1(self):
        s = bh.evaluate(_row(trend_state="下跌"), "", price_date="2026-08-07")
        assert _sig(s, "downtrend")["priority"] == "P1"
        assert "trend_box_break" not in _names(s), "两者互斥，不该同时出"


class TestPriceVolume:
    def test_heavy_large_bear_is_p1(self):
        s = bh.evaluate(_row(price_volume={"available": True, "heavy_large_bear": True}), "",
                        price_date="2026-08-07")
        assert _sig(s, "heavy_large_bear")["priority"] == "P1"

    def test_shrink_small_bear_is_only_p3(self):
        """缩量小阴只 P3「条件持有一天」—— 缩量说明抛压衰减，不该按风险处置。"""
        s = bh.evaluate(_row(price_volume={"available": True, "shrink_small_bear": True}), "",
                        price_date="2026-08-07")
        assert _sig(s, "shrink_small_bear")["priority"] == "P3"

    def test_heavy_bear_wins_over_shrink(self):
        s = bh.evaluate(_row(price_volume={
            "available": True, "heavy_large_bear": True, "shrink_small_bear": True}), "",
            price_date="2026-08-07")
        assert "heavy_large_bear" in _names(s) and "shrink_small_bear" not in _names(s)

    def test_two_bull_profit_take_needs_above_bbi(self):
        """连续两根中大阳的分批止盈**只在 BBI 上方**成立 —— BBI 下方是反弹不是利润。"""
        pv = {"available": True, "two_medium_large_bull": True}
        on = bh.evaluate(_row(price_volume=pv, above_bbi=True), "", price_date="2026-08-07")
        off = bh.evaluate(_row(price_volume=pv, above_bbi=False,
                               consecutive_closes_below_bbi=1), "", price_date="2026-08-07")
        assert "two_bull_profit_take" in _names(on)
        assert "two_bull_profit_take" not in _names(off)

    def test_stale_price_volume_suppresses_pv_signals(self):
        """⚠️ 量价数据不是目标日的 ⇒ **所有量价信号都不出**，并记 `current_price_volume`。

        用昨天的量价下今天的处置结论，是这条链最容易犯的错。
        """
        s = bh.evaluate(_row(latest_date="2026-08-06",
                             price_volume={"available": True, "heavy_large_bear": True}),
                        "", price_date="2026-08-07")
        assert "heavy_large_bear" not in _names(s)
        assert "current_price_volume" in s["unavailable"]

    def test_unavailable_price_volume_recorded(self):
        s = bh.evaluate(_row(price_volume={"available": False}), "", price_date="2026-08-07")
        assert "price_volume" in s["unavailable"]


class TestKdjAndReversalK:
    def test_death_cross_is_p2_observation(self):
        s = bh.evaluate(_row(daily_kdj_death_cross=True), "", price_date="2026-08-07")
        sig = _sig(s, "kdj_death_cross")
        assert sig["priority"] == "P2" and "需结合趋势和结构确认" in sig["reason"]

    def test_reversal_k_needs_both_pattern_and_low_j(self):
        """反转K 候选要求**形态 + J<13 同时** —— 单有形态不算。"""
        pv = {"available": True, "reversal_k_candidate_without_j": True}
        both = bh.evaluate(_row(price_volume=pv, daily_j=12.0), "", price_date="2026-08-07")
        high_j = bh.evaluate(_row(price_volume=pv, daily_j=13.0), "", price_date="2026-08-07")
        assert "reversal_k_candidate" in _names(both)
        assert "reversal_k_candidate" not in _names(high_j)

    def test_reversal_k_is_p3_not_a_buy(self):
        """它只是 P3 观察 —— **反转K 不是买点**（B1 主规则原话）。"""
        s = bh.evaluate(_row(price_volume={
            "available": True, "reversal_k_candidate_without_j": True}, daily_j=8.0),
            "", price_date="2026-08-07")
        sig = _sig(s, "reversal_k_candidate")
        assert sig["priority"] == "P3" and "仍需后续修复确认" in sig["reason"]


class TestBearRegime:
    def test_bear_regime_always_adds_reduce_signal(self):
        """0AMV 空头 ⇒ 无条件加一条 P1「反弹减仓（最高优先级）」并禁止补仓。"""
        s = bh.evaluate(_row(), "空头", price_date="2026-08-07")
        sig = _sig(s, "bear_regime_reduce_top_priority")
        assert sig["priority"] == "P1" and "禁止加仓补仓" in sig["reason"]

    def test_bear_rebound_needs_positive_change(self):
        """空头里**出现反弹**（当日涨）才加第二条 —— 下跌日不重复报。"""
        up = bh.evaluate(_row(price_volume={"available": True, "change_pct": 1.5}),
                         "空头", price_date="2026-08-07")
        down = bh.evaluate(_row(price_volume={"available": True, "change_pct": -1.5}),
                           "空头", price_date="2026-08-07")
        assert "bear_rebound_reduce" in _names(up)
        assert "bear_rebound_reduce" not in _names(down)

    def test_non_bear_regime_adds_nothing(self):
        s = bh.evaluate(_row(), "做多", price_date="2026-08-07")
        assert not ({"bear_regime_reduce_top_priority", "bear_rebound_reduce"} & _names(s))


class TestNumberCoercion:
    """⚠️ 这个模块的数值转换**必须返回 None 而非 0.0**。

    历史（2026-08-07 收敛重复助手时的近失）：本文件原有一个私有函数叫 `finite()`，
    但它返回 `float | None` —— 与 `code_utils.finite()`（失败返回默认值 `0.0`）
    **同名反语义**。按名字合并会把 11 个调用点从「缺数→None」改成「缺数→0.0」，
    后果是：

        current = fnum(row.get("close"))   # close 缺失
        # 若返回 0.0： current < l1(8.0) 恒真
        #   ⇒ 凭空产生 n_l1_breach 「N型主结构清仓评估」P0

    即**没有价格的持仓每天被判最高优先级清仓**。已改为语义正确的 `code_utils.fnum`。
    """

    def test_zero_is_kept(self):
        """`0` 是合法读数（涨跌幅 0、盈亏 0 都真实存在），不得变 None。"""
        assert bh.fnum(0) == 0.0 and bh.fnum("0") == 0.0

    def test_missing_returns_none_not_zero(self):
        assert bh.fnum(None) is None and bh.fnum("x") is None

    def test_nan_and_inf_rejected(self):
        assert bh.fnum(float("nan")) is None and bh.fnum(float("inf")) is None

    def test_missing_close_does_not_fabricate_a_breach(self):
        """端到端钉住上面那个后果：close 缺失时**不得**出结构破位信号。"""
        s = bh.evaluate(_row(close=None), "", price_date="2026-08-07")
        assert not ({"n_l1_breach", "n_l2_breach", "desc_n_confirmed"} & _names(s))
