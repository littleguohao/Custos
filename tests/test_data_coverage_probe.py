# -*- coding: utf-8 -*-
"""data_coverage_probe（TODO #91）钉测 —— 全部用合成 vipdoc 目录（本机无通达信数据）。

构造的宇宙：
  sh600000  70 根跨 2005-11~2006-02（eligible_date=第 60 根那天 = 2006-01-23）
  sz000001  30 根（永不 eligible）
  bj920001  65 根（eligible 2022-08-23）
  sh880001  板块指数（vipdoc 880 段，不得进个股统计）
  sh600999  80 根跨 2026-06~2026-09，尾部 10 根 ≥ 2026-09-05（holdout 截断验证）
  sh000001  沪市指数（既非个股也非 880/881，不得进任何统计）
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pandas as pd
import pytest

from custos.research import data_coverage_probe as dcp


def _dates(start: str, n: int) -> list[int]:
    """start 起 n 个工作日的 YYYYMMDD int 列表。"""
    return [int(d.strftime("%Y%m%d")) for d in pd.bdate_range(start, periods=n)]


def _write_day(path: Path, date_ints: list[int], *, tail_bytes: int = 0) -> None:
    """按 vipdoc .day 布局（<IIIIIfII，32 字节/根）写合成日线文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        for d in date_ints:
            f.write(struct.pack("<IIIIIfII", d, 100, 110, 90, 105, 1000.0, 5000, 0))
        if tail_bytes:
            f.write(b"\x00" * tail_bytes)  # 尾部残段应被忽略


@pytest.fixture
def vipdoc_root(tmp_path: Path) -> Path:
    root = tmp_path / "tdx"
    _write_day(root / "vipdoc/sh/lday/sh600000.day", _dates("2005-11-01", 70))
    _write_day(root / "vipdoc/sz/lday/sz000001.day", _dates("2010-01-04", 30))
    _write_day(root / "vipdoc/bj/lday/bj920001.day", _dates("2022-06-01", 65))
    _write_day(
        root / "vipdoc/sh/lday/sh880001.day", _dates("2008-01-02", 100), tail_bytes=10
    )
    _write_day(root / "vipdoc/sh/lday/sh600999.day", _dates("2026-06-01", 80))
    _write_day(root / "vipdoc/sh/lday/sh000001.day", _dates("2000-01-04", 300))
    return root


def _write_ledger(path: Path) -> None:
    """合成 0AMV 台账：2 条 confirmed + 1 条 superseded（应被剔除）。"""
    rows = [
        {
            "date": "2003-05-09",
            "amv_change_pct": 1.2,
            "quality": "confirmed",
            "recorded_at": "2003-05-09T15:00:00",
        },
        {
            "date": "2015-04-20",
            "amv_change_pct": -3.1,
            "quality": "confirmed",
            "recorded_at": "2015-04-20T15:00:00",
        },
        {
            "date": "2016-01-04",
            "amv_change_pct": 2.0,
            "quality": "confirmed",
            "superseded": True,
            "recorded_at": "2016-01-04T15:00:00",
        },
    ]
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


# ---------- 二进制读取与单票口径 ----------


class TestDayFileReading:
    def test_zero_date_rows_skipped_and_tail_ignored(self, tmp_path):
        p = tmp_path / "vipdoc/sh/lday/sh600000.day"
        _write_day(p, [20051101, 0, 20051103], tail_bytes=7)
        got = dcp._day_date_ints(p).tolist()
        assert got == [20051101, 20051103]  # date_int==0 跳过；7 字节残段忽略

    def test_scan_per_stock_first_and_eligible(self, vipdoc_root):
        scanned = dcp.scan_vipdoc(vipdoc_root, min_bars=60, quiet=True)
        stocks = {r["code"]: r for r in scanned["stocks"]}
        # 880001（板块指数）与 000001（沪市指数）不进个股统计
        assert sorted(stocks) == ["000001", "600000", "600999", "920001"]
        s = stocks["600000"]
        assert s["first_date"] == "2005-11-01"
        assert s["eligible_date"] == "2006-01-23"  # 第 60 根 bar 的日期
        assert s["years"] == {2005, 2006}
        assert stocks["000001"]["eligible_date"] is None  # 30 根 < 60
        assert stocks["920001"]["eligible_date"] == "2022-08-23"
        assert stocks["920001"]["board"] == "北交所"
        idx = scanned["sector_index"]
        assert len(idx) == 1 and idx[0]["code"] == "880001"
        assert idx[0]["first_date"] == "2008-01-02"

    def test_holdout_bars_never_counted(self, vipdoc_root):
        """≥ 2026-09-05（FORWARD_HOLDOUT_START）的 bar 不进任何统计。"""
        scanned = dcp.scan_vipdoc(vipdoc_root, min_bars=60, quiet=True)
        s = {r["code"]: r for r in scanned["stocks"]}["600999"]
        assert s["n_bars"] == 70  # 80 根里 10 根在 holdout 段被截掉
        assert s["eligible_date"] == "2026-08-21"  # 截断后第 60 根
        assert s["years"] == {2026}
        assert dcp.HOLDOUT_CAP == "2026-09-04"


# ---------- 逐年聚合 ----------


class TestYearlyAggregation:
    def test_active_and_eligible_by_year(self, vipdoc_root):
        scanned = dcp.scan_vipdoc(vipdoc_root, min_bars=60, quiet=True)
        agg = dcp.aggregate_stocks(scanned["stocks"], cap_year=2026)
        by_year = {r["year"]: r for r in agg["yearly"]}
        assert by_year[2005]["n_active"] == 1  # sh600000
        assert by_year[2005]["n_eligible"] == 0  # 资格日在 2006-01
        assert by_year[2006]["n_active"] == 1
        assert by_year[2006]["n_eligible"] == 1  # eligible 2006-01-23 ≤ 2006-12-31
        assert by_year[2008]["n_active"] == 0  # 880001 是板块指数，不算个股
        assert by_year[2010]["n_active"] == 1  # sz000001
        assert by_year[2010]["n_eligible"] == 0  # 永不 eligible
        assert by_year[2022]["n_active"] == 1  # bj920001
        assert by_year[2022]["n_eligible"] == 1
        assert by_year[2026]["n_active"] == 1  # sh600999（holdout 截断后仍在市）
        assert by_year[2026]["n_eligible"] == 1
        assert agg["n_files"] == 4
        assert agg["by_market"] == {"sh": 2, "sz": 1, "bj": 1}
        assert agg["by_board"] == {
            "沪主板": 2,
            "科创板": 0,
            "深主板": 1,
            "创业板": 0,
            "北交所": 1,
        }
        assert agg["first_date_min"] == "2005-11-01"
        assert agg["n_eligible"] == 3

    def test_sector_index_yearly_and_percentiles(self, vipdoc_root):
        scanned = dcp.scan_vipdoc(vipdoc_root, min_bars=60, quiet=True)
        agg = dcp.aggregate_sector_index(scanned["sector_index"], cap_year=2026)
        by_year = {r["year"]: r["n_active"] for r in agg["yearly"]}
        assert agg["n_files"] == 1
        assert by_year[2008] == 1
        assert by_year[2005] == 0
        assert agg["first_date_pct"] == {
            "p10": "2008-01-02",
            "p50": "2008-01-02",
            "p90": "2008-01-02",
        }


# ---------- 0AMV 台账块 ----------


class TestAmvLedgerBlock:
    def test_confirmed_min_max(self, tmp_path):
        ledger = tmp_path / "0amv_observations.jsonl"
        _write_ledger(ledger)
        blk = dcp.amv_ledger_block(ledger)
        assert blk is not None
        assert blk["n_confirmed"] == 2  # superseded 剔除
        assert blk["first_date"] == "2003-05-09"
        assert blk["last_date"] == "2015-04-20"

    def test_missing_ledger_is_null_not_fatal(self, tmp_path, capsys):
        blk = dcp.amv_ledger_block(tmp_path / "nope.jsonl")
        assert blk is None
        assert "WARN" in capsys.readouterr().err


# ---------- main() 冒烟 / 护栏 ----------


class TestMain:
    def test_smoke_with_synthetic_vipdoc(self, vipdoc_root, tmp_path):
        ledger = tmp_path / "ledger.jsonl"
        _write_ledger(ledger)
        out = tmp_path / "probe.json"
        rc = dcp.main(
            [
                "--tdx-root",
                str(vipdoc_root),
                "--amv-ledger",
                str(ledger),
                "--out",
                str(out),
                "--sector-index-dir",
                str(tmp_path / "no_such_dir"),
            ]
        )
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["tool"] == "data_coverage_probe"
        assert payload["generated_at"]
        assert payload["tdx_root"] == str(vipdoc_root)
        assert payload["min_bars"] == 60
        assert payload["holdout_cap"] == "2026-09-04"
        assert payload["amv_ledger"]["n_confirmed"] == 2
        assert payload["stocks"]["n_files"] == 4
        assert payload["sector_index_vipdoc"]["n_files"] == 1
        # sector_index 目录不存在 ⇒ 该块 null，不致命
        assert payload["sector_index_csv"] is None

    def test_missing_ledger_still_exits_zero(self, vipdoc_root, tmp_path):
        out = tmp_path / "probe.json"
        rc = dcp.main(
            [
                "--tdx-root",
                str(vipdoc_root),
                "--amv-ledger",
                str(tmp_path / "nope.jsonl"),
                "--out",
                str(out),
            ]
        )
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["amv_ledger"] is None

    def test_empty_vipdoc_guard(self, tmp_path, capsys):
        """空结果护栏：vipdoc 三目录在但 0 个可用文件 ⇒ 非零退出且不写产物。"""
        root = tmp_path / "tdx"
        for mkt in ("sh", "sz", "bj"):
            (root / "vipdoc" / mkt / "lday").mkdir(parents=True)
        out = tmp_path / "probe.json"
        rc = dcp.main(["--tdx-root", str(root), "--out", str(out)])
        assert rc == 2
        assert not out.exists()
        assert "空结果" in capsys.readouterr().err

    def test_bad_tdx_root_guard(self, tmp_path):
        out = tmp_path / "probe.json"
        rc = dcp.main(
            ["--tdx-root", str(tmp_path / "not_a_tdx_root"), "--out", str(out)]
        )
        assert rc == 2
        assert not out.exists()


# ---------- sector_index csv 块 ----------


class TestSectorIndexCsvBlock:
    def test_coverage(self, tmp_path):
        d = tmp_path / "sector_index"
        d.mkdir()
        pd.DataFrame(
            {"date": ["2010-01-04", "2010-01-05", "2027-01-04"], "close": [1, 2, 3]}
        ).to_csv(d / "880001.csv", index=False)
        pd.DataFrame({"date": ["2011-03-01", "2011-03-02"], "close": [1, 2]}).to_csv(
            d / "880002.csv", index=False
        )
        blk = dcp.sector_index_csv_block(d)
        assert blk is not None
        assert blk["n_files"] == 2
        assert blk["date_min"] == "2010-01-04"
        assert blk["date_max"] == "2011-03-02"  # 2027 行超 cap 不进统计
        by_year = {r["year"]: r["n_files"] for r in blk["yearly"]}
        assert by_year[2010] == 1
        assert by_year[2011] == 1
        assert by_year[2012] == 0

    def test_missing_dir_is_null(self, tmp_path, capsys):
        blk = dcp.sector_index_csv_block(tmp_path / "nope")
        assert blk is None
        assert "WARN" in capsys.readouterr().err
