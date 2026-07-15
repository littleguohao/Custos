# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trading_calendar import extract_dates, merge_range


class TradingCalendarTests(unittest.TestCase):
    def test_extract_json_rpc_result(self):
        payload = {"result": {"Date": [20260701, "2026-07-02", "bad"]}}
        self.assertEqual(extract_dates(payload), ["2026-07-01", "2026-07-02"])

    def test_refresh_replaces_only_covered_range(self):
        cfg = {
            "trading_days": ["2026-06-30", "2026-07-01"],
            "non_trading_days": ["2026-07-02"],
            "covered_ranges": [],
        }
        merged = merge_range(cfg, date(2026, 7, 1), date(2026, 7, 3), ["2026-07-01", "2026-07-03"])
        self.assertEqual(merged["trading_days"], ["2026-06-30", "2026-07-01", "2026-07-03"])
        self.assertEqual(merged["non_trading_days"], ["2026-07-02"])


if __name__ == "__main__":
    unittest.main()
