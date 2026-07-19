# -*- coding: utf-8 -*-
"""盘前情报 schema 校验:daily_report 加载后先校验,不合规时显式降级标注。"""
from __future__ import annotations

import json

import daily_report
from premarket_intel_schema import validate_premarket_intelligence

STANDARD = {
    "date": "2026-07-16",
    "window": {"start": "2026-07-15 15:00:00", "end": "2026-07-16 09:00:00", "timezone": "Asia/Shanghai"},
    "market_events": [
        {"published_at": "2026-07-15 17:14:09", "title": "港股三大指数继续走强",
         "direction": "neutral_positive", "impact": "外围候选线索", "source": "RSS候选", "quality": "candidate"}
    ],
    "holding_events": [],
    "data_quality": {"status": "degraded", "reason": "补做核验"},
}

# 20260717 漂移形态:只有 date/collected_at/holdings/data_quality
DRIFTED = {
    "date": "20260717",
    "collected_at": "2026-07-17T08:50:00+08:00",
    "holdings": [{"code": "688114", "name": "华大智造", "notices": []}],
    "data_quality": "confirmed",
}


class TestValidatePremarketIntelligence:
    def test_standard_file_valid(self):
        result = validate_premarket_intelligence(STANDARD)
        assert result["valid"] is True
        assert result["errors"] == []
        assert result["warnings"] == []

    def test_drifted_file_invalid(self):
        result = validate_premarket_intelligence(DRIFTED)
        assert result["valid"] is False
        assert any("market_events" in e for e in result["errors"])
        assert any("holding_events" in e for e in result["errors"])

    def test_empty_dict_invalid(self):
        result = validate_premarket_intelligence({})
        assert result["valid"] is False
        assert any("date" in e for e in result["errors"])
        assert any("market_events" in e for e in result["errors"])
        assert any("holding_events" in e for e in result["errors"])

    def test_non_dict_invalid(self):
        result = validate_premarket_intelligence([1, 2, 3])
        assert result["valid"] is False
        assert result["errors"]

    def test_wrong_types_are_errors(self):
        bad = dict(STANDARD, date=20260716, market_events="none", holding_events={})
        result = validate_premarket_intelligence(bad)
        assert result["valid"] is False
        assert any("date" in e for e in result["errors"])
        assert any("market_events" in e and "list" in e for e in result["errors"])
        assert any("holding_events" in e and "list" in e for e in result["errors"])

    def test_missing_window_is_warning_only(self):
        data = {k: v for k, v in STANDARD.items() if k != "window"}
        result = validate_premarket_intelligence(data)
        assert result["valid"] is True
        assert any("window" in w for w in result["warnings"])

    def test_event_missing_basic_fields_are_warnings(self):
        data = dict(STANDARD, market_events=[{"impact": "只有 impact"}])
        result = validate_premarket_intelligence(data)
        assert result["valid"] is True
        assert any("title" in w for w in result["warnings"])
        assert any("direction" in w for w in result["warnings"])

    def test_event_non_dict_is_warning(self):
        data = dict(STANDARD, holding_events=["oops"])
        result = validate_premarket_intelligence(data)
        assert result["valid"] is True
        assert any("holding_events[0]" in w for w in result["warnings"])


class TestPremarketSchemaNote:
    def test_valid_returns_empty(self):
        assert daily_report.premarket_schema_note({"valid": True, "errors": [], "warnings": []}) == ""

    def test_invalid_renders_degraded_banner(self):
        note = daily_report.premarket_schema_note(
            {"valid": False, "errors": ["缺 market_events(list)", "缺 holding_events(list)"], "warnings": []})
        assert "schema 不合规" in note
        assert "market_events" in note and "holding_events" in note
        assert "RSS" in note


class TestPremarketSchemaMarker:
    def test_valid_no_warnings_returns_empty(self):
        assert daily_report.premarket_schema_marker({"valid": True, "errors": [], "warnings": []}) == ""

    def test_invalid_marks_schema_invalid(self):
        marker = daily_report.premarket_schema_marker({"valid": False, "errors": ["缺 market_events(list)"], "warnings": []})
        assert marker.startswith("（schema invalid: ")
        assert "market_events" in marker

    def test_valid_with_warnings_marks_count(self):
        marker = daily_report.premarket_schema_marker({"valid": True, "errors": [], "warnings": ["w1", "w2"]})
        assert marker == "（schema warnings: 2）"


class TestMainDegradesOnInvalidSchema:
    def _setup_data(self, monkeypatch, tmp_path, intel):
        data = tmp_path / "01_data"
        (data / "news" / "premarket").mkdir(parents=True)
        (data / "news" / "rss" / "filtered").mkdir(parents=True)
        (data / "decisions").mkdir(parents=True)
        if intel is not None:
            (data / "news" / "premarket" / "2026-07-17_premarket_intelligence.json").write_text(
                json.dumps(intel, ensure_ascii=False), encoding="utf-8")
        (data / "decisions" / "2026-07-17_chief_decision.json").write_text(json.dumps({
            "date": "2026-07-17", "market_state": "震荡", "total_position_range": "30%-40%",
            "market_quality": {"status": "ok"}, "position_freshness": {"status": "ok"},
            "position_gate": {}, "holding_actions": [], "buy_actions": [],
        }), encoding="utf-8")
        monkeypatch.setattr(daily_report, "DATA", data)
        monkeypatch.setattr(daily_report, "BASE", tmp_path)
        return data

    def _run_main(self, monkeypatch, tmp_path, out):
        import sys
        monkeypatch.setattr(sys, "argv", ["daily_report.py", "--date", "2026-07-17", "--output", str(out)])
        daily_report.main()
        return out.read_text(encoding="utf-8")

    def test_invalid_intel_shows_banner_and_marker(self, monkeypatch, tmp_path):
        self._setup_data(monkeypatch, tmp_path, DRIFTED)
        text = self._run_main(monkeypatch, tmp_path, tmp_path / "report.md")
        assert "schema 不合规" in text
        assert "（schema invalid:" in text

    def test_valid_intel_has_no_banner(self, monkeypatch, tmp_path):
        self._setup_data(monkeypatch, tmp_path, dict(STANDARD, date="2026-07-17"))
        text = self._run_main(monkeypatch, tmp_path, tmp_path / "report.md")
        assert "schema 不合规" not in text
        assert "（schema invalid:" not in text
        assert "schema warnings" not in text
        assert "港股三大指数继续走强" in text

    def test_missing_file_has_no_schema_marker(self, monkeypatch, tmp_path):
        self._setup_data(monkeypatch, tmp_path, None)
        text = self._run_main(monkeypatch, tmp_path, tmp_path / "report.md")
        assert "schema 不合规" not in text
        assert "（schema invalid:" not in text
