# -*- coding: utf-8 -*-
"""交易日 16:50 投资账本自动同步：日历门、同步门、零成交与幂等入库。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from custos.datasource import tzzb_trades

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "automation" / "tzzb_auto_ingest.py"
SPEC = importlib.util.spec_from_file_location("tzzb_auto_ingest", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)

DAY = "2026-11-24"
ROW = {
    "成交日期": DAY,
    "成交时间": "",
    "代码": "600000",
    "名称": "测试",
    "交易类别": "买入",
    "成交数量": 100.0,
    "成交价格": 10.0,
    "成交金额": 1000.0,
    "发生金额": -1000.0,
    "费用": 0.0,
    "备注": "投资账本MCP（测试）",
}


def test_non_trading_day_skips_without_fetch():
    result = sync.run_sync(
        DAY,
        trading_day_fn=lambda _day: False,
        fetch_fn=lambda _day: pytest.fail("非交易日不得读取投资账本"),
    )
    assert result["trading_day"] is False
    assert result["status"] == "skipped"


def test_sync_not_covering_close_is_waiting_only(tmp_path: Path):
    def fetch(_day):
        raise tzzb_trades.TzzbError("投资账本 PC 同步时间为 14:28:31，未覆盖收盘")

    result = sync.run_sync(
        DAY,
        repo=tmp_path,
        trading_day_fn=lambda _day: True,
        fetch_fn=fetch,
        import_fn=lambda *_args: pytest.fail("未覆盖收盘不得入库"),
    )
    assert result["status"] == "waiting_sync"
    assert result["row_count"] == 0
    assert not list(tmp_path.rglob("*.csv"))


def test_zero_trades_does_not_write_no_trade_confirmation(tmp_path: Path):
    result = sync.run_sync(
        DAY,
        repo=tmp_path,
        trading_day_fn=lambda _day: True,
        fetch_fn=lambda _day: ([], "2026-11-24 16:36:38"),
        import_fn=lambda *_args: pytest.fail("零成交不得调用入库"),
    )
    assert result["status"] == "no_trades"
    assert result["row_count"] == 0
    assert "不写无交易确认" in result["summary"]


def test_trades_write_cache_and_import_idempotently(tmp_path: Path):
    calls: list[tuple[Path, str, Path]] = []

    def import_csv(path, day, repo):
        calls.append((path, day, repo))
        return {"appended_rows": 0, "duplicate_rows_skipped": 1}

    result = sync.run_sync(
        DAY,
        repo=tmp_path,
        trading_day_fn=lambda _day: True,
        fetch_fn=lambda _day: ([ROW], "2026-11-24 16:36:38"),
        import_fn=import_csv,
    )
    assert result["status"] == "unchanged"
    assert result["row_count"] == 1
    assert result["appended_rows"] == 0
    assert result["duplicate_rows_skipped"] == 1
    assert len(calls) == 1
    csv_path = tmp_path / "artifacts" / "cache" / "tzzb" / f"{DAY}_trades.csv"
    assert calls[0][0] == csv_path
    assert csv_path.is_file()
    assert "600000" in csv_path.read_text(encoding="utf-8-sig")


def test_other_tzzb_errors_remain_failures(tmp_path: Path):
    def fetch(_day):
        raise tzzb_trades.TzzbError("投资账本 maxPage 无效")

    with pytest.raises(tzzb_trades.TzzbError, match="maxPage"):
        sync.run_sync(
            DAY,
            repo=tmp_path,
            trading_day_fn=lambda _day: True,
            fetch_fn=fetch,
        )
