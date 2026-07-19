# -*- coding: utf-8 -*-
"""compass_amv 单测：临时 fixture 二进制文件覆盖解析/拼接/真值识别/error 路径；
真实 day.vdat 存在时与已知真值（0amv_observations.jsonl 已确认值）核对。"""
from __future__ import annotations

import datetime as dt
import json
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


def _write_vdat(root: Path, *chunks: bytes, gap: bytes = GAP) -> Path:
    path = root / compass_amv.DAY_VDAT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(HEADER + gap.join(chunks))
    return path


def _pcts(closes: list) -> list:
    """与解析器同公式计算 change_pct 序列（首条 None）。"""
    out = [None]
    for i in range(1, len(closes)):
        out.append(round((closes[i] / closes[i - 1] - 1) * 100, 2))
    return out


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
        # 不存在的真值台账：隔离本机真实 ledger，走 fallback 路径
        self.no_truth = str(self.root / "no_such_ledger.jsonl")
        self.path = _write_vdat(
            self.root,
            _series(OLD_START, OLD_CLOSES),
            _series(MAIN_START, MAIN_CLOSES),
        )

    def _parse(self, since: str = "2024-01-01") -> dict:
        return compass_amv.parse_amv_daily(since=since, root=str(self.root),
                                           truth_path=self.no_truth)

    def test_parse_selects_latest_ending_series(self) -> None:
        out = self._parse()
        self.assertNotIn("error", out)
        self.assertEqual(out["source"], "compass_day_vdat")
        self.assertEqual(out["count"], 30)
        self.assertEqual(out["first_date"], MAIN_START.isoformat())
        self.assertEqual(out["series_start"], MAIN_START.isoformat())
        self.assertTrue(out["identification"].startswith("fallback"))
        last_date = MAIN_START + dt.timedelta(days=29)
        self.assertEqual(out["latest_date"], last_date.isoformat())
        self.assertEqual(out["records"][-1]["close"], 190.0)

    def test_change_pct(self) -> None:
        recs = self._parse()["records"]
        self.assertIsNone(recs[0]["change_pct"])  # 首条无前值
        self.assertEqual(recs[28]["change_pct"], 100.0)   # 100 -> 200
        self.assertEqual(recs[29]["change_pct"], -5.0)    # 200 -> 190

    def test_since_filter(self) -> None:
        since = (MAIN_START + dt.timedelta(days=25)).isoformat()
        out = self._parse(since=since)
        self.assertEqual(out["count"], 5)
        self.assertEqual(out["first_date"], since)
        # since 之后的首条 change_pct 基于全序列前值，不为 None
        self.assertIsNotNone(out["records"][0]["change_pct"])

    def test_compass_root_env_override(self) -> None:
        with mock.patch.dict(os.environ, {"COMPASS_ROOT": str(self.root)}):
            out = compass_amv.parse_amv_daily(since="2024-01-01",
                                              truth_path=self.no_truth)
        self.assertNotIn("error", out)
        self.assertEqual(out["count"], 30)
        # 显式 root 参数优先于环境变量
        with mock.patch.dict(os.environ, {"COMPASS_ROOT": str(self.root / "nope")}):
            out2 = compass_amv.parse_amv_daily(root=str(self.root),
                                               truth_path=self.no_truth)
        self.assertNotIn("error", out2)

    def test_latest_amv(self) -> None:
        out = compass_amv.latest_amv(root=str(self.root), truth_path=self.no_truth)
        self.assertTrue(out["ok"])
        self.assertEqual(out["close"], 190.0)
        self.assertEqual(out["prev_close"], 200.0)
        self.assertEqual(out["change_pct"], -5.0)


class MultiBlockChainTest(unittest.TestCase):
    """多块拼接：两个 250 块 + 一个尾块（块间 36 字节间隔、对齐变化）应拼成一条系列。"""

    CHAIN_START = dt.date(2024, 1, 2)

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.no_truth = str(self.root / "no_such_ledger.jsonl")
        d = self.CHAIN_START
        self.closes = [100.0] * 250 + [101.0] * 250 + [102.0] * 30
        gap36 = b"\x00" * 36  # 非 28 倍数，块间对齐随之变化
        self.path = _write_vdat(
            self.root,
            _series(d, self.closes[:250]),
            _series(d + dt.timedelta(days=250), self.closes[250:500]),
            _series(d + dt.timedelta(days=500), self.closes[500:]),
            gap=gap36,
        )

    def test_blocks_chained_into_single_series(self) -> None:
        out = compass_amv.parse_amv_daily(since="1990-01-01", root=str(self.root),
                                          truth_path=self.no_truth)
        self.assertNotIn("error", out)
        self.assertEqual(out["count"], 530)
        self.assertEqual(out["first_date"], self.CHAIN_START.isoformat())
        self.assertEqual(out["series_start"], self.CHAIN_START.isoformat())
        last = self.CHAIN_START + dt.timedelta(days=529)
        self.assertEqual(out["latest_date"], last.isoformat())
        # 跨块边界的 change_pct 连续计算（块1尾 100 -> 块2首 101）
        self.assertEqual(out["records"][250]["change_pct"], 1.0)
        self.assertEqual(out["records"][-1]["close"], 102.0)


# 真值识别 fixture：WRONG 系列结束更新也更长，TRUE 系列（0AMV）靠真值匹配胜出
WRONG_START = dt.date(2026, 5, 10)
WRONG_CLOSES = [500.0] * 60                       # 平盘，change_pct 全 0
TRUE_START = dt.date(2026, 6, 1)
# 周期性波动收盘（2.0/1.96/1.92/1.89/-7.41%），与 WRONG 的全 0 明显区分
TRUE_CLOSES = [100.0 + (i % 5) * 2 for i in range(30)]


class TruthSelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.path = _write_vdat(
            self.root,
            _series(WRONG_START, WRONG_CLOSES),
            _series(TRUE_START, TRUE_CLOSES),
        )
        # 真值台账：TRUE 系列后半段的 date -> change_pct（confirmed）
        self.truth = self.root / "truth.jsonl"
        pcts = _pcts(TRUE_CLOSES)
        lines = []
        for i in range(20, len(TRUE_CLOSES)):
            d = (TRUE_START + dt.timedelta(days=i)).isoformat()
            lines.append(json.dumps({"date": d, "amv_change_pct": pcts[i],
                                     "quality": "confirmed"}))
        self.truth.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_truth_match_selects_amv_series(self) -> None:
        out = compass_amv.parse_amv_daily(since="1990-01-01", root=str(self.root),
                                          truth_path=str(self.truth))
        self.assertNotIn("error", out)
        # 真值匹配应选中 TRUE 系列，尽管 WRONG 结束日期更新也更长
        self.assertTrue(out["identification"].startswith("truth_match"),
                        out["identification"])
        self.assertEqual(out["series_start"], TRUE_START.isoformat())
        self.assertEqual(out["count"], len(TRUE_CLOSES))
        self.assertEqual(out["records"][-1]["close"], TRUE_CLOSES[-1])
        self.assertEqual(out["records"][1]["change_pct"], 2.0)   # 100 -> 102
        self.assertEqual(out["records"][2]["change_pct"], 1.96)  # 102 -> 104

    def test_no_truth_fallback_picks_latest_longest(self) -> None:
        out = compass_amv.parse_amv_daily(
            since="1990-01-01", root=str(self.root),
            truth_path=str(self.root / "no_such_ledger.jsonl"))
        self.assertNotIn("error", out)
        self.assertEqual(out["identification"],
                         "fallback: latest end + longest history")
        self.assertEqual(out["series_start"], WRONG_START.isoformat())
        self.assertEqual(out["count"], len(WRONG_CLOSES))

    def test_unmatched_truth_still_falls_back(self) -> None:
        # 真值存在但与任何链都不匹配（日期不在链内）→ 回退
        junk = self.root / "junk_truth.jsonl"
        junk.write_text(json.dumps({"date": "2031-01-05", "amv_change_pct": 1.0,
                                    "quality": "confirmed"}) + "\n", encoding="utf-8")
        out = compass_amv.parse_amv_daily(since="1990-01-01", root=str(self.root),
                                          truth_path=str(junk))
        self.assertNotIn("error", out)
        self.assertEqual(out["identification"],
                         "fallback: no chain matched truth; latest end + longest history")
        self.assertEqual(out["series_start"], WRONG_START.isoformat())


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

    def test_series_identified_by_truth(self) -> None:
        self.assertTrue(self.out["identification"].startswith("truth_match"),
                        self.out["identification"])

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
        self.assertEqual(latest["change_pct"], -5.84)

    def test_full_history_since_1993(self) -> None:
        # 多块拼接后 0AMV 全历史起点 1993-01-03
        self.assertEqual(self.out["series_start"], "1993-01-03")
        full = compass_amv.parse_amv_daily(since="1990-01-01")
        self.assertNotIn("error", full)
        self.assertEqual(full["first_date"], "1993-01-03")
        self.assertEqual(full["latest_date"], "2026-07-17")
        self.assertGreaterEqual(full["count"], 8000)

    def test_2024_09_24_present(self) -> None:
        self.assertIn("2024-09-24", self.by_date)
        self.assertEqual(self.by_date["2024-09-24"]["change_pct"], 9.87)


if __name__ == "__main__":
    unittest.main()
