# -*- coding: utf-8 -*-
"""list_local_vipdoc_codes：本地 vipdoc 枚举 universe（A股个股保留，指数/ETF/债券剔除）。"""
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "07_tools" / "local_tdx"))

import local_tdx_data as ltd


def _make(tmp_path, market, fname):
    d = tmp_path / "vipdoc" / market / "lday"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{fname}.day").write_bytes(b"\x00" * 32)


def test_list_local_vipdoc_codes_filters_ashare(tmp_path):
    keep = [("sh", "sh600000"), ("sh", "sh601318"), ("sh", "sh603259"), ("sh", "sh605090"),
            ("sh", "sh688111"), ("sz", "sz000001"), ("sz", "sz002415"), ("sz", "sz300750"),
            ("sz", "sz301029"), ("bj", "bj920819"), ("bj", "bj830799"), ("bj", "bj871981")]
    drop = [("sh", "sh000001"), ("sh", "sh880001"), ("sh", "sh510300"),  # 指数/ETF
            ("sz", "sz159915"), ("sz", "sz399001"),                       # ETF/指数
            ("sh", "sh999999")]                                            # 上证指数
    for mkt, fn in keep + drop:
        _make(tmp_path, mkt, fn)
    codes = set(ltd.list_local_vipdoc_codes(tdx_root=tmp_path))
    assert codes == {"600000", "601318", "603259", "605090", "688111",
                     "000001", "002415", "300750", "301029",
                     "920819", "830799", "871981"}
    for x in ("510300", "880001", "159915", "399001", "999999"):
        assert x not in codes


def test_list_local_vipdoc_codes_empty_when_no_dir(tmp_path):
    assert ltd.list_local_vipdoc_codes(tdx_root=tmp_path) == []
