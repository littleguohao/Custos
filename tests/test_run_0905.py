# -*- coding: utf-8 -*-
"""Tests for run_0905 observability: run-log writing (shared pipeline_kit helpers)."""
from __future__ import annotations

import json

import run_0905


class TestWriteRunLog:
    def test_structure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_0905, "LOG_DIR", tmp_path)
        stage = run_0905._log_stage("calendar", {"ok": True, "returncode": 0, "timeout": False},
                                    "2026-07-19T09:05:00", "2026-07-19T09:05:01", 1.0)
        path = run_0905._write_run_log("2026-07-19", "closed", "2026-07-19T09:05:00",
                                       __import__("time").time(), [stage])
        log = json.loads(path.read_text(encoding="utf-8"))
        assert path.name == "2026-07-19_0905_run_log.json"
        assert log["date"] == "2026-07-19"
        assert log["status"] == "closed"
        assert log["script"] == "run_0905"
        assert isinstance(log["duration_sec"], (int, float))
        assert log["stages"][0]["name"] == "calendar"
        assert log["stages"][0]["ok"] is True

    def test_failed_status_with_stage_info(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_0905, "LOG_DIR", tmp_path)
        stage = run_0905._log_stage("daily_pipeline premarket",
                                    {"ok": False, "returncode": 1, "timeout": False,
                                     "stdout": "boom", "stderr": "err"},
                                    "2026-07-19T09:05:00", "2026-07-19T09:05:30", 30.0)
        path = run_0905._write_run_log("2026-07-19", "failed", "2026-07-19T09:05:00",
                                       __import__("time").time(), [stage])
        log = json.loads(path.read_text(encoding="utf-8"))
        assert log["status"] == "failed"
        assert log["stages"][0]["ok"] is False
        assert log["stages"][0]["returncode"] == 1
        assert log["stages"][0]["stdout_tail"] == "boom"
        assert log["stages"][0]["stderr_tail"] == "err"
