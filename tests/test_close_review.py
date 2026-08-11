# -*- coding: utf-8 -*-
from __future__ import annotations

import math
import unittest

from custos.pipeline.close_review.review_core import build_delivery_digest, classify, json_safe, validate_quote_snapshot, validate_report
from custos.pipeline.close_review import review_core as rc


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


class RegimeAdviceTests(unittest.TestCase):
    """操作建议口径必须跟随实际 0AMV regime(原为硬编码"实质空头",做多时报告自相矛盾)。"""

    def test_bear_keeps_reduction_wording(self):
        self.assertIn("空头", rc.regime_advice("空头"))
        self.assertIn("减仓", rc.regime_advice("空头"))

    def test_long_does_not_claim_bear(self):
        text = rc.regime_advice("做多")
        self.assertIn("做多", text)
        self.assertNotIn("实质空头", text)

    def test_neutral_forbids_treating_as_long(self):
        text = rc.regime_advice("中性")
        self.assertIn("中性", text)
        self.assertNotIn("实质空头", text)

    def test_unknown_regime_falls_back_to_conservative(self):
        text = rc.regime_advice("未知")
        self.assertIn("不加仓", text)
        self.assertNotIn("实质空头", text)


class RiskSourceDateTests(unittest.TestCase):
    """risk_decision 回退到旧文件时,日期必须能被报告标注出来。"""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = rc.RISK
        rc.RISK = self.tmp

    def tearDown(self):
        rc.RISK = self._orig

    def _write(self, day):
        (self.tmp / f"{day}_risk_decision.json").write_text('{"stock_risks": []}', encoding="utf-8")

    def test_same_day_file_used(self):
        self._write("2026-07-20")
        path, src = rc.risk_source_date("2026-07-20")
        self.assertEqual(src, "2026-07-20")
        self.assertTrue(path.exists())

    def test_falls_back_to_latest_and_reports_its_date(self):
        self._write("2026-07-16")
        self._write("2026-07-17")
        _path, src = rc.risk_source_date("2026-07-20")
        self.assertEqual(src, "2026-07-17")      # 最近一份,且日期可被标注

    def test_no_file_returns_empty(self):
        path, src = rc.risk_source_date("2026-07-20")
        self.assertIsNone(path)
        self.assertEqual(src, "")


if __name__ == "__main__":
    unittest.main()
