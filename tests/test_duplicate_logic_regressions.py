# -*- coding: utf-8 -*-
"""重复实现收敛回归（owner 2026-11-28）：行为修复与零变化门面统一钉测。"""

from __future__ import annotations

import json

import pytest

from custos.core.code_utils import fnum, is_ashare_stock
from custos.core.factors import capital_intent as ci
from custos.core.pipeline_kit import resolve_numeric_weights
from custos.datasource import eastmoney_pagination as ep
from custos.datasource import refresh_market_indices as rmi
from custos.datasource.local_tdx import fetch_market_cap as mc
from custos.datasource.local_tdx import fetch_pit_financials as fp
from custos.datasource.local_tdx import tq_sector
from custos.pipeline.market_timing import market_timing_collector as mtc
from custos.pipeline.market_timing import theme_tracker_report as ttr
from custos.pipeline.screening import enrich_candidates as ec
from custos.pipeline.screening import formula_screen as fs
from custos.pipeline.screening import manual_pools as mp
from custos.pipeline.screening import score_candidates as sc


def test_formula_universe_uses_single_stock_classification(monkeypatch):
    """880/881 裸码是指数；BJ88 必须带市场语境，689 CDR 保持原排除口径。"""
    monkeypatch.setattr(
        fs.local_tdx_data,
        "list_local_vipdoc_codes",
        lambda ashare_only=True: [
            "880863",
            "881105.SH",
            "920123",
            "430047.BJ",
            "880123.BJ",
            "689009",
            "600000",
        ],
    )
    monkeypatch.setattr(fs, "_load_name_map", lambda diag=None: {})
    codes, _ = fs.build_universe({"exclude_bj": False})
    assert codes == ["920123", "430047", "880123", "600000"]
    assert fs._is_bj("880863") is False
    assert fs._is_bj("880863.SH") is False
    assert fs._is_bj("920123") is True


def test_manual_pool_separates_indices_cdr_and_bj88(tmp_path):
    (tmp_path / "blocknew.cfg").write_bytes(
        "测试".encode("gbk") + b"\x00" + b"T",
    )
    (tmp_path / "T.blk").write_bytes(
        b"1880863\r\n1600000\r\n1689009\r\n2880123\r\n",
    )
    pool = mp.load_pool("测试", "2026-11-28", block_dir=tmp_path)
    assert [row["code"] for row in pool["hits"]] == ["600000", "880123"]
    assert pool["hits"][1]["market"] == "BJ"
    assert {row["code"] for row in pool["excluded"]} == {"880863", "689009"}
    assert all(row["reason"] == "not_a_share" for row in pool["excluded"])
    assert is_ashare_stock("689009") is False


def test_enrich_precheck_uses_same_index_boundary():
    def reason(code, market=""):
        return ec._precheck_exclusion(
            code, "测试", {"exclude_bj": False}, False, False, set(), market
        )

    assert reason("880863") == "not_a_share"
    assert reason("881105") == "not_a_share"
    assert reason("880123", "BJ") is None
    assert reason("689009") == "not_a_share"
    assert reason("920808") is None
    assert reason("600000") is None

    merged = ec._merge_hits(
        {
            "formulas": [
                {
                    "id": "pool",
                    "hits": [
                        {"code": "880123", "name": "BJ票", "market": "BJ"},
                        {"code": "880863.SH", "name": "板块指数"},
                    ],
                }
            ]
        }
    )
    assert merged["880123"]["market"] == "BJ"
    assert merged["880863"]["market"] == "SH"

    formula_hits = fs._extract_hits(
        {
            "880123.BJ": {"S": ["0", "1"]},
            "600000.SH": {"S": ["0", "1"]},
        },
        "2026-11-28",
        {},
    )
    by_code = {row["code"]: row for row in formula_hits}
    assert by_code["880123"]["market"] == "BJ"
    assert "market" not in by_code["600000"]


@pytest.mark.parametrize("parser", [rmi.to_float, mtc.to_float, fnum])
@pytest.mark.parametrize(
    "raw", [float("nan"), float("inf"), float("-inf"), "inf", "-inf"]
)
def test_shared_float_parsers_reject_all_non_finite(parser, raw):
    assert parser(raw) is None


@pytest.mark.parametrize("parser", [rmi.to_float, mtc.to_float, fnum])
def test_shared_float_parsers_preserve_zero_and_missing(parser):
    assert parser(0) == 0.0
    assert parser("0.0") == 0.0
    assert parser(None) is None
    assert parser("--") is None


def test_latest_sector_map_single_reader_preserves_error_modes(tmp_path, monkeypatch):
    old = tmp_path / "2026-11-27_tq_sector_map.json"
    new = tmp_path / "2026-11-28_tq_sector_map.json"
    old.write_text(json.dumps({"sectors": [{"name": "旧"}]}), encoding="utf-8")
    new.write_text(json.dumps({"sectors": [{"name": "AI"}]}), encoding="utf-8")
    assert tq_sector.latest_tq_sector_map(tmp_path)["sectors"][0]["name"] == "AI"

    monkeypatch.setattr(ec, "SECTORS_DIR", tmp_path)
    monkeypatch.setattr(ttr, "SECTOR_DIR", tmp_path)
    assert ec.latest_tq_sector_map()["sectors"][0]["name"] == "AI"
    assert ttr.latest_tq_sector_map()["sectors"][0]["name"] == "AI"

    new.write_text("{broken", encoding="utf-8")
    assert ec.latest_tq_sector_map() == {}
    with pytest.raises(ValueError):
        ttr.latest_tq_sector_map()


def test_numeric_weight_resolver_keeps_defaults_and_independent_warn_sets(capsys):
    warned = set()
    defaults = {"a": 1, "b": 2.5}
    got = resolve_numeric_weights(defaults, {"a": "bad", "b": 4, "unknown": 99}, warned)
    assert got == {"a": 1, "b": 4}
    resolve_numeric_weights(defaults, {"a": "bad"}, warned)
    assert capsys.readouterr().err.count("[WARN]") == 1

    sc._WARNED_BAD_WEIGHT_KEYS.clear()
    ci._WARNED_BAD_WEIGHT_KEYS.clear()
    sc.resolve_tech_weights({"j_low": "bad"})
    ci.resolve_capital_weights({"j_low": "bad"})  # 未知键不占资金意图告警集合
    assert "j_low" in sc._WARNED_BAD_WEIGHT_KEYS
    assert "j_low" not in ci._WARNED_BAD_WEIGHT_KEYS


def test_eastmoney_pagination_protocol_and_exceptions_are_shared():
    assert mc.FetchIncomplete is fp.FetchIncomplete is ep.FetchIncomplete
    assert mc._as_int is fp._as_int is ep.as_int
    assert mc._is_empty_ok is fp._is_empty_ok is ep.is_empty_ok
    assert mc._brief is fp._brief is ep.brief
    assert mc._as_int("inf") is None
    assert mc._page_done("2026-11-28", 2, 2, None, [{}]) is True
    assert fp._page_done(None, 1, 1, [{}], "2026-11-28") is True
    with pytest.raises(ep.FetchIncomplete, match="未给 pages/count"):
        mc._page_done("2026-11-28", 1, None, None, [])
    with pytest.raises(ep.FetchIncomplete, match="未给 pages/count"):
        fp._page_done(None, None, 1, [], "2026-11-28")
