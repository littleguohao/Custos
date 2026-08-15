# -*- coding: utf-8 -*-
"""v0.59 公司地位证据（东财 F10 简介关键词）的测试。

口径钉：证据层（不进技术分/分层/gate）；非 PIT——只可用于 live/近端。
"""

import json

from custos.datasource.local_tdx import fetch_company_profile as fcp
from custos.pipeline.screening import enrich_candidates as ec
from custos.pipeline.screening import score_candidates as sc


def test_scan_keywords_hit_longest_snippet():
    text = "公司是国内领先的锂电池正极材料生产商，产能全球第一。近年来业绩增长。"
    r = fcp.scan_position_keywords(text)
    assert "国内领先" in r["keywords"] and "全球第一" in r["keywords"]
    assert "领先" in r["keywords"] and "第一" in r["keywords"]
    # snippet 取最长命中词所在句（「全球第一」比「第一」长）
    assert "全球第一" in r["snippet"]


def test_scan_keywords_no_hit():
    r = fcp.scan_position_keywords("公司主要从事纺织品加工与销售。")
    assert r["keywords"] == [] and r["snippet"] == ""


def test_fetch_one_parses_jbzl(monkeypatch):
    class _Resp:
        encoding = "utf-8"

        def json(self):
            return {
                "jbzl": [
                    {
                        "SECURITY_NAME_ABBR": "示例股份",
                        "EM2016": "制造-示例",
                        "ORG_PROFILE": "公司是行业龙头企业。",
                        "BUSINESS_SCOPE": "一般项目：示例。",
                    }
                ]
            }

    class _Sess:
        def get(self, *a, **kw):
            return _Resp()

    r = fcp.fetch_one("600000", session=_Sess())
    assert r["available"] and r["name"] == "示例股份"
    assert "龙头" in r["keywords"]


def test_fetch_one_empty_jbzl_unavailable():
    class _Resp:
        encoding = "utf-8"

        def json(self):
            return {"jbzl": []}

    class _Sess:
        def get(self, *a, **kw):
            return _Resp()

    r = fcp.fetch_one("600000", session=_Sess())
    assert r["available"] is False


def test_ledger_roundtrip_and_bad_line_skipped(tmp_path):
    p = tmp_path / "company_profile.jsonl"
    recs = {
        "600000": {"code": "600000", "available": True, "keywords": ["龙头"]},
        "000001": {"code": "000001", "available": False, "error": "jbzl 为空"},
    }
    fcp._write_ledger(recs, path=p)
    with p.open("a", encoding="utf-8") as f:
        f.write("这不是JSON\n")  # 坏行不得炸全量
    got = fcp.load_ledger(path=p)
    assert set(got) == {"600000", "000001"}
    assert got["600000"]["keywords"] == ["龙头"]


def test_rescan_rewrites_keywords(tmp_path):
    p = tmp_path / "company_profile.jsonl"
    fcp._write_ledger(
        {
            "600000": {
                "code": "600000",
                "available": True,
                "profile": "全球领先的制造商。",
                "business_scope": "",
                "keywords": ["过期词"],
            }
        },
        path=p,
    )
    monkeypatch_ledger = p
    orig = fcp.LEDGER
    fcp.LEDGER = monkeypatch_ledger
    try:
        assert fcp.main(["--rescan"]) == 0
    finally:
        fcp.LEDGER = orig
    rec = fcp.load_ledger(path=p)["600000"]
    assert "全球领先" in rec["keywords"] and "过期词" not in rec["keywords"]


def test_company_position_of_reads_cache():
    ec._COMPANY_POSITION_CACHE = {
        "600519": {
            "available": True,
            "keywords": ["龙头", "第一"],
            "snippet": "……全球第一……",
            "industry_em": "食品饮料",
        }
    }
    try:
        r = ec.company_position_of("600519")
        assert r["available"] and "龙头" in r["keywords"]
        assert ec.company_position_of("000001")["available"] is False
    finally:
        ec._COMPANY_POSITION_CACHE = None


def test_score_candidate_passthrough_evidence_only():
    """company_position 必须透传到落盘候选，且不进技术分（证据层）。"""
    cand = {
        "code": "600000",
        "name": "示例",
        "patterns": {"j_low": True},
        "daily_j": 10.0,
        "stop_loss_ref": {"price": 10.0, "basis": "x"},
        "company_position": {"available": True, "keywords": ["唯一"], "snippet": "s"},
    }
    scored = sc.score_candidate(cand, None, "做多")
    assert scored["company_position"]["keywords"] == ["唯一"]
    # 证据层：技术分只含 j_low 的 20 分，company_position 不加分
    assert scored["score_detail"]["technical_score"] == 20
