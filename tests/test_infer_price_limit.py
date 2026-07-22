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
    @pytest.mark.parametrize("code", ["300750", "301269", "688981", "920808"])
    def test_quiet_20pct_prefix_stays_20(self, code):
        # 修复前：20% 品种近 20 日波动 <=5.2 会被错误降级为 5
        assert _infer_price_limit(code, _df(QUIET_20)) == 20

    @pytest.mark.parametrize("code", ["600519", "000001"])
    def test_quiet_10pct_prefix_demotes_to_5(self, code):
        assert _infer_price_limit(code, _df(QUIET_20)) == 5

    def test_10pct_prefix_with_big_move_upgrades_to_20(self):
        df = _df(QUIET_20[:10] + [12.0] + QUIET_20[10:])
        assert _infer_price_limit("600519", df) == 20

    def test_short_history_uses_prefix(self):
        assert _infer_price_limit("300750", _df([1.0] * 5)) == 20
        assert _infer_price_limit("600519", _df([1.0] * 5)) == 10
