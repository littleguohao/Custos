# -*- coding: utf-8 -*-
from __future__ import annotations

import math
import unittest

from close_review.review_core import build_delivery_digest, classify, json_safe, validate_quote_snapshot, validate_report


POSITIONS = [{"代码": "600000.SH", "名称": "测试股票"}]


def valid_snapshot():
    return {
        "as_of_date": "2026-07-15",
        "captured_at": "2026-07-15T14:45:30+08:00",
        "source": "tdx_quotes",
        "quotes": [{"code": "600000", "date": "2026-07-15", "time": "14:45:29", "price": 10, "previous_close": 9.8, "change_pct": 2.04}],
        "indices": [
            {"code": "000001", "date": "2026-07-15", "time": "14:45:28", "price": 3500, "change_pct": 0.1},
            {"code": "399001", "date": "2026-07-15", "time": "14:45:28", "price": 11000, "change_pct": -0.2},
            {"code": "399006", "date": "2026-07-15", "time": "14:45:28", "price": 2200, "change_pct": -0.3},
        ],
    }


class CloseReviewValidationTests(unittest.TestCase):
    def test_valid_snapshot_passes(self):
        self.assertEqual(validate_quote_snapshot("2026-07-15", POSITIONS, valid_snapshot()), [])

    def test_missing_holding_and_index_fail(self):
        snapshot = valid_snapshot()
        snapshot["quotes"] = []
        snapshot["indices"] = snapshot["indices"][:2]
        errors = validate_quote_snapshot("2026-07-15", POSITIONS, snapshot)
        self.assertIn("holding quote missing: 600000", errors)
        self.assertIn("index quote missing: 399006", errors)

    def test_report_requires_current_quote_gate(self):
        report = "\n".join([
            "# 14:45 收盘前操作建议 — 2026-07-15",
            "## 0. 主要指数快照",
            "## 1. 当日行情重估持仓",
            "| 600000 | 测试股票 |",
            "## 2. 动态持仓优先级",
            "## 5. 运行权限",
        ])
        errors = validate_report("2026-07-15", POSITIONS, report, {"position_gate": {"quotes_current": False}})
        self.assertEqual(errors, ["runtime gate does not confirm current holding quotes"])

    def test_non_finite_numbers_become_null_values(self):
        value = {"nan": math.nan, "nested": [math.inf, -math.inf, 1.0]}
        self.assertEqual(json_safe(value), {"nan": None, "nested": [None, None, 1.0]})

    def test_delivery_digest_is_bounded_and_complete(self):
        digest = build_delivery_digest(
            "2026-07-15",
            valid_snapshot(),
            valid_snapshot()["indices"],
            POSITIONS,
            {"600000": {"price": 10.0, "bbi": {"state": "当前价在2026-07-14 BBI上方"}, "n_structure": {"state": "N型前低 9.00"}}},
            {"600000": valid_snapshot()["quotes"][0]},
            [{"code": "600000", "priority": "P2", "action": "持有观察"}],
            0.2,
            {"status": "confirmed", "reason": "当日已确认"},
            {"position_gate": {"allow_precise_quantity": True, "allow_position_reduction": True, "allow_position_increase": False}},
            "缺失",
            "空头",
        )
        self.assertLessEqual(len(digest), 3500)
        for text in ("600000", "上证指数", "P2 持有观察", "精确数量允许", "提高仓位禁止", "禁止动作"):
            self.assertIn(text, digest)

    def test_n_structure_breach_precedes_bbi_and_other_rules(self):
        tech = {
            "trend_state": "上涨",
            "box20_position": "箱体上半区",
            "bbi": 10,
            "above_bbi": True,
            "n_structure": {"available": True, "prior_low": 9.5, "prior_low_date": "2026-06-01"},
        }
        priority, action, reason = classify(
            {"单位成本": 8, "持有盈亏率": 0.2}, tech, [], {"price": 9.4, "change_pct": -1}, False
        )
        self.assertEqual((priority, action), ("P0", "N型前低清仓评估"))
        self.assertIn("主结构前低已失守", reason)


if __name__ == "__main__":
    unittest.main()
