# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from final_review_validator import REQUIRED_SECTIONS, validate


def valid_payload():
    return {
        "date": "2026-07-15",
        "report_quality": "degraded",
        "news_digest": {"permission_rule": "news cannot directly increase trading permissions"},
        "execution_review": {"rows": []},
        "theme_lifecycles": [],
        "market_quality_checks": [],
        "revalued_positions": [],
        "next_day_plan": {"holding_plans": []},
        "rule_review": {},
        "unavailable": ["turnover"],
    }


class FinalReviewValidatorTests(unittest.TestCase):
    def test_valid_degraded_report(self):
        self.assertEqual(validate("2026-07-15", "\n".join(REQUIRED_SECTIONS), valid_payload()), [])

    def test_complete_cannot_hide_missing_inputs(self):
        payload = valid_payload()
        payload["report_quality"] = "complete"
        self.assertIn("complete report cannot contain unavailable inputs", validate("2026-07-15", "\n".join(REQUIRED_SECTIONS), payload))

    def test_missing_section_fails(self):
        errors = validate("2026-07-15", "", valid_payload())
        self.assertTrue(any(x.startswith("markdown section missing") for x in errors))


if __name__ == "__main__":
    unittest.main()
