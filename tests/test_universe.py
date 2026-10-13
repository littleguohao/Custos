# -*- coding: utf-8 -*-
"""研究宇宙单源钉测（TODO #89，v0.356）：is_ashare_stock 口径、resolve 优先级
与缺省规则构造、universe_meta/sha、bt._resolve_universe 委托。"""

from __future__ import annotations

import argparse

import pytest

from custos.core.code_utils import is_ashare_stock
from custos.datasource.local_tdx import local_tdx_data
from custos.research import backtest_factors as bt
from custos.research import universe as uv


class TestIsAshareStock:
    @pytest.mark.parametrize(
        "code",
        [
            "600000",
            "601318",
            "603259",
            "605499",
            "688981",  # SH 个股
            "000001",
            "001979",
            "002594",
            "003816",
            "300750",
            "301236",  # SZ 个股
            "430047",
            "830799",
            "870199",
            "889999",
            "920001",  # BJ 个股
            "600000.SH",
            "000001.SZ",
            "830799.BJ",
            "880123.BJ",  # 显式后缀
            # 前缀规则与 live 同源判过（_is_ashare_stock_file("sz",·) 同判 True）：
            # 指数归属是 market_of/SH_INDEX_BARE 的路由口径、不是个股资格口径；
            # vipdoc 无 sz000300.day 指数文件，实枚举永不进入宇宙。
            "000300",
        ],
    )
    def test_stocks_accepted(self, code):
        assert is_ashare_stock(code) is True

    @pytest.mark.parametrize(
        "code",
        [
            "399001",
            "399006",  # 深指
            "880001",
            "881156",  # 通达信板块指数（裸码按 market_of 同源=指数）
            "510300",
            "159915",  # ETF
            "900901",
            "200002",  # B 股
            "113050",
            "123456",  # 转债
            "880123",  # 裸码歧义 → 按指数（BJ 个股须带 .BJ）
            "880123.SH",  # 沪目录下 88 段非个股前缀
            "600000.SZ",  # 后缀与前缀矛盾（后缀口径下不匹配）
            "ABC",
            "",
            "60000",
            "6000000",  # 非 6 位数字
        ],
    )
    def test_non_stocks_rejected(self, code):
        assert is_ashare_stock(code) is False

    def test_delegation_equivalence_vipdoc(self):
        """local_tdx_data._is_ashare_stock_file = 前缀集单源委托（目录语境逐位等价）。"""
        cases = [
            ("sh", "600000", True),
            ("sh", "688981", True),
            ("sh", "000300", False),
            ("sh", "510300", False),
            ("sz", "000001", True),
            ("sz", "300750", True),
            ("sz", "159915", False),
            ("sz", "399001", False),
            ("bj", "830799", True),
            ("bj", "920001", True),
            ("bj", "880123", True),
            ("bj", "600000", False),
            ("xx", "600000", False),
        ]
        for mkt, code6, want in cases:
            assert local_tdx_data._is_ashare_stock_file(mkt, code6) is want


def _ns(**kw):
    base = dict(
        codes_file="", universe_local=False, universe_sample=0, seed=0, codes=""
    )
    base.update(kw)
    return argparse.Namespace(**base)


class TestResolve:
    def test_codes_file_highest_priority(self, tmp_path, monkeypatch):
        f = tmp_path / "c.txt"
        f.write_text("# 注释\n600000\n\n300750\n", encoding="utf-8")
        monkeypatch.setattr(uv, "default_codes", lambda: ["999999"])
        ap = argparse.ArgumentParser()
        codes = uv.resolve(_ns(codes_file=str(f)), ap)
        assert codes == ["600000", "300750"]  # 注释/空行剔除

    def test_default_rule_construction(self, monkeypatch):
        """缺省（什么都不传）= 规则构造全量名单（v0.356 起不再 fail-closed）。"""
        monkeypatch.setattr(uv, "default_codes", lambda: ["600000", "000001"])
        ap = argparse.ArgumentParser()
        codes = uv.resolve(_ns(), ap)
        assert codes == ["600000", "000001"]

    def test_explicit_codes_fallback(self, monkeypatch):
        monkeypatch.setattr(uv, "default_codes", lambda: ["999999"])
        codes = uv.resolve(_ns(codes="600000, 300750"), argparse.ArgumentParser())
        assert codes == ["600000", "300750"]

    def test_empty_universe_fail_closed(self, monkeypatch):
        monkeypatch.setattr(uv, "default_codes", lambda: [])
        with pytest.raises(SystemExit):
            uv.resolve(_ns(), argparse.ArgumentParser())

    def test_universe_meta_side_effect(self, monkeypatch):
        monkeypatch.setattr(uv, "default_codes", lambda: ["600000", "000001"])
        ns = _ns()
        uv.resolve(ns, argparse.ArgumentParser())
        meta = ns.universe_meta
        assert meta["universe_rule"] == uv.UNIVERSE_RULE
        assert meta["n_codes"] == 2
        assert meta["universe_sha256"] == uv.universe_sha256(["600000", "000001"])

    def test_bt_delegates(self, monkeypatch):
        """bt._resolve_universe 已下沉委托（缺省=规则构造）。"""
        monkeypatch.setattr(uv, "default_codes", lambda: ["600000"])
        codes = bt._resolve_universe(_ns(), argparse.ArgumentParser())
        assert codes == ["600000"]


class TestUniverseSha:
    def test_deterministic_and_order_insensitive(self):
        a = uv.universe_sha256(["600000", "000001", "300750"])
        b = uv.universe_sha256(["300750", "600000", "000001"])
        assert a == b and len(a) == 16

    def test_content_sensitive(self):
        a = uv.universe_sha256(["600000", "000001"])
        b = uv.universe_sha256(["600000", "000002"])
        c = uv.universe_sha256(["600000", "000001", "000002"])
        assert a != b and a != c and b != c

    def test_min_bars_pin(self):
        """N=60（owner 2026-10-13 拍板）= 引擎扫描起点与 live min_list_days 同源。"""
        assert uv.UNIVERSE_MIN_BARS == 60
