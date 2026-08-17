# -*- coding: utf-8 -*-
"""止损冷却名单 + 胜率降仓提示（`close_review/cooldowns.py`，TODO #51）。

owner 2026-08-12 定：冷却机制（原 #31 + #51②）落复盘报告节，**只提示不拦截**
（自动链没有仓位/买入决策可拦）。
"""

from __future__ import annotations

import pathlib

import pytest

from custos.pipeline.close_review import cooldowns as cd
from custos.pipeline.close_review import weekly_review as wr


def closing(code, sell_date, pnl_pct, *, status="full", name="甲"):
    return {
        "code": code,
        "name": name,
        "sell_date": sell_date,
        "pnl_pct": pnl_pct,
        "match_status": status,
    }


def weekday_calendar(day: str) -> dict:
    """测试用日历：周一~周五=交易日（不查节假日，够用且确定）。"""
    import datetime as _dt

    d = _dt.date.fromisoformat(day)
    return {"is_trading_day": d.weekday() < 5}


def run(closings, as_of, **kw):
    kw.setdefault("day_status", weekday_calendar)
    return cd.stop_cooldowns(closings, as_of=as_of, **kw)


class TestThresholdPinned:
    def test_matches_weekly_review_stop_loss_pct(self):
        """止损判据阈值必须与 weekly_review 的止损线同值——不另立口径。"""
        assert cd.STOP_COOLDOWN_THRESHOLD_PCT == wr.STOP_LOSS_PCT == -7.0


class TestCooldownMembership:
    def test_stop_close_enters_cooldown(self):
        r = run([closing("600000", "2026-08-03", -8.5)], "2026-08-05")
        assert r["active"] == ["600000"]
        assert r["stops"]["600000"]["pnl_pct"] == -8.5

    def test_shallow_loss_not_a_stop(self):
        """−7% 线以下才算止损平仓；浅亏不进冷却。"""
        r = run([closing("600000", "2026-08-03", -6.9)], "2026-08-05")
        assert r["active"] == [] and r["stops"] == {}

    def test_win_not_a_stop(self):
        r = run([closing("600000", "2026-08-03", 12.0)], "2026-08-05")
        assert r["active"] == []

    def test_partial_and_none_excluded_and_reported(self):
        """partial/none 的盈亏不可信 ⇒ 不计入，但必须如实报 excluded。"""
        r = run(
            [
                closing("600000", "2026-08-03", -9.0, status="partial"),
                closing("000001", "2026-08-03", -9.0, status="none"),
                closing("300750", "2026-08-03", None),
            ],
            "2026-08-05",
        )
        assert r["active"] == []
        # v0.53 起 excluded 增 nan_pnl/bad_date 两键（目标机 review 实测缺陷）
        assert {k: r["excluded"][k] for k in ("partial", "none", "no_pnl_pct")} == {
            "partial": 1,
            "none": 1,
            "no_pnl_pct": 1,
        }

    def test_latest_stop_determines_cooldown(self):
        """同一票多次止损，冷却期从**最近一次**止损平仓日起算。"""
        r = run(
            [
                closing("600000", "2026-07-01", -9.0),
                closing("600000", "2026-08-03", -8.0),
            ],
            "2026-08-05",
        )
        assert r["stops"]["600000"]["last_stop_date"] == "2026-08-03"


class TestCooldownWindow:
    """冷却期 = 止损平仓后 10 个交易日（system_principles 既有约定）。"""

    def test_inside_window_active(self):
        # 2026-08-03(周一) 止损；第 10 个交易日 = 2026-08-17(周一)
        r = run([closing("600000", "2026-08-03", -8.0)], "2026-08-14")
        assert r["active"] == ["600000"]
        assert r["stops"]["600000"]["cooldown_until"] == "2026-08-17"

    def test_last_day_still_active(self):
        r = run([closing("600000", "2026-08-03", -8.0)], "2026-08-17")
        assert r["active"] == ["600000"]

    def test_outside_window_removed(self):
        r = run([closing("600000", "2026-08-03", -8.0)], "2026-08-18")
        assert r["active"] == [], "冷却期外必须从名单移除"

    def test_unknown_calendar_keeps_warning_with_note(self):
        """日历全不确定 ⇒ 数不出截止日：保守视为仍在冷却，但如实标注。"""

        def unknown(_day: str) -> dict:
            return {"is_trading_day": None}

        r = run(
            [closing("600000", "2026-08-03", -8.0)], "2026-08-05", day_status=unknown
        )
        v = r["stops"]["600000"]
        assert v["cooldown_until"] is None and v["cooldown_until_unknown"] is True
        assert r["active"] == ["600000"], "宁可警告多留，不提前消失"


class TestFormatLines:
    def test_no_stops_says_so_not_silent(self):
        lines = cd.format_cooldown_lines(run([], "2026-08-05"))
        assert any("无止损冷却中的票" in ln for ln in lines)

    def test_unavailable_not_claimed_empty(self):
        lines = cd.format_cooldown_lines({"available": False, "reason": "台账缺失"})
        assert any("unavailable" in ln and "不等于" in ln for ln in lines)

    def test_watch_hit_prompts(self):
        """冷却期内的票出现在 watch（如当日在持）⇒ 追加提示行。"""
        r = run([closing("600000", "2026-08-03", -8.0)], "2026-08-05")
        lines = cd.format_cooldown_lines(r, watch={"600000": "当日在持"})
        assert any("600000（当日在持）" in ln for ln in lines)
        lines2 = cd.format_cooldown_lines(r, watch={"000001": "当日在持"})
        assert not any("当日在持）" in ln for ln in lines2), "不在名单的票不该被点"

    def test_calendar_unknown_note_rendered(self):
        def unknown(_day: str) -> dict:
            return {"is_trading_day": None}

        r = run(
            [closing("600000", "2026-08-03", -8.0)], "2026-08-05", day_status=unknown
        )
        lines = cd.format_cooldown_lines(r)
        assert any("无法确定（日历缺失）" in ln for ln in lines)


class TestWinRateCheck:
    def test_below_threshold_flags(self):
        r = cd.win_rate_check(30.0)
        assert r["available"] and r["below"] is True
        lines = cd.format_win_rate_lines(r)
        assert any("提示降低短线仓位" in ln for ln in lines)

    def test_at_or_above_threshold_quiet(self):
        r = cd.win_rate_check(35.0)
        assert r["below"] is False, "35% 本身不算低于阈值"
        lines = cd.format_win_rate_lines(r)
        assert any("未低于" in ln for ln in lines)

    def test_none_is_unavailable(self):
        r = cd.win_rate_check(None)
        assert r["available"] is False
        lines = cd.format_win_rate_lines(r)
        assert any("unavailable" in ln for ln in lines)


class TestBadLedgerRows:
    """目标机 review 实测（2026-08-13）：台账坏行不得炸掉三份复盘报告，
    也不得静默吞——跳过并计数上报。"""

    def test_non_iso_date_skipped_and_counted(self):
        """非 ISO 日期（如「2026/08/03」或空串）以前会把 _cooldown_until 的
        fromisoformat 直接打炸 ⇒ 三份复盘全挂。"""
        r = run(
            [
                closing("600000", "2026-08-03", -8.0),
                closing("000001", "2026/08/04", -9.0),  # 非 ISO
                closing("300750", "", -9.0),  # 空日期
            ],
            "2026-08-05",
        )
        assert r["active"] == ["600000"], "正常行不受影响"
        assert r["excluded"]["bad_date"] == 2
        lines = cd.format_cooldown_lines(r)
        assert any("日期无法解析 2" in ln for ln in lines), "跳过必须计数上报"

    def test_basic_format_date_rejected(self):
        """2026-08-16 review 修复：3.11+ 的 fromisoformat 接受基本格式
        「20260803」——它能过解析但会与「2026-08-03」字符串比较时混排 ⇒
        必须按扩展格式（带连字符）拒绝。"""
        r = run(
            [
                closing("600000", "2026-08-03", -8.0),
                closing("000001", "20260804", -9.0),  # 基本格式，非 YYYY-MM-DD
            ],
            "2026-08-05",
        )
        assert r["active"] == ["600000"]
        assert r["excluded"]["bad_date"] == 1

    def test_nan_pnl_not_in_cooldown_list(self):
        """NaN 比较恒 False ⇒ 不写防御会误入冷却名单（-7 判定挡不住它）。"""
        r = run(
            [
                closing("600000", "2026-08-03", float("nan")),
                closing("000001", "2026-08-03", "not-a-number"),
            ],
            "2026-08-05",
        )
        assert r["active"] == [] and r["stops"] == {}
        assert r["excluded"]["nan_pnl"] == 2
        lines = cd.format_cooldown_lines(r)
        assert any("pnl 非法(NaN) 2" in ln for ln in lines), "排除必须如实报"


class TestWinRateThresholdSingleSource:
    def test_monthly_review_uses_the_constant(self):
        """降仓阈值必须同源（同 STOP_LOSS_PCT 的钉法）——monthly_review 的
        「低于 35%」字面量已改引 cooldowns.WIN_RATE_REDUCE_THRESHOLD_PCT。"""
        src = (
            pathlib.Path(cd.__file__)
            .parent.joinpath("monthly_review.py")
            .read_text(encoding="utf-8")
        )
        assert "WIN_RATE_REDUCE_THRESHOLD_PCT" in src
        assert "win_rate < 35" not in src, "字面量 35 回潮了——改成引用常量"
