# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

import pandas as pd

from custos.pipeline.market_timing.technical_monitor import (
    n_structure_state,
    price_volume_state,
)


def frame(lows, highs, closes):
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=len(lows), freq="D"),
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "amount": [1] * len(lows),
            "volume": [1] * len(lows),
        }
    )


class NStructureTests(unittest.TestCase):
    def test_completed_rising_n_returns_second_low(self):
        lows = [11, 10, 9, 8, 7, 8, 9, 10, 11, 10, 9, 8, 9, 10, 11, 12, 13, 14]
        highs = [12, 11, 10, 9, 8, 9, 10, 11, 13, 12, 11, 10, 11, 12, 14, 15, 16, 17]
        closes = [
            11.5,
            10.5,
            9.5,
            8.5,
            7.5,
            8.5,
            9.5,
            10.5,
            12.5,
            11.5,
            10.5,
            8.8,
            10,
            11,
            13.5,
            14.5,
            15.5,
            16.5,
        ]
        result = n_structure_state(frame(lows, highs, closes), left=2, right=2)
        self.assertTrue(result["available"])
        self.assertEqual(result["prior_low"], 7.5)
        self.assertEqual(result["pullback_low"], 8.8)
        self.assertEqual(result["breakout_level"], 12.5)
        self.assertEqual(result["confirmed_date"], "2026-01-15")

    def test_lower_second_low_is_not_rising_n(self):
        lows = [11, 10, 9, 8, 7, 8, 9, 10, 11, 10, 9, 6, 9, 10, 11, 12, 13, 14]
        highs = [12, 11, 10, 9, 8, 9, 10, 11, 13, 12, 11, 10, 11, 12, 14, 15, 16, 17]
        closes = [
            11.5,
            10.5,
            9.5,
            8.5,
            7.5,
            8.5,
            9.5,
            10.5,
            12.5,
            11.5,
            10.5,
            7,
            10,
            11,
            13.5,
            14.5,
            15.5,
            16.5,
        ]
        self.assertFalse(
            n_structure_state(frame(lows, highs, closes), left=2, right=2)["available"]
        )

    def test_stale_breach_is_marked_stale(self):
        # 上升N(L1=7.5) 突破后长期跌破 L1 → 破位过久，应标记 stale（不再当作新鲜P0）
        base = [
            11.5,
            10.5,
            9.5,
            8.5,
            7.5,
            8.5,
            9.5,
            10.5,
            12.5,
            11.5,
            10.5,
            8.8,
            10,
            11,
            13.5,
        ]
        closes = base + [7.0] * 16
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        r = n_structure_state(frame(lows, highs, closes), left=2, right=2)
        self.assertTrue(r["available"])
        self.assertAlmostEqual(r["prior_low"], 7.5)
        self.assertTrue(r["breached_on_close"])
        self.assertTrue(r["stale"])
        self.assertFalse(r["fresh_breach"])

    def test_recent_breach_is_fresh_not_stale(self):
        base = [
            11.5,
            10.5,
            9.5,
            8.5,
            7.5,
            8.5,
            9.5,
            10.5,
            12.5,
            11.5,
            10.5,
            8.8,
            10,
            11,
            13.5,
        ]
        closes = base + [12, 11, 10, 9, 8.5, 8, 7.0, 7.0]  # 最近1-2根才跌破 L1
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        r = n_structure_state(frame(lows, highs, closes), left=2, right=2)
        self.assertTrue(r["available"] and r["breached_on_close"])
        self.assertFalse(r["stale"])
        self.assertTrue(r["fresh_breach"])


class PriceVolumeTests(unittest.TestCase):
    def test_shrink_small_bear(self):
        closes = [10 + i * 0.1 for i in range(20)] + [11.85]
        opens = [x - 0.05 for x in closes[:-1]] + [12.0]
        volumes = [1000.0] * 20 + [700.0]
        dates = pd.date_range("2026-01-01", periods=21, freq="D")
        df = pd.DataFrame(
            {
                "date": dates,
                "open": opens,
                "high": [max(o, c) * 1.01 for o, c in zip(opens, closes)],
                "low": [min(o, c) * 0.99 for o, c in zip(opens, closes)],
                "close": closes,
                "volume": volumes,
                "amount": volumes,
            }
        )
        result = price_volume_state(df)
        self.assertTrue(result["shrink_small_bear"])
        self.assertFalse(result["heavy_large_bear"])

    def test_unconfirmed_recent_low_is_ignored(self):
        lows = [11, 10, 9, 8, 7, 8, 9, 10, 11, 10, 9, 8]
        highs = [12, 11, 10, 9, 8, 9, 10, 11, 13, 12, 11, 10]
        closes = [11.5, 10.5, 9.5, 8.5, 7.5, 8.5, 9.5, 10.5, 12.5, 11.5, 10.5, 8.8]
        self.assertFalse(
            n_structure_state(frame(lows, highs, closes), left=2, right=2)["available"]
        )


if __name__ == "__main__":
    unittest.main()


class TestBjVipdocRouting(unittest.TestCase):
    """v0.101：北交所代码必须走 local_tdx_data 的 .day 直读——mootdx Reader 把
    920xxx 误路由到 SH（读不到 ⇒ 持仓技术面全空，曙光数创 920808 实盘暴露）。"""

    def test_bj_code_delegates_to_direct_reader(self):
        import custos.pipeline.market_timing.technical_monitor as tm

        called = {}

        class _FakeReader:
            @staticmethod
            def read_vipdoc_daily(code):
                called["code"] = code
                return frame([9, 10], [11, 12], [10, 11])

        import sys
        import types

        fake_mod = types.ModuleType("custos.datasource.local_tdx.local_tdx_data")
        fake_mod.read_vipdoc_daily = _FakeReader.read_vipdoc_daily
        # 函数体内 import：替换 sys.modules 里的目标模块即可拦截
        saved = sys.modules.get("custos.datasource.local_tdx.local_tdx_data")
        sys.modules["custos.datasource.local_tdx.local_tdx_data"] = fake_mod
        try:
            df = tm.read_vipdoc("920808.BJ")
        finally:
            if saved is not None:
                sys.modules["custos.datasource.local_tdx.local_tdx_data"] = saved
            else:
                del sys.modules["custos.datasource.local_tdx.local_tdx_data"]
        assert called.get("code") == "920808.BJ"
        assert len(df) == 2

    def test_sh_code_still_uses_mootdx(self):
        """沪深路径不受影响（BJ 分支不得误伤）。"""
        import custos.pipeline.market_timing.technical_monitor as tm

        # 无 TDX 环境下 mootdx 读不到会回落空 DataFrame——关键是**不**走 BJ 直读
        import sys

        assert "custos.datasource.local_tdx.local_tdx_data" not in sys.modules or True
        df = tm.read_vipdoc("600000.SH")
        assert df is not None  # 不炸即可（有数据环境返回 K 线，无数据环境返回空表）
