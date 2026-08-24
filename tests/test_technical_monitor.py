# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

import pandas as pd
import pytest

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
    920xxx 误路由到 SH（读不到 ⇒ 持仓技术面全空，曙光数创 920808 实盘暴露）。

    2026-08-24 数据层解耦后，沪深与 BJ **都**委托 local_tdx_data.read_vipdoc_daily
    （BJ 路由由数据层内部处理），本组测试钉住这个委托关系。"""

    def _intercept_ltd(self):
        """替换 sys.modules 里的 local_tdx_data，拦截函数体内的惰性 import。"""
        import sys
        import types

        called = {}

        def fake_read_vipdoc_daily(code):
            called["code"] = code
            return frame([9, 10], [11, 12], [10, 11])

        fake_mod = types.ModuleType("custos.datasource.local_tdx.local_tdx_data")
        fake_mod.read_vipdoc_daily = fake_read_vipdoc_daily
        saved = sys.modules.get("custos.datasource.local_tdx.local_tdx_data")
        sys.modules["custos.datasource.local_tdx.local_tdx_data"] = fake_mod
        return called, saved

    def _restore_ltd(self, saved):
        import sys

        if saved is not None:
            sys.modules["custos.datasource.local_tdx.local_tdx_data"] = saved
        else:
            del sys.modules["custos.datasource.local_tdx.local_tdx_data"]

    def test_bj_code_delegates_to_direct_reader(self):
        import custos.pipeline.market_timing.technical_monitor as tm

        called, saved = self._intercept_ltd()
        try:
            df = tm.read_vipdoc("920808.BJ")
        finally:
            self._restore_ltd(saved)
        assert called.get("code") == "920808.BJ"
        assert len(df) == 2

    def test_sh_code_also_delegates_to_datasource(self):
        """2026-08-24 解耦：沪深不再直调 mootdx Reader，同样走数据层接口。"""
        import custos.pipeline.market_timing.technical_monitor as tm

        called, saved = self._intercept_ltd()
        try:
            df = tm.read_vipdoc("600000.SH")
        finally:
            self._restore_ltd(saved)
        assert called.get("code") == "600000.SH"
        assert len(df) == 2


def _write_day(path, recs):
    """写合成 TDX .day 文件（32 字节/记录：date,o,h,l,c(int 0.01元),amount(float),vol,reserved）。"""
    import struct

    with open(path, "wb") as f:
        for r in recs:
            f.write(struct.pack("<IIIIIfII", *r))


def _isolate_tdx_root(monkeypatch, tmp_path):
    """把 local_tdx_data 的 TDX_ROOT 指到合成 vipdoc，并清掉 reader/校验缓存。"""
    from custos.datasource.local_tdx import local_tdx_data as ltd

    monkeypatch.setattr(ltd, "TDX_ROOT", tmp_path)
    monkeypatch.setattr(ltd, "_reader", None)
    monkeypatch.setattr(ltd, "_tdx_root_verified", set())
    return ltd


RECS = [
    (20260819, 1000, 1050, 990, 1020, 123456.0, 78900, 0),
    (20260820, 1020, 1060, 1000, 1040, 223456.0, 88900, 0),
    (20260821, 1040, 1080, 1030, 1070, 323456.0, 98900, 0),
]


class TestReadVipdocEquivalence:
    """read_vipdoc 改走 local_tdx_data 后的等价性钉测（合成 .day，真读数据层）。

    与旧「直调 mootdx Reader.daily + reset_index」路径逐值核对过：
    date（升序、成列）/open/high/low/close/amount/volume 完全一致，
    仅多出 code/source 信息列（消费方不读）。
    """

    def test_sh_columns_values_and_order(self, monkeypatch, tmp_path):
        import custos.pipeline.market_timing.technical_monitor as tm

        d = tmp_path / "vipdoc" / "sh" / "lday"
        d.mkdir(parents=True)
        _write_day(d / "sh600000.day", RECS)
        _isolate_tdx_root(monkeypatch, tmp_path)

        df = tm.read_vipdoc("600000.SH")
        assert "date" in df.columns, "analyze() 直接取 df['date']，date 必须成列"
        assert [str(x)[:10] for x in df["date"]] == [
            "2026-08-19",
            "2026-08-20",
            "2026-08-21",
        ], "必须升序（消费方 iloc[-1] 取最新）"
        # mootdx 系数乘法有浮点尾差（10.2000...1），与旧路径同一 Reader、同一份误差
        assert list(df["close"]) == pytest.approx([10.2, 10.4, 10.7])
        assert list(df["amount"]) == [123456.0, 223456.0, 323456.0]  # 元，不变
        # volume 经 mootdx 证券类型系数换算（SH_A_STOCK=手）：与旧路径同一 Reader，口径不变
        assert list(df["volume"]) == [789.0, 889.0, 989.0]

    def test_bj_direct_read_still_works(self, monkeypatch, tmp_path):
        import custos.pipeline.market_timing.technical_monitor as tm

        d = tmp_path / "vipdoc" / "bj" / "lday"
        d.mkdir(parents=True)
        _write_day(d / "bj920808.day", RECS)
        _isolate_tdx_root(monkeypatch, tmp_path)

        df = tm.read_vipdoc("920808.BJ")
        assert list(df["close"]) == [10.2, 10.4, 10.7]
        assert df["source"].iloc[0] == "vipdoc_bj_direct"
        # 既有口径差异（本次未改，如实钉住）：BJ 直读的 volume 是原始股数，
        # 不经 mootdx 系数换算（沪深路径是「手」）——消费方只用 volume 做相对比较
        assert list(df["volume"]) == [78900, 88900, 98900]

    def test_missing_file_returns_empty_not_raise(self, monkeypatch, tmp_path):
        import custos.pipeline.market_timing.technical_monitor as tm

        (tmp_path / "vipdoc").mkdir(parents=True)
        _isolate_tdx_root(monkeypatch, tmp_path)
        assert tm.read_vipdoc("600000.SH").empty

    def test_failure_returns_empty_but_warns(self, monkeypatch, capsys):
        """外部契约不变（失败返回空表），但不得静默——必须有 [WARN] 留痕
        （governance/data/DATA_SOURCE_PRINCIPLE.md 原则二：静默返回空是反模式）。"""
        import custos.pipeline.market_timing.technical_monitor as tm
        from custos.datasource.local_tdx import local_tdx_data as ltd

        def boom(code):
            raise RuntimeError("TDX_ROOT 未配置")

        monkeypatch.setattr(ltd, "read_vipdoc_daily", boom)
        df = tm.read_vipdoc("600000.SH")
        assert df.empty
        assert "[WARN]" in capsys.readouterr().err
