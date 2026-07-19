# -*- coding: utf-8 -*-
"""Tests for run_0850 observability: summary fragments and run-log writing."""
from __future__ import annotations

import json

import run_0850


class TestRssSummaryFragments:
    def test_both_stages_parsed(self):
        results = {
            "rss_collect": {"stdout": json.dumps(
                {"output": "x", "log": "y", "items": 12, "sources_ok": 5, "sources_failed": 1},
                ensure_ascii=False)},
            "rss_filter": {"stdout": json.dumps({"selected_count": 7})},
        }
        assert run_0850._rss_summary_fragments(results) == [
            "rss_items=12(5/6)", "rss_candidates=7",
        ]

    def test_noise_around_json_tolerated(self):
        results = {
            "rss_collect": {"stdout": '[WARN] x\n{"items": 3, "sources_ok": 2, "sources_failed": 0}\n'},
            "rss_filter": {"stdout": 'noise\n{\n  "selected_count": 2\n}\n'},
        }
        assert run_0850._rss_summary_fragments(results) == [
            "rss_items=3(2/2)", "rss_candidates=2",
        ]

    def test_unparseable_stages_add_nothing(self):
        assert run_0850._rss_summary_fragments({}) == []
        assert run_0850._rss_summary_fragments({
            "rss_collect": {"stdout": "not json at all"},
            "rss_filter": {"stdout": ""},
        }) == []

    def test_partial_keys_skipped(self):
        results = {
            "rss_collect": {"stdout": '{"items": 4}'},  # sources_ok/failed missing
            "rss_filter": {"stdout": '{"selected_count": 2}'},
        }
        assert run_0850._rss_summary_fragments(results) == ["rss_candidates=2"]


class TestLogStage:
    def test_tails_truncated_to_1000(self):
        r = {"ok": True, "returncode": 0, "timeout": False,
             "stdout": "s" * 1500, "stderr": "e" * 1500}
        entry = run_0850._log_stage("stage1", r, "2026-07-17T08:50:00", "2026-07-17T08:50:10", 10.0)
        assert entry["name"] == "stage1"
        assert entry["ok"] is True
        assert entry["returncode"] == 0
        assert entry["timeout"] is False
        assert len(entry["stdout_tail"]) == 1000
        assert len(entry["stderr_tail"]) == 1000
        assert entry["duration_sec"] == 10.0
        assert "note" not in entry

    def test_missing_fields_default(self):
        entry = run_0850._log_stage("stage1", {}, "a", "b", 0.1, note="why")
        assert entry["ok"] is False
        assert entry["returncode"] is None
        assert entry["stdout_tail"] == ""
        assert entry["note"] == "why"


class TestWriteRunLog:
    def test_structure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_0850, "LOG_DIR", tmp_path)
        stage = run_0850._log_stage("calendar", {"ok": True, "returncode": 0, "timeout": False},
                                    "2026-07-18T08:50:00", "2026-07-18T08:50:01", 1.0)
        path = run_0850._write_run_log("2026-07-18", "closed", "2026-07-18T08:50:00",
                                       __import__("time").time(), [stage])
        log = json.loads(path.read_text(encoding="utf-8"))
        assert path.name == "2026-07-18_0850_run_log.json"
        assert log["date"] == "2026-07-18"
        assert log["status"] == "closed"
        assert log["script"] == "run_0850"
        assert isinstance(log["duration_sec"], (int, float))
        assert log["stages"][0]["name"] == "calendar"
        assert log["stages"][0]["ok"] is True
