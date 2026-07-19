# -*- coding: utf-8 -*-
"""Tests for run_1445 observability: run-log writing (shared pipeline_kit helpers)."""
from __future__ import annotations

import json

import run_1445


class TestWriteRunLog:
    def test_structure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_1445, "LOG_DIR", tmp_path)
        stage = run_1445._log_stage("calendar", {"ok": True, "returncode": 0, "timeout": False},
                                    "2026-07-19T14:45:00", "2026-07-19T14:45:01", 1.0)
        path = run_1445._write_run_log("2026-07-19", "closed", "2026-07-19T14:45:00",
                                       __import__("time").time(), [stage])
        log = json.loads(path.read_text(encoding="utf-8"))
        assert path.name == "2026-07-19_1445_run_log.json"
        assert log["date"] == "2026-07-19"
        assert log["status"] == "closed"
        assert log["script"] == "run_1445"
        assert isinstance(log["duration_sec"], (int, float))
        assert log["stages"][0]["name"] == "calendar"
        assert log["stages"][0]["ok"] is True

    def test_failed_status_with_stage_info(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_1445, "LOG_DIR", tmp_path)
        stage = run_1445._log_stage("collect_holding_quotes",
                                    {"ok": False, "returncode": 1, "timeout": False,
                                     "stdout": "boom", "stderr": "err"},
                                    "2026-07-19T14:45:00", "2026-07-19T14:45:30", 30.0)
        path = run_1445._write_run_log("2026-07-19", "failed", "2026-07-19T14:45:00",
                                       __import__("time").time(), [stage])
        log = json.loads(path.read_text(encoding="utf-8"))
        assert log["status"] == "failed"
        assert log["stages"][0]["ok"] is False
        assert log["stages"][0]["returncode"] == 1
        assert log["stages"][0]["stdout_tail"] == "boom"
        assert log["stages"][0]["stderr_tail"] == "err"
