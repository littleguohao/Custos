# -*- coding: utf-8 -*-
"""Parametrized boundary tests for code_utils.market_of and its consumers.

All exchange-classification logic converged on code_utils.market_of; these
tests lock the SH/SZ/BJ/880-index boundaries and the cross-module agreement
of norm_code / suffix / local_tdx_data / collect_holding_quotes.
"""
from __future__ import annotations

import pytest

import code_utils
from code_utils import market_of
from local_tdx.local_tdx_data import _is_bj_code, normalize_code
from collect_holding_quotes import get_market


@pytest.mark.parametrize("code,expected", [
    # SH stocks / funds / indices
    ("600519", "SH"), ("688981", "SH"), ("510300", "SH"), ("900901", "SH"),
    ("999999", "SH"),
    # 880 系列：通达信沪市统计指数，不是北交所股票
    ("880005", "SH"), ("880001", "SH"),
    # SZ stocks / indices
    ("000001", "SZ"), ("300750", "SZ"), ("399006", "SZ"), ("200002", "SZ"),
    ("159915", "SZ"),
    # BJ stocks
    ("920808", "BJ"), ("830799", "BJ"), ("430047", "BJ"), ("889999", "BJ"),
    # 显式后缀优先，压过前缀启发式
    ("880005.SH", "SH"), ("880005.BJ", "BJ"), ("920808.SH", "SH"),
    ("600519.sz", "SZ"),
    # 短码 zfill(6) 后再判定
    ("9208", "SZ"),
    # 未知/非数字
    ("700000", ""), ("ABC", ""), ("", ""),
])
def test_market_of(code, expected):
    assert market_of(code) == expected


@pytest.mark.parametrize("code,expected", [
    ("600519", "600519.SH"), ("000001", "000001.SZ"), ("920808", "920808.BJ"),
    ("880005", "880005.SH"),  # 收敛前 norm_code 误判为 .BJ
    ("700000", "700000"),     # 未知前缀原样返回
])
def test_norm_code_consistent_with_market_of(code, expected):
    assert code_utils.norm_code(code) == expected
    assert normalize_code(code) == expected  # local_tdx 委托同一实现


@pytest.mark.parametrize("code,is_bj", [
    ("920808", True), ("830799", True), ("430047", True),
    ("880005", False),        # 收敛前 _is_bj_code 对无后缀 880 误判 True
    ("880005.SH", False), ("600519", False), ("300750", False),
])
def test_is_bj_code_consistent(code, is_bj):
    assert _is_bj_code(code) is is_bj
    assert (market_of(code) == "BJ") is is_bj


@pytest.mark.parametrize("code,mkt", [
    ("920808", 2), ("830799", 2), ("430047", 2),
    ("600519", 1), ("880005", 1),  # 收敛前 get_market 把 880 落到默认 SZ
    ("000001", 0), ("300750", 0),
])
def test_get_market_consistent(code, mkt):
    assert get_market(code) == mkt
