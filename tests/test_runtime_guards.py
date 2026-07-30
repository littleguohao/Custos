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


class MarketQualityAsOfTests(unittest.TestCase):
    """新鲜度必须按 as_of 判定:当日文件里装 T-1 数据同样是 stale。"""

    DAY = "2026-07-20"

    def _market(self, as_of, quality="auto"):
        return {
            "amv_0": {"amv_change_pct": 1.0, "quality": "confirmed", "as_of": self.DAY},
            "market_breadth": {"up_count": 3000, "down_count": 1000, "quality": quality,
                               "as_of": as_of},
            "sentiment": {"limit_up_count": 50, "quality": quality, "as_of": as_of},
            "turnover": {"turnover_change_pct": 5.0, "quality": quality, "as_of": as_of},
            "overseas_market": {"nasdaq_change_pct": 0.5, "as_of": self.DAY},
        }

    def _check(self, result, field):
        return next(x for x in result["checks"] if x["field"] == field)

    def test_current_day_as_of_passes(self):
        r = runtime_guards.market_quality_gate(self._market(self.DAY), self.DAY)
        self.assertEqual(self._check(r, "market_breadth")["quality"], "auto")
        self.assertFalse(self._check(r, "market_breadth")["stale_as_of"])
        self.assertEqual(r["status"], "pass")

    def test_prior_day_as_of_in_today_file_is_stale(self):
        r = runtime_guards.market_quality_gate(self._market("2026-07-17"), self.DAY)
        for field in ("market_breadth", "sentiment", "turnover"):
            chk = self._check(r, field)
            self.assertEqual(chk["quality"], "stale", field)
            self.assertTrue(chk["stale_as_of"], field)
        self.assertLess(r["quality_score"], 0.8)
        self.assertNotEqual(r["status"], "pass")

    def test_confirmed_quality_also_downgraded_when_as_of_is_old(self):
        r = runtime_guards.market_quality_gate(self._market("2026-07-17", quality="confirmed"),
                                               self.DAY)
        self.assertEqual(self._check(r, "market_breadth")["quality"], "stale")


if __name__ == "__main__":
    unittest.main()
