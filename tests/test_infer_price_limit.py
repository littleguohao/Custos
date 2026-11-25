# -*- coding: utf-8 -*-
"""Tests for technical_monitor._infer_price_limit (ST downgrade ordering)."""

from __future__ import annotations

import pandas as pd
import pytest

from custos.pipeline.market_timing.technical_monitor import _infer_price_limit


def _df(pct_changes, start=10.0):
    """Build a close-price df from a list of daily pct changes."""
    closes = [start]
    for pct in pct_changes:
        closes.append(closes[-1] * (1 + pct / 100))
    return pd.DataFrame({"close": closes})


QUIET_20 = [1.0, -1.0] * 10  # 20 日最大 |涨跌幅| = 1% <= 5.2


class TestStDowngradeOnlyForTenPercentPrefix:
    @pytest.mark.parametrize(
        "code,want",
        [
            ("300750", 20),
            ("301269", 20),
            ("688981", 20),
            ("689009", 20),
            ("920808", 30),
            ("830799", 30),
        ],
    )
    def test_quiet_wide_limit_prefix_not_demoted(self, code, want):
        """安静窗口不得把宽幅品种降级为 5%（原意），期望值按真实限制。

        ⚠️ 2026-08-07：原本这条参数化里 `920808` 期望 **20**，**把一个 bug 锁死了**
        —— 北交所是 **30%**。同时补上此前完全没测的 `689`（科创板 CDR）与
        `830799`（北交所老前缀，此前连 20 都拿不到、只有 10）。
        见 `code_utils.price_limit_pct`。
        """
        assert _infer_price_limit(code, _df(QUIET_20)) == want

    @pytest.mark.parametrize("code", ["600519", "000001"])
    def test_quiet_10pct_prefix_not_demoted_v0590(self, code):
        """v0.590（owner 2026-11-25）：有代码只看前缀；安静窗口不再降成 ST 5%（主板 J<13 样本 50% 被误降）。"""
        assert _infer_price_limit(code, _df(QUIET_20)) == 10

    def test_10pct_prefix_with_limit_up_not_upgraded_v0590(self):
        """主板一个涨停（+10%）不再把限幅升成 20%（原 >9.9 即升级，主板 J<13 样本 24% 被误升）。"""
        df = _df(QUIET_20[:10] + [10.0] + QUIET_20[10:])
        assert _infer_price_limit("600519", df) == 10

    def test_no_code_infers_from_data_v0590(self):
        assert _infer_price_limit("", _df(QUIET_20)) == 10
        assert _infer_price_limit("", _df(QUIET_20[:10] + [19.9] + QUIET_20[10:])) == 20

    def test_short_history_uses_prefix(self):
        assert _infer_price_limit("300750", _df([1.0] * 5)) == 20
        assert _infer_price_limit("600519", _df([1.0] * 5)) == 10


class TestSelfCorrectWindowIsRecent20:
    """数据自纠只看**最近 20 个交易日**（docstring 一直写「近20日」，实现曾取整条序列）。

    2026-08-08 前：10% 板块新股的窗口含上市首日 +44% 时，max_change 被永久顶穿 9.9
    ⇒ 该股**永久**升级为 20% 口径，首日异动滚出窗口也回不去。
    """

    def test_ipo_first_day_spike_rolls_out_of_window(self):
        """上市首日 +44% 已滚出近 20 日窗口 ⇒ 不再升级为 20%。

        窗口内放一天 +6%（>5.2、<9.9）是为了**隔离升级与降级两条分支**：
        全安静的窗口会走 ST 降级（==5），断言 ==10 就分不清测的是哪条。
        """
        changes = [44.0] + [1.0, -1.0] * 10 + [6.0]
        assert len(changes) == 22 and max(changes[-20:]) == 6.0  # 首日已出窗
        assert _infer_price_limit("600519", _df(changes)) == 10

    def test_big_move_inside_window_upgrades_only_without_code(self):
        """v0.590：+44% 落在窗口内也只在**没有代码**时升级；有代码以前缀为准。"""
        changes = [1.0, -1.0] * 9 + [44.0] + [1.0]
        assert _infer_price_limit("600519", _df(changes)) == 10
        assert _infer_price_limit("", _df(changes)) == 20
