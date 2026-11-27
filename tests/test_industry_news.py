# -*- coding: utf-8 -*-
"""新闻热度与重点行业影响评分。"""

from __future__ import annotations

from datetime import datetime, timezone

from custos.datasource.news import industry_news as industry


def test_guangdong_compute_plan_is_high_impact_without_holding_code():
    result = industry.industry_assessment(
        themes=["AI算力", "半导体", "宏观政策"],
        category="cn_financial_media",
        text="广东省算力发展十五五规划：鼓励国产算力芯片，建设数据中心，推进CANN生态",
        cfg={},
    )
    assert result["matched_industries"] == ["科技", "AI"]
    assert result["industry_impact_score"] >= 60
    assert result["industry_impact_level"] == "高"
    assert "政策规划" in result["impact_drivers"]
    assert "技术产品" in result["impact_drivers"]
    assert "政策驱动" in industry.impact_narrative(result)


def test_all_owner_focus_industries_are_mapped():
    cases = [
        (["科技"], ["科技"]),
        (["AI算力"], ["AI"]),
        (["医疗医药"], ["医疗医药"]),
        (["电力"], ["电力"]),
        (["电网"], ["电网"]),
    ]
    for themes, expected in cases:
        assert industry.matched_industries(themes, {}) == expected


def test_non_focus_news_has_no_industry_impact():
    result = industry.industry_assessment(
        themes=["船舶军工"], category="media", text="普通航运新闻", cfg={}
    )
    assert result["matched_industries"] == []
    assert result["industry_impact_score"] == 0


def test_heat_uses_cross_source_recency_and_recommend():
    items = [
        {
            "title": "同一重大政策",
            "duplicate_group_id": "same-event",
            "source_id": "source-a",
            "published_at": "2026-11-27T08:00:00+00:00",
            "recommend": 1,
            "relevance_score": 10,
        },
        {
            "title": "同一重大政策",
            "duplicate_group_id": "same-event",
            "source_id": "source-b",
            "published_at": "2026-11-27T08:00:00+00:00",
            "recommend": 0,
            "relevance_score": 10,
        },
    ]
    industry.apply_heat(items, datetime(2026, 11, 27, 9, tzinfo=timezone.utc))
    assert items[0]["cross_source_count"] == 2
    assert items[0]["heat_score"] == 45  # 20 时效 + 15 跨源 + 10 推荐
    assert items[1]["heat_score"] == 35
    assert items[0]["heat_level"] == "高"
    assert items[0]["relevance_score"] == 55
    assert items[1]["relevance_score"] == 45
