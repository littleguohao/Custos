# -*- coding: utf-8 -*-
"""Tests for refresh_eod_klines: batching logic and best-effort degradation."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from market_timing import refresh_eod_klines as rek


def _ok(value=None):
    return {"ok": True, "value": value, "error": None}


def _err(code="tq_error"):
    return {"ok": False, "value": None, "error": {"code": code}}


class TestLoadHoldingsCodes:
    def test_norm_codes(self, tmp_path):
        p = tmp_path / "current_positions.json"
        p.write_text(json.dumps([{"代码": "600150"}, {"代码": "920808"}, {"代码": "399006"}]),
                     encoding="utf-8")
        assert rek.load_holdings_codes(p) == ["600150.SH", "920808.BJ", "399006.SZ"]

    def test_missing_file(self, tmp_path):
        assert rek.load_holdings_codes(tmp_path / "nope.json") == []

    def test_invalid_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json", encoding="utf-8")
        assert rek.load_holdings_codes(p) == []


class TestBuildBatches:
    def test_two_batches_with_holdings(self):
        batches = rek.build_batches(["600150.SH", "920808.BJ"])
        assert len(batches) == 2
        assert batches[0]["name"] == "indices+880"
        assert batches[0]["stock_list"] == rek.INDEX_CODES + rek.BREADTH_880_CODES
        assert batches[1]["name"] == "holdings"
        assert batches[1]["stock_list"] == ["600150.SH", "920808.BJ"]

    def test_no_holdings_skips_second_batch(self):
        batches = rek.build_batches([])
        assert len(batches) == 1
        assert batches[0]["name"] == "indices+880"


class TestRefreshAll:
    def test_success_verified(self, monkeypatch):
        calls = []

        def fake_call(method, params):
            calls.append((method, params))
            return _ok({"Msg": "refresh kline cache success."})

        monkeypatch.setattr(rek, "verify_latest_date", lambda: "2026-07-20")
        summary = rek.refresh_all("2026-07-20", ["600150.SH"], call_fn=fake_call)
        assert summary["refreshed"] is True
        assert summary["verified"] is True
        assert summary["latest_date"] == "2026-07-20"
        assert len(calls) == 2
        assert all(m == "refresh_kline" for m, _ in calls)
        assert calls[0][1]["period"] == "1d"
        assert calls[1][1]["stock_list"] == ["600150.SH"]
        assert all(b["ok"] for b in summary["batches"])
        assert all("duration_sec" in b for b in summary["batches"])

    def test_stale_vipdoc_not_verified(self, monkeypatch):
        monkeypatch.setattr(rek, "verify_latest_date", lambda: "2026-07-17")
        summary = rek.refresh_all("2026-07-20", [], call_fn=lambda m, p: _ok())
        assert summary["refreshed"] is True
        assert summary["verified"] is False
        assert summary["latest_date"] == "2026-07-17"

    def test_tdxw_not_running_degrades(self, monkeypatch):
        verify_called = []

        def fake_call(method, params):
            return _err("tdxw_not_running")

        monkeypatch.setattr(rek, "verify_latest_date", lambda: verify_called.append(1) or None)
        summary = rek.refresh_all("2026-07-20", ["600150.SH"], call_fn=fake_call)
        assert summary["refreshed"] is False
        assert summary["verified"] is False
        assert summary["latest_date"] is None
        assert verify_called == []  # 失败时不做 vipdoc 抽查
        assert summary["batches"][0]["error"]["code"] == "tdxw_not_running"

    def test_partial_batch_failure(self, monkeypatch):
        def fake_call(method, params):
            return _ok() if "999999.SH" in params["stock_list"] else _err("timeout")

        monkeypatch.setattr(rek, "verify_latest_date", lambda: "2026-07-20")
        summary = rek.refresh_all("2026-07-20", ["600150.SH"], call_fn=fake_call)
        assert summary["refreshed"] is False
        assert summary["verified"] is False
        assert summary["batches"][0]["ok"] is True
        assert summary["batches"][1]["ok"] is False


class TestVerifyLatestDate:
    def test_datetime_column(self):
        df = pd.DataFrame({"date": [pd.Timestamp("2026-07-17"), pd.Timestamp("2026-07-20")]})
        assert rek.verify_latest_date(read_fn=lambda code: df) == "2026-07-20"

    def test_empty_df(self):
        assert rek.verify_latest_date(read_fn=lambda code: pd.DataFrame()) is None

    def test_read_raises(self):
        def boom(code):
            raise RuntimeError("vipdoc missing")

        assert rek.verify_latest_date(read_fn=boom) is None


class TestMain:
    def test_exit_zero_on_failure(self, monkeypatch, capsys):
        monkeypatch.setattr(rek.tq_http, "call", lambda m, p, **kw: _err("tdxw_not_running"))
        monkeypatch.setattr(rek, "load_holdings_codes", lambda: [])
        rc = rek.main(["--date", "2026-07-20"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[WARN]" in out
        summary = json.loads(out.strip().splitlines()[-1])
        assert summary["refreshed"] is False


class TestMarketIndicesStaleness:
    """refresh_market_indices 的过期判定：available 但 latest_date/as_of 早于 --date 也要刷新。"""

    def test_is_stale_formats(self):
        from market_timing import refresh_market_indices as rmi

        assert rmi._is_stale("20260717", "2026-07-20") is True
        assert rmi._is_stale("2026-07-17", "2026-07-20") is True
        assert rmi._is_stale("20260720", "2026-07-20") is False
        assert rmi._is_stale("2026-07-20", "2026-07-20") is False
        assert rmi._is_stale(None, "2026-07-20") is True
        assert rmi._is_stale("", "2026-07-20") is True
