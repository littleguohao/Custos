# -*- coding: utf-8 -*-
"""Tests for run_0850 observability: summary fragments and run-log writing."""
from __future__ import annotations

import json

from custos.pipeline import run_0850


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


class TestCollectionStatus:
    """08:50 采集失败必须写 degraded——写 completed 会让 09:05 静默复用空数据。"""

    def _run(self, tmp_path, monkeypatch, failing: set[str], capsys):
        monkeypatch.setattr(run_0850, "LOG_DIR", tmp_path)
        monkeypatch.setattr(run_0850, "check_trading_day",
                            lambda target: {"is_trading_day": True, "date": target})
        monkeypatch.setattr(run_0850, "_stage", lambda cmd, name: {
            "ok": name not in failing, "returncode": 0 if name not in failing else 1,
            "timeout": False, "stdout": "", "stderr": "", "out": "",
        })
        rc = run_0850.main(["--date", "2026-07-20"])
        printed = capsys.readouterr().out
        log = json.loads((tmp_path / "2026-07-20_0850_run_log.json").read_text(encoding="utf-8"))
        return rc, log, printed

    def test_all_ok_is_completed(self, tmp_path, monkeypatch, capsys):
        rc, log, printed = self._run(tmp_path, monkeypatch, set(), capsys)
        assert rc == 0
        assert log["status"] == "completed"
        summary = [s for s in log["stages"] if s.get("stage") == "collection_summary"][0]
        assert summary["failed_stages"] == [] and summary["ok"] is True
        assert "降级" not in printed

    def test_any_failure_is_degraded_and_lists_failed_stages(self, tmp_path, monkeypatch, capsys):
        rc, log, printed = self._run(tmp_path, monkeypatch, {"overseas", "rss_collect"}, capsys)
        assert rc == 0                                   # 采集仍是 best-effort,不硬失败
        assert log["status"] == "degraded"
        summary = [s for s in log["stages"] if s.get("stage") == "collection_summary"][0]
        assert set(summary["failed_stages"]) == {"overseas", "rss_collect"}
        assert "降级" in printed and "overseas" in printed

    def test_degraded_log_blocks_0905_reuse(self, tmp_path, monkeypatch, capsys):
        """端到端语义:0850 的 degraded 日志 → 0905 拒绝复用 discovery。"""
        from custos.pipeline import run_0905
        self._run(tmp_path, monkeypatch, {"rss_filter"}, capsys)
        monkeypatch.setattr(run_0905, "LOG_DIR", tmp_path)
        reuse, note = run_0905._check_0850_status("2026-07-20")
        assert reuse is False and "rss_filter" in note


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
