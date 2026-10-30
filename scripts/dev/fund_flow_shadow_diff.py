# -*- coding: utf-8 -*-
"""个股资金流新旧源只读对账（不写生产台账）。

旧源 = collect_fund_flow 的东财 push2；新源 = Kimi Work 东财妙想。
默认产物落 artifacts/logs/fund_flow_shadow/，供切换主备前人工复核。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from custos.core.paths import ARTIFACT_LOGS, cn_now, cn_today
from custos.datasource.collect import collect_fund_flow as cff

FIELDS = (
    "price",
    "change_pct",
    "main_net_inflow",
    "main_net_pct",
    "super_large_net",
    "super_large_pct",
    "large_net",
    "large_pct",
    "medium_net",
    "medium_pct",
    "small_net",
    "small_pct",
)
AMOUNT_FIELDS = {x for x in FIELDS if x.endswith("_net") or x.endswith("_inflow")}


def _compare_field(old: list[dict], new: list[dict], field: str) -> dict[str, Any]:
    old_map = {str(x.get("code")): x.get(field) for x in old}
    new_map = {str(x.get("code")): x.get(field) for x in new}
    diffs = []
    for code in sorted(set(old_map) & set(new_map)):
        a, b = old_map[code], new_map[code]
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            continue
        diffs.append((code, a, b, b - a, abs(b - a)))
    abs_values = [x[4] for x in diffs]
    rel_values = [x[4] / max(abs(x[1]), 1.0) for x in diffs]
    tolerance = 1e6 if field in AMOUNT_FIELDS else 0.005
    return {
        "n_compared": len(diffs),
        "n_exact": sum(x[4] == 0 for x in diffs),
        "n_within_display_tolerance": sum(x[4] <= tolerance for x in diffs),
        "max_abs": max(abs_values, default=None),
        "median_abs": statistics.median(abs_values) if abs_values else None,
        "max_rel": max(rel_values, default=None),
        "display_tolerance": tolerance,
        "largest_diffs": [
            {"code": x[0], "legacy": x[1], "kimiwork": x[2], "diff": x[3]}
            for x in sorted(diffs, key=lambda y: y[4], reverse=True)[:5]
        ],
    }


def compare(old: list[dict], new: list[dict]) -> dict[str, Any]:
    old_codes = [str(x.get("code")) for x in old]
    new_codes = [str(x.get("code")) for x in new]
    old_set, new_set = set(old_codes), set(new_codes)
    common = old_set & new_set
    return {
        "legacy_rows": len(old),
        "kimiwork_rows": len(new),
        "common_codes": len(common),
        "only_legacy": sorted(old_set - new_set),
        "only_kimiwork": sorted(new_set - old_set),
        "same_order_prefix": next(
            (i for i, (a, b) in enumerate(zip(old_codes, new_codes)) if a != b),
            min(len(old_codes), len(new_codes)),
        ),
        "fields": {field: _compare_field(old, new, field) for field in FIELDS},
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=cn_today().isoformat())
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    old = cff.legacy_stock_rows()
    new = cff.kimiwork_stock_rows(limit=args.limit)
    if not old or not new:
        print("[ERR] 任一侧为空，不写对账产物", file=sys.stderr)
        return 1
    report = {
        "date": args.date,
        "collected_at": cn_now().isoformat(),
        "legacy_source": "eastmoney_direct_api",
        "kimiwork_source": "kimiwork_dongcai_screener",
        "note": "金额为元；display tolerance 仅用于识别内置中文单位展示舍入，不代表策略容差。",
        **compare(old, new),
    }
    out = (
        Path(args.out)
        if args.out
        else ARTIFACT_LOGS / "fund_flow_shadow" / f"{args.date}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "legacy_rows",
                    "kimiwork_rows",
                    "common_codes",
                    "same_order_prefix",
                )
            },
            ensure_ascii=False,
        )
    )
    print(f"shadow diff -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
