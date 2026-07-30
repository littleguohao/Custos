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


class TestCheck0850Status:
    def _write_0850_log(self, log_dir, target, status, stages=None):
        if stages is None:                      # 默认三个 discovery stage 全 ok
            stages = [{"name": n, "ok": True} for n in run_0905.DISCOVERY_STAGES]
        (log_dir / f"{target}_0850_run_log.json").write_text(
            json.dumps({"date": target, "script": "run_0850", "status": status,
                        "stages": stages}),
            encoding="utf-8")

    def test_completed_allows_reuse(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_0905, "LOG_DIR", tmp_path)
        self._write_0850_log(tmp_path, "2026-07-18", "completed")
        reuse, note = run_0905._check_0850_status("2026-07-18")
        assert reuse is True
        assert note == ""

    def test_completed_without_stage_records_refuses_reuse(self, tmp_path, monkeypatch):
        """旧格式(无 stages 明细)无法证明 discovery 成功 → 保守重采,不复用。"""
        monkeypatch.setattr(run_0905, "LOG_DIR", tmp_path)
        self._write_0850_log(tmp_path, "2026-07-18", "completed", stages=[])
        reuse, note = run_0905._check_0850_status("2026-07-18")
        assert reuse is False
        assert "discovery_failed" in note

    def test_discovery_failure_refuses_reuse_even_if_completed(self, tmp_path, monkeypatch):
        """核心回归:08:50 采集失败却写 completed 时,09:05 必须重采而非用空数据出报告。"""
        monkeypatch.setattr(run_0905, "LOG_DIR", tmp_path)
        self._write_0850_log(tmp_path, "2026-07-18", "completed", stages=[
            {"name": "overseas", "ok": False}, {"name": "rss_collect", "ok": False},
            {"name": "rss_filter", "ok": False},
        ])
        reuse, note = run_0905._check_0850_status("2026-07-18")
        assert reuse is False
        assert "overseas" in note and "rss_collect" in note and "rss_filter" in note

    def test_degraded_with_healthy_discovery_may_reuse(self, tmp_path, monkeypatch):
        """只有非 discovery 项(如 incremental)失败时,discovery 产物仍可复用。"""
        monkeypatch.setattr(run_0905, "LOG_DIR", tmp_path)
        self._write_0850_log(tmp_path, "2026-07-18", "degraded", stages=[
            {"name": "overseas", "ok": True}, {"name": "rss_collect", "ok": True},
            {"name": "rss_filter", "ok": True}, {"name": "incremental", "ok": False},
        ])
        reuse, note = run_0905._check_0850_status("2026-07-18")
        assert reuse is True
        assert "degraded" in note

    def test_missing_log_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_0905, "LOG_DIR", tmp_path)
        reuse, note = run_0905._check_0850_status("2026-07-18")
        assert reuse is False
        assert "0850_log_missing" in note

    def test_failed_status_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_0905, "LOG_DIR", tmp_path)
        self._write_0850_log(tmp_path, "2026-07-18", "failed")
        reuse, note = run_0905._check_0850_status("2026-07-18")
        assert reuse is False
        assert "0850_status=failed" in note

    def test_calendar_failed_status_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_0905, "LOG_DIR", tmp_path)
        self._write_0850_log(tmp_path, "2026-07-18", "calendar_failed")
        reuse, note = run_0905._check_0850_status("2026-07-18")
        assert reuse is False
        assert "0850_status=calendar_failed" in note

    def test_unreadable_log_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_0905, "LOG_DIR", tmp_path)
        (tmp_path / "2026-07-18_0850_run_log.json").write_text("not json", encoding="utf-8")
        reuse, note = run_0905._check_0850_status("2026-07-18")
        assert reuse is False
        assert "0850_log_unreadable" in note


class TestDailyPipelineCmd:
    def test_reuse_discovery_appended(self):
        cmd = run_0905._daily_pipeline_cmd("2026-07-18", reuse_discovery=True)
        assert "--reuse-discovery" in cmd
        assert "--session-type" in cmd and "premarket" in cmd

    def test_full_collection_omits_reuse(self):
        cmd = run_0905._daily_pipeline_cmd("2026-07-18", reuse_discovery=False)
        assert "--reuse-discovery" not in cmd
        assert "--session-type" in cmd and "premarket" in cmd
