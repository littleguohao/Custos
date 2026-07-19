# -*- coding: utf-8 -*-
"""14:45 盘中市场快照采集（TQ-Local HTTP get_market_snapshot）。

抓取 999999.SH / 880005.SH / 880006.SH / 880001.SH 四个指数的实时快照，
写入 ``01_data/market/{date}_intraday_snapshot.json`` 并打印一行 JSON 摘要。

best-effort 语义：TdxW 未运行或任一指数失败都不会 raise、exit 恒为 0；
失败体现在 error / quality 字段（quality=unavailable），绝不让 run_1445 挂。

CLI::

    uv run python 07_tools/market_timing/collect_intraday_snapshot.py --date YYYY-MM-DD
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
LOCAL_TDX_DIR = TOOLS_DIR / "local_tdx"
if str(LOCAL_TDX_DIR) not in sys.path:
    sys.path.insert(0, str(LOCAL_TDX_DIR))

from paths import MARKET_DIR  # noqa: E402
import tq_http  # noqa: E402

SOURCE = "tq_http_snapshot"

# 指数 → (角色, 字段映射)。字段含义见 tq-tq-local SKILL 文档与实测：
# 999999.SH 上证指数；880005.SH Now=上涨家数；
# 880006.SH Now=涨停数/Max=曾涨停/Min=跌停；880001.SH Amount=成交额(万元)
INDEX_SPECS: dict[str, dict[str, Any]] = {
    "999999.SH": {
        "role": "sh_index",
        "fields": {
            "Now": "now", "LastClose": "last_close",
            "UpHome": "up_home", "DownHome": "down_home", "Amount": "amount",
        },
    },
    "880005.SH": {"role": "advance_count", "fields": {"Now": "up_count"}},
    "880006.SH": {
        "role": "limit_stats",
        "fields": {"Now": "limit_up", "Max": "ever_limit_up", "Min": "limit_down"},
    },
    "880001.SH": {"role": "turnover", "fields": {"Amount": "amount_wan"}},
}


def _num(value: Any) -> Any:
    """快照数值为字符串；可解析则转 float，否则保留原值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def collect() -> dict:
    """采集全部指数快照，任何失败结构化返回，绝不 raise。"""
    as_of = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    indices: dict[str, Any] = {}
    ok_count = 0
    for code, spec in INDEX_SPECS.items():
        resp = tq_http.snapshot(code)
        if not resp["ok"]:
            indices[code] = {"role": spec["role"], "ok": False, "error": resp["error"]}
            continue
        raw = resp["value"] if isinstance(resp["value"], dict) else {}
        entry = {"role": spec["role"], "ok": True}
        for src_key, dst_key in spec["fields"].items():
            entry[dst_key] = _num(raw.get(src_key))
        indices[code] = entry
        ok_count += 1

    total = len(INDEX_SPECS)
    if ok_count == total:
        quality = "ok"
    elif ok_count > 0:
        quality = "partial"
    else:
        quality = "unavailable"
    result: dict[str, Any] = {
        "as_of": as_of,
        "source": SOURCE,
        "quality": quality,
        "indices_ok": ok_count,
        "indices_total": total,
        "indices": indices,
        "error": None,
    }
    if quality == "unavailable":
        first_err = next((v["error"] for v in indices.values() if not v["ok"]), None)
        result["error"] = first_err or {"code": "snapshot_failed"}
    return result


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="14:45 盘中市场快照采集（TQ-Local HTTP，best-effort）")
    parser.add_argument("--date", required=True, help="采集日期 YYYY-MM-DD，用于输出文件命名")
    args = parser.parse_args(argv)

    result = collect()
    result["date"] = args.date

    MARKET_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MARKET_DIR / f"{args.date}_intraday_snapshot.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "date": args.date,
        "quality": result["quality"],
        "indices_ok": result["indices_ok"],
        "output": str(out_path),
    }
    if result["error"]:
        summary["error"] = result["error"]
    print(json.dumps(summary, ensure_ascii=False))
    return 0  # best-effort：失败也 exit 0，错误体现在 quality/error 字段


if __name__ == "__main__":
    raise SystemExit(main())
