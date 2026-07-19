# -*- coding: utf-8 -*-
"""postclose_news_digest:premarket_intelligence 命名兼容(带连字符优先、无连字符回退),与 daily_report 对齐。"""
from __future__ import annotations

import json

import daily_report
import postclose_news_digest


def _run(monkeypatch, tmp_path, intel_filenames, day="2026-07-17"):
    news_dir = tmp_path / "news" / "premarket"
    news_dir.mkdir(parents=True)
    for name in intel_filenames:
        (news_dir / name).write_text(json.dumps({"market_events": [{"title": "t"}]}),
                                     encoding="utf-8")
    # daily_report 的加载函数读 daily_report.DATA;postclose 其余路径读自身 DATA
    monkeypatch.setattr(daily_report, "DATA", tmp_path)
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
        result = _run(monkeypatch, tmp_path, ["2026-07-17_premarket_intelligence.json",
                                              "20260717_premarket_intelligence.json"])
        assert "2026-07-17_premarket_intelligence.json" in result["sources"][1]

    def test_missing_reported(self, monkeypatch, tmp_path):
        result = _run(monkeypatch, tmp_path, [])
        assert "premarket_intelligence" in result["missing"]
        assert result["status"] == "degraded"
