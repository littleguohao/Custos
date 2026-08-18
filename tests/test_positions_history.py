# -*- coding: utf-8 -*-
"""positions_history —— 持仓快照历史归档（TODO #49 的前置机制）。

`current_positions.json` 只有当前一份；rss_filter.entities(date) 回填历史日期
需要「那天的持仓」。归档由两个写入点落盘（incremental_ledger 增量 /
standardize_trades 全量），读语义是「≤ date 的最近一份」——持仓不变的日子
不产生归档，精确匹配会大量落空。
"""

from __future__ import annotations

import json

import pytest

from custos.core import positions_history as ph


@pytest.fixture()
def hist(tmp_path, monkeypatch):
    d = tmp_path / "positions_history"
    monkeypatch.setattr(ph, "HISTORY_DIR", d)
    return d


class TestArchive:
    def test_archive_writes_dated_file(self, hist):
        dest = ph.archive_snapshot([{"代码": "600000", "持有数量": 100}], "2026-08-10")
        assert dest == hist / "2026-08-10.json"
        assert json.loads(dest.read_text(encoding="utf-8")) == [
            {"代码": "600000", "持有数量": 100}
        ]

    def test_archive_same_date_overwrites(self, hist):
        """同日重复归档（如当天两次导入）后者覆盖前者 —— 归档的是「当日终态」。"""
        ph.archive_snapshot([{"代码": "600000"}], "2026-08-10")
        ph.archive_snapshot([{"代码": "000001"}], "2026-08-10")
        rows, resolved = ph.load_snapshot("2026-08-10")
        assert resolved == "2026-08-10"
        assert rows == [{"代码": "000001"}]


class TestLoad:
    def test_exact_date(self, hist):
        ph.archive_snapshot([{"代码": "600000"}], "2026-08-10")
        rows, resolved = ph.load_snapshot("2026-08-10")
        assert resolved == "2026-08-10" and rows == [{"代码": "600000"}]

    def test_latest_earlier_when_no_exact(self, hist):
        """持仓不变的日子没有归档：查询落在两份归档之间时取最近一份旧的。"""
        ph.archive_snapshot([{"代码": "600000"}], "2026-08-05")
        ph.archive_snapshot([{"代码": "000001"}], "2026-08-12")
        rows, resolved = ph.load_snapshot("2026-08-10")
        assert resolved == "2026-08-05" and rows == [{"代码": "600000"}]

    def test_later_archive_never_used(self, hist):
        """⚠️ 比查询日期**晚**的归档绝不可用 —— 那是用未来持仓筛过去新闻。"""
        ph.archive_snapshot([{"代码": "600000"}], "2026-08-12")
        rows, resolved = ph.load_snapshot("2026-08-10")
        assert (rows, resolved) == (None, None)

    def test_no_history_returns_none(self, hist):
        """归档目录都不存在（机制启用前）→ None，调用方回退当前快照。"""
        assert ph.load_snapshot("2026-08-10") == (None, None)
