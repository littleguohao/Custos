# -*- coding: utf-8 -*-
"""b1_holding_state.build_pre_checks 单测：注入 fake tq，不触网。"""
from __future__ import annotations

import unittest
from datetime import date

import b1_holding_state as b1


def _ok(value) -> dict:
    return {"ok": True, "value": value, "error": None}


def _bad(code: str = "tdxw_not_running") -> dict:
    return {"ok": False, "value": None, "error": {"code": code}}


class FakeTQ:
    def __init__(self, info: dict, more: dict) -> None:
        self._info = info
        self._more = more
        self.calls: list[str] = []

    def stock_info(self, code: str, timeout: int = 15) -> dict:
        self.calls.append(code)
        return self._info

    def more_info(self, code: str, fields=None, timeout: int = 15) -> dict:
        return self._more


INFO = _ok({"J_start": "20100608", "Name": "测试股", "IsSTGP": "0"})
MORE = _ok({"TPFlag": "0", "ZTPrice": "33.00", "DTPrice": "27.00", "HqDate": "20260717"})


class PreChecksTest(unittest.TestCase):
    def test_normal(self) -> None:
        tq = FakeTQ(INFO, MORE)
        out = b1.build_pre_checks("600150", as_of=date(2026, 7, 17), tq=tq)
        self.assertTrue(out["available"])
        self.assertFalse(out["partial"])
        self.assertEqual(tq.calls, ["600150.SH"])  # norm_code 补后缀
        self.assertEqual(out["listing_date"], "20100608")
        self.assertEqual(out["listing_days"], (date(2026, 7, 17) - date(2010, 6, 8)).days)
        self.assertFalse(out["new_listing_lt20"])
        self.assertFalse(out["is_suspended"])
        self.assertEqual(out["limit_up_price"], 33.0)
        self.assertEqual(out["limit_down_price"], 27.0)
        self.assertEqual(out["hq_date"], "20260717")

    def test_new_listing_lt20(self) -> None:
        tq = FakeTQ(_ok({"J_start": "20260705"}), MORE)
        out = b1.build_pre_checks("688114", as_of=date(2026, 7, 17), tq=tq)
        self.assertEqual(out["listing_days"], 12)
        self.assertTrue(out["new_listing_lt20"])

    def test_bj_code_suffix(self) -> None:
        tq = FakeTQ(INFO, MORE)
        b1.build_pre_checks("920808", as_of=date(2026, 7, 17), tq=tq)
        self.assertEqual(tq.calls, ["920808.BJ"])

    def test_suspended(self) -> None:
        tq = FakeTQ(INFO, _ok({"TPFlag": "1", "ZTPrice": "0.00", "DTPrice": "0.00"}))
        out = b1.build_pre_checks("600150", as_of=date(2026, 7, 17), tq=tq)
        self.assertTrue(out["is_suspended"])
        self.assertEqual(out["limit_up_price"], 0.0)

    def test_tq_unavailable(self) -> None:
        tq = FakeTQ(_bad(), _bad("connection_failed"))
        out = b1.build_pre_checks("600150", as_of=date(2026, 7, 17), tq=tq)
        self.assertFalse(out["available"])
        self.assertEqual(out["error"]["code"], "tdxw_not_running")

    def test_partial_when_stock_info_fails(self) -> None:
        tq = FakeTQ(_bad("connection_failed"), MORE)
        out = b1.build_pre_checks("600150", as_of=date(2026, 7, 17), tq=tq)
        self.assertTrue(out["available"])
        self.assertTrue(out["partial"])
        self.assertIsNone(out["listing_date"])
        self.assertIsNone(out["listing_days"])
        self.assertIsNone(out["new_listing_lt20"])
        self.assertFalse(out["is_suspended"])  # more_info 成功，字段仍可用

    def test_bad_listing_date(self) -> None:
        tq = FakeTQ(_ok({"J_start": "0"}), MORE)
        out = b1.build_pre_checks("600150", as_of=date(2026, 7, 17), tq=tq)
        self.assertIsNone(out["listing_days"])
        self.assertIsNone(out["new_listing_lt20"])

    def test_no_as_of(self) -> None:
        tq = FakeTQ(INFO, MORE)
        out = b1.build_pre_checks("600150", tq=tq)
        self.assertIsNone(out["listing_days"])
        self.assertIsNone(out["new_listing_lt20"])


if __name__ == "__main__":
    unittest.main()
