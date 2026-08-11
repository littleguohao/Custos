# -*- coding: utf-8 -*-
"""runtime_gate 退出码测试——门控必须能真正阻断，不能只写 JSON。"""
from __future__ import annotations

from custos.core import runtime_gate as rg


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


def test_missing_sections_fail_closed():
    """门控自身坏掉(空 JSON / 字段拼错 / 文件截断)必须阻断,不能放行。

    此前这里断言 == 0 并取名 "are_safe",把 fail-open 当成了安全行为:
    `None == "blocked"` 为假,于是门控读不到任何状态却返回 0,cron 判定通过。
    风控组件的未知状态必须等于阻断。
    """
    assert rg.decide_exit_code({}, require_quality=True) == rg.EXIT_QUALITY_BLOCKED
    assert rg.decide_exit_code({}, require_position_gate=True) == rg.EXIT_POSITION_BLOCKED
    assert rg.decide_exit_code({}, require_trading_day=True) == rg.EXIT_NOT_TRADING_DAY
    # 开关全关时仍不阻断(向后兼容:未要求门控的链路不受影响)
    assert rg.decide_exit_code({}) == 0


def test_unknown_status_fails_closed():
    """状态是拼错的/新加的未知值时按阻断处理,而不是"不等于 blocked 就放行"。"""
    for bad in ("blockd", "unknown", "", None, "PASS"):
        assert rg.decide_exit_code({"market_quality": {"status": bad}},
                                   require_quality=True) == rg.EXIT_QUALITY_BLOCKED
        assert rg.decide_exit_code({"position_gate": {"status": bad}},
                                   require_position_gate=True) == rg.EXIT_POSITION_BLOCKED


def test_main_writes_gate_and_returns_code(monkeypatch, capsys):
    monkeypatch.setattr(rg, "write_runtime_gate",
                        lambda day, expected_day=None: _gate(quality="blocked"))
    rc = rg.main(["--date", "2026-07-20", "--require-quality"])
    out = capsys.readouterr()
    assert rc == rg.EXIT_QUALITY_BLOCKED
    assert "market_quality" in out.out and "阻断" in out.err


def test_main_preclose_session_uses_prev_trading_day(monkeypatch):
    calls = []
    monkeypatch.setattr(rg, "write_runtime_gate",
                        lambda day, expected_day=None: calls.append(expected_day) or _gate())
    monkeypatch.setattr("custos.core.runtime_guards.previous_confirmed_trading_day", lambda d: "2026-07-17")
    rg.main(["--date", "2026-07-20", "--data-session", "preclose"])
    rg.main(["--date", "2026-07-20"])                        # 默认 postclose
    assert calls == ["2026-07-17", None]                     # 盘前=T-1;盘后不传(=当日)
