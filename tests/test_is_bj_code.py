# -*- coding: utf-8 -*-
"""Tests for _is_bj_code semantics in local_tdx_data."""
from local_tdx.local_tdx_data import _is_bj_code


class TestIsBjCodeExplicitSuffix:
    def test_bj_suffix_true(self):
        assert _is_bj_code("920808.BJ") is True
        assert _is_bj_code("830799.BJ") is True
        assert _is_bj_code("430047.BJ") is True

    def test_sh_suffix_false(self):
        # 880 系列是沪市统计指数, 显式 .SH 不得误判为北交所
        assert _is_bj_code("880005.SH") is False
        assert _is_bj_code("880001.SH") is False
        assert _is_bj_code("600000.SH") is False

    def test_sz_suffix_false(self):
        assert _is_bj_code("399006.SZ") is False
        assert _is_bj_code("880005.SZ") is False

    def test_suffix_case_insensitive(self):
        assert _is_bj_code("920808.bj") is True
        assert _is_bj_code("880005.sh") is False

    def test_whitespace_tolerated(self):
        assert _is_bj_code(" 880005.SH ") is False
        assert _is_bj_code(" 920808.BJ ") is True


class TestIsBjCodeHeuristic:
    def test_bj_prefixes_without_suffix(self):
        assert _is_bj_code("920808") is True
        assert _is_bj_code("830799") is True
        assert _is_bj_code("430047") is True

    def test_non_bj_without_suffix(self):
        assert _is_bj_code("600000") is False
        assert _is_bj_code("000001") is False
        assert _is_bj_code("300750") is False

    def test_short_code_zfilled(self):
        # zfill(6) 后 "9208" -> "009208" 非 BJ
        assert _is_bj_code("9208") is False
