# -*- coding: utf-8 -*-
"""runtime_gate 退出码测试——门控必须能真正阻断，不能只写 JSON。"""
from __future__ import annotations

import runtime_gate as rg


def _gate(trading=True, quality="pass", position="pass"):
    return {"calendar": {"is_trading_day": trading},
            "market_quality": {"status": quality, "quality_score": 0.9},
            "position_gate": {"status": position}}


def test_all_pass_returns_zero():
    assert rg.decide_exit_code(_gate(), require_trading_day=True,
                              require_quality=True, require_position_gate=True) == 0


def test_non_trading_day_exits_3_only_when_required():
    g = _gate(trading=False)
    assert rg.decide_exit_code(g) == 0
    assert rg.decide_exit_code(g, require_trading_day=True) == rg.EXIT_NOT_TRADING_DAY


def test_quality_blocked_exits_4_only_when_required():
    g = _gate(quality="blocked")
    assert rg.decide_exit_code(g) == 0                      # 未开开关时行为不变(向后兼容)
    assert rg.decide_exit_code(g, require_quality=True) == rg.EXIT_QUALITY_BLOCKED


def test_quality_degraded_does_not_block():
    """degraded 只降权限文案，不阻断——盘中缺盘后指标属正常。"""
    assert rg.decide_exit_code(_gate(quality="degraded"), require_quality=True) == 0


def test_position_gate_blocked_exits_5_only_when_required():
    g = _gate(position="blocked")
    assert rg.decide_exit_code(g, require_quality=True) == 0
    assert rg.decide_exit_code(g, require_position_gate=True) == rg.EXIT_POSITION_BLOCKED


def test_priority_trading_day_over_quality():
    g = _gate(trading=False, quality="blocked", position="blocked")
    assert rg.decide_exit_code(g, require_trading_day=True, require_quality=True,
                              require_position_gate=True) == rg.EXIT_NOT_TRADING_DAY


def test_missing_sections_are_safe():
    assert rg.decide_exit_code({}, require_quality=True, require_position_gate=True) == 0
    assert rg.decide_exit_code({}, require_trading_day=True) == rg.EXIT_NOT_TRADING_DAY


def test_main_writes_gate_and_returns_code(monkeypatch, capsys):
    monkeypatch.setattr(rg, "write_runtime_gate", lambda day: _gate(quality="blocked"))
    rc = rg.main(["--date", "2026-07-20", "--require-quality"])
    out = capsys.readouterr()
    assert rc == rg.EXIT_QUALITY_BLOCKED
    assert "market_quality" in out.out and "阻断" in out.err
