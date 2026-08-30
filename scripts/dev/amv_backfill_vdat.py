# -*- coding: utf-8 -*-
"""一次性数据手术：把指南针 day.vdat 全历史回填进 0AMV 台账（owner 2026-08-30 拍板：
「2000-01-04 至今整理到一个台账文件，后续只维护一个文件」）。

背景：vdat 主序列（2000-01-04→2026-07-17，6430 条）停更于 2026-07-17；人工/系统
台账 `data/market/0amv_observations.jsonl`（2024-01-02 起）此后独立维护。
本脚本把 vdat 中**台账没有的日期**（主要 2000-01-04→2024-01-01）写进台账，
合并后按日期排序落盘 ⇒ 台账成为全量单源（`backtest_factors.load_amv_regime`
v0.150 起从台账全量读，vdat 仅作台账缺失时的兜底）。

口径（钉死）：
- **已有日期不动**：台账现存记录优先（人工修正值 > vdat 值；实测重叠 614 日零冲突），
  vdat 只补缺；同日多条台账记录原样保留（不改写、不去重）。
- 回填条目形态：{date, amv_change_pct, as_of=date, quality="confirmed",
  source="compass_day_vdat", recorded_at=回填时间}——与 vdat 历史拼接段的原口径一致。
- **幂等**：已存在的日期跳过 ⇒ 可复跑，第二次应新增 0 条。
- 数据手术安全：写前备份（``.bak_YYYYMMDD``），原子写（temp + os.replace）。

用法（仓库根目录）：
    uv run python scripts/dev/amv_backfill_vdat.py            # 实跑
    uv run python scripts/dev/amv_backfill_vdat.py --dry-run  # 只统计不写
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[2]


def load_ledger(ledger_path: Path) -> list[dict[str, Any]]:
    """读台账全部行（保留原记录不动；坏行跳过并计数告警，不静默丢数据）。"""
    if not ledger_path.is_file():
        return []
    out: list[dict[str, Any]] = []
    bad = 0
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            bad += 1
    if bad:
        print(
            f"[WARN] 台账 {bad} 行 JSON 解析失败（已跳过，不进入合并）", file=sys.stderr
        )
    return out


# vdat 实际序列起点是 1993-01-03（series_start 元数据，首日记录 1993-01-04）。
# v0.150 按 owner 第一版口径只填 2000-01-04 起；v0.151 owner 拍板「全历史都补进来」
# ⇒ 起点放到 1993-01-01（早于序列起点即可，解析层 since 过滤 + 条目层 d>=常量双保险）。
BACKFILL_SINCE = "1993-01-01"


def vdat_backfill_entries(
    existing_dates: set[str], now_iso: str, root: Optional[str] = None
) -> list[dict[str, Any]]:
    """vdat 全历史中台账没有的日期 → 回填条目列表（confirmed/compass_day_vdat）。"""
    from custos.datasource.local_tdx import compass_amv  # noqa: PLC0415

    parsed = compass_amv.parse_amv_daily(since=BACKFILL_SINCE, root=root)
    if parsed.get("error"):
        raise RuntimeError(f"vdat 读取失败: {parsed['error']}")
    out = []
    for r in parsed.get("records") or []:
        d = str(r.get("date") or "")[:10]
        if len(d) != 10 or d in existing_dates or d < BACKFILL_SINCE:
            continue
        if r.get("change_pct") is None:
            continue
        out.append(
            {
                "date": d,
                "amv_change_pct": float(r["change_pct"]),
                "as_of": d,
                "quality": "confirmed",
                "source": "compass_day_vdat",
                "recorded_at": now_iso,
            }
        )
    return out


def backfill(
    ledger_path: Path,
    root: Optional[str] = None,
    now_iso: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """回填主流程：读台账 → vdat 补缺 → （非 dry-run）备份 + 原子写。幂等。

    返回统计：existing/added/total/first/last/backup。
    """
    if now_iso is None:
        from custos.core.paths import cn_now  # noqa: PLC0415

        now_iso = cn_now().isoformat(timespec="seconds")
    existing = load_ledger(ledger_path)
    existing_dates = {str(r.get("date") or "") for r in existing}
    added = vdat_backfill_entries(existing_dates, now_iso, root=root)
    merged = existing + added
    # 按日期排序（Python sort 稳定：同日期的既有记录保持原相对顺序）
    merged.sort(key=lambda r: str(r.get("date") or ""))
    stats = {
        "existing": len(existing),
        "added": len(added),
        "total": len(merged),
        "first": merged[0]["date"] if merged else None,
        "last": merged[-1]["date"] if merged else None,
        "dry_run": dry_run,
        "backup": None,
    }
    if dry_run or not added:
        return stats
    # 数据手术：先备份再原子写（temp + os.replace）。
    # 备份名带时分秒：同日多次回填不互相覆盖（v0.151 前曾同日覆盖过一次，教训）
    backup = ledger_path.with_suffix(
        ledger_path.suffix + f".bak_{now_iso[:10].replace('-', '')}"
        f"_{now_iso[11:19].replace(':', '')}"
    )
    shutil.copy2(ledger_path, backup)
    tmp = ledger_path.with_suffix(ledger_path.suffix + ".tmp")
    tmp.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in merged),
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, ledger_path)
    stats["backup"] = str(backup)
    return stats


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="0AMV 台账：vdat 全历史回填（幂等）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写")
    ap.add_argument(
        "--ledger",
        default=str(BASE / "data" / "market" / "0amv_observations.jsonl"),
        help="台账路径（默认 data/market/0amv_observations.jsonl）",
    )
    args = ap.parse_args(argv)
    stats = backfill(Path(args.ledger), dry_run=args.dry_run)
    print(
        f"[{'DRY' if stats['dry_run'] else 'OK'}] 回填 {stats['added']} 条；"
        f"台账 {stats['existing']} → {stats['total']} 条，"
        f"范围 {stats['first']} → {stats['last']}"
        + (f"；备份 {stats['backup']}" if stats["backup"] else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
