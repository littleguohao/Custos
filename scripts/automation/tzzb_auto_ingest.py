# -*- coding: utf-8 -*-
"""交易日 16:50 投资账本成交同步（纯 Python、无聊天）。

lastSyncTime 必须覆盖目标日 15:00；当日有成交才写标准 CSV 并经 incremental_ledger
唯一入口幂等入库。零成交不自动登记 no_trades；未覆盖收盘只返回 waiting_sync。
本任务不能触发 PC 客户端同步，每次检查投资账本插件均会消耗 credits。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from custos.datasource import tzzb_trades

CST = timezone(timedelta(hours=8))


def _is_trading_day(day: str, repo: Path) -> bool:
    result = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "python",
            "src/custos/datasource/trading_calendar.py",
            "--check-date",
            day,
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"交易日历检查失败：{result.stderr.strip()[:300]}")
    output = result.stdout.strip()
    start, end = output.find("{"), output.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("交易日历输出缺少 JSON 对象")
    payload = json.loads(output[start : end + 1])
    if "is_trading_day" not in payload:
        raise RuntimeError("交易日历输出缺少 is_trading_day")
    return payload["is_trading_day"] is True


def _import_csv(csv_path: Path, day: str, repo: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "python",
            "src/custos/core/trades/incremental_ledger.py",
            "--input",
            str(csv_path),
            "--date",
            day,
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"投资账本成交入库失败：{(result.stderr or result.stdout).strip()[-500:]}"
        )
    text = result.stdout.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("投资账本入库审计缺少 JSON 对象")
    audit = json.loads(text[start : end + 1])
    appended = audit.get("appended_rows")
    skipped = audit.get("duplicate_rows_skipped")
    if type(appended) is not int or appended < 0:
        raise RuntimeError("入库审计缺少有效 appended_rows")
    if type(skipped) is not int or skipped < 0:
        raise RuntimeError("入库审计缺少有效 duplicate_rows_skipped")
    return audit


def run_sync(
    day: str,
    *,
    repo: Path = REPO,
    trading_day_fn: Callable[[str], bool] | None = None,
    fetch_fn: Callable[[str], tuple[list[dict[str, Any]], str]] | None = None,
    import_fn: Callable[[Path, str, Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """执行一次同步；依赖可注入，测试不触网、不写真实台账。"""
    datetime.strptime(day, "%Y-%m-%d")
    repo = Path(repo)
    trading_day_fn = trading_day_fn or (lambda d: _is_trading_day(d, repo))
    fetch_fn = fetch_fn or tzzb_trades.fetch_trades
    import_fn = import_fn or _import_csv
    base: dict[str, Any] = {
        "date": day,
        "trading_day": True,
        "source": "tzzb_mcp",
        "last_sync": "",
        "row_count": 0,
        "appended_rows": 0,
        "duplicate_rows_skipped": 0,
    }
    if not trading_day_fn(day):
        return {
            **base,
            "trading_day": False,
            "status": "skipped",
            "summary": "非交易日，未查询投资账本",
        }
    try:
        rows, last_sync = fetch_fn(day)
    except tzzb_trades.TzzbError as exc:
        if "同步时间" not in str(exc):
            raise
        return {
            **base,
            "status": "waiting_sync",
            "summary": str(exc),
        }
    base.update(last_sync=last_sync, row_count=len(rows))
    if not rows:
        return {
            **base,
            "status": "no_trades",
            "summary": "投资账本当日 0 笔成交；仅记录，不写无交易确认",
        }

    csv_path = repo / "artifacts" / "cache" / "tzzb" / f"{day}_trades.csv"
    tzzb_trades.write_trade_csv(rows, csv_path)
    audit = import_fn(csv_path, day, repo)
    appended = int(audit["appended_rows"])
    skipped = int(audit["duplicate_rows_skipped"])
    return {
        **base,
        "status": "imported" if appended else "unchanged",
        "appended_rows": appended,
        "duplicate_rows_skipped": skipped,
        "csv": str(csv_path),
        "summary": (
            f"投资账本成交 {len(rows)} 笔：新增 {appended} 笔，"
            f"重复跳过 {skipped} 笔（PC 同步 {last_sync}）"
        ),
    }


def run(ctx: dict[str, Any]) -> dict[str, Any]:
    day = datetime.now(CST).date().isoformat()
    return {"artifact": run_sync(day)}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=datetime.now(CST).date().isoformat())
    args = parser.parse_args(argv)
    print(json.dumps({"artifact": run_sync(args.date)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
