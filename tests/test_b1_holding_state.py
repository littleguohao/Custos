# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from b1_holding_state import evaluate


class B1HoldingStateTests(unittest.TestCase):
    def test_l1_hard_breach_has_p0_priority(self):
        row = {"code": "600000", "close": 9.0, "holding_pnl_pct": 0.2, "trend_state": "上涨", "box20_position": "箱体上半区", "above_bbi": True, "n_structure": {"available": True, "prior_low": 10, "pullback_low": 12}, "price_volume": {"available": True, "two_medium_large_bull": True}}
        state = evaluate(row, "做多")
        self.assertEqual((state["final_priority"], state["final_action"]), ("P0", "N型主结构清仓评估"))

    def test_l2_failure_is_distinct_from_l1(self):
        row = {"code": "600000", "close": 11.0, "trend_state": "横盘震荡", "box20_position": "箱体上半区", "above_bbi": True, "n_structure": {"available": True, "prior_low": 10, "pullback_low": 12}, "price_volume": {"available": True}}
        state = evaluate(row, "中性")
        self.assertEqual((state["final_priority"], state["final_action"]), ("P1", "N型回踩失守评估"))
        self.assertNotIn("n_l1_breach", [x["signal"] for x in state["signals"]])

    def test_bear_rebound_reduces_and_never_allows_add(self):
        row = {"code": "600000", "close": 11.0, "trend_state": "横盘震荡", "box20_position": "箱体上半区", "above_bbi": True, "n_structure": {"available": False}, "price_volume": {"available": True, "change_pct": 2.0}}
        state = evaluate(row, "空头")
        signals = [x["signal"] for x in state["signals"]]
        self.assertIn("bear_rebound_reduce", signals)
        self.assertIn("bear_regime_reduce_top_priority", signals)
        self.assertEqual((state["final_priority"], state["final_action"]), ("P1", "空头区间反弹减仓(最高优先级)"))
        self.assertFalse(state["permissions"]["allow_add"])

    def test_bear_regime_reduce_top_priority_fires_without_other_signals(self):
        row = {"code": "600000", "close": 11.0, "trend_state": "横盘震荡", "box20_position": "箱体上半区", "above_bbi": True, "n_structure": {"available": False}, "price_volume": {"available": True}}
        state = evaluate(row, "空头")
        self.assertEqual(state["final_priority"], "P1")
        self.assertEqual(state["signals"][0]["signal"], "bear_regime_reduce_top_priority")
        self.assertFalse(state["permissions"]["allow_add"])

    def test_bear_regime_rule_does_not_fire_outside_bear_regime(self):
        row = {"code": "600000", "close": 11.0, "trend_state": "横盘震荡", "box20_position": "箱体上半区", "above_bbi": True, "n_structure": {"available": False}, "price_volume": {"available": True}}
        for regime in ("做多", "中性", "未知"):
            state = evaluate(row, regime)
            self.assertEqual((state["final_priority"], state["final_action"]), ("P3", "条件持有"))
            self.assertNotIn("bear_regime_reduce_top_priority", [x["signal"] for x in state["signals"]])

    def test_p0_hard_risk_still_outranks_bear_regime_rule(self):
        row = {"code": "600000", "close": 9.0, "holding_pnl_pct": 0.2, "trend_state": "上涨", "box20_position": "箱体上半区", "above_bbi": True, "n_structure": {"available": True, "prior_low": 10, "pullback_low": 12}, "price_volume": {"available": True}}
        state = evaluate(row, "空头")
        self.assertEqual(state["final_priority"], "P0")
        self.assertIn("bear_regime_reduce_top_priority", [x["signal"] for x in state["signals"]])

    def test_two_bull_profit_take_is_below_hard_risk(self):
        row = {"code": "600000", "close": 13.0, "trend_state": "上涨", "box20_position": "上沿/突破区", "above_bbi": True, "n_structure": {"available": True, "prior_low": 10, "pullback_low": 11}, "price_volume": {"available": True, "two_medium_large_bull": True}}
        state = evaluate(row, "做多")
        self.assertEqual((state["final_priority"], state["final_action"]), ("P2", "分批止盈"))

    def test_action_plan_is_directional_not_exact_quantity(self):
        row = {"code": "600000", "close": 11.0, "trend_state": "横盘震荡", "box20_position": "箱体上半区", "above_bbi": True, "n_structure": {"available": True, "prior_low": 10, "pullback_low": 12}, "price_volume": {"available": True}}
        plan = evaluate(row, "中性")["action_plan"]
        self.assertEqual(plan["suggested_reduction_pct_of_holding"], [10, 25])
        self.assertIsNone(plan["exact_quantity"])

    def test_stale_price_volume_does_not_trigger_current_action(self):
        row = {"code": "600000", "latest_date": "2026-07-14", "close": 13.0, "trend_state": "上涨", "box20_position": "上沿/突破区", "above_bbi": True, "n_structure": {"available": True, "prior_low": 10, "pullback_low": 11}, "price_volume": {"available": True, "two_medium_large_bull": True, "heavy_large_bear": True}}
        state = evaluate(row, "做多", 13.0, "2026-07-15")
        self.assertEqual((state["final_priority"], state["final_action"]), ("P3", "条件持有"))
        self.assertIn("current_price_volume", state["unavailable"])


if __name__ == "__main__":
    unittest.main()
