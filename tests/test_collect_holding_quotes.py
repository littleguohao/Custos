# -*- coding: utf-8 -*-
"""collect_holding_quotes 单测：mock tq_http / 各数据源，覆盖快照成败、回退顺序、BJ 路径、Now<=0。"""
from __future__ import annotations

import unittest
from unittest import mock

import collect_holding_quotes as chq

TARGET = "2026-07-19"


def _ok(value: dict) -> dict:
    return {"ok": True, "value": value, "error": None}


def _bad(code: str = "tdxw_not_running") -> dict:
    return {"ok": False, "value": None, "error": {"code": code}}


def _quote(source: str, d: str = TARGET) -> dict:
    return {"code": "600000", "name": "浦发银行", "market": "SH", "available": True,
            "date": d, "close": 10.0, "source": source}


class TqSnapshotQuoteTest(unittest.TestCase):
    def test_success_schema_and_change_pct(self) -> None:
        value = {"Now": "10.50", "LastClose": "10.00", "Open": "10.10",
                 "Max": "10.80", "Min": "10.05", "Volume": "123456", "Amount": "1296000"}
        with mock.patch.object(chq.tq_http, "snapshot", return_value=_ok(value)) as m:
            q = chq._tq_snapshot_quote("600000", "浦发银行", 1, TARGET)
        m.assert_called_once_with("600000.SH")
        self.assertEqual(q["source"], "tq_http_snapshot")
        self.assertEqual(q["market"], "SH")
        self.assertTrue(q["available"])
        self.assertEqual(q["date"], TARGET)
        self.assertEqual(q["close"], 10.5)
        self.assertEqual(q["previous_close"], 10.0)
        self.assertEqual(q["change_pct"], 5.0)
        self.assertEqual(q["open"], 10.10)
        self.assertEqual(q["high"], 10.80)
        self.assertEqual(q["low"], 10.05)
        self.assertEqual(q["volume"], 123456.0)
        self.assertEqual(q["amount"], 1296000.0)
        self.assertTrue(q["time"])

    def test_code_suffix_conversion(self) -> None:
        value = {"Now": "5.0", "LastClose": "5.0"}
        cases = [("600000", 1, "600000.SH"), ("000001", 0, "000001.SZ"),
                 ("300750", 0, "300750.SZ"), ("920808", 2, "920808.BJ"),
                 ("830799", 2, "830799.BJ"), ("430047", 2, "430047.BJ")]
        for code, mkt, expect in cases:
            with self.subTest(code=code), \
                 mock.patch.object(chq.tq_http, "snapshot", return_value=_ok(value)) as m:
                chq._tq_snapshot_quote(code, "x", mkt, TARGET)
            m.assert_called_once_with(expect)

    def test_tq_error_returns_none(self) -> None:
        with mock.patch.object(chq.tq_http, "snapshot", return_value=_bad()):
            self.assertIsNone(chq._tq_snapshot_quote("600000", "x", 1, TARGET))

    def test_now_zero_or_missing_returns_none(self) -> None:
        for value in [{"Now": "0", "LastClose": "10"}, {"Now": "-1.5"},
                      {"LastClose": "10"}, {"Now": "-"}, "not-a-dict", None]:
            with self.subTest(value=value), \
                 mock.patch.object(chq.tq_http, "snapshot", return_value=_ok(value)):
                self.assertIsNone(chq._tq_snapshot_quote("600000", "x", 1, TARGET))

    def test_snapshot_raise_returns_none(self) -> None:
        with mock.patch.object(chq.tq_http, "snapshot", side_effect=RuntimeError("boom")):
            self.assertIsNone(chq._tq_snapshot_quote("600000", "x", 1, TARGET))


class FallbackOrderTest(unittest.TestCase):
    def test_intraday_tq_first_skips_others(self) -> None:
        with mock.patch.object(chq, "_tq_snapshot_quote", return_value=_quote("tq_http_snapshot")) as tq, \
             mock.patch.object(chq, "_online_bars_quote") as ob, \
             mock.patch.object(chq, "_reader_quote") as rd:
            q = chq._holding_quote("600000", "浦发银行", 1, "intraday", TARGET)
        self.assertEqual(q["source"], "tq_http_snapshot")
        tq.assert_called_once()
        ob.assert_not_called()
        rd.assert_not_called()

    def test_intraday_tq_fail_falls_to_online_bars(self) -> None:
        with mock.patch.object(chq, "_tq_snapshot_quote", return_value=None), \
             mock.patch.object(chq, "_online_bars_quote", return_value=_quote("mootdx_online_bars")) as ob, \
             mock.patch.object(chq, "_reader_quote") as rd:
            q = chq._holding_quote("600000", "浦发银行", 1, "intraday", TARGET)
        self.assertEqual(q["source"], "mootdx_online_bars")
        ob.assert_called_once()
        rd.assert_not_called()

    def test_intraday_all_online_fail_falls_to_reader(self) -> None:
        with mock.patch.object(chq, "_tq_snapshot_quote", return_value=None), \
             mock.patch.object(chq, "_online_bars_quote", side_effect=RuntimeError("conn")), \
             mock.patch.object(chq, "_online_daily_quote", return_value=None), \
             mock.patch.object(chq, "_reader_quote", return_value=_quote("mootdx_reader")):
            q = chq._holding_quote("600000", "浦发银行", 1, "intraday", TARGET)
        self.assertEqual(q["source"], "mootdx_reader")

    def test_intraday_online_bars_fail_falls_to_domain_b(self) -> None:
        with mock.patch.object(chq, "_tq_snapshot_quote", return_value=None), \
             mock.patch.object(chq, "_online_bars_quote", return_value=None), \
             mock.patch.object(chq, "_online_daily_quote", return_value=_quote("tencent_daily")) as ob, \
             mock.patch.object(chq, "_reader_quote") as rd:
            q = chq._holding_quote("600000", "浦发银行", 1, "intraday", TARGET)
        self.assertEqual(q["source"], "tencent_daily")
        ob.assert_called_once()
        rd.assert_not_called()

    def test_postclose_reader_stale_tq_fail_falls_to_domain_b(self) -> None:
        stale = _quote("mootdx_reader", d="2026-07-17")
        with mock.patch.object(chq, "_reader_quote", return_value=stale), \
             mock.patch.object(chq, "_tq_snapshot_quote", return_value=None), \
             mock.patch.object(chq, "_online_daily_quote", return_value=_quote("sina_daily")) as ob:
            q = chq._holding_quote("600000", "浦发银行", 1, "postclose", TARGET)
        self.assertEqual(q["source"], "sina_daily")
        ob.assert_called_once()

    def test_postclose_bj_skips_domain_b(self) -> None:
        with mock.patch.object(chq, "_reader_quote", return_value=None), \
             mock.patch.object(chq, "_tq_snapshot_quote", return_value=None), \
             mock.patch.object(chq, "_online_daily_quote") as ob, \
             mock.patch.object(chq, "_eastmoney_bj_quote", return_value=_quote("eastmoney_push2_bj")):
            q = chq._holding_quote("920808", "北证股", 2, "postclose", TARGET)
        self.assertEqual(q["source"], "eastmoney_push2_bj")
        ob.assert_not_called()

    def test_intraday_bj_skips_domain_b(self) -> None:
        with mock.patch.object(chq, "_tq_snapshot_quote", return_value=None), \
             mock.patch.object(chq, "_online_daily_quote") as ob, \
             mock.patch.object(chq, "_reader_quote", return_value=_quote("mootdx_reader")):
            q = chq._holding_quote("920808", "北证股", 2, "intraday", TARGET)
        self.assertEqual(q["source"], "mootdx_reader")
        ob.assert_not_called()

    def test_intraday_bj_order_tq_reader_eastmoney(self) -> None:
        # tq 失败 → reader（不走 online bars）
        with mock.patch.object(chq, "_tq_snapshot_quote", return_value=None), \
             mock.patch.object(chq, "_online_bars_quote") as ob, \
             mock.patch.object(chq, "_reader_quote", return_value=_quote("mootdx_reader")) as rd, \
             mock.patch.object(chq, "_eastmoney_bj_quote") as em:
            q = chq._holding_quote("920808", "北证股", 2, "intraday", TARGET)
        self.assertEqual(q["source"], "mootdx_reader")
        ob.assert_not_called()
        rd.assert_called_once()
        em.assert_not_called()

    def test_intraday_bj_all_local_fail_falls_to_eastmoney(self) -> None:
        with mock.patch.object(chq, "_tq_snapshot_quote", return_value=None), \
             mock.patch.object(chq, "_reader_quote", return_value=None), \
             mock.patch.object(chq, "_eastmoney_bj_quote", return_value=_quote("eastmoney_push2_bj")) as em:
            q = chq._holding_quote("920808", "北证股", 2, "intraday", TARGET)
        self.assertEqual(q["source"], "eastmoney_push2_bj")
        em.assert_called_once()

    def test_intraday_bj_tq_success_short_circuits(self) -> None:
        with mock.patch.object(chq, "_tq_snapshot_quote", return_value=_quote("tq_http_snapshot")), \
             mock.patch.object(chq, "_reader_quote") as rd, \
             mock.patch.object(chq, "_eastmoney_bj_quote") as em:
            q = chq._holding_quote("920808", "北证股", 2, "intraday", TARGET)
        self.assertEqual(q["source"], "tq_http_snapshot")
        rd.assert_not_called()
        em.assert_not_called()

    def test_postclose_non_bj_reader_first_unchanged(self) -> None:
        # reader 当日数据直接命中，不调 tq_http
        with mock.patch.object(chq, "_tq_snapshot_quote") as tq, \
             mock.patch.object(chq, "_reader_quote", return_value=_quote("mootdx_reader")) as rd, \
             mock.patch.object(chq, "_online_bars_quote") as ob:
            q = chq._holding_quote("600000", "浦发银行", 1, "postclose", TARGET)
        self.assertEqual(q["source"], "mootdx_reader")
        rd.assert_called_once()
        tq.assert_not_called()
        ob.assert_not_called()

    def test_postclose_bj_reader_stale_falls_to_tq(self) -> None:
        stale = _quote("mootdx_reader", d="2026-07-17")
        with mock.patch.object(chq, "_reader_quote", return_value=stale), \
             mock.patch.object(chq, "_tq_snapshot_quote", return_value=_quote("tq_http_snapshot")) as tq, \
             mock.patch.object(chq, "_eastmoney_bj_quote") as em:
            q = chq._holding_quote("920808", "北证股", 2, "postclose", TARGET)
        self.assertEqual(q["source"], "tq_http_snapshot")
        tq.assert_called_once()
        em.assert_not_called()

    def test_postclose_bj_reader_and_tq_fail_falls_to_eastmoney(self) -> None:
        with mock.patch.object(chq, "_reader_quote", return_value=None), \
             mock.patch.object(chq, "_tq_snapshot_quote", return_value=None), \
             mock.patch.object(chq, "_eastmoney_bj_quote", return_value=_quote("eastmoney_push2_bj")):
            q = chq._holding_quote("920808", "北证股", 2, "postclose", TARGET)
        self.assertEqual(q["source"], "eastmoney_push2_bj")


class TqSnapshotIndexQuoteTest(unittest.TestCase):
    def test_code_mapping_and_canonical_output(self) -> None:
        # 内部用正确 TDX 代码（000001→999999.SH），输出 code 保持 canonical
        value = {"Now": "3700.50", "LastClose": "3680.00", "Volume": "12345"}
        with mock.patch.object(chq.tq_http, "snapshot", return_value=_ok(value)) as m:
            q = chq._tq_snapshot_index_quote("000001", "上证指数")
        m.assert_called_once_with("999999.SH")
        self.assertEqual(q["code"], "000001")
        self.assertEqual(q["name"], "上证指数")
        self.assertEqual(q["source"], "tq_http_snapshot")
        self.assertEqual(q["close"], 3700.5)
        self.assertEqual(q["price"], 3700.5)
        self.assertEqual(q["previous_close"], 3680.0)
        self.assertAlmostEqual(q["change_pct"], round((3700.5 / 3680.0 - 1) * 100, 2))
        self.assertEqual(q["volume"], 12345.0)
        self.assertTrue(q["date"])
        self.assertTrue(q["time"])

    def test_sz_index_codes(self) -> None:
        value = {"Now": "13700.0", "LastClose": "13700.0"}
        for code, expect in [("399001", "399001.SZ"), ("399006", "399006.SZ")]:
            with self.subTest(code=code), \
                 mock.patch.object(chq.tq_http, "snapshot", return_value=_ok(value)) as m:
                q = chq._tq_snapshot_index_quote(code, "x")
            m.assert_called_once_with(expect)
            self.assertEqual(q["code"], code)

    def test_tq_failure_returns_none(self) -> None:
        with mock.patch.object(chq.tq_http, "snapshot", return_value=_bad()):
            self.assertIsNone(chq._tq_snapshot_index_quote("000001", "x"))
        with mock.patch.object(chq.tq_http, "snapshot", side_effect=RuntimeError("boom")):
            self.assertIsNone(chq._tq_snapshot_index_quote("000001", "x"))
        with mock.patch.object(chq.tq_http, "snapshot", return_value=_ok({"Now": "0"})):
            self.assertIsNone(chq._tq_snapshot_index_quote("000001", "x"))


class OnlineDailyQuoteTest(unittest.TestCase):
    BARS = [{"date": "2026-07-17", "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1, "volume": 100.0},
            {"date": "2026-07-20", "open": 10.2, "high": 10.5, "low": 10.1, "close": 10.4, "volume": 200.0}]

    def test_schema_and_change_pct(self) -> None:
        with mock.patch.object(chq.online_quotes, "fetch_online_daily",
                               return_value=(self.BARS, "tencent_daily")) as m:
            q = chq._online_daily_quote("600000", "浦发银行", 1, TARGET)
        m.assert_called_once_with("600000", count=3)
        self.assertEqual(q["source"], "tencent_daily")
        self.assertEqual(q["market"], "SH")
        self.assertTrue(q["available"])
        self.assertEqual(q["date"], "2026-07-20")
        self.assertEqual(q["close"], 10.4)
        self.assertEqual(q["previous_close"], 10.1)
        self.assertAlmostEqual(q["change_pct"], round((10.4 / 10.1 - 1) * 100, 2))
        self.assertEqual(q["open"], 10.2)
        self.assertEqual(q["high"], 10.5)
        self.assertEqual(q["low"], 10.1)
        self.assertEqual(q["volume"], 200.0)
        self.assertEqual(q["amount"], 0.0)

    def test_failure_returns_none(self) -> None:
        with mock.patch.object(chq.online_quotes, "fetch_online_daily", return_value=(None, None)):
            self.assertIsNone(chq._online_daily_quote("600000", "x", 1, TARGET))


class CollectIndicesFallbackTest(unittest.TestCase):
    def test_tq_success_short_circuits(self) -> None:
        snap = {"code": "000001", "name": "上证指数", "source": "tq_http_snapshot",
                "close": 3700.0, "price": 3700.0}
        with mock.patch.object(chq, "_tq_snapshot_index_quote", return_value=snap) as tq, \
             mock.patch.object(chq, "_get_client") as cli, \
             mock.patch.object(chq, "_get_reader") as rd:
            indices = chq._collect_indices("intraday")
        self.assertEqual(indices[0]["source"], "tq_http_snapshot")
        tq.assert_called()
        cli.assert_not_called()
        rd.assert_not_called()

    def test_tq_fail_falls_to_online_index(self) -> None:
        import pandas as pd
        df = pd.DataFrame(
            {"close": [3680.0, 3700.0], "volume": [1.0, 2.0],
             "datetime": ["2026-07-17 15:00:00", "2026-07-20 15:00:00"]})
        client = mock.Mock()
        client.index.return_value = df
        with mock.patch.object(chq, "_tq_snapshot_index_quote", return_value=None), \
             mock.patch.object(chq, "_get_client", return_value=client), \
             mock.patch.object(chq, "_get_reader") as rd:
            indices = chq._collect_indices("intraday")
        self.assertEqual(indices[0]["source"], "mootdx_online_index")
        self.assertEqual(indices[0]["close"], 3700.0)
        rd.assert_not_called()

    def test_reader_fallback_uses_correct_tdx_symbol(self) -> None:
        # 999999 才是上证指数日线；000001 在 reader 里是平安银行
        import pandas as pd
        df = pd.DataFrame(
            {"open": [1.0, 1.0], "high": [1.0, 1.0], "low": [1.0, 1.0],
             "close": [3680.0, 3700.0], "volume": [1.0, 2.0]},
            index=pd.to_datetime(["2026-07-17", "2026-07-20"]))
        reader = mock.Mock()
        reader.daily.return_value = df
        with mock.patch.object(chq, "_tq_snapshot_index_quote", return_value=None), \
             mock.patch.object(chq, "_get_client", side_effect=RuntimeError("conn")), \
             mock.patch.object(chq, "_get_reader", return_value=reader):
            indices = chq._collect_indices("postclose")
        called_symbols = [c.kwargs.get("symbol") for c in reader.daily.call_args_list]
        self.assertEqual(called_symbols, ["999999", "399001", "399006"])
        self.assertEqual(indices[0]["source"], "mootdx_reader")
        self.assertEqual(indices[0]["code"], "000001")
        self.assertEqual(indices[0]["close"], 3700.0)

    def test_all_tdx_fail_falls_to_domain_b(self) -> None:
        bars = [{"date": "2026-07-17", "open": 1.0, "high": 1.0, "low": 1.0,
                 "close": 3680.0, "volume": 1.0},
                {"date": "2026-07-20", "open": 1.0, "high": 1.0, "low": 1.0,
                 "close": 3700.0, "volume": 2.0}]
        with mock.patch.object(chq, "_tq_snapshot_index_quote", return_value=None), \
             mock.patch.object(chq, "_get_client", side_effect=RuntimeError("conn")), \
             mock.patch.object(chq, "_get_reader", side_effect=RuntimeError("no local")), \
             mock.patch.object(chq.online_quotes, "fetch_online_daily",
                               return_value=(bars, "tencent_daily")) as m:
            indices = chq._collect_indices("postclose")
        called = [c.args[0] for c in m.call_args_list]
        self.assertEqual(called, ["sh000001", "sz399001", "sz399006"])
        self.assertEqual(indices[0]["source"], "tencent_daily")
        self.assertEqual(indices[0]["code"], "000001")
        self.assertEqual(indices[0]["close"], 3700.0)
        self.assertEqual(indices[0]["price"], 3700.0)
        self.assertEqual(indices[0]["previous_close"], 3680.0)

    def test_domain_b_also_fail_marks_unavailable(self) -> None:
        with mock.patch.object(chq, "_tq_snapshot_index_quote", return_value=None), \
             mock.patch.object(chq, "_get_client", side_effect=RuntimeError("conn")), \
             mock.patch.object(chq, "_get_reader", side_effect=RuntimeError("no local")), \
             mock.patch.object(chq.online_quotes, "fetch_online_daily", return_value=(None, None)):
            indices = chq._collect_indices("postclose")
        self.assertFalse(indices[0]["available"])


if __name__ == "__main__":
    unittest.main()


class BreadthCollectionTests(unittest.TestCase):
    """880 系列市场宽度：**本地 Reader 优先、在线兜底**。

    ⚠️ 这五个代码的 `date` 会成为下游 `market_breadth.as_of` ——
    而它决定 `market_timing_scorer.is_stale` 是否按满分计入。
    `date` 落成空串时 merge 会标 `quality: raw_only`（v0.40 后算「不新鲜」）。
    """

    CODES = ("880001", "880005", "880006", "880390", "880863")

    def _frame(self, closes, last_name=None):
        import pandas as pd
        idx = (pd.date_range("2026-08-10", periods=len(closes), freq="B")
               if last_name is None else last_name)
        return pd.DataFrame({"close": closes}, index=idx)

    def test_local_reader_wins_and_no_online_call(self):
        """本地可用时**不得**发起在线调用（在线要建 TCP、选 bestip）。"""
        with mock.patch.object(chq, "_get_reader") as rd, \
             mock.patch.object(chq, "_client_call",
                               side_effect=AssertionError("本地成功时不该走在线")):
            rd.return_value.daily.return_value = self._frame([100.0, 102.0])
            out = chq._collect_breadth()
        self.assertEqual(set(out), set(self.CODES))
        self.assertEqual(out["880005"]["source"], "mootdx_reader")
        self.assertAlmostEqual(out["880005"]["change_pct"], 2.0)

    def test_online_fallback_when_local_raises(self):
        """本地 Reader 抛错 ⇒ 走在线兜底并**成功产出读数**。

        ⚠️ 在线分支用 `df.iloc[-1]` + `last["datetime"]`，桩必须带 datetime 列 ——
        不带的话在线分支 KeyError 落进错误记录路径，`out` 非空、调用次数也对，
        断言照样全过而「兜底成功」从未发生（2026-08-11 评审抓到的实际形态）。
        """
        import pandas as pd
        online = pd.DataFrame({"close": [100.0, 101.0],
                               "datetime": ["2026-08-10", "2026-08-11"]})
        with mock.patch.object(chq, "_get_reader") as rd, \
             mock.patch.object(chq, "_client_call") as cc:
            rd.return_value.daily.side_effect = RuntimeError("TdxW 没开")
            cc.return_value = online
            out = chq._collect_breadth()
        self.assertEqual(len(cc.call_args_list), len(self.CODES))
        for code in self.CODES:
            self.assertEqual(out[code]["source"], "mootdx_online",
                             f"{code} 应来自在线兜底：{out[code]}")
            self.assertAlmostEqual(out[code]["change_pct"], 1.0)
            self.assertEqual(out[code]["date"], "2026-08-11")
            self.assertNotIn("error", out[code], "兜底成功时不该是错误记录")

    def test_single_bar_local_falls_through_to_online(self):
        """⚠️ 只有一根 K 线算不出环比 ⇒ 继续走在线兜底，
        而不是写一个 `change_pct: None` 的半成品。

        （在线那支用 `df.iloc[-1]` + `last["datetime"]`，所以桩要带 datetime 列。）
        """
        import pandas as pd
        online = pd.DataFrame({"close": [100.0, 103.0],
                               "datetime": ["2026-08-10", "2026-08-11"]})
        with mock.patch.object(chq, "_get_reader") as rd, \
             mock.patch.object(chq, "_client_call") as cc:
            rd.return_value.daily.return_value = self._frame([100.0])
            cc.return_value = online
            out = chq._collect_breadth()
        self.assertTrue(cc.called, "只有一根 K 线时必须走在线")
        self.assertAlmostEqual(out["880001"]["change_pct"], 3.0)
        self.assertEqual(out["880001"]["source"], "mootdx_online")


    def test_both_sources_failing_records_the_error(self):
        """⚠️ 两路都失败时写 `{name, error}` **显式错误记录** —— 我原以为会缺席，
        实测是记录下来，而这更好：复盘时能看出「是采集失败」而不是「今天没这个指标」。

        ⚠️ 顺带核实过一个**看着像 bug 其实不可达**的点：
        `merge_incremental_market._usable()` 只看 `status` 字段，
        对 `{name, error}` 返回 True ⇒ 若这份 breadth 流进 merge 会被当可用。
        但它落在 **`{date}_holding_quotes.json`**（另一份产物），不经 merge；
        merge 读的是 `collect_incremental_market`，那边显式写 `status: unavailable`。
        ⇒ 两份产物用了**两套错误约定**，目前各自自洽；若哪天把本函数的输出接进 merge，
        必须先统一约定。
        """
        with mock.patch.object(chq, "_get_reader") as rd, \
             mock.patch.object(chq, "_client_call") as cc:
            rd.return_value.daily.side_effect = RuntimeError("local down")
            cc.side_effect = RuntimeError("online down")
            out = chq._collect_breadth()
        self.assertEqual(set(out), set(self.CODES))
        for code in self.CODES:
            self.assertIn("error", out[code], f"{code} 应记录失败原因")
            self.assertNotIn("close", out[code], "失败时不得给出 close，否则是编数据")


    def test_zero_prev_close_yields_none_not_div_by_zero(self):
        """前收为 0（指数停牌/数据异常）时 `change_pct` 是 None，不得崩。"""
        with mock.patch.object(chq, "_get_reader") as rd, \
             mock.patch.object(chq, "_client_call", side_effect=AssertionError("no")):
            rd.return_value.daily.return_value = self._frame([0.0, 100.0])
            out = chq._collect_breadth()
        self.assertIsNone(out["880001"]["change_pct"])

    def test_non_datetime_index_yields_empty_date_string(self):
        """⚠️ 索引不是日期时 `date` 落成**空串** —— 这正是
        「mootdx Reader 返回 DatetimeIndex 而非列」那个坑的下游表现。

        空串会让 `merge_incremental_market` 标 `quality: raw_only`
        （v0.40 后被 `is_stale` 认作不新鲜）⇒ 宽度按中性 7.5 计而非满分 ——
        **方向是安全的**，本条钉住这条链不要断。
        """
        with mock.patch.object(chq, "_get_reader") as rd, \
             mock.patch.object(chq, "_client_call", side_effect=AssertionError("no")):
            rd.return_value.daily.return_value = self._frame([100.0, 101.0],
                                                             last_name=[0, 1])
            out = chq._collect_breadth()
        self.assertEqual(out["880001"]["date"], "")
        import contracts as C
        self.assertIn("raw_only", C.SECTION_NOT_FRESH,
                      "空 date ⇒ raw_only ⇒ 必须被判不新鲜，这条链不能断")
