# -*- coding: utf-8 -*-
"""compass_amv 单测：临时 fixture 二进制文件覆盖解析/过滤/涨跌/error 路径；
真实 day.vdat 存在时与已知真值（0amv_observations.jsonl 已确认值）核对。"""
from __future__ import annotations

import datetime as dt
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import compass_amv

HEADER = b"\x00" * 16
GAP = b"\x00" * 28  # 全零记录，用于打断两段序列


def _record(date: dt.date, o: float, h: float, l: float, c: float,
            v: float = 1e11, a: float = 2e12) -> bytes:
    return struct.pack("<I6f", date.year * 10000 + date.month * 100 + date.day,
                       o, h, l, c, v, a)


def _series(start: dt.date, closes: list) -> bytes:
    """生成连续日历日的升序序列，OHLC 关系合理。"""
    out = []
    for i, c in enumerate(closes):
        d = start + dt.timedelta(days=i)
        out.append(_record(d, c - 0.5, c + 1.0, c - 1.0, c))
    return b"".join(out)


def _write_vdat(root: Path, *chunks: bytes) -> Path:
    path = root / compass_amv.DAY_VDAT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(HEADER + GAP.join(chunks))
    return path


# 主序列：30 条，收盘 100.0 ~ 100.29 之后两条已知涨跌
MAIN_START = dt.date(2026, 6, 18)
MAIN_CLOSES = [100.0] * 28 + [200.0, 190.0]  # 末两条 -5.0%
OLD_START = dt.date(1996, 12, 1)
OLD_CLOSES = [50.0] * 25


class ParseFixtureTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.path = _write_vdat(
            self.root,
            _series(OLD_START, OLD_CLOSES),
            _series(MAIN_START, MAIN_CLOSES),
        )

    def test_parse_selects_latest_ending_series(self) -> None:
        out = compass_amv.parse_amv_daily(since="2024-01-01", root=str(self.root))
        self.assertNotIn("error", out)
        self.assertEqual(out["source"], "compass_day_vdat")
        self.assertEqual(out["count"], 30)
        self.assertEqual(out["first_date"], MAIN_START.isoformat())
        last_date = MAIN_START + dt.timedelta(days=29)
        self.assertEqual(out["latest_date"], last_date.isoformat())
        self.assertEqual(out["records"][-1]["close"], 190.0)

    def test_change_pct(self) -> None:
        out = compass_amv.parse_amv_daily(since="2024-01-01", root=str(self.root))
        recs = out["records"]
        self.assertIsNone(recs[0]["change_pct"])  # 首条无前值
        self.assertEqual(recs[28]["change_pct"], 100.0)   # 100 -> 200
        self.assertEqual(recs[29]["change_pct"], -5.0)    # 200 -> 190

    def test_since_filter(self) -> None:
        since = (MAIN_START + dt.timedelta(days=25)).isoformat()
        out = compass_amv.parse_amv_daily(since=since, root=str(self.root))
        self.assertEqual(out["count"], 5)
        self.assertEqual(out["first_date"], since)
        # since 之后的首条 change_pct 基于全序列前值，不为 None
        self.assertIsNotNone(out["records"][0]["change_pct"])

    def test_compass_root_env_override(self) -> None:
        with mock.patch.dict(os.environ, {"COMPASS_ROOT": str(self.root)}):
            out = compass_amv.parse_amv_daily(since="2024-01-01")
        self.assertNotIn("error", out)
        self.assertEqual(out["count"], 30)
        # 显式 root 参数优先于环境变量
        with mock.patch.dict(os.environ, {"COMPASS_ROOT": str(self.root / "nope")}):
            out2 = compass_amv.parse_amv_daily(root=str(self.root))
        self.assertNotIn("error", out2)

    def test_latest_amv(self) -> None:
        out = compass_amv.latest_amv(root=str(self.root))
        self.assertTrue(out["ok"])
        self.assertEqual(out["close"], 190.0)
        self.assertEqual(out["prev_close"], 200.0)
        self.assertEqual(out["change_pct"], -5.0)


class ErrorPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_missing_file(self) -> None:
        out = compass_amv.parse_amv_daily(root=str(self.root))
        self.assertIn("error", out)
        self.assertTrue(out["error"].startswith("file_not_found"))
        self.assertEqual(out["records"], [])
        self.assertEqual(out["count"], 0)
        latest = compass_amv.latest_amv(root=str(self.root))
        self.assertFalse(latest["ok"])
        self.assertIn("error", latest)

    def test_too_small_file(self) -> None:
        _write_vdat(self.root, b"\x01" * 10)
        out = compass_amv.parse_amv_daily(root=str(self.root))
        self.assertIn("error", out)
        self.assertTrue(out["error"].startswith("file_too_small"))

    def test_no_valid_series(self) -> None:
        # 足够大但没有任何合法日期序列
        path = self.root / compass_amv.DAY_VDAT_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(os.urandom(4096))
        out = compass_amv.parse_amv_daily(root=str(self.root))
        self.assertIn("error", out)
        self.assertEqual(out["records"], [])

    def test_short_series_rejected(self) -> None:
        # 只有 10 条连续记录（< MIN_RUN=20），应判无有效序列
        path = _write_vdat(self.root, _series(MAIN_START, [100.0] * 10))
        # 补足文件长度，避免先触发 file_too_small 前置检查
        path.write_bytes(path.read_bytes() + b"\x00" * 1024)
        out = compass_amv.parse_amv_daily(root=str(self.root))
        self.assertIn("error", out)
        self.assertEqual(out["error"], "no_valid_series")


REAL_PATH = compass_amv._day_vdat_path()


@unittest.skipUnless(REAL_PATH.is_file(), f"真实 day.vdat 不存在: {REAL_PATH}")
class RealFileTest(unittest.TestCase):
    """真实数据与真值台账 01_data/market/0amv_observations.jsonl 已确认值核对。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.out = compass_amv.parse_amv_daily(since="2024-01-01")
        cls.by_date = {r["date"]: r for r in cls.out["records"]}

    def test_no_error(self) -> None:
        self.assertNotIn("error", self.out)

    def test_known_change_pct(self) -> None:
        # 与 0amv_observations.jsonl 中 quality=confirmed 记录一致
        expected = {"2026-07-14": 1.79, "2026-07-15": -2.53, "2026-07-17": -5.84}
        for date, pct in expected.items():
            self.assertIn(date, self.by_date, f"缺少记录 {date}")
            self.assertEqual(self.by_date[date]["change_pct"], pct, date)

    def test_latest_close(self) -> None:
        latest = compass_amv.latest_amv()
        self.assertTrue(latest["ok"])
        self.assertAlmostEqual(latest["close"], 207162.61, places=2)

    def test_main_series_range(self) -> None:
        # 主序列起点不早于 2025-11-11（本机已知范围），记录数 >= 160
        self.assertGreaterEqual(self.out["first_date"], "2025-11-11")
        self.assertGreaterEqual(self.out["count"], 160)


if __name__ == "__main__":
    unittest.main()
