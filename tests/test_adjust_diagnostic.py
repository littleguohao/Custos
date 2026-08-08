# -*- coding: utf-8 -*-
"""复权口径诊断的回归测试。

背景（90_research_summary.md:31/40 记录、至今未解决）：live 选股与默认回测都读
通达信 vipdoc `.day` = **未复权**；只有 `--data-source qlib/csv` 是前复权。
`get_adjusted_daily()` 有复权能力但从未被生产链或回测链调用。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.adjust_diagnostic import SPLIT_RATIOS, _risk_frac_stats, detect_gaps


def _mk(rows):
    a = np.array(rows, float)
    df = pd.DataFrame(a, columns=["open", "high", "low", "close"])
    df.insert(0, "date", pd.bdate_range("2025-01-01", periods=len(df)))
    df["volume"] = 5e5
    df["amount"] = df["close"] * 5e5
    return df


class TestSplitDetection:
    def test_recognizes_common_split_ratios(self):
        """10送2 及以上都要能识别并匹配到具体比例。"""
        for name, ratio in SPLIT_RATIOS.items():
            after = 20.0 * (1 - ratio)
            df = _mk([(20, 20.2, 19.8, 20)] * 5
                     + [(after, after * 1.02, after * 0.99, after * 1.01)])
            g = detect_gaps(df, thr=0.02)
            assert g, f"{name} 未检出"
            if abs(ratio) >= 0.11:
                assert g[0]["split_match"] == name, f"{name} 比例未匹配"

    def test_cash_dividend_not_matched_as_split(self):
        """现金分红除息幅度小，不该被误判成送转。"""
        df = _mk([(20, 20.2, 19.8, 20)] * 5 + [(19.5, 19.7, 19.4, 19.6)])
        g = detect_gaps(df, thr=0.02)
        assert len(g) == 1 and g[0]["split_match"] is None

    def test_normal_moves_not_flagged(self):
        df = _mk([(20, 20.3, 19.7, 20.1)] * 20)
        assert detect_gaps(df, thr=0.02) == []

    def test_survives_degenerate_input(self):
        assert detect_gaps(pd.DataFrame()) == []
        assert detect_gaps(_mk([(10, 10, 10, 10)])) == []
        zero = _mk([(0, 0, 0, 0), (10, 10, 10, 10)])
        detect_gaps(zero)                       # 不得因除零崩溃


class TestRiskFracContext:
    """核心洞察：B1 止损空间极小，连现金分红除息都能触发假止损。"""

    def test_narrow_stop_room_measured(self):
        rows = [(20, 20.15, 19.92, 20.05)] * 40      # 窄振幅（超卖贴低的典型形态）
        med, q25 = _risk_frac_stats({"000001": _mk(rows)})
        assert 0 < med < 0.02, f"止损空间应很窄，实测 {med:.2%}"

    def test_dividend_gap_exceeds_stop_room(self):
        """止损空间 < 除息幅度 ⇒ 假止损必然发生。这条是整个问题的要害。"""
        rows = [(20, 20.15, 19.92, 20.05)] * 40
        med, _ = _risk_frac_stats({"000001": _mk(rows)})
        div_gap = 0.022                               # 2.2% 现金分红除息
        assert div_gap > med, "构造前提：除息幅度要大于止损空间"
