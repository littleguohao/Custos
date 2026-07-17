# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trading_calendar import extract_dates, merge_range
from runtime_guards import previous_confirmed_trading_day, trading_day_status


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

    def test_official_year_weekday_is_trading_day(self):
        result = trading_day_status("2026-07-16")
        self.assertIs(result["is_trading_day"], True)
        self.assertEqual(result["quality"], "confirmed")
        self.assertIn("官方年度安排", result["reason"])

    def test_official_holiday_weekday_is_closed(self):
        result = trading_day_status("2026-02-16")
        self.assertIs(result["is_trading_day"], False)
        self.assertIn("春节", result["reason"])

    def test_adjusted_weekend_remains_closed(self):
        result = trading_day_status("2026-02-28")
        self.assertIs(result["is_trading_day"], False)
        self.assertEqual(result["reason"], "周末休市")

    def test_previous_day_skips_long_holiday(self):
        self.assertEqual(previous_confirmed_trading_day("2026-02-24"), "2026-02-13")

    def test_unregistered_future_year_remains_unknown(self):
        result = trading_day_status("2027-07-15")
        self.assertIsNone(result["is_trading_day"])


if __name__ == "__main__":
    unittest.main()
