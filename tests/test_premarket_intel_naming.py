# -*- coding: utf-8 -*-
"""盘前情报文件命名兼容:加载端同时支持带连字符与无连字符两种命名。"""
from __future__ import annotations

import json

import daily_report
from news import premarket_intel_schema as intel


def _setup(monkeypatch, tmp_path, filenames):
    """⚠️ 打桩目标是 `premarket_intel_schema.PREMARKET_DIR`。

    2026-08-07 这两个函数从 `daily_report` 移到 `news/premarket_intel_schema`
    （原位置让 `news/` 反向依赖根层编排）。此前本测试打桩 `daily_report.DATA`，
    函数搬走后**桩打在旧模块上、静默失效** —— 于是加了独立的
    `PREMARKET_DIR` 常量作为唯一打桩点。
    """
    news_dir = tmp_path / "news" / "premarket"
    news_dir.mkdir(parents=True)
    for name in filenames:
        (news_dir / name).write_text(json.dumps({"market_events": [{"title": name}]}), encoding="utf-8")
    monkeypatch.setattr(intel, "PREMARKET_DIR", news_dir)
    return news_dir


def test_daily_report_reexports_the_same_functions():
    """`daily_report` 仍导出这两个名字（调用点与既有测试都用它），
    且必须是**同一个对象**而非副本。"""
    assert daily_report.premarket_intelligence_path is intel.premarket_intelligence_path
    assert daily_report.load_premarket_intelligence is intel.load_premarket_intelligence


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
