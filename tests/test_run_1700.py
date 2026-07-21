# -*- coding: utf-8 -*-
"""Tests for run_1700 observability: run-log writing (shared pipeline_kit helpers)."""
from __future__ import annotations

import json

import run_1700


class TestWriteRunLog:
    def test_structure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_1700, "LOG_DIR", tmp_path)
        stage = run_1700._log_stage("calendar", {"ok": True, "returncode": 0, "timeout": False},
                                    "2026-07-19T17:00:00", "2026-07-19T17:00:01", 1.0)
        path = run_1700._write_run_log("2026-07-19", "closed", "2026-07-19T17:00:00",
                                       __import__("time").time(), [stage])
        log = json.loads(path.read_text(encoding="utf-8"))
        assert path.name == "2026-07-19_1700_run_log.json"
        assert log["date"] == "2026-07-19"
        assert log["status"] == "closed"
        assert log["script"] == "run_1700"
        assert isinstance(log["duration_sec"], (int, float))
        assert log["stages"][0]["name"] == "calendar"
        assert log["stages"][0]["ok"] is True

    def test_failed_status_with_stage_info(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_1700, "LOG_DIR", tmp_path)
        stage = run_1700._log_stage("daily_pipeline",
                                    {"ok": False, "returncode": 1, "timeout": False,
                                     "stdout": "boom", "stderr": "err"},
                                    "2026-07-19T17:00:00", "2026-07-19T17:00:30", 30.0)
        path = run_1700._write_run_log("2026-07-19", "failed", "2026-07-19T17:00:00",
                                       __import__("time").time(), [stage])
        log = json.loads(path.read_text(encoding="utf-8"))
        assert log["status"] == "failed"
        assert log["stages"][0]["ok"] is False
        assert log["stages"][0]["returncode"] == 1
        assert log["stages"][0]["stdout_tail"] == "boom"
        assert log["stages"][0]["stderr_tail"] == "err"

    def test_best_effort_stage_keeps_completed_status(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_1700, "LOG_DIR", tmp_path)
        stages = [
            run_1700._log_stage("collect_fund_flow",
                                {"ok": False, "returncode": 1, "timeout": False, "stdout": "boom"},
                                "2026-07-19T17:00:00", "2026-07-19T17:00:05", 5.0,
                                note="best-effort，失败不中断"),
            run_1700._log_stage("daily_pipeline", {"ok": True, "returncode": 0, "timeout": False},
                                "2026-07-19T17:00:05", "2026-07-19T20:31:00", 55.0),
        ]
        path = run_1700._write_run_log("2026-07-19", "completed", "2026-07-19T17:00:00",
                                       __import__("time").time(), stages)
        log = json.loads(path.read_text(encoding="utf-8"))
        assert log["status"] == "completed"
        assert log["stages"][0]["name"] == "collect_fund_flow"
        assert log["stages"][0]["ok"] is False
        assert log["stages"][0]["note"] == "best-effort，失败不中断"
        assert log["stages"][1]["ok"] is True
