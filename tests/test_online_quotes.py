# -*- coding: utf-8 -*-
"""online_quotes 单测：腾讯/新浪响应解析（mock）、前缀映射、回退顺序。"""
from __future__ import annotations

import unittest
from unittest import mock

import online_quotes as oq


def _resp(payload):
    r = mock.Mock()
    r.json.return_value = payload
    return r


TENCENT_PAYLOAD = {
    "code": 0,
    "data": {"sh600000": {"day": [
        ["2026-07-16", "10.00", "10.10", "10.20", "9.90", "1000"],
        ["2026-07-17", "10.10", "10.30", "10.40", "10.00", "2000"],
        ["2026-07-20", "10.30", "10.50", "10.60", "10.20", "3000"],
    ]}},
}

SINA_PAYLOAD = [
    {"day": "2026-07-16", "open": "10.00", "high": "10.20", "low": "9.90", "close": "10.10", "volume": "1000"},
    {"day": "2026-07-17", "open": "10.10", "high": "10.40", "low": "10.00", "close": "10.30", "volume": "2000"},
    {"day": "2026-07-20", "open": "10.30", "high": "10.60", "low": "10.20", "close": "10.50", "volume": "3000"},
]


class PrefixMapTest(unittest.TestCase):
    def test_prefix_mapping(self) -> None:
        cases = [("600000", "sh600000"), ("900901", "sh900901"),
                 ("000001", "sz000001"), ("300750", "sz300750"),
                 ("920808", None), ("830799", None), ("430047", None),
                 ("sh000001", "sh000001"), ("sz399001", "sz399001")]
        for code, expect in cases:
            with self.subTest(code=code):
                self.assertEqual(oq._prefixed_symbol(code), expect)


class TencentDailyTest(unittest.TestCase):
    def test_parse_column_order(self) -> None:
        # 腾讯列序 date/open/close/high/low/volume → 统一 date/open/high/low/close/volume
        with mock.patch.object(oq, "fetch_with_retry", return_value=_resp(TENCENT_PAYLOAD)) as m:
            bars = oq.fetch_tencent_daily("600000", count=3)
        url = m.call_args.args[0]
        self.assertIn("param=sh600000,day,,,3,", url)
        self.assertEqual(len(bars), 3)
        last = bars[-1]
        self.assertEqual(last, {"date": "2026-07-20", "open": 10.30, "high": 10.60,
                                "low": 10.20, "close": 10.50, "volume": 3000.0})

    def test_qfqday_key_accepted(self) -> None:
        payload = {"code": 0, "data": {"sz000001": {"qfqday": [
            ["2026-07-20", "1", "2", "3", "0.5", "10"]]}}}
        with mock.patch.object(oq, "fetch_with_retry", return_value=_resp(payload)):
            bars = oq.fetch_tencent_daily("000001")
        self.assertEqual(bars[-1]["close"], 2.0)
        self.assertEqual(bars[-1]["high"], 3.0)

    def test_failures_return_none(self) -> None:
        with mock.patch.object(oq, "fetch_with_retry", side_effect=RuntimeError("conn")):
            self.assertIsNone(oq.fetch_tencent_daily("600000"))
        for payload in [None, {}, {"data": {}}, {"data": {"sh600000": {}}},
                        {"data": {"sh600000": {"day": []}}},
                        {"data": {"sh600000": {"day": [["2026-07-20", "a", "b"]]}}}]:
            with self.subTest(payload=payload), \
                 mock.patch.object(oq, "fetch_with_retry", return_value=_resp(payload)):
                self.assertIsNone(oq.fetch_tencent_daily("600000"))

    def test_bj_not_supported_no_request(self) -> None:
        with mock.patch.object(oq, "fetch_with_retry") as m:
            self.assertIsNone(oq.fetch_tencent_daily("920808"))
        m.assert_not_called()


class SinaDailyTest(unittest.TestCase):
    def test_parse_and_referer(self) -> None:
        with mock.patch.object(oq, "fetch_with_retry", return_value=_resp(SINA_PAYLOAD)) as m:
            bars = oq.fetch_sina_daily("600000", count=3)
        url = m.call_args.args[0]
        self.assertIn("symbol=sh600000", url)
        self.assertIn("datalen=3", url)
        headers = m.call_args.kwargs.get("headers") or {}
        self.assertEqual(headers.get("Referer"), "https://finance.sina.com.cn")
        self.assertEqual(len(bars), 3)
        last = bars[-1]
        self.assertEqual(last, {"date": "2026-07-20", "open": 10.30, "high": 10.60,
                                "low": 10.20, "close": 10.50, "volume": 3000.0})

    def test_failures_return_none(self) -> None:
        with mock.patch.object(oq, "fetch_with_retry", side_effect=RuntimeError("conn")):
            self.assertIsNone(oq.fetch_sina_daily("600000"))
        for payload in [None, {}, [], "null", [{"day": "2026-07-20"}]]:
            with self.subTest(payload=payload), \
                 mock.patch.object(oq, "fetch_with_retry", return_value=_resp(payload)):
                self.assertIsNone(oq.fetch_sina_daily("600000"))

    def test_bj_not_supported_no_request(self) -> None:
        with mock.patch.object(oq, "fetch_with_retry") as m:
            self.assertIsNone(oq.fetch_sina_daily("830799"))
        m.assert_not_called()


class FetchOnlineDailyOrderTest(unittest.TestCase):
    def test_tencent_first_short_circuits(self) -> None:
        with mock.patch.object(oq, "fetch_tencent_daily", return_value=[{"close": 1}]) as t, \
             mock.patch.object(oq, "fetch_sina_daily") as s:
            bars, source = oq.fetch_online_daily("600000")
        self.assertEqual(source, "tencent_daily")
        self.assertTrue(bars)
        t.assert_called_once_with("600000", 3)
        s.assert_not_called()

    def test_tencent_fail_falls_to_sina(self) -> None:
        with mock.patch.object(oq, "fetch_tencent_daily", return_value=None), \
             mock.patch.object(oq, "fetch_sina_daily", return_value=[{"close": 1}]) as s:
            bars, source = oq.fetch_online_daily("600000")
        self.assertEqual(source, "sina_daily")
        self.assertTrue(bars)
        s.assert_called_once()

    def test_all_fail_returns_none_none(self) -> None:
        with mock.patch.object(oq, "fetch_tencent_daily", return_value=None), \
             mock.patch.object(oq, "fetch_sina_daily", return_value=None):
            self.assertEqual(oq.fetch_online_daily("600000"), (None, None))

    def test_bj_returns_none_none_without_request(self) -> None:
        with mock.patch.object(oq, "fetch_tencent_daily", return_value=None) as t, \
             mock.patch.object(oq, "fetch_sina_daily", return_value=None) as s:
            self.assertEqual(oq.fetch_online_daily("920808"), (None, None))
        t.assert_called_once()
        s.assert_called_once()


if __name__ == "__main__":
    unittest.main()
