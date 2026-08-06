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
        # 用远超任何日历缓存覆盖范围的远期年份(本地通达信缓存已延伸到 2027，不能再拿 2027 测"未知")
        result = trading_day_status("2099-07-15")  # Wednesday
        self.assertIsNone(result["is_trading_day"])


if __name__ == "__main__":
    unittest.main()


class TransportConvergenceTests(unittest.TestCase):
    """`trading_calendar` 的 RPC 必须走 `tq_http`，且两处端点不得漂移。

    2026-08-06 收敛前：`trading_calendar` 自己拼 JSON-RPC + `urlopen`，
    与 `tq_http` 是**同一个服务**（两处都硬编码 `http://127.0.0.1:17709/`）却各写一套
    ⇒ 拿不到 TdxW 预检、统一错误分类，将来加在 `tq_http.call` 的安全拦截也漏掉这条路径。
    """

    def _tq_http(self):
        import sys
        from paths import TOOLS
        d = str(TOOLS / "local_tdx")
        if d not in sys.path:
            sys.path.insert(0, d)
        import tq_http
        return tq_http

    def test_endpoint_single_source(self):
        """两处端点必须一致 —— 改了一处忘另一处，调试时会连到不存在的端口。"""
        import trading_calendar as tc
        self.assertEqual(tc.DEFAULT_ENDPOINT, self._tq_http().TQ_HTTP_URL)

    def test_does_not_build_its_own_rpc(self):
        """代码不得再出现自拼 JSON-RPC 的痕迹（urlopen / 手写 payload）。

        ⚠️ **必须剥掉 docstring 再查**。第一版直接扫 `inspect.getsource`，
        被函数自己那句「原先这里自己拼 JSON-RPC + urlopen」的说明**误判为违规**
        —— 解释历史的注释和残留的实现，字符串层面长得一样。
        """
        import ast
        import inspect
        import textwrap
        import trading_calendar as tc
        fn = ast.parse(textwrap.dedent(inspect.getsource(tc.rpc_trading_dates))).body[0]
        if (fn.body and isinstance(fn.body[0], ast.Expr)
                and isinstance(fn.body[0].value, ast.Constant)):
            fn.body = fn.body[1:]                    # 去掉 docstring
        code = ast.unparse(fn)
        self.assertNotIn("urlopen", code)
        self.assertNotIn('"method"', code, "不应再自己拼 JSON-RPC payload")
        self.assertIn("tq_http.call", code)

    def test_error_carries_unified_code(self):
        """失败时错误码要进 message —— refresh 会把它记进 source.last_error 供排查。"""
        from unittest import mock
        import trading_calendar as tc
        tq = self._tq_http()
        with mock.patch.object(tq, "call",
                               return_value={"ok": False, "value": None,
                                             "error": {"code": "tdxw_not_running",
                                                       "detail": "TdxW.exe 未运行"}}):
            with self.assertRaises(RuntimeError) as cm:
                tc.rpc_trading_dates(tc.DEFAULT_ENDPOINT, "SH",
                                     date(2026, 8, 1), date(2026, 8, 6), 5)
        self.assertIn("tdxw_not_running", str(cm.exception))
        self.assertIn("TdxW.exe 未运行", str(cm.exception))

    def test_success_unwraps_value(self):
        """`tq_http.call` 的 value 是去掉 ErrorId 的 result 本体 ⇒ extract_dates 要能吃。"""
        from unittest import mock
        import trading_calendar as tc
        tq = self._tq_http()
        with mock.patch.object(tq, "call",
                               return_value={"ok": True, "error": None,
                                             "value": {"Date": [20260803, "2026-08-04"]}}):
            got = tc.rpc_trading_dates(tc.DEFAULT_ENDPOINT, "SH",
                                       date(2026, 8, 1), date(2026, 8, 6), 5)
        self.assertEqual(got, ["2026-08-03", "2026-08-04"])
