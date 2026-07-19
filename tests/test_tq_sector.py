# -*- coding: utf-8 -*-
"""Tests for 07_tools/local_tdx/tq_sector.py.

离线用例不依赖 TdxW；需要 TdxW 的用例用 skipUnless 守卫。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "07_tools" / "local_tdx"))

import tq_sector  # noqa: E402
from tq_sector import TQSectorSession, classify_sector, is_tdxw_running, load_sector_names  # noqa: E402

TDXW_UP = is_tdxw_running()

# tdxzs.cfg 样本（GBK 编码、管道分隔）
FIXTURE_TEXT = (
    "轮动趋势|880081|5|2|0|轮动趋势\r\n"
    "黑龙江|880201|3|1|0|1\r\n"
    "煤炭|880301|2|1|1|T0101\r\n"
    "机器人概念|880904|4|2|0|智能机器\r\n"
    "煤炭开采|881002|12|1|0|X1001\r\n"
)


class TestLoadSectorNames(unittest.TestCase):
    def _write_fixture(self, tmp: Path) -> Path:
        cfg = tmp / "tdxzs.cfg"
        cfg.write_bytes(FIXTURE_TEXT.encode("gbk"))
        return cfg

    def test_parse_fixture(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            cfg = self._write_fixture(Path(d))
            names = load_sector_names(cfg)
        self.assertEqual(names["880201"], {"name": "黑龙江", "tdx_type": "3"})
        self.assertEqual(names["880301"]["tdx_type"], "2")
        self.assertEqual(names["880904"]["name"], "机器人概念")
        self.assertEqual(names["881002"], {"name": "煤炭开采", "tdx_type": "12"})
        self.assertEqual(len(names), 5)

    def test_missing_file_returns_empty(self):
        names = load_sector_names(Path("nonexistent_tdxzs.cfg"))
        self.assertEqual(names, {})


class TestClassifySector(unittest.TestCase):
    def test_official_type_takes_priority(self):
        r = classify_sector("880904", "4")
        self.assertEqual(r["category"], "concept")
        self.assertFalse(r["heuristic"])

    def test_stat_index_range_with_type5(self):
        r = classify_sector("880081", "5")
        self.assertEqual(r["category"], "stat_index")
        self.assertFalse(r["heuristic"])

    def test_style_type5_outside_index_range(self):
        r = classify_sector("880801", "5")
        self.assertEqual(r["category"], "style")

    def test_sub_industry_type12(self):
        r = classify_sector("881002", "12")
        self.assertEqual(r["category"], "sub_industry")
        self.assertFalse(r["heuristic"])

    def test_heuristic_fallback_without_type(self):
        self.assertEqual(classify_sector("880201")["category"], "region")
        self.assertEqual(classify_sector("880301")["category"], "industry")
        self.assertEqual(classify_sector("880660")["category"], "concept")
        self.assertEqual(classify_sector("880904")["category"], "concept_or_style")
        self.assertEqual(classify_sector("880001")["category"], "stat_index")
        self.assertEqual(classify_sector("881002")["category"], "sub_industry")
        self.assertTrue(classify_sector("880904")["heuristic"])

    def test_suffix_and_case_insensitive(self):
        self.assertEqual(classify_sector("880201.sh", "3")["category"], "region")


class TestStructuredErrors(unittest.TestCase):
    def test_tdxw_not_running(self):
        with mock.patch.object(tq_sector, "is_tdxw_running", return_value=False):
            session = TQSectorSession(name_map={})
            result = session.build_sector_map()
        self.assertEqual(result["error"], "tdxw_not_running")
        self.assertEqual(result["sector_count"], 0)
        self.assertEqual(result["sectors"], [])

    def test_initialize_failed(self):
        with (
            mock.patch.object(tq_sector, "is_tdxw_running", return_value=True),
            mock.patch.object(tq_sector, "_import_tq", side_effect=RuntimeError("boom")),
        ):
            session = TQSectorSession(name_map={})
            result = session.build_sector_map()
        self.assertEqual(result["error"], "initialize_failed")
        self.assertIn("boom", result["detail"])

    def test_single_sector_failure_isolated(self):
        fake_tq = mock.Mock()
        fake_tq.get_sector_list.return_value = ["880201.SH", "880904.SH"]

        def stocks(code):
            if code == "880904.SH":
                raise RuntimeError("rpc timeout")
            return ["600000.SH"]

        fake_tq.get_stock_list_in_sector.side_effect = stocks
        with (
            mock.patch.object(tq_sector, "is_tdxw_running", return_value=True),
            mock.patch.object(tq_sector, "_import_tq", return_value=fake_tq),
        ):
            session = TQSectorSession(name_map={"880201": {"name": "黑龙江", "tdx_type": "3"}})
            result = session.build_sector_map()
        self.assertNotIn("error", result)
        self.assertEqual(result["sector_count"], 2)
        self.assertEqual(result["sectors"][0]["stock_count"], 1)
        self.assertEqual(result["sectors"][1]["stock_count"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["error"], "sector_stocks_failed")
        self.assertEqual(result["errors"][0]["sector"], "880904.SH")
        self.assertEqual(result["quality"]["named_sectors"], 1)
        self.assertFalse(result["quality"]["names_unavailable"])

    def test_names_unavailable_quality_flag(self):
        fake_tq = mock.Mock()
        fake_tq.get_sector_list.return_value = ["880201.SH"]
        fake_tq.get_stock_list_in_sector.return_value = []
        with (
            mock.patch.object(tq_sector, "is_tdxw_running", return_value=True),
            mock.patch.object(tq_sector, "_import_tq", return_value=fake_tq),
        ):
            session = TQSectorSession(name_map={})
            result = session.build_sector_map()
        self.assertTrue(result["quality"]["names_unavailable"])
        self.assertEqual(result["quality"]["name_coverage"], 0.0)


@unittest.skipUnless(TDXW_UP, "TdxW.exe 未运行，跳过 TQ 集成测试")
class TestTdxwIntegration(unittest.TestCase):
    def test_sector_list_live(self):
        with TQSectorSession() as session:
            codes = session.get_sector_list()
        self.assertIsInstance(codes, list)
        self.assertGreater(len(codes), 500)

    def test_known_sector_stocks_live(self):
        with TQSectorSession() as session:
            stocks = session.get_sector_stocks("880201.SH")
        self.assertIsInstance(stocks, list)
        self.assertEqual(len(stocks), 38)


if __name__ == "__main__":
    unittest.main()
