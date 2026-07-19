# -*- coding: utf-8 -*-
"""同步指南针(Compass) 0AMV 数据到台账与 market_timing_input。

两件事（均 best-effort，任何失败打印 WARN 并 exit 0，绝不中断管线）：

1. **台账合并**：解析 day.vdat 日线主序列，把缺失日期的记录追加进
   ``01_data/market/0amv_observations.jsonl``（已存在日期的记录——任何
   source——跳过不重复）。默认只补最近 30 天，``--backfill-since``
   可指定更早起点做全量回填。
2. **当日自动填充**：``--date``（默认今天）是交易日且 compass 最新日期
   == 该日时，把 ``amv_0day``（= 最新 change_pct）写入
   ``01_data/market/{date}_market_timing_input.json``（文件存在才写；
   键已存在且 amv_0.quality=confirmed 则不覆盖）。

指南针运行时独占锁 day.vdat（PermissionError），解析器返回 error 字段，
本脚本此时优雅降级，现有人工输入路径不受影响。

CLI::

    uv run python 07_tools/market_timing/sync_compass_amv.py
    uv run python 07_tools/market_timing/sync_compass_amv.py --backfill-since 2025-11-11
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

TOOLS_DIR = Path(__file__).resolve().parents[1]
for _p in (TOOLS_DIR, TOOLS_DIR / "local_tdx"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from paths import BASE  # noqa: E402
import compass_amv  # noqa: E402
from runtime_guards import trading_day_status  # noqa: E402

LEDGER = BASE / "01_data" / "market" / "0amv_observations.jsonl"
MARKET_DIR = BASE / "01_data" / "market"
DEFAULT_WINDOW_DAYS = 30


def _existing_dates(ledger_path: Path) -> set:
    dates = set()
    if not ledger_path.is_file():
        return dates
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            dates.add(json.loads(line)["date"])
        except (json.JSONDecodeError, KeyError):
            continue
    return dates


def merge_ledger(records: list, ledger_path: Path) -> tuple[int, int]:
    """把 records 合并进台账，返回 (added, skipped_existing)。

    已存在日期（任何 source）跳过；change_pct 为 None 的记录（序列首条）
    无涨跌可记，静默跳过。
    """
    existing = _existing_dates(ledger_path)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    added, skipped = 0, 0
    lines = []
    for r in records:
        pct = r.get("change_pct")
        if pct is None:
            continue
        if r["date"] in existing:
            skipped += 1
            continue
        existing.add(r["date"])
        lines.append(json.dumps({
            "date": r["date"],
            "amv_change_pct": round(pct, 2),
            "as_of": r["date"],
            "quality": "confirmed",
            "source": "compass_day_vdat",
            "recorded_at": now,
        }, ensure_ascii=False))
        added += 1
    if lines:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with ledger_path.open("a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
    return added, skipped


def fill_amv_0day(target: str, change_pct: float, market_dir: Path) -> bool:
    """把 amv_0day 写入 {target}_market_timing_input.json，返回是否写入。

    文件不存在不写；amv_0day 已存在且 amv_0.quality=confirmed 不覆盖。
    """
    path = market_dir / f"{target}_market_timing_input.json"
    if not path.is_file():
        return False
    mkt = json.loads(path.read_text(encoding="utf-8"))
    if mkt.get("amv_0day") is not None and mkt.get("amv_0", {}).get("quality") == "confirmed":
        return False
    mkt["amv_0day"] = change_pct
    path.write_text(json.dumps(mkt, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="同步指南针 0AMV 到台账与 market_timing_input（best-effort）")
    ap.add_argument("--date", default=date.today().isoformat(), help="当日填充目标日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--backfill-since", default=None, help="台账回填起点 YYYY-MM-DD（默认最近 30 天）")
    args = ap.parse_args(argv)

    summary = {"added": 0, "skipped_existing": 0, "amv_0day_filled": False, "latest_date": None}
    try:
        since = args.backfill_since or (date.today() - timedelta(days=DEFAULT_WINDOW_DAYS)).isoformat()
        parsed = compass_amv.parse_amv_daily(since=since)
        if parsed.get("error") or not parsed["records"]:
            print(f"[WARN] compass 0AMV 解析失败，保持人工输入路径: {parsed.get('error', 'no_records')}")
            summary["error"] = parsed.get("error", "no_records")
            print(json.dumps(summary, ensure_ascii=False))
            return 0

        summary["latest_date"] = parsed["latest_date"]
        added, skipped = merge_ledger(parsed["records"], LEDGER)
        summary["added"], summary["skipped_existing"] = added, skipped
        if added:
            print(f"[OK] 0AMV 台账合并: +{added} 条（跳过已存在 {skipped} 条）")

        # 当日自动填充：交易日且 compass 最新日期 == 目标日
        target = args.date
        is_trading = trading_day_status(target).get("is_trading_day") is True
        if is_trading and parsed["latest_date"] == target:
            latest_pct = parsed["records"][-1]["change_pct"]
            if latest_pct is not None:
                summary["amv_0day_filled"] = fill_amv_0day(target, latest_pct, MARKET_DIR)
                if summary["amv_0day_filled"]:
                    print(f"[OK] amv_0day 已写入 {target}_market_timing_input.json (value={latest_pct}%)")
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 —— best-effort，绝不炸管线
        print(f"[WARN] sync_compass_amv 失败（不中断管线）: {exc}")
        summary["error"] = str(exc)
        print(json.dumps(summary, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
