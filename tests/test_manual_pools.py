# -*- coding: utf-8 -*-
"""manual_pools 自选池通道测试：cfg 名称解析、blk 解析、screen_formulas 集成。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "07_tools" / "screening"))

import manual_pools
import formula_screen as fs


def _make_block_dir(tmp_path):
    d = tmp_path / "blocknew"
    d.mkdir()
    cfg = "持仓".encode("gbk") + b"\x00" * 88 + b"CC" + b"\x00" * 58 \
        + "震荡".encode("gbk") + b"\x00" * 88 + b"ZD" + b"\x00" * 58
    (d / "blocknew.cfg").write_bytes(cfg)
    (d / "ZD.blk").write_bytes(
        b"1600150\r\n0000977\r\n2920808\r\n\r\nX600150\r\n160015\r\n")
    return d


def test_resolve_block_file_by_name(tmp_path):
    d = _make_block_dir(tmp_path)
    assert manual_pools.resolve_block_file("震荡", d) == d / "ZD.blk"
    assert manual_pools.resolve_block_file("不存在", d) is None


def test_read_blk_parses_market_prefix_and_skips_dirty(tmp_path):
    d = _make_block_dir(tmp_path)
    rows = manual_pools.read_blk(d / "ZD.blk")
    assert rows == [
        {"code": "600150", "market": "SH"},
        {"code": "000977", "market": "SZ"},
        {"code": "920808", "market": "BJ"},
    ]  # 空行、非法前缀、短行均被跳过


def test_load_pool_hits_and_missing(tmp_path):
    d = _make_block_dir(tmp_path)
    pool = manual_pools.load_pool("震荡", "2026-07-22", block_dir=d,
                                  name_map={"600150": "中国船舶"})
    assert pool["error"] is None
    assert pool["hits"][0] == {"code": "600150", "name": "中国船舶",
                               "signal_date": "2026-07-22", "market": "SH"}
    missing = manual_pools.load_pool("幽灵池", "2026-07-22", block_dir=d)
    assert missing["hits"] == [] and "block_not_found" in missing["error"]


def _registry_with_pool():
    return {
        "universe": {},
        "manual_pools": [{"id": "POOL_ZHENDANG", "block_name": "震荡", "enabled": True}],
        "formulas": [{"id": "UPN_3", "tq_name": "UPN", "enabled": True}],
    }


def test_screen_includes_pool_when_tq_down(tmp_path, monkeypatch):
    d = _make_block_dir(tmp_path)
    monkeypatch.setattr(manual_pools, "TDX_BLOCK_DIR", d)
    r = fs.screen_formulas("2026-07-22", registry=_registry_with_pool(),
                           stock_list=["600150"], name_map={"600150": "中国船舶"},
                           running_check=lambda: False)
    assert r["status"] == "partial"  # 池命中让 TQ 离线降级为 partial 而非 unavailable
    pool_entry = r["formulas"][0]
    assert pool_entry["category"] == "manual_pool"
    # BJ 代码也原样透出（exclude_bj 在 enrich 段统一过滤）
    assert {h["code"] for h in pool_entry["hits"]} == {"600150", "000977", "920808"}
    assert pool_entry["hits"][0]["name"] == "中国船舶"  # 名称来自 universe 名称表
    assert r["formulas"][1]["error"] == "tdxw_not_running"


def test_screen_pool_plus_formula_merge(tmp_path, monkeypatch):
    d = _make_block_dir(tmp_path)
    monkeypatch.setattr(manual_pools, "TDX_BLOCK_DIR", d)
    r = fs.screen_formulas(
        "2026-07-22", registry=_registry_with_pool(),
        stock_list=["600150"], name_map={"600150": "中国船舶"},
        call=lambda m, p, timeout: {"ok": True, "value": {"600150.SH": {"s": ["0", "1"]}}, "error": None},
        running_check=lambda: True)
    assert r["status"] == "ok"
    assert r["formulas"][0]["category"] == "manual_pool"
    assert r["formulas"][1]["hits"] == [{"code": "600150", "name": "中国船舶",
                                         "signal_date": "2026-07-22"}]
