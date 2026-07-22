# -*- coding: utf-8 -*-
"""Tests for the 0AMV regime state machine (market_timing/amv_state.py).

Locks the documented contract: only quality == "confirmed" readings drive
regime transitions; a candidate >+4% reading must not break the 空头 lock.
"""
from __future__ import annotations

import json

import pytest

import amv_state


@pytest.fixture()
def env(tmp_path, monkeypatch):
    market = tmp_path / "market"
    market.mkdir()
    monkeypatch.setattr(amv_state, "MARKET", market)
    monkeypatch.setattr(amv_state, "STATE", market / "0amv_regime_history.json")
    monkeypatch.setattr(amv_state, "LEDGER", market / "0amv_observations.jsonl")
    return market


def _feed(market, day, value, quality="confirmed"):
    (market / f"{day}_market_timing_input.json").write_text(
        json.dumps({"amv_0": {"amv_change_pct": value, "quality": quality}},
                   ensure_ascii=False),
        encoding="utf-8")
    return amv_state.compute(day)


class TestConfirmedTransitions:
    def test_confirmed_drop_enters_bear(self, env):
        rec = _feed(env, "2026-07-01", -5.84, "confirmed")
        assert rec["effective_state"] == "空头"
        assert rec["confirmed"] is True

    def test_confirmed_surge_breaks_bear_lock(self, env):
        _feed(env, "2026-07-01", -5.84, "confirmed")
        rec = _feed(env, "2026-07-02", 4.5, "confirmed")
        assert rec["effective_state"] == "做多"

    def test_between_thresholds_keeps_bear_lock(self, env):
        _feed(env, "2026-07-01", -5.84, "confirmed")
        rec = _feed(env, "2026-07-02", 3.9, "confirmed")
        assert rec["effective_state"] == "空头"
        assert "锁定" in rec["transition_reason"]


class TestUnconfirmedReadingsDoNotDriveTransitions:
    def test_candidate_surge_does_not_break_bear_lock(self, env):
        _feed(env, "2026-07-01", -5.84, "confirmed")
        rec = _feed(env, "2026-07-02", 6.0, "candidate")
        assert rec["effective_state"] == "空头"
        assert rec["confirmed"] is False
        assert "未确认" in rec["transition_reason"]

    def test_candidate_drop_does_not_flip_bull(self, env):
        _feed(env, "2026-07-01", 5.0, "confirmed")
        rec = _feed(env, "2026-07-02", -6.0, "candidate")
        assert rec["effective_state"] == "做多"

    def test_missing_quality_treated_as_unconfirmed(self, env):
        _feed(env, "2026-07-01", -5.84, "confirmed")
        (env / "2026-07-02_market_timing_input.json").write_text(
            json.dumps({"amv_0": {"amv_change_pct": 6.0}}, ensure_ascii=False),
            encoding="utf-8")
        rec = amv_state.compute("2026-07-02")
        assert rec["effective_state"] == "空头"

    def test_unconfirmed_without_prior_goes_neutral(self, env):
        rec = _feed(env, "2026-07-01", 6.0, "candidate")
        assert rec["effective_state"] == "中性"


class TestMissingValue:
    def test_none_keeps_prior(self, env):
        _feed(env, "2026-07-01", -5.84, "confirmed")
        (env / "2026-07-02_market_timing_input.json").write_text(
            json.dumps({"amv_0": {}}, ensure_ascii=False), encoding="utf-8")
        rec = amv_state.compute("2026-07-02")
        assert rec["effective_state"] == "空头"
        assert "缺值" in rec["transition_reason"]
