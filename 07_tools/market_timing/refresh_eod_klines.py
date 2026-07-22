# -*- coding: utf-8 -*-
"""Best-effort refresh of EOD daily K-lines into local vipdoc via TQ-Local.

Runs before refresh_market_indices in the 17:00 post-close pipeline so that
vipdoc .day files contain today's EOD bar (refresh_kline writes it directly
into the TdxW local cache). Requires TdxW.exe running; any failure degrades
to WARN + exit 0 (never aborts the pipeline).

stdout prints a single JSON summary line:
  {"refreshed": bool, "verified": bool, "latest_date": str|None, ...}
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import BASE  # noqa: E402
from code_utils import norm_code  # noqa: E402

LOCAL_TDX_DIR = BASE / "07_tools" / "local_tdx"
if str(LOCAL_TDX_DIR) not in sys.path:
    sys.path.insert(0, str(LOCAL_TDX_DIR))

import local_tdx_data as ltd  # type: ignore  # noqa: E402
import tq_http  # type: ignore  # noqa: E402

# 指数：上证 / 深成指 / 创业板指 / 科创50 / 北证50
INDEX_CODES = ["999999.SH", "399001.SZ", "399006.SZ", "000688.SH", "899050.BJ"]

# 880 系列：全市场成交额 / 涨家数 / 涨跌停 / 细分指数
BREADTH_880_CODES = ["880001.SH", "880005.SH", "880006.SH", "880390.SH", "880863.SH"]

POSITIONS_PATH = BASE / "01_data" / "trades" / "current_positions.json"

# tqcenter 建议单次 refresh_kline 不要塞太多标的（会堵塞），分批调用
VERIFY_CODE = "999999.SH"


def load_holdings_codes(positions_path: Path = POSITIONS_PATH) -> list[str]:
    """当前持仓代码（norm_code 转换，如 600150 -> 600150.SH / 920808 -> 920808.BJ）。"""
    if not positions_path.exists():
        return []
    try:
        rows = json.loads(positions_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    codes = []
    for row in rows if isinstance(rows, list) else []:
        raw = row.get("代码") if isinstance(row, dict) else None
        if raw:
            codes.append(norm_code(str(raw)))
    return codes


def build_batches(holdings_codes: list[str]) -> list[dict]:
    """分批：批次1 = 指数 + 880 系列；批次2 = 当前持仓（无持仓则省略）。"""
    batches = [{"name": "indices+880", "stock_list": list(INDEX_CODES) + list(BREADTH_880_CODES)}]
    if holdings_codes:
        batches.append({"name": "holdings", "stock_list": list(holdings_codes)})
    return batches


def verify_latest_date(code: str = VERIFY_CODE, read_fn=None) -> str | None:
    """refresh 后抽查 vipdoc 最新交易日；失败返回 None。"""
    read_fn = read_fn or ltd.read_vipdoc_daily
    try:
        df = read_fn(code)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    dt = df.iloc[-1].get("date")
    return dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]


def refresh_all(target_date: str, holdings_codes: list[str], call_fn=None) -> dict:
    """分批调用 refresh_kline，返回摘要。任何失败都不 raise。"""
    call_fn = call_fn or tq_http.call
    batches = build_batches(holdings_codes)
    batch_results = []
    all_ok = True
    t0 = time.time()
    for batch in batches:
        b_t0 = time.time()
        r = call_fn("refresh_kline", {"stock_list": batch["stock_list"], "period": "1d"})
        b_dur = round(time.time() - b_t0, 2)
        ok = bool(r.get("ok"))
        all_ok = all_ok and ok
        batch_results.append({
            "name": batch["name"],
            "count": len(batch["stock_list"]),
            "ok": ok,
            "duration_sec": b_dur,
            "error": r.get("error"),
        })
    duration = round(time.time() - t0, 2)

    latest_date = verify_latest_date() if all_ok else None
    verified = latest_date == target_date
    return {
        "date": target_date,
        "refreshed": all_ok,
        "verified": verified,
        "latest_date": latest_date,
        "duration_sec": duration,
        "batches": batch_results,
    }


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args(argv)

    holdings = load_holdings_codes()
    summary = refresh_all(args.date, holdings)

    if not summary["refreshed"]:
        print(f"[WARN] refresh_eod_klines 未全部成功（best-effort，不中断）: "
              f"{[b['name'] for b in summary['batches'] if not b['ok']]}")
    elif not summary["verified"]:
        print(f"[WARN] refresh 成功但 vipdoc 最新日期={summary['latest_date']} != {args.date}")
    else:
        print(f"[OK] EOD K线已刷新并验证至 {summary['latest_date']} "
              f"(耗时 {summary['duration_sec']}s)")
    print(json.dumps(summary, ensure_ascii=False))
    return 0  # best-effort：永不因本阶段失败中断管线


if __name__ == "__main__":
    sys.exit(main())
