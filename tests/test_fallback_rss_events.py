# -*- coding: utf-8 -*-
"""fallback_rss_events:新源体系下按 relevance_score>=60 排序取 top3(不再要求 matched_market_keywords)。"""
from __future__ import annotations

import json

import daily_report


def _setup(monkeypatch, tmp_path, items):
    rss_dir = tmp_path / "news" / "rss" / "filtered"
    rss_dir.mkdir(parents=True)
    (rss_dir / "2026-07-19_premarket_rss_candidates.json").write_text(
        json.dumps(items, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(daily_report, "DATA", tmp_path)


def _item(score, title="事件", keywords=None, quality="candidate"):
    return {
        "published_at": "2026-07-19T08:00:00+08:00",
        "title": title,
        "direction": "neutral",
        "relevance_score": score,
        "matched_market_keywords": keywords or [],
        "source_name": "源A",
        "quality": quality,
    }


class TestFallbackRssEvents:
    def test_selects_top3_by_score_without_keyword_requirement(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, [
            _item(69, "低分但有词", keywords=["美联储"]),
            _item(92, "高分无词A"),
            _item(77, "中分无词"),
            _item(92, "高分无词B"),
            _item(88, "高分无词C"),
        ])
        events = daily_report.fallback_rss_events("2026-07-19")
        assert [e["title"] for e in events] == ["高分无词A", "高分无词B", "高分无词C"]

    def test_score_below_threshold_excluded(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, [_item(59, "不达标"), _item(60, "刚好达标")])
        events = daily_report.fallback_rss_events("2026-07-19")
        assert [e["title"] for e in events] == ["刚好达标"]

    def test_no_qualified_items_returns_empty(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, [_item(50), _item(None), _item(0)])
        assert daily_report.fallback_rss_events("2026-07-19") == []

    def test_missing_file_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(daily_report, "DATA", tmp_path)
        assert daily_report.fallback_rss_events("2026-07-19") == []

    def test_keeps_quality_and_impact_annotation(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, [_item(92, "官方源", quality="confirmed")])
        events = daily_report.fallback_rss_events("2026-07-19")
        assert events[0]["quality"] == "confirmed"
        assert events[0]["impact"] == "仅作候选风险证据"

    def test_quality_defaults_to_candidate(self, monkeypatch, tmp_path):
        item = _item(92)
        del item["quality"]
        _setup(monkeypatch, tmp_path, [item])
        events = daily_report.fallback_rss_events("2026-07-19")
        assert events[0]["quality"] == "candidate"
