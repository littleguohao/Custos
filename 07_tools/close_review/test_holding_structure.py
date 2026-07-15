# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from holding_structure import n_structure_basis


class HoldingStructureTests(unittest.TestCase):
    def test_breach_has_structural_clear_priority(self):
        row = {"n_structure": {"available": True, "prior_low": 10, "prior_low_date": "2026-06-01"}}
        result = n_structure_basis(row, 9.9)
        self.assertEqual(result["signal"], "structural_clear")
        self.assertTrue(result["breached"])

    def test_price_above_low_keeps_structure(self):
        row = {"n_structure": {"available": True, "prior_low": 10, "prior_low_date": "2026-06-01"}}
        self.assertEqual(n_structure_basis(row, 10)["signal"], "structure_hold")

    def test_pullback_failure_does_not_claim_hard_floor_breach(self):
        row = {"n_structure": {"available": True, "prior_low": 10, "prior_low_date": "2026-06-01", "pullback_low": 12}}
        result = n_structure_basis(row, 11)
        self.assertEqual(result["signal"], "pullback_failure")
        self.assertIn("主结构前低尚未失守", result["reminder"])

    def test_missing_structure_does_not_invent_level(self):
        result = n_structure_basis({"n_structure": {"available": False}}, 10)
        self.assertEqual(result["signal"], "unavailable")
        self.assertIn("待确认", result["state"])


if __name__ == "__main__":
    unittest.main()
