# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from review_enrichment import lifecycle


class ReviewEnrichmentTests(unittest.TestCase):
    def test_downtrend_is_retreat(self):
        row = {"theme_id": "x", "sector": "测试", "raw_stage": "退潮/下跌", "trend": "下跌", "score": 20}
        result = lifecycle(row, 2)
        self.assertEqual(result["phase"], "退潮")
        self.assertEqual(result["continuity"], "weak")
        self.assertEqual(result["event_evidence_count"], 2)

    def test_repair_does_not_claim_continuity(self):
        result = lifecycle({"sector": "测试", "raw_stage": "修复/上行", "trend": "上涨"}, 0)
        self.assertEqual(result["phase"], "修复")
        self.assertEqual(result["continuity"], "unavailable")
        self.assertEqual(result["fund_flow_evidence"], "unavailable")


if __name__ == "__main__":
    unittest.main()
