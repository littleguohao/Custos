# -*- coding: utf-8 -*-
"""sync_compass_amv 单测：台账合并（新增/跳过重复/格式）、amv_0day 写入
（存在/不存在/已 confirmed 不覆盖）、compass 失败优雅降级。"""
from __future__ import annotations

import json
from pathlib import Path

import sync_compass_amv as sync_mod


def _records() -> list:
    return [
        {"date": "2026-07-15", "open": 1, "high": 2, "low": 0.5, "close": 1.5,
         "volume": 1e11, "amount": 2e12, "change_pct": -2.53},
        {"date": "2026-07-16", "open": 1, "high": 2, "low": 0.5, "close": 1.4,
         "volume": 1e11, "amount": 2e12, "change_pct": -3.5},
        {"date": "2026-07-17", "open": 1, "high": 2, "low": 0.5, "close": 1.3,
         "volume": 1e11, "amount": 2e12, "change_pct": -5.84},
    ]


class TestMergeLedger:
    def test_add_new_records(self, tmp_path: Path) -> None:
        ledger = tmp_path / "0amv_observations.jsonl"
        added, skipped = sync_mod.merge_ledger(_records(), ledger)
        assert (added, skipped) == (3, 0)
        lines = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()]
        assert [l["date"] for l in lines] == ["2026-07-15", "2026-07-16", "2026-07-17"]
        rec = lines[0]
        assert rec == {
            "date": "2026-07-15",
            "amv_change_pct": -2.53,
            "as_of": "2026-07-15",
            "quality": "confirmed",
            "source": "compass_day_vdat",
            "recorded_at": rec["recorded_at"],
        }
        assert "T" in rec["recorded_at"]

    def test_skip_existing_any_source(self, tmp_path: Path) -> None:
        ledger = tmp_path / "0amv_observations.jsonl"
        ledger.write_text(
            json.dumps({"date": "2026-07-16", "amv_change_pct": -3.5, "as_of": "2026-07-16",
                        "quality": "confirmed", "source": "user_manual_input",
                        "recorded_at": "2026-07-16T21:00:00+08:00"}) + "\n",
            encoding="utf-8")
        added, skipped = sync_mod.merge_ledger(_records(), ledger)
        assert (added, skipped) == (2, 1)
        lines = ledger.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        # 原行保持不变，新记录追加在后
        assert json.loads(lines[0])["source"] == "user_manual_input"
        assert json.loads(lines[1])["source"] == "compass_day_vdat"

    def test_rerun_is_idempotent(self, tmp_path: Path) -> None:
        ledger = tmp_path / "0amv_observations.jsonl"
        sync_mod.merge_ledger(_records(), ledger)
        added, skipped = sync_mod.merge_ledger(_records(), ledger)
        assert (added, skipped) == (0, 3)
        assert len(ledger.read_text(encoding="utf-8").splitlines()) == 3

    def test_none_change_pct_skipped(self, tmp_path: Path) -> None:
        ledger = tmp_path / "0amv_observations.jsonl"
        recs = [_records()[0]]
        recs[0] = {**recs[0], "change_pct": None}
        added, skipped = sync_mod.merge_ledger(recs, ledger)
        assert (added, skipped) == (0, 0)
        assert not ledger.exists()


class TestFillAmv0day:
    def _market(self, tmp_path: Path, **extra) -> Path:
        mkt = {"amv_0": {"amv_change_pct": None, "amv_zone": ""}}
        mkt.update(extra)
        p = tmp_path / "2026-07-17_market_timing_input.json"
        p.write_text(json.dumps(mkt, ensure_ascii=False), encoding="utf-8")
        return p

    def test_file_missing_no_write(self, tmp_path: Path) -> None:
        assert sync_mod.fill_amv_0day("2026-07-17", -5.84, tmp_path) is False

    def test_writes_new_key(self, tmp_path: Path) -> None:
        p = self._market(tmp_path)
        assert sync_mod.fill_amv_0day("2026-07-17", -5.84, tmp_path) is True
        mkt = json.loads(p.read_text(encoding="utf-8"))
        assert mkt["amv_0day"] == -5.84
        assert mkt["amv_0"]["amv_change_pct"] is None  # 不动 amv_0

    def test_confirmed_not_overwritten(self, tmp_path: Path) -> None:
        p = self._market(tmp_path, amv_0day=-5.84,
                         amv_0={"amv_change_pct": -5.84, "quality": "confirmed"})
        assert sync_mod.fill_amv_0day("2026-07-17", -9.99, tmp_path) is False
        assert json.loads(p.read_text(encoding="utf-8"))["amv_0day"] == -5.84

    def test_unconfirmed_overwritten(self, tmp_path: Path) -> None:
        p = self._market(tmp_path, amv_0day=-1.0,
                         amv_0={"amv_change_pct": None, "quality": "candidate"})
        assert sync_mod.fill_amv_0day("2026-07-17", -5.84, tmp_path) is True
        assert json.loads(p.read_text(encoding="utf-8"))["amv_0day"] == -5.84


class TestMainDegradation:
    def test_parse_error_exits_zero(self, tmp_path: Path, monkeypatch, capsys) -> None:
        monkeypatch.setattr(sync_mod.compass_amv, "parse_amv_daily",
                            lambda **kw: {"source": "compass_day_vdat", "path": "x",
                                          "count": 0, "first_date": None, "latest_date": None,
                                          "records": [], "error": "parse_failed: PermissionError"})
        monkeypatch.setattr(sync_mod, "LEDGER", tmp_path / "ledger.jsonl")
        assert sync_mod.main(["--date", "2026-07-17"]) == 0
        out = capsys.readouterr().out
        assert "[WARN]" in out
        summary = json.loads(out.strip().splitlines()[-1])
        assert summary["added"] == 0
        assert summary["error"].startswith("parse_failed")
        assert not (tmp_path / "ledger.jsonl").exists()

    def test_success_merges_and_prints_summary(self, tmp_path: Path, monkeypatch, capsys) -> None:
        recs = _records()
        monkeypatch.setattr(sync_mod.compass_amv, "parse_amv_daily",
                            lambda **kw: {"source": "compass_day_vdat", "path": "x",
                                          "count": 3, "first_date": "2026-07-15",
                                          "latest_date": "2026-07-17", "records": recs})
        monkeypatch.setattr(sync_mod, "LEDGER", tmp_path / "ledger.jsonl")
        monkeypatch.setattr(sync_mod, "MARKET_DIR", tmp_path)
        # 非交易日（周日）→ 不做 amv_0day 填充
        assert sync_mod.main(["--date", "2026-07-19"]) == 0
        out = capsys.readouterr().out
        summary = json.loads(out.strip().splitlines()[-1])
        assert summary == {"added": 3, "skipped_existing": 0,
                           "amv_0day_filled": False, "latest_date": "2026-07-17"}
        assert len((tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()) == 3
