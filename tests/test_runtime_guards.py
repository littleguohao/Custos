# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest.mock import patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime_guards


class RuntimeGuardTests(unittest.TestCase):
    def test_same_day_confirmation_is_confirmed(self):
        confirmations = {"2026-07-15": {"no_trades": True, "note": "无交易"}}
        with patch.object(runtime_guards, "position_freshness", return_value={"status": "stale", "confirmed": False}), patch.object(runtime_guards, "load_json", return_value=confirmations), patch.object(runtime_guards, "ledger_trades_on", return_value=[]):
            result = runtime_guards.position_freshness_with_confirmation("2026-07-15")
        self.assertEqual(result["status"], "confirmed")
        self.assertTrue(result["confirmed"])

    def test_previous_day_no_trade_confirmation_is_default_intraday_baseline(self):
        confirmations = {"2026-07-14": {"no_trades": True, "note": "无交易"}}
        with patch.object(runtime_guards, "position_freshness", return_value={"status": "stale", "confirmed": False}), patch.object(runtime_guards, "load_json", return_value=confirmations), patch.object(runtime_guards, "ledger_trades_on", return_value=[]):
            result = runtime_guards.position_freshness_with_confirmation("2026-07-15")
        self.assertEqual(result["status"], "confirmed")
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["inherited_from"], "2026-07-14")
        self.assertIn("B1", result["assumption"])

    def test_ledger_trades_override_inherited_no_trade_baseline(self):
        confirmations = {"2026-07-14": {"no_trades": True, "note": "无交易"}}
        trades = [{"交易类别": "买入", "名称": "中国船舶", "代码": "600150", "成交数量": "900.0", "成交价格": "32.92"}]
        with patch.object(runtime_guards, "position_freshness", return_value={"status": "stale", "confirmed": False}), patch.object(runtime_guards, "load_json", return_value=confirmations), patch.object(runtime_guards, "ledger_trades_on", return_value=trades):
            result = runtime_guards.position_freshness_with_confirmation("2026-07-15")
        self.assertEqual(result["status"], "confirmed")
        self.assertFalse(result["inherited"])
        self.assertNotIn("assumption", result)
        self.assertIn("买入中国船舶", result["reason"])


if __name__ == "__main__":
    unittest.main()
