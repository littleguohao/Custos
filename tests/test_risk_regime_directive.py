# -*- coding: utf-8 -*-
from __future__ import annotations

import json

import generate_risk_and_sectors as grs


def _write_market_input(tmp_path, date, effective_state):
    market_dir = tmp_path / "market"
    market_dir.mkdir(parents=True, exist_ok=True)
    (market_dir / f"{date}_market_timing_input.json").write_text(
        json.dumps({"amv_0": {"effective_state": effective_state}}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_regime_directive_in_bear_regime(tmp_path, monkeypatch):
    monkeypatch.setattr(grs, "DATA", tmp_path)
    _write_market_input(tmp_path, "2026-07-17", "空头")
    result = grs.build_risk_decision("2026-07-17")
    assert result["market_regime"] == "空头"
    assert result["regime_directive"] == {
        "reduce_top_priority": True,
        "allow_add": False,
        "note": "0AMV空头区间:降低仓位为最高优先级,任何反弹都是卖出机会",
    }


def test_regime_directive_in_non_bear_regime(tmp_path, monkeypatch):
    monkeypatch.setattr(grs, "DATA", tmp_path)
    _write_market_input(tmp_path, "2026-07-10", "做多")
    result = grs.build_risk_decision("2026-07-10")
    assert result["market_regime"] == "做多"
    assert result["regime_directive"] == {"reduce_top_priority": False}


def test_regime_defaults_to_unknown_when_input_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(grs, "DATA", tmp_path)
    result = grs.build_risk_decision("2026-07-10")
    assert result["market_regime"] == "未知"
    assert result["regime_directive"] == {"reduce_top_priority": False}
