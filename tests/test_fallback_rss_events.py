# -*- coding: utf-8 -*-
"""fallback_rss_events:新源体系下按 relevance_score>=60 排序取 top3(不再要求 matched_market_keywords)。"""

from __future__ import annotations

import json

from custos.pipeline import daily_report
import sys


def _setup(monkeypatch, tmp_path, items):
    rss_dir = tmp_path / "news" / "rss" / "filtered"
    rss_dir.mkdir(parents=True)
    (rss_dir / "2026-07-19_premarket_rss_candidates.json").write_text(
        json.dumps(items, ensure_ascii=False), encoding="utf-8"
    )
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
    def test_selects_top3_by_score_without_keyword_requirement(
        self, monkeypatch, tmp_path
    ):
        _setup(
            monkeypatch,
            tmp_path,
            [
                _item(69, "低分但有词", keywords=["美联储"]),
                _item(92, "高分无词A"),
                _item(77, "中分无词"),
                _item(92, "高分无词B"),
                _item(88, "高分无词C"),
            ],
        )
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


class TestDailyReportAuditBlock:
    """可审计块（待办 #29）：盘前日报头部必须带 report_id / 策略版本 / 数据截止 / 输入清单。"""

    def test_md_header_carries_audit(self, monkeypatch, tmp_path):
        data = tmp_path / "data"
        (data / "decisions").mkdir(parents=True)
        (data / "decisions" / "2026-08-07_chief_decision.json").write_text(
            json.dumps(
                {
                    "market_state": "震荡",
                    "market_quality": {},
                    "position_freshness": {},
                    "position_gate": {},
                    "holding_actions": [],
                    "buy_actions": [],
                    "forbidden_actions": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(daily_report, "DATA", data)
        monkeypatch.setattr(daily_report, "PLAN", tmp_path / "artifacts/reports/daily")
        # 盘前情报路径走模块自身常量，钉成「无」保证环境无关
        monkeypatch.setattr(
            daily_report, "premarket_intelligence_path", lambda day: None
        )
        monkeypatch.setattr(daily_report, "load_premarket_intelligence", lambda day: {})
        import sys

        monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-08-07"])
        daily_report.main()
        body = (
            tmp_path
            / "artifacts/reports/daily"
            / "2026-08-07"
            / "2026-08-07_daily_report.md"
        ).read_text(encoding="utf-8")
        header = body.split("## 1.")[0]
        assert "report_id `2026-08-07_premarket_" in header
        assert "策略版本" in header and "数据截止" in header and "输入清单" in header
        # chief_decision 存在 → 有 sha1；其余输入缺失 → 如实标「缺失」
        assert "chief_decision.json`（" in header and "缺失" in header
