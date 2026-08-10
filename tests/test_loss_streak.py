# -*- coding: utf-8 -*-
"""同股连亏统计（`close_review/loss_streak.py`）。

owner 2026-08-10 定：连亏冷却**放在复盘环节**，每日/每周统计并判断。
⇒ 本模块只产出事实，不拦交易（自动链里 `buy_actions` 恒空，没有买入决策可拦）。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "07_tools"))
sys.path.insert(0, str(ROOT / "07_tools" / "close_review"))

from close_review import loss_streak as ls  # noqa: E402


def closing(code, sell_date, net_pnl, *, status="full", name="甲"):
    return {"code": code, "name": name, "sell_date": sell_date,
            "net_pnl": net_pnl, "match_status": status}


class TestStreakCounting:
    def test_two_consecutive_losses_flagged(self):
        r = ls.loss_streaks([closing("600000", "2026-08-03", -500),
                             closing("600000", "2026-08-06", -300)])
        assert r["flagged"] == ["600000"]
        assert r["streaks"]["600000"]["count"] == 2
        assert r["streaks"]["600000"]["total_net_pnl"] == -800
        assert r["streaks"]["600000"]["last_sell_date"] == "2026-08-06"

    def test_single_loss_not_flagged(self):
        r = ls.loss_streaks([closing("600000", "2026-08-06", -300)])
        assert r["flagged"] == []
        assert r["streaks"]["600000"]["count"] == 1, "仍要记录，只是不达阈值"

    def test_a_win_resets_the_streak(self):
        """⚠️ 「连续」的原意：被任何一次盈利打断即归零。

        不是「历史累计亏损次数」—— 那会让一只长期做得不错的票因为几年前的
        两次亏损被永久标记。
        """
        r = ls.loss_streaks([closing("600000", "2026-08-01", -500),
                             closing("600000", "2026-08-03", -400),
                             closing("600000", "2026-08-05", +900),   # 打断
                             closing("600000", "2026-08-07", -100)])
        assert r["streaks"]["600000"]["count"] == 1
        assert r["flagged"] == [], "盈利之后只剩 1 次亏损，不该仍被标"

    def test_streak_is_the_most_recent_segment(self):
        r = ls.loss_streaks([closing("600000", "2026-08-01", -100),
                             closing("600000", "2026-08-02", +50),
                             closing("600000", "2026-08-03", -200),
                             closing("600000", "2026-08-04", -300),
                             closing("600000", "2026-08-05", -400)])
        v = r["streaks"]["600000"]
        assert v["count"] == 3
        assert v["sell_dates"] == ["2026-08-03", "2026-08-04", "2026-08-05"]

    def test_zero_pnl_breaks_streak(self):
        """净盈亏恰好 0 不算亏损 —— 判据是 `< 0`，不是 `<= 0`。"""
        r = ls.loss_streaks([closing("600000", "2026-08-01", -100),
                             closing("600000", "2026-08-02", 0.0),
                             closing("600000", "2026-08-03", -200)])
        assert r["streaks"]["600000"]["count"] == 1

    def test_out_of_order_input_is_sorted_by_sell_date(self):
        """入参顺序不可靠（可能来自多个来源），必须按卖出日排序后再判连续。"""
        r = ls.loss_streaks([closing("600000", "2026-08-05", -400),
                             closing("600000", "2026-08-01", -100),
                             closing("600000", "2026-08-03", +50)])
        assert r["streaks"]["600000"]["count"] == 1, \
            "排序后最近一段只有 08-05 一次亏损"

    def test_multiple_codes_sorted_by_count_desc(self):
        r = ls.loss_streaks([closing("600000", "2026-08-01", -1), closing("600000", "2026-08-02", -1),
                             closing("000001", "2026-08-01", -1), closing("000001", "2026-08-02", -1),
                             closing("000001", "2026-08-03", -1)])
        assert r["flagged"] == ["000001", "600000"], "连亏多的排前面"


class TestExclusionsAreHonest:
    """⚠️ 口径与 `weekly_review` 判胜率时一致 —— 不另立一套。"""

    def test_partial_match_excluded_and_counted(self):
        """`partial` 的 gross/net 只覆盖已配平部分、**系统性少算**
        ⇒ 拿它判盈亏会把赚的算成亏的。必须排除**且如实计数**。"""
        r = ls.loss_streaks([closing("600000", "2026-08-01", -500, status="partial"),
                             closing("600000", "2026-08-02", -300, status="partial")])
        assert r["flagged"] == []
        assert r["streaks"] == {}
        assert r["excluded"]["partial"] == 2

    def test_none_match_excluded(self):
        r = ls.loss_streaks([closing("600000", "2026-08-01", None, status="none")])
        assert r["excluded"]["none"] == 1

    def test_missing_net_pnl_excluded_not_guessed(self):
        """`net_pnl` 缺失时该单不计入 —— 不拿 gross 顶替、不当成 0。"""
        r = ls.loss_streaks([closing("600000", "2026-08-01", None)])
        assert r["excluded"]["no_net_pnl"] == 1
        assert r["streaks"] == {}

    def test_exclusions_surface_in_report_lines(self):
        """⚠️ 被排除的单子必须出现在报告里 —— 否则结论建立在残缺台账上而读者不知道。"""
        r = ls.loss_streaks([closing("600000", "2026-08-01", -500, status="partial")])
        text = "\n".join(ls.format_lines(r))
        assert "未计入" in text and "残缺台账" in text


class TestReportFormatting:
    def test_no_hit_still_prints_a_line(self):
        """⚠️ 无命中时出「无连亏」而不是整节消失 ——
        节消失读者分不清「查了没有」与「没查」。"""
        text = "\n".join(ls.format_lines(ls.loss_streaks([])))
        assert "无同股连亏" in text
        assert text.strip().startswith("### ")

    def test_hit_renders_table_with_evidence(self):
        r = ls.loss_streaks([closing("600000", "2026-08-03", -500, name="浦发银行"),
                             closing("600000", "2026-08-06", -300, name="浦发银行")])
        text = "\n".join(ls.format_lines(r))
        for token in ("600000", "浦发银行", "2026-08-06", "-800"):
            assert token in text, token


class TestReusesFifoPair:
    """⚠️ 配平必须复用 `weekly_review.fifo_pair`，不得自己再写一遍 FIFO。

    「持仓/盈亏推导逻辑只有一份」是既有不变量（同 `reconcile_positions` 的约束）——
    两份实现就是在比两个都可能错的东西。
    """

    def test_no_local_fifo_implementation(self):
        src = (ROOT / "07_tools" / "close_review" / "loss_streak.py").read_text(encoding="utf-8")
        for bad in ("open_lots", "matched_qty", "'买入'", '"买入"'):
            assert bad not in src, f"loss_streak 里出现 {bad!r} —— 疑似自己实现了配平"

    def test_end_to_end_via_fifo_pair(self):
        """用 `fifo_pair` 的真实输出驱动一次 —— 证明字段名对得上。

        ⚠️ 这条是必须的：`loss_streak` 读的是 `match_status`/`net_pnl`/`sell_date`/`code`，
        任一字段被 `fifo_pair` 改名，上面所有测试仍会通过（它们用的是手写字典），
        而生产会静默算不出连亏。
        """
        from close_review import weekly_review as wr

        BUY, SELL = wr.BUY, wr.SELL
        # ⚠️ `amount`（成交金额）是 `fifo_pair` 的必需字段 —— 第一版漏了它，
        #    KeyError('amount')。这正是本条测试的价值：手写字典的那些测试
        #    永远发现不了「入参形状对不上」。
        def t(side, qty, price, date):
            return {"code": "600000", "name": "浦发银行", "side": side, "qty": qty,
                    "price": price, "date": date, "fee": 5.0, "amount": qty * price}

        trades = [t(BUY, 100, 10.0, "2026-08-01"), t(SELL, 100, 9.0, "2026-08-03"),
                  t(BUY, 100, 9.5, "2026-08-05"), t(SELL, 100, 9.0, "2026-08-07")]
        closings = wr.fifo_pair(trades)
        assert closings, "fifo_pair 没产出平仓单 —— 入参形状变了？"
        assert all("match_status" in c and "net_pnl" in c for c in closings), \
            "fifo_pair 的字段名变了，loss_streak 读不到"
        r = ls.loss_streaks(closings)
        assert r["flagged"] == ["600000"], f"两次亏损平仓应被标，实际 {r}"
        assert r["streaks"]["600000"]["count"] == 2
