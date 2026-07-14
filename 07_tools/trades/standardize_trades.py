# -*- coding: utf-8 -*-
# Standardize trade records from a user-provided xlsx into the project trades db.
#
# Usage:
#   uv run python standardize_trades.py --src <path-to-xlsx>
#   uv run python standardize_trades.py --src "C:/Users/gh/Downloads/交易记录2.xlsx"
#
# If --src is omitted, falls back to the latest xlsx in --src-dir (default ~/Downloads).
# The xlsx must contain three sheets: 持仓数据, 已清仓, 交易记录.
#
# Outputs (project single source of truth):
#   01_data/trades/trades_all.csv         — 全量流水 (cleaned)
#   01_data/trades/trades_stock.json      — 股票买卖明细
#   01_data/trades/closed_positions.json  — 已清仓汇总
#   01_data/trades/current_positions.json — 当前持仓快照
#   01_data/trades/_import_meta.json      — 导入元数据 (源路径 + 时间 + 行数)
from __future__ import annotations
import argparse
import json
import sys
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

BASE = Path("C:/Users/gh/.openclaw-tdxclaw/workspace/strategy_team")
OUT_DIR = BASE / "01_data" / "trades"
DEFAULT_SRC_DIR = Path("C:/Users/gh/Downloads")


def find_latest_xlsx(src_dir: Path) -> Path | None:
    """Fallback: pick the most recent xlsx in Downloads that contains 交易."""
    candidates = []
    for p in src_dir.glob("*.xlsx"):
        if not p.name.startswith("~$"):
            candidates.append(p)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def clean_code(s: str) -> str:
    s = str(s).strip()
    s = s.replace(".0", "", 1) if s.endswith(".0") else s
    return s.zfill(6) if s.isdigit() and len(s) < 6 else s


def main() -> None:
    ap = argparse.ArgumentParser(prog="standardize_trades", description="Import trade records from a user-provided xlsx into strategy_team 01_data/trades/. Every time you download a new xlsx, pass --src.")
    ap.add_argument("--src", required=True, help="path to xlsx file, e.g. C:/Users/gh/Downloads/交易记录3.xlsx")
    args = ap.parse_args()
    src = Path(args.src)
    if not src.exists():
        raise FileNotFoundError(src)


    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[trades] src: {src}")
    print(f"[trades] mtime: {datetime.fromtimestamp(src.stat().st_mtime).isoformat()}")

    # ── 1. 交易记录 流水 ──
    raw = pd.read_excel(src, sheet_name="交易记录", dtype={"代码": str, "名称": str})
    trades = raw.dropna(subset=["代码", "名称"]).copy()
    trades["成交日期"] = pd.to_datetime(trades["成交日期"], errors="coerce")
    trades["成交时间"] = trades["成交时间"].astype(str).str.strip()
    trades["代码"] = trades["代码"].apply(clean_code)
    for col in ["成交数量", "成交价格", "成交金额", "发生金额", "费用"]:
        trades[col] = pd.to_numeric(trades[col], errors="coerce")

    col_order = ["成交日期", "成交时间", "代码", "名称", "交易类别",
                 "成交数量", "成交价格", "成交金额", "发生金额", "费用", "备注"]
    trades = trades[[c for c in col_order if c in trades.columns]]
    trades = trades.sort_values(["成交日期", "成交时间"]).reset_index(drop=True)

    trades_csv = OUT_DIR / "trades_all.csv"
    trades.to_csv(trades_csv, index=False, encoding="utf-8-sig")
    print(f"[trades] trades_all.csv: {len(trades)} rows")

    stock = trades[trades["交易类别"].isin(["买入", "卖出"])].copy()
    stock_json = OUT_DIR / "trades_stock.json"
    stock.to_json(stock_json, orient="records", force_ascii=False, indent=2, date_format="iso")
    print(f"[trades] trades_stock.json: {len(stock)} rows")

    # ── 2. 已清仓 ──
    cls = pd.read_excel(src, sheet_name="已清仓", dtype={"代码": str})
    cls["代码"] = cls["代码"].apply(clean_code)
    cls["清仓日期"] = pd.to_datetime(cls["清仓日期"], errors="coerce")
    cls["建仓日期"] = pd.to_datetime(cls["建仓日期"], errors="coerce")
    cls_rows = cls.to_dict(orient="records")
    (OUT_DIR / "closed_positions.json").write_text(
        json.dumps(cls_rows, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"[trades] closed_positions.json: {len(cls_rows)} entries")

    # ── 3. 当前持仓 ──
    pos = pd.read_excel(src, sheet_name="持仓数据", dtype={"代码": str})
    pos = pos[pos["代码"].notna() & (pos["代码"] != "汇总")].copy()
    pos["代码"] = pos["代码"].apply(clean_code)
    pos_rows = pos.to_dict(orient="records")
    (OUT_DIR / "current_positions.json").write_text(
        json.dumps(pos_rows, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"[trades] current_positions.json: {len(pos_rows)} holdings")

    # ── 4. 导入元数据 ──
    meta = {
        "imported_at": datetime.now().isoformat(timespec="seconds"),
        "source_path": str(src),
        "source_mtime": datetime.fromtimestamp(src.stat().st_mtime).isoformat(),
        "rows": {
            "trades_all": int(len(trades)),
            "trades_stock": int(len(stock)),
            "closed_positions": int(len(cls_rows)),
            "current_positions": int(len(pos_rows)),
        },
        "sheets": ["持仓数据", "已清仓", "交易记录"],
    }
    meta_path = OUT_DIR / "_import_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[trades] _import_meta.json: {meta_path}")

    # ── 5. 摘要 ──
    print("\n=== 当前持仓摘要 ===")
    for r in pos_rows:
        try:
            print(f"  {r['代码']} {r['名称']} 仓位{r['仓位占比']:.1%} 盈亏{r['持有盈亏率']:+.2%} 天数{r['持仓天数']} 成本{r['单位成本']} 现价{r['最新价']}")
        except (TypeError, KeyError):
            print(f"  {r}")


if __name__ == "__main__":
    main()
