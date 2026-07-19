# -*- coding: utf-8 -*-
"""盘前情报文件命名兼容:加载端同时支持带连字符与无连字符两种命名。"""
from __future__ import annotations

import json

import daily_report


def _setup(monkeypatch, tmp_path, filenames):
    news_dir = tmp_path / "news" / "premarket"
    news_dir.mkdir(parents=True)
    for name in filenames:
        (news_dir / name).write_text(json.dumps({"market_events": [{"title": name}]}), encoding="utf-8")
    monkeypatch.setattr(daily_report, "DATA", tmp_path)
    return news_dir


class TestPremarketIntelligencePath:
    def test_hyphenated_preferred(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, ["2026-07-16_premarket_intelligence.json",
                                       "20260716_premarket_intelligence.json"])
        path = daily_report.premarket_intelligence_path("2026-07-16")
        assert path is not None and path.name == "2026-07-16_premarket_intelligence.json"

    def test_unhyphenated_fallback(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, ["20260717_premarket_intelligence.json"])
        path = daily_report.premarket_intelligence_path("2026-07-17")
        assert path is not None and path.name == "20260717_premarket_intelligence.json"

    def test_missing_returns_none(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, [])
        assert daily_report.premarket_intelligence_path("2026-07-17") is None


class TestLoadPremarketIntelligence:
    def test_loads_unhyphenated_content(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, ["20260717_premarket_intelligence.json"])
        intel = daily_report.load_premarket_intelligence("2026-07-17")
        assert intel["market_events"] == [{"title": "20260717_premarket_intelligence.json"}]

    def test_missing_returns_empty_dict(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, [])
        assert daily_report.load_premarket_intelligence("2026-07-17") == {}
