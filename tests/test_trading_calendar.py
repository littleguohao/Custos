# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import pathlib
import unittest
from datetime import date, timedelta
from pathlib import Path

from custos.datasource.trading_calendar import extract_dates, merge_range
from custos.core.runtime_guards import (
    previous_confirmed_trading_day,
    trading_day_status,
)
import sys


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
        merged = merge_range(
            cfg, date(2026, 7, 1), date(2026, 7, 3), ["2026-07-01", "2026-07-03"]
        )
        self.assertEqual(
            merged["trading_days"], ["2026-06-30", "2026-07-01", "2026-07-03"]
        )
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
        from custos.core.paths import TOOLS

        d = str(TOOLS / "local_tdx")
        from custos.datasource.local_tdx import tq_http

        return tq_http

    def test_endpoint_single_source(self):
        """两处端点必须一致 —— 改了一处忘另一处，调试时会连到不存在的端口。"""
        from custos.datasource import trading_calendar as tc

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
        from custos.datasource import trading_calendar as tc

        fn = ast.parse(textwrap.dedent(inspect.getsource(tc.rpc_trading_dates))).body[0]
        if (
            fn.body
            and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)
        ):
            fn.body = fn.body[1:]  # 去掉 docstring
        code = ast.unparse(fn)
        self.assertNotIn("urlopen", code)
        self.assertNotIn('"method"', code, "不应再自己拼 JSON-RPC payload")
        self.assertIn("tq_http.call", code)

    def test_error_carries_unified_code(self):
        """失败时错误码要进 message —— refresh 会把它记进 source.last_error 供排查。"""
        from unittest import mock
        from custos.datasource import trading_calendar as tc

        tq = self._tq_http()
        with mock.patch.object(
            tq,
            "call",
            return_value={
                "ok": False,
                "value": None,
                "error": {"code": "tdxw_not_running", "detail": "TdxW.exe 未运行"},
            },
        ):
            with self.assertRaises(RuntimeError) as cm:
                tc.rpc_trading_dates(
                    tc.DEFAULT_ENDPOINT, "SH", date(2026, 8, 1), date(2026, 8, 6), 5
                )
        self.assertIn("tdxw_not_running", str(cm.exception))
        self.assertIn("TdxW.exe 未运行", str(cm.exception))

    def test_success_unwraps_value(self):
        """`tq_http.call` 的 value 是去掉 ErrorId 的 result 本体 ⇒ extract_dates 要能吃。"""
        from unittest import mock
        from custos.datasource import trading_calendar as tc

        tq = self._tq_http()
        with mock.patch.object(
            tq,
            "call",
            return_value={
                "ok": True,
                "error": None,
                "value": {"Date": [20260803, "2026-08-04"]},
            },
        ):
            got = tc.rpc_trading_dates(
                tc.DEFAULT_ENDPOINT, "SH", date(2026, 8, 1), date(2026, 8, 6), 5
            )
        self.assertEqual(got, ["2026-08-03", "2026-08-04"])


class RefreshAndCliTests(unittest.TestCase):
    """`refresh()` 与 CLI 退出码 —— **cron 直接依赖它们**。

    覆盖率清点（2026-08-07）显示这两块此前 0 覆盖：`refresh()` 全函数、
    `main()` 的两个 `exit 2` 分支。而 cron 周五 14:35 跑
    `trading_calendar.py --require-refresh`，**靠退出码判成败**；
    五个 runner 又都用 `trading_day_status` 判交易日。
    这块坏了不是「少一个功能」，是**整周的调度判断都可能错**。
    """

    def setUp(self):
        import tempfile
        from custos.datasource import trading_calendar as tc

        self.tc = tc
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self._cache, self._config = tc.CACHE, tc.CONFIG
        tc.CACHE = self.tmp / "cache.json"
        tc.CONFIG = self.tmp / "config.json"

    def tearDown(self):
        self.tc.CACHE, self.tc.CONFIG = self._cache, self._config

    def test_refresh_success_updates_cache(self):
        from unittest import mock

        tc = self.tc
        with mock.patch.object(
            tc,
            "rpc_trading_dates",
            return_value=["2026-08-03", "2026-08-04", "2026-08-05"],
        ):
            r = tc.refresh(
                date(2026, 8, 3), date(2026, 8, 5), tc.DEFAULT_ENDPOINT, "SH", 5
            )
        self.assertEqual(r["status"], "updated")
        cached = json.loads(tc.CACHE.read_text(encoding="utf-8"))
        self.assertIn("2026-08-04", cached["trading_days"])
        self.assertIsNone(cached["source"]["last_error"])
        self.assertEqual(
            cached["source"]["last_success_at"], cached["source"]["last_refresh_at"]
        )

    def test_refresh_failure_preserves_cache(self):
        """RPC 挂掉时**必须保住旧缓存**并记 last_error。

        这是最要紧的语义：日历缓存是五个 runner 判交易日的依据，
        一次刷新失败绝不能把它清空 —— 那会让所有 runner 以为「今天不是交易日」。
        """
        from unittest import mock

        tc = self.tc
        tc.CACHE.write_text(
            json.dumps(
                {
                    "version": 1,
                    "covered_ranges": [["2026-07-01", "2026-07-31"]],
                    "trading_days": ["2026-07-15"],
                    "non_trading_days": [],
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.object(
            tc, "rpc_trading_dates", side_effect=RuntimeError("TdxW 未运行")
        ):
            r = tc.refresh(
                date(2026, 8, 3), date(2026, 8, 5), tc.DEFAULT_ENDPOINT, "SH", 5
            )
        self.assertEqual(r["status"], "cache_preserved")
        self.assertIsNone(r["covered"])
        cached = json.loads(tc.CACHE.read_text(encoding="utf-8"))
        self.assertEqual(
            cached["trading_days"], ["2026-07-15"], "旧缓存被刷新失败清掉了"
        )
        self.assertIn("TdxW 未运行", cached["source"]["last_error"])
        self.assertIn("RuntimeError", cached["source"]["last_error"])

    def test_refresh_covered_span_reflects_answer_not_request(self):
        """`covered` 报的是**RPC 实际答到的区间**，不是请求区间。

        交易所只发布当年安排，请求 370 天可能只答到年底 ——
        若按请求区间记「已覆盖」，之后的日期会被当成「已知非交易日」。
        """
        from unittest import mock

        tc = self.tc
        with mock.patch.object(
            tc, "rpc_trading_dates", return_value=["2026-08-03", "2026-08-04"]
        ):
            r = tc.refresh(
                date(2026, 8, 1), date(2027, 8, 1), tc.DEFAULT_ENDPOINT, "SH", 5
            )
        self.assertEqual(r["covered"], {"start": "2026-08-03", "end": "2026-08-04"})

    def test_default_range_starts_at_month_begin(self):
        start, end = self.tc.default_range(date(2026, 8, 7))
        self.assertEqual(start, date(2026, 8, 1))
        self.assertEqual(end, date(2026, 8, 7) + timedelta(days=370))

    def test_extract_dates_non_list_returns_empty(self):
        self.assertEqual(self.tc.extract_dates({"result": {"Date": "not-a-list"}}), [])
        self.assertEqual(self.tc.extract_dates(None), [])

    def test_normalize_day_rejects_garbage(self):
        self.assertIsNone(self.tc.normalize_day("not-a-date"))
        self.assertIsNone(self.tc.normalize_day(""))


class CliExitCodeTests(unittest.TestCase):
    """CLI 退出码 —— cron 按码判定，不能靠 stdout。"""

    def test_check_date_exit_2_when_unknown(self):
        """`--check-date` 落在日历覆盖范围外时 exit 2（**未知 ≠ 非交易日**）。"""
        from unittest import mock
        from custos.datasource import trading_calendar as tc

        with mock.patch.object(
            tc,
            "trading_day_status",
            return_value={"is_trading_day": None, "reason": "超出覆盖"},
        ):
            with self.assertRaises(SystemExit) as cm:
                tc.main.__wrapped__() if hasattr(tc.main, "__wrapped__") else self._run(
                    tc, ["--check-date", "2030-01-01"]
                )
        self.assertEqual(cm.exception.code, 2)

    def _run(self, tc, argv):
        import sys
        from unittest import mock

        with mock.patch.object(sys, "argv", ["trading_calendar"] + argv):
            return tc.main()

    def test_check_date_ok_returns_none(self):
        from unittest import mock
        from custos.datasource import trading_calendar as tc

        with mock.patch.object(
            tc,
            "trading_day_status",
            return_value={"is_trading_day": True, "reason": "ok"},
        ):
            self.assertIsNone(self._run(tc, ["--check-date", "2026-08-07"]))

    def test_require_refresh_exit_2_when_not_updated(self):
        """`--require-refresh` 且刷新未成功 → exit 2。cron 周五 14:35 就靠这个。"""
        from unittest import mock
        from custos.datasource import trading_calendar as tc

        with mock.patch.object(
            tc, "refresh", return_value={"status": "cache_preserved", "covered": None}
        ):
            with self.assertRaises(SystemExit) as cm:
                self._run(tc, ["--require-refresh"])
        self.assertEqual(cm.exception.code, 2)

    def test_require_refresh_ok_when_updated(self):
        from unittest import mock
        from custos.datasource import trading_calendar as tc

        with mock.patch.object(
            tc, "refresh", return_value={"status": "updated", "covered": None}
        ):
            self.assertIsNone(self._run(tc, ["--require-refresh"]))

    def test_end_before_start_is_a_usage_error(self):
        from custos.datasource import trading_calendar as tc

        with self.assertRaises(SystemExit):
            self._run(tc, ["--start", "2026-08-10", "--end", "2026-08-01"])
