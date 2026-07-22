# -*- coding: utf-8 -*-
"""concept_tags 解析与 build_stock_theme_map 映射优先级的测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "07_tools" / "local_tdx"))
sys.path.insert(0, str(TESTS_DIR.parent / "07_tools" / "screening"))

import concept_tags
import enrich_candidates as ec


@pytest.fixture()
def miscinfo_file(tmp_path):
    data = [
        {"sc": "0", "code": "", "xq": "概念和主题", "id": "10001"},  # 表头行
        {"sc": "0", "code": "002439", "xq": "网络安全,信创,军工信息化", "id": "10001"},
        {"sc": "0", "code": "603986", "xq": "存储芯片,半导体,MCU芯片", "id": "10001"},
        {"sc": "0", "code": "600150", "xq": "船舶制造,军工,央企改革", "id": "10001"},
        {"sc": "0", "code": "600150", "xq": "非概念类条目", "id": "10004"},  # 非概念 id 忽略
    ]
    p = tmp_path / "miscinfo.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_parse_miscinfo_filters_concept_id(miscinfo_file):
    tags = concept_tags.parse_miscinfo(miscinfo_file)
    assert tags["002439"] == ["网络安全", "信创", "军工信息化"]
    assert tags["603986"] == ["存储芯片", "半导体", "MCU芯片"]
    assert tags["600150"] == ["船舶制造", "军工", "央企改革"]  # id=10004 未混入


def test_refresh_degrades_when_tq_fails():
    def fake_call(method, params, timeout):
        assert method == "download_file" and params == {"down_type": 4}
        return {"ok": False, "value": None, "error": {"code": "tdxw_not_running"}}

    r = concept_tags.refresh("2026-07-21", call_fn=fake_call)
    assert r["status"] == "unavailable"
    assert "tdxw_not_running" in r["degraded_reason"]


def test_match_theme_tags_bidirectional():
    # 主题标签 ⊂ 个股概念（"芯片" ∈ "存储芯片"）；个股概念 ⊂ 主题标签（≥3字）
    matched = ec._match_theme_tags(["存储芯片", "半导体"], ["芯片", "DRAM"])
    assert matched == ["芯片"]
    matched = ec._match_theme_tags(["半导体设备"], ["半导体"])
    assert matched == ["半导体"]


def test_build_stock_theme_map_prefers_concept_tags(monkeypatch):
    monkeypatch.setattr(concept_tags, "load_tags",
                        lambda: {"603986": ["存储芯片", "半导体"], "002439": ["网络安全", "信创"]})
    monkeypatch.setattr(ec, "_load_json", lambda p, d: {
        "themes": [
            {"theme_id": "semi", "theme_name": "半导体/芯片/存储/封测",
             "semantic_tags": ["芯片", "半导体"], "primary_sector_codes": ["881319.SH"],
             "candidate_sector_codes": []},
            {"theme_id": "ai", "theme_name": "AI算力/服务器/液冷",
             "semantic_tags": ["人工智能"], "primary_sector_codes": ["880545.SH"],
             "candidate_sector_codes": []},
        ]} if p == ec.SECTOR_CODE_MAP else d)
    # 880 兜底源故意给一个错配：concept_tags 存在时必须被忽略
    monkeypatch.setattr(ec, "latest_tq_sector_map",
                        lambda: {"sectors": [{"code": "880545.SH", "stocks": ["603986.SH"]}]})
    stock_theme, ok = ec.build_stock_theme_map()
    assert ok
    assert stock_theme["603986"]["theme_id"] == "semi"
    assert stock_theme["603986"]["sector_source"] == "concept_tags"
    assert "002439" not in stock_theme  # 无主题命中 → 宁缺毋滥


def test_build_stock_theme_map_falls_back_to_880(monkeypatch):
    monkeypatch.setattr(concept_tags, "load_tags", lambda: {})
    monkeypatch.setattr(ec, "_load_json", lambda p, d: {
        "themes": [
            {"theme_id": "ai", "theme_name": "AI算力/服务器/液冷",
             "semantic_tags": ["人工智能"], "primary_sector_codes": ["880545.SH"],
             "candidate_sector_codes": []},
        ]} if p == ec.SECTOR_CODE_MAP else d)
    monkeypatch.setattr(ec, "latest_tq_sector_map",
                        lambda: {"sectors": [{"code": "880545.SH", "stocks": ["000977.SZ"]}]})
    stock_theme, ok = ec.build_stock_theme_map()
    assert ok
    assert stock_theme["000977"]["theme_id"] == "ai"
    assert stock_theme["000977"]["sector_source"] == "tq_880_fallback"


def test_build_stock_theme_map_min_match_requires_stronger_evidence(monkeypatch):
    # 603986 命中2标签(芯片/半导体)，600111 仅命中1标签(稀土)
    monkeypatch.setattr(concept_tags, "load_tags",
                        lambda: {"603986": ["存储芯片", "半导体"], "600111": ["稀土永磁"]})
    monkeypatch.setattr(ec, "_load_json", lambda p, d: {
        "themes": [
            {"theme_id": "semi", "theme_name": "半导体/芯片/存储/封测",
             "semantic_tags": ["芯片", "半导体"], "primary_sector_codes": [], "candidate_sector_codes": []},
            {"theme_id": "rare", "theme_name": "稀土永磁",
             "semantic_tags": ["稀土"], "primary_sector_codes": [], "candidate_sector_codes": []},
        ]} if p == ec.SECTOR_CODE_MAP else d)

    # min_match=1（默认）：两只都归类，match_count 落盘
    m1, ok1 = ec.build_stock_theme_map(min_match=1)
    assert ok1
    assert m1["603986"]["theme_id"] == "semi" and m1["603986"]["match_count"] == 2
    assert m1["600111"]["theme_id"] == "rare" and m1["600111"]["match_count"] == 1

    # min_match=2：仅证据≥2的保留，单标签命中被剔除（宁缺毋滥）
    m2, ok2 = ec.build_stock_theme_map(min_match=2)
    assert "603986" in m2 and "600111" not in m2

    # 非法 min_match 回退默认（≥1），不抛错
    m3, _ = ec.build_stock_theme_map(min_match=0)
    assert "603986" in m3 and "600111" in m3
