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
        with patch.object(runtime_guards, "position_freshness", return_value={"status": "stale", "confirmed": False}), patch.object(runtime_guards, "load_json", return_value=confirmations):
            result = runtime_guards.position_freshness_with_confirmation("2026-07-15")
        self.assertEqual(result["status"], "confirmed")
        self.assertTrue(result["confirmed"])

    def test_previous_day_no_trade_confirmation_is_default_intraday_baseline(self):
        confirmations = {"2026-07-14": {"no_trades": True, "note": "无交易"}}
        with patch.object(runtime_guards, "position_freshness", return_value={"status": "stale", "confirmed": False}), patch.object(runtime_guards, "load_json", return_value=confirmations):
            result = runtime_guards.position_freshness_with_confirmation("2026-07-15")
        self.assertEqual(result["status"], "confirmed")
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["inherited_from"], "2026-07-14")
        self.assertIn("B1", result["assumption"])


if __name__ == "__main__":
    unittest.main()
