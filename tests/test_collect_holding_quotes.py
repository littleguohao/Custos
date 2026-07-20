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
             mock.patch.object(chq, "_reader_quote", return_value=_quote("mootdx_reader")):
            q = chq._holding_quote("600000", "浦发银行", 1, "intraday", TARGET)
        self.assertEqual(q["source"], "mootdx_reader")

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


if __name__ == "__main__":
    unittest.main()
