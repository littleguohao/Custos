# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

import pandas as pd

from technical_monitor import n_structure_state


def frame(lows, highs, closes):
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(lows), freq="D"),
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "amount": [1] * len(lows),
        "volume": [1] * len(lows),
    })


class NStructureTests(unittest.TestCase):
    def test_completed_rising_n_returns_second_low(self):
        lows = [11,10,9,8,7,8,9,10,11,10,9,8,9,10,11,12,13,14]
        highs = [12,11,10,9,8,9,10,11,13,12,11,10,11,12,14,15,16,17]
        closes = [11.5,10.5,9.5,8.5,7.5,8.5,9.5,10.5,12.5,11.5,10.5,8.8,10,11,13.5,14.5,15.5,16.5]
        result = n_structure_state(frame(lows, highs, closes), left=2, right=2)
        self.assertTrue(result["available"])
        self.assertEqual(result["prior_low"], 7.5)
        self.assertEqual(result["pullback_low"], 8.8)
        self.assertEqual(result["breakout_level"], 12.5)
        self.assertEqual(result["confirmed_date"], "2026-01-15")

    def test_lower_second_low_is_not_rising_n(self):
        lows = [11,10,9,8,7,8,9,10,11,10,9,6,9,10,11,12,13,14]
        highs = [12,11,10,9,8,9,10,11,13,12,11,10,11,12,14,15,16,17]
        closes = [11.5,10.5,9.5,8.5,7.5,8.5,9.5,10.5,12.5,11.5,10.5,7,10,11,13.5,14.5,15.5,16.5]
        self.assertFalse(n_structure_state(frame(lows, highs, closes), left=2, right=2)["available"])

    def test_unconfirmed_recent_low_is_ignored(self):
        lows = [11,10,9,8,7,8,9,10,11,10,9,8]
        highs = [12,11,10,9,8,9,10,11,13,12,11,10]
        closes = [11.5,10.5,9.5,8.5,7.5,8.5,9.5,10.5,12.5,11.5,10.5,8.8]
        self.assertFalse(n_structure_state(frame(lows, highs, closes), left=2, right=2)["available"])


if __name__ == "__main__":
    unittest.main()
