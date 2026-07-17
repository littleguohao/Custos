# -*- coding: utf-8 -*-
# Import trades from xlsx into project-local database.
# Config-driven: update trades_config.json for path changes.
# Supports: full import (fresh) and incremental append (--append).
#
# Usage:
#   uv run python sync_trades.py                         # full import from config source
#   uv run python sync_trades.py --path path/to/new.xlsx # override source
#   uv run python sync_trades.py --append                 # append new trades only (no dupes)
#
# Output: all files under 01_data/trades/
from __future__ import annotations
import sys, json, warnings, shutil, argparse
from datetime import datetime
from pathlib import Path
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

SELF = Path(__file__).resolve()
OUT = SELF.parent.parents[2] / "01_data" / "trades"
CONFIG = OUT / "trades_config.json"
ARCHIVE = OUT / "_archive"
ARCHIVE.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def source_path(args_path: str) -> Path:
    if args_path:
        return Path(args_path)
    cfg = load_config()
    return Path(cfg["source_path"])


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["成交日期"] = pd.to_datetime(df["成交日期"], errors="coerce")
    df["成交时间"] = df["成交时间"].astype(str).str.strip()
    if "代码" in df.columns:
        df["代码"] = df["代码"].astype(str).str.replace(r"\.0$", "", regex=True)
        # standardize to TQ-like prefix
        df["代码"] = df["代码"].apply(lambda x: f"{x}.SH" if x.startswith(("6","5","9")) else (f"{x}.SZ" if x.startswith(("0","1","2","3")) else f"{x}.BJ") if len(str(x).strip()) > 0 else x)
    for col in ["成交数量","成交价格","成交金额","发生金额","费用"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def archive_source(src: Path) -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = ARCHIVE / f"{src.stem}_{ts}{src.suffix}"
    shutil.copy2(src, dst)
    print(f"archived: {dst}")


def merge_existing(path: Path, new: pd.DataFrame, key_fields: list[str], dedup: bool) -> pd.DataFrame:
    if path.exists() and path.stat().st_size > 10:
        existing = pd.read_csv(path)
        existing["成交日期"] = pd.to_datetime(existing["成交日期"], errors="coerce")
        if dedup and key_fields:
            merged = pd.concat([existing, new], ignore_index=True).drop_duplicates(subset=key_fields, keep="last")
        else:
            merged = pd.concat([existing, new], ignore_index=True)
        return merged.sort_values(["成交日期", "成交时间"]).reset_index(drop=True)
    return new.sort_values(["成交日期", "成交时间"]).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="", help="override xlsx path")
    ap.add_argument("--append", action="store_true", help="incremental import, keep existing")
    args = ap.parse_args()
    src = source_path(args.path)
    print(f"source: {src}")
    if not src.exists():
        raise SystemExit(f"xlsx not found: {src}")

    cfg = load_config()
    pos_raw = pd.read_excel(src, sheet_name=cfg["sheets"]["pos"])
    closed = pd.read_excel(src, sheet_name=cfg["sheets"]["closed"])
    trades = normalize(pd.read_excel(src, sheet_name=cfg["sheets"]["trades"]))

    # Archive raw source once
    archive_source(src)

    # --- positions raw (always overwrite — xlsx is snapshot) ---
    pos_raw.to_json(OUT / cfg["files"]["positions_raw"], orient="records", force_ascii=False, indent=2, default=str)
    # Clean: no "汇总" row, no codes that appear in closed sheet
    closed_codes = set(closed["代码"].astype(str).str.replace(r"\.0$","",regex=True).dropna())
    pos_clean = pos_raw[pos_raw["代码"].notna() & (pos_raw["代码"] != "汇总")].copy()
    pos_clean["代码"] = pos_clean["代码"].astype(str).str.replace(r"\.0$","",regex=True)
    pos_clean = pos_clean[~pos_clean["代码"].isin(closed_codes)]
    pos_clean.to_json(OUT / cfg["files"]["positions"], orient="records", force_ascii=False, indent=2, default=str)
    print(f"positions: {len(pos_clean)} (removed {len(pos_raw)-len(pos_clean)-1} closed rows)")

    # --- closed positions ---
    closed["代码"] = closed["代码"].astype(str).str.replace(r"\.0$","",regex=True)
    closed.to_json(OUT / cfg["files"]["closed"], orient="records", force_ascii=False, indent=2, default=str)
    print(f"closed: {len(closed)}")

    # --- all trades (append or full) ---
    all_path = OUT / cfg["files"]["all"]
    if args.append:
        merged = merge_existing(all_path, trades, ["成交日期","成交时间","代码","名称","交易类别","成交数量"], dedup=True)
    else:
        merged = trades
    merged.to_csv(all_path, index=False, encoding="utf-8-sig")
    print(f"trades: {len(merged)} ({'append' if args.append else 'full'})")

    # --- stock trades only ---
    stock = merged[merged["交易类别"].isin(["买入","卖出"])].copy()
    stock.to_json(OUT / cfg["files"]["stock"], orient="records", force_ascii=False, indent=2, default=str)
    print(f"stock trades: {len(stock)}")

    # --- summary ---
    print(f"\n=== current holdings (clean) ===")
    for _, r in pos_clean.iterrows():
        print(f"  {r.get('代码')} {r.get('名称')} 仓位{r.get('仓位占比','?'):.1%} 盈亏{r.get('持有盈亏率','?'):+.2%} 天数{r.get('持仓天数','?')} 成本{r.get('单位成本','?')} 现价{r.get('最新价','?')}")
    print(f"\nsaved under: {OUT}")


if __name__ == "__main__":
    main()
