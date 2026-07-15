# -*- coding: utf-8 -*-
from __future__ import annotations

import math
import unittest

from close_review import json_safe, validate_quote_snapshot, validate_report


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


if __name__ == "__main__":
    unittest.main()
