# -*- coding: utf-8 -*-
"""持仓快照历史归档（TODO #49）。

`current_positions.json` 只有**当前一份**、没有历史版本，导致
`rss_filter.entities(date)` 回填历史日期时只能用今天的持仓筛那天的新闻
（结论不可复现）。本模块提供最小归档机制：

- **写**：两个快照写入点（`incremental_ledger` 增量导入 /
  `standardize_trades` 全量导入）在成功写入当前快照后，把同一内容
  按日期归档到 ``positions_history/{date}.json``。
- **读**：``load_snapshot(date)`` 取 **≤ date 的最近一份**归档——
  持仓不变的日子不会产生归档，精确匹配会大量落空，所以读语义是
  「那天收盘时的持仓 = 最近一次变动后的快照」。一份归档都没有时返回
  ``(None, None)``，由调用方决定回退策略。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from custos.core.paths import TRADES_DIR

# 模块级常量便于测试 monkeypatch（同 run_* 的 LOG_DIR 惯例）
HISTORY_DIR = TRADES_DIR / "positions_history"


def archive_snapshot(rows: list[dict], date: str) -> Path:
    """把持仓快照按日期归档（temp + os.replace，读者永远看不到半个文件）。"""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    dest = HISTORY_DIR / f"{date}.json"
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    os.replace(tmp, dest)
    return dest


def load_snapshot(date: str) -> tuple[list[dict] | None, str | None]:
    """取 ≤ date 的最近一份归档。返回 ``(rows, 归档日期)``；无归档返回 ``(None, None)``。

    ISO 日期字符串可字典序比较，文件名即日期，直接排序即可。
    """
    if not HISTORY_DIR.is_dir():
        return None, None
    eligible = sorted(d for p in HISTORY_DIR.glob("*.json") if (d := p.stem) <= date)
    if not eligible:
        return None, None
    resolved = eligible[-1]
    rows = json.loads((HISTORY_DIR / f"{resolved}.json").read_text(encoding="utf-8"))
    return rows, resolved
