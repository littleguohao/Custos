# -*- coding: utf-8 -*-
"""daily_pipeline 门控接线测试。

回归背景:2026-07-30 曾让 postclose 默认带 --require-quality(blocked → exit 4 → 硬失败),
同时又收紧了 as_of 陈旧判定,两者叠加使 17:00 盘后复盘直接失败。此后硬闸改为显式 opt-in,
默认只落盘+留痕。本文件把"默认不阻断"钉住。
"""
from __future__ import annotations

import json

import daily_pipeline as dp


class TestBuildGateCmd:
    def test_postclose_default_does_not_hard_block(self):
        cmd = dp.build_gate_cmd("2026-07-30", "postclose")
        assert "--require-trading-day" in cmd
        assert "--require-quality" not in cmd            # 默认不得阻断盘后链

    def test_premarket_default_does_not_hard_block(self):
        assert "--require-quality" not in dp.build_gate_cmd("2026-07-30", "premarket")

    def test_strict_flag_enables_block_only_for_postclose(self):
        assert "--require-quality" in dp.build_gate_cmd("2026-07-30", "postclose", True)
        # 盘前/盘中即使开了开关也不阻断:0AMV/宽度本就要等收盘,blocked 属正常
        assert "--require-quality" not in dp.build_gate_cmd("2026-07-30", "premarket", True)

    def test_date_and_script_present(self):
        cmd = dp.build_gate_cmd("2026-07-30", "postclose")
        assert "--date" in cmd and "2026-07-30" in cmd
        assert cmd[1].endswith("runtime_gate.py")


class TestPipelineLogOnEarlyExit:
    """门控阻断(退出码 3/4/5 穿透)时也必须留下 pipeline log。

    回归:日志只在 main 末尾写,门控失败处直接 raise SystemExit 会让这次阻断连记录都不留,
    "门控留痕"的初衷就没了(事后只能翻 stdout)。
    """

    def test_write_pipeline_log_creates_file_with_stages(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dp, "LOGS", tmp_path)
        stages = [{"stage": "runtime_gate", "ok": False, "returncode": 4,
                   "note": "market_quality=blocked(score=0.2)"}]
        path = dp._write_pipeline_log("2026-07-31", stages)
        assert path.name == "2026-07-31_daily_pipeline_log.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["date"] == "2026-07-31"
        assert data["stages"][0]["returncode"] == 4
        assert "blocked" in data["stages"][0]["note"]

    def test_creates_missing_log_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dp, "LOGS", tmp_path / "nested" / "logs")
        path = dp._write_pipeline_log("2026-07-31", [])
        assert path.is_file()


class TestGateStatusNote:
    def _write(self, tmp_path, monkeypatch, gate: dict):
        monkeypatch.setattr(dp, "DATA", tmp_path)
        (tmp_path / "quality").mkdir(parents=True, exist_ok=True)
        (tmp_path / "quality" / "2026-07-30_runtime_gate.json").write_text(
            json.dumps(gate, ensure_ascii=False), encoding="utf-8")

    def test_note_records_status_and_stale_fields(self, tmp_path, monkeypatch):
        self._write(tmp_path, monkeypatch, {
            "market_quality": {"status": "degraded", "quality_score": 0.4,
                               "checks": [{"field": "market_breadth", "quality": "stale"},
                                          {"field": "0AMV", "quality": "confirmed"}]},
            "position_gate": {"status": "degraded"}})
        note = dp.gate_status_note("2026-07-30")
        assert "market_quality=degraded" in note and "score=0.4" in note
        assert "stale=market_breadth" in note and "position_gate=degraded" in note

    def test_no_stale_section_omitted(self, tmp_path, monkeypatch):
        self._write(tmp_path, monkeypatch, {
            "market_quality": {"status": "pass", "quality_score": 1.0, "checks": []},
            "position_gate": {"status": "pass"}})
        assert "stale=" not in dp.gate_status_note("2026-07-30")

    def test_missing_file_is_reported_not_raised(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dp, "DATA", tmp_path)
        assert dp.gate_status_note("2026-07-30") == "gate_json_unreadable"
