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


def _seg(kind, price, idx, ongoing=False):
    return {
        "kind": kind,
        "price": price,
        "idx": idx,
        "date": None,
        "start": None,
        "end": None,
        "ongoing": ongoing,
    }


def _flat(n=60, last=10.0):
    closes = [10.0] * (n - 1) + [last]
    return frame([c * 0.99 for c in closes], [c * 1.01 for c in closes], closes)


def _seg_og(kind, price, idx, start_i):
    return {**_seg(kind, price, idx, ongoing=True), "start_i": start_i}


def _bear_tail(n, start, close_tail):
    """前 start 根收 10、开 = 收；之后收 close_tail 的阴线（开 = 收 × 1.01）。"""
    closes = [10.0] * start + [close_tail] * (n - start)
    opens = closes[:start] + [c * 1.01 for c in closes[start:]]
    df = frame([c * 0.99 for c in closes], [c * 1.02 for c in closes], closes)
    df["open"] = opens
    return df


_UP = [_seg("H", 10.0, 20), _seg("L", 9.0, 30), _seg("H", 11.0, 38), _seg("L", 9.5, 45)]


class NStructureTests(unittest.TestCase):
    """v0.604（owner 2026-11-26）：最近两组已完成平台点，底、顶都抬高 ⇒ 上升 N；当前绿柱段的平台底跌破 L1 ⇒ P0。"""

    def setUp(self):
        from custos.core.factors import b1_structure as bs

        self.bs = bs
        self._orig = bs.macd_platform_segments

    def tearDown(self):
        self.bs.macd_platform_segments = self._orig

    def _n(self, segs, df):
        self.bs.macd_platform_segments = lambda d: segs
        return n_structure_state(df), self.bs.check_descending_n_structure(df)

    def test_latest_two_groups_rising_is_up_n(self):
        r, _ = self._n(_UP + [_seg_og("H", 10.5, 59, 50)], _flat(last=10.0))
        self.assertTrue(r["available"])
        self.assertEqual(
            (r["prior_low"], r["pullback_low"], r["breakout_level"]), (9.0, 9.5, 11.0)
        )
        self.assertEqual((r["prior_high"], r["latest_high"]), (10.0, 11.0))
        self.assertEqual(r["status"], "candidate")
        self.assertFalse(r["platform_breach"])

    def test_lower_second_low_is_not_rising_n(self):
        segs = [
            _seg("H", 10.0, 20),
            _seg("L", 9.0, 30),
            _seg("H", 11.0, 38),
            _seg("L", 8.5, 45),
        ]
        r, _ = self._n(segs, _flat())
        self.assertFalse(r["available"])

    def test_low_rising_but_high_falling_is_not_rising_n(self):
        """v0.604 起顶也要抬高（🔄 v0.589「只看底抬高」停用）。"""
        segs = [
            _seg("H", 12.0, 20),
            _seg("L", 9.0, 30),
            _seg("H", 11.0, 38),
            _seg("L", 9.5, 45),
        ]
        r, d = self._n(segs, _flat())
        self.assertFalse(r["available"])
        self.assertFalse(d["available"])

    def test_no_older_n_when_latest_groups_not_rising(self):
        """🔄 v0.589「往回翻旧的 N」停用：只看最近两组。"""
        segs = (
            [_seg("L", 8.0, 5), _seg("H", 10.0, 12)]
            + _UP[1:]
            + [_seg("H", 10.6, 50), _seg("L", 9.2, 55)]
        )
        r, _ = self._n(segs, _flat())
        self.assertFalse(r["available"])

    def test_ongoing_segment_not_used_as_structure(self):
        """进行中末段的平台值随价格移动，不当作结构位。"""
        segs = [
            _seg("H", 10.0, 20),
            _seg("L", 9.0, 30),
            _seg("H", 11.0, 38),
            _seg_og("L", 9.5, 59, 50),
        ]
        r, _ = self._n(segs, _flat())
        self.assertFalse(r["available"])

    def test_current_green_segment_breaks_l1_is_fresh_p0(self):
        df = _bear_tail(60, 56, 8.8)
        r, _ = self._n(_UP + [_seg("H", 10.6, 50), _seg_og("L", 8.8, 59, 54)], df)
        # 最近两组已完成：L 9 → 9.5、H 11 → 10.6 不抬高 ⇒ 换成顶也抬高的序列
        self.assertFalse(r["available"])
        r, _ = self._n(_UP + [_seg("H", 11.5, 50), _seg_og("L", 8.8, 59, 54)], df)
        self.assertTrue(r["available"] and r["platform_breach"] and r["fresh_breach"])
        self.assertEqual((r["breach_bars_ago"], r["current_platform_low"]), (3, 8.8))
        self.assertFalse(r["stale"])

    def test_platform_breach_long_ago_is_stale(self):
        df = _bear_tail(60, 47, 8.8)
        r, _ = self._n(_UP + [_seg("H", 11.5, 46), _seg_og("L", 8.8, 59, 47)], df)
        self.assertTrue(r["platform_breach"] and r["stale"])
        self.assertFalse(r["fresh_breach"])

    def test_current_low_between_l1_and_l2_is_only_p1(self):
        df = _bear_tail(60, 56, 9.2)
        r, _ = self._n(_UP + [_seg("H", 11.5, 50), _seg_og("L", 9.2, 59, 54)], df)
        self.assertTrue(r["available"] and r["pullback_breached_on_close"])
        self.assertFalse(r["platform_breach"] or r["breached_on_close"])

    def test_descending_n_latest_two_groups_falling(self):
        segs = [
            _seg("H", 12.0, 20),
            _seg("L", 10.0, 30),
            _seg("H", 11.0, 38),
            _seg("L", 9.5, 45),
        ]
        _, d = self._n(segs, _flat(last=9.0))
        self.assertTrue(d["available"])
        self.assertEqual(
            (d["prior_high"], d["structural_low"], d["lower_high"]), (12.0, 9.5, 11.0)
        )
        self.assertEqual(d["status"], "confirmed")


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


if __name__ == "__main__":
    unittest.main()


class TestBjVipdocRouting(unittest.TestCase):
    """v0.101：北交所代码必须走 local_tdx_data 的 .day 直读——mootdx Reader 把
    920xxx 误路由到 SH（读不到 ⇒ 持仓技术面全空，曙光数创 920808 实盘暴露）。

    2026-08-24 数据层解耦后，沪深与 BJ **都**委托数据层（BJ 路由由数据层内部处理）；
    v0.486 起委托 get_ohlcv_table（前复权），本组测试钉住这个委托关系。"""

    def _intercept_ltd(self):
        """替换 sys.modules 里的 local_tdx_data，拦截函数体内的惰性 import。"""
        import sys
        import types

        called = {}

        def fake_read_vipdoc_daily(code):
            called["raw"] = code
            return frame([9, 10], [11, 12], [10, 11])

        def fake_get_ohlcv_table(code, count=260, adjust="qfq", **_kw):
            called["code"] = code
            called["count"] = count
            called["adjust"] = adjust
            df = frame([9, 10], [11, 12], [10, 11])
            df.attrs["adjust"] = called.get("force_adjust", "qfq")
            return df

        fake_mod = types.ModuleType("custos.datasource.local_tdx.local_tdx_data")
        fake_mod.read_vipdoc_daily = fake_read_vipdoc_daily
        fake_mod.get_ohlcv_table = fake_get_ohlcv_table
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

    def test_read_vipdoc_is_qfq_full_history_v0486(self):
        """v0.486（owner「需要全部前复权」）：live 入口走 get_ohlcv_table 默认 qfq、全历史；
        原始价只留 read_vipdoc_raw。"""
        import custos.pipeline.market_timing.technical_monitor as tm

        called, saved = self._intercept_ltd()
        try:
            tm.read_vipdoc("600000.SH")
            assert called["adjust"] == "qfq" and called["count"] == tm.QFQ_LOAD_BARS
            assert "raw" not in called
            tm.read_vipdoc_raw("600000.SH")
            assert called["raw"] == "600000.SH"
        finally:
            self._restore_ltd(saved)

    def test_qfq_failure_warns(self):
        import contextlib
        import io

        import custos.pipeline.market_timing.technical_monitor as tm

        called, saved = self._intercept_ltd()
        called["force_adjust"] = "none"
        buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(buf):
                df = tm.read_vipdoc("600000.SH")
        finally:
            self._restore_ltd(saved)
        assert len(df) == 2 and "前复权失败" in buf.getvalue()


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
