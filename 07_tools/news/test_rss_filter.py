# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import datetime, timezone

import rss_filter


class RssFilterTests(unittest.TestCase):
    def test_missing_publish_date_is_not_parseable(self):
        self.assertIsNone(rss_filter.parse_dt(None))

    def test_valid_publish_date_is_utc(self):
        value = rss_filter.parse_dt("2026-07-15T08:00:00+08:00")
        self.assertEqual(value, datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))

    def test_canonical_url_removes_tracking(self):
        value = rss_filter.canonical_url("https://example.com/a?utm_source=x&id=1")
        self.assertEqual(value, "https://example.com/a?id=1")


if __name__ == "__main__":
    unittest.main()
