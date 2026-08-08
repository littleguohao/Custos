# -*- coding: utf-8 -*-
"""Tests for technical_monitor._infer_price_limit (ST downgrade ordering)."""
from __future__ import annotations

import pandas as pd
import pytest

from market_timing.technical_monitor import _infer_price_limit


def _df(pct_changes, start=10.0):
    """Build a close-price df from a list of daily pct changes."""
    closes = [start]
    for pct in pct_changes:
        closes.append(closes[-1] * (1 + pct / 100))
    return pd.DataFrame({"close": closes})


QUIET_20 = [1.0, -1.0] * 10  # 20 日最大 |涨跌幅| = 1% <= 5.2


class TestStDowngradeOnlyForTenPercentPrefix:
    @pytest.mark.parametrize("code,want", [("300750", 20), ("301269", 20),
                                           ("688981", 20), ("689009", 20),
                                           ("920808", 30), ("830799", 30)])
    def test_quiet_wide_limit_prefix_not_demoted(self, code, want):
        """安静窗口不得把宽幅品种降级为 5%（原意），期望值按真实限制。

        ⚠️ 2026-08-07：原本这条参数化里 `920808` 期望 **20**，**把一个 bug 锁死了**
        —— 北交所是 **30%**。同时补上此前完全没测的 `689`（科创板 CDR）与
        `830799`（北交所老前缀，此前连 20 都拿不到、只有 10）。
        见 `code_utils.price_limit_pct`。
        """
        assert _infer_price_limit(code, _df(QUIET_20)) == want

    @pytest.mark.parametrize("code", ["600519", "000001"])
    def test_quiet_10pct_prefix_demotes_to_5(self, code):
        assert _infer_price_limit(code, _df(QUIET_20)) == 5

    def test_10pct_prefix_with_big_move_upgrades_to_20(self):
        df = _df(QUIET_20[:10] + [12.0] + QUIET_20[10:])
        assert _infer_price_limit("600519", df) == 20

    def test_short_history_uses_prefix(self):
        assert _infer_price_limit("300750", _df([1.0] * 5)) == 20
        assert _infer_price_limit("600519", _df([1.0] * 5)) == 10
