# -*- coding: utf-8 -*-
"""Tests for screening.formula_screen degrade paths (mocked TQ, no TdxW needed)."""
import formula_screen


def _registry(n=2):
    return {
        "version": "test",
        "universe": {"exclude_st": True, "exclude_bj": True, "min_list_days": 60},
        "formulas": [
            {"id": f"F{i}", "tq_name": f"FAKE{i}", "args": "", "stock_period": "1d",
             "enabled": True, "category": "test", "note": ""}
            for i in range(n)
        ],
    }


def _ok(value):
    return {"ok": True, "value": value, "error": None}


def _err(code):
    return {"ok": False, "value": None, "error": {"code": code, "detail": "x"}}


STOCKS = ["600000", "000001"]


def test_tdxw_not_running_degrades_cleanly():
    result = formula_screen.screen_formulas(
        "2026-07-21", registry=_registry(), stock_list=STOCKS,
        running_check=lambda: False,
    )
    assert result["status"] == "unavailable"
    assert result["degraded_reason"] == "tdxw_not_running"
    assert len(result["formulas"]) == 2
    assert all(f["hits"] == [] for f in result["formulas"])


def test_tq_error_id_recorded_and_partial():
    calls = iter([_ok({"600000.SH": {"UP3": ["0", "1"]}}), _err("tq_error")])
    result = formula_screen.screen_formulas(
        "2026-07-21", registry=_registry(), stock_list=STOCKS,
        call=lambda *a, **k: next(calls), running_check=lambda: True,
    )
    assert result["status"] == "partial"
    assert result["formulas"][0]["error"] is None
    assert result["formulas"][1]["error"] == "tq_error"


def test_circuit_breaker_after_two_consecutive_failures():
    def always_fail(*a, **k):
        return _err("timeout")

    result = formula_screen.screen_formulas(
        "2026-07-21", registry=_registry(n=3), stock_list=STOCKS,
        call=always_fail, running_check=lambda: True,
    )
    errors = [f["error"] for f in result["formulas"]]
    assert errors == ["timeout", "timeout", "circuit_open_skipped"]
    assert result["status"] == "unavailable"
    assert result["degraded_reason"] == "all_formulas_failed"


def test_hit_extraction_last_bar_only():
    value = {
        "600000.SH": {"UP3": ["0", "1"]},   # 当日命中
        "000001.SZ": {"UP3": ["1", "0"]},   # 昨日命中当日未命中 → 不算
    }
    result = formula_screen.screen_formulas(
        "2026-07-21", registry=_registry(n=1), stock_list=STOCKS,
        name_map={"600000": "浦发银行"},
        call=lambda *a, **k: _ok(value), running_check=lambda: True,
    )
    assert result["status"] == "ok"
    hits = result["formulas"][0]["hits"]
    assert hits == [{"code": "600000", "name": "浦发银行", "signal_date": "2026-07-21"}]


def test_empty_universe_degrades():
    result = formula_screen.screen_formulas(
        "2026-07-21", registry=_registry(), stock_list=[],
        running_check=lambda: True,
    )
    assert result["status"] == "unavailable"
    assert result["degraded_reason"] == "universe_unavailable"
