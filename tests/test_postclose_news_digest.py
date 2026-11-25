# -*- coding: utf-8 -*-
"""postclose_news_digest:premarket_intelligence 命名兼容(带连字符优先、无连字符回退),与 daily_report 对齐。"""

from __future__ import annotations

import json

from custos.pipeline import daily_report
from custos.datasource.news import postclose_news_digest
import sys


def _run(monkeypatch, tmp_path, intel_filenames, day="2026-07-17"):
    news_dir = tmp_path / "news" / "premarket"
    news_dir.mkdir(parents=True)
    for name in intel_filenames:
        (news_dir / name).write_text(
            json.dumps({"market_events": [{"title": "t"}]}), encoding="utf-8"
        )
    monkeypatch.setattr(daily_report, "DATA", tmp_path)
    # ⚠️ 盘前情报访问器已移到 `news/premarket_intel_schema`，需单独打桩它的 PREMARKET_DIR
    from custos.datasource.news import premarket_intel_schema as _intel

    monkeypatch.setattr(_intel, "PREMARKET_DIR", tmp_path / "news" / "premarket")
    monkeypatch.setattr(postclose_news_digest, "DATA", tmp_path)
    monkeypatch.setattr("sys.argv", ["postclose_news_digest.py", "--date", day])
    postclose_news_digest.main()
    out = tmp_path / "news" / "postclose" / f"{day}_postclose_news_digest.json"
    return json.loads(out.read_text(encoding="utf-8"))


class TestPremarketIntelligenceNaming:
    def test_hyphenated_naming_accepted(self, monkeypatch, tmp_path):
        result = _run(monkeypatch, tmp_path, ["2026-07-17_premarket_intelligence.json"])
        assert "premarket_intelligence" not in result["missing"]
        assert result["premarket_market_event_count"] == 1

    def test_unhyphenated_fallback_accepted(self, monkeypatch, tmp_path):
        result = _run(monkeypatch, tmp_path, ["20260717_premarket_intelligence.json"])
        assert "premarket_intelligence" not in result["missing"]
        assert result["premarket_market_event_count"] == 1
        assert "20260717_premarket_intelligence.json" in result["sources"][1]

    def test_hyphenated_preferred_over_unhyphenated(self, monkeypatch, tmp_path):
        result = _run(
            monkeypatch,
            tmp_path,
            [
                "2026-07-17_premarket_intelligence.json",
                "20260717_premarket_intelligence.json",
            ],
        )
        assert "2026-07-17_premarket_intelligence.json" in result["sources"][1]

    def test_missing_reported(self, monkeypatch, tmp_path):
        result = _run(monkeypatch, tmp_path, [])
        assert "premarket_intelligence" in result["missing"]
        assert result["status"] == "degraded"


def test_priority_event_kept_with_summary_and_focus(monkeypatch, tmp_path):
    rss_dir = tmp_path / "news" / "rss" / "filtered"
    rss_dir.mkdir(parents=True)
    (rss_dir / "2026-07-17_postclose_rss_candidates.json").write_text(
        json.dumps(
            [
                {
                    "published_at": "2026-07-17T15:20:00+08:00",
                    "title": "7月17日涨停分析",
                    "source_name": "财联社-电报快讯",
                    "source_tier": "B",
                    "cls_priority": "涨停与舆情分析",
                    "analysis_focus": "看封板率与焦点股",
                    "summary": "今日全市场涨停与连板结构",
                    "recommend": 1,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = _run(monkeypatch, tmp_path, [])
    assert result["priority_count"] == 1
    row = result["priority_events"][0]
    assert row["cls_priority"] == "涨停与舆情分析"
    assert row["summary"] == "今日全市场涨停与连板结构"
    assert row["analysis_focus"] == "看封板率与焦点股"
    assert row["recommend"] == 1


def test_priority_event_excludes_same_day_premarket(monkeypatch, tmp_path):
    """v0.598：1700 不重复展示 0905 已收录文章，但保留收盘后的新文章。"""
    rss_dir = tmp_path / "news" / "rss" / "filtered"
    rss_dir.mkdir(parents=True)
    morning = {
        "cls_priority_articles": [
            {
                "title": "7月17日股市避雷针",
                "source_url": "https://www.cls.cn/detail/100",
            }
        ]
    }
    (rss_dir / "2026-07-17_premarket_rss_topic_clusters.json").write_text(
        json.dumps(morning), encoding="utf-8"
    )
    rows = [
        {
            "published_at": "2026-07-17T08:30:00+08:00",
            "title": "7月17日股市避雷针",
            "source_name": "财联社-电报快讯",
            "source_tier": "B",
            "source_url": "https://m.cls.cn/detail/100",
            "cls_priority": "盘前避雷针",
            "summary": "早间已收录",
        },
        {
            "published_at": "2026-07-17T15:20:00+08:00",
            "title": "7月17日涨停分析",
            "source_name": "财联社-电报快讯",
            "source_tier": "B",
            "source_url": "https://www.cls.cn/detail/101",
            "cls_priority": "涨停与舆情分析",
            "summary": "收盘后新增",
        },
    ]
    (rss_dir / "2026-07-17_postclose_rss_candidates.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8"
    )
    result = _run(monkeypatch, tmp_path, [])
    assert [x["title"] for x in result["priority_events"]] == ["7月17日涨停分析"]
    assert result["premarket_excluded_count"] == 1
    assert result["premarket_dedup_articles"] == 1
