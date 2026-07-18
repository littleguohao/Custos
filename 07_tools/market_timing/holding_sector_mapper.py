# -*- coding: utf-8 -*-
"""Map current holdings to TDX sectors using TQ get_relation."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import BASE, TDX_ROOT  # noqa: E402

OUT_DIR = BASE / "01_data" / "holdings"
DEFAULT_POSITIONS = BASE / "01_data" / "trades" / "current_positions.json"


def norm_code(x) -> str:
    if pd.isna(x): return ""
    s = str(x).strip()
    if s.endswith(".0"): s = s[:-2]
    return s.zfill(6) if s.isdigit() and len(s) <= 6 else s


def suffix(code: str) -> str:
    if code.startswith(("92", "8", "4")): return ".BJ"
    if code.startswith(("6", "5")): return ".SH"
    if code.startswith(("0", "1", "2", "3")): return ".SZ"
    return ""


def init_tq():
    user_path = TDX_ROOT / "PYPlugins" / "user"
    sys.path.insert(0, str(user_path))
    from tqcenter import tq  # type: ignore
    tq.initialize(__file__)
    return tq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(DEFAULT_POSITIONS), help="standardized current_positions.json")
    ap.add_argument("--date", default="")
    args = ap.parse_args()
    source = Path(args.input)
    if source.suffix.lower() == ".json":
        hold = pd.DataFrame(json.loads(source.read_text(encoding="utf-8")))
    else:
        hold = pd.read_excel(source, sheet_name="持仓数据")
    hold.columns = [str(c).strip() for c in hold.columns]
    hold["代码"] = hold["代码"].map(norm_code)
    hold = hold[hold["代码"].ne("") & hold["名称"].notna() & hold["代码"].ne("汇总")].copy()

    tq = init_tq()
    rows = []
    try:
        for _, r in hold.iterrows():
            code = r["代码"]
            tcode = code + suffix(code)
            try:
                rel = tq.get_relation(tcode)
            except Exception as e:
                rel = []
                err = repr(e)
            else:
                err = None
            industries = [x for x in rel if x.get("BlockType") == "行业"]
            concepts = [x for x in rel if x.get("BlockType") == "概念"]
            styles = [x for x in rel if x.get("BlockType") == "风格"]
            indices = [x for x in rel if x.get("BlockType") == "指数"]
            rows.append({
                "code": code,
                "name": r.get("名称"),
                "tdx_code": tcode,
                "holding_amount": r.get("持有金额"),
                "holding_pnl": r.get("持有盈亏"),
                "holding_pnl_pct": r.get("持有盈亏率"),
                "position_pct": r.get("仓位占比"),
                "holding_days": r.get("持仓天数"),
                "industry": industries[0].get("BlockName") if industries else "",
                "industry_code": industries[0].get("BlockCode") if industries else "",
                "concepts": [x.get("BlockName") for x in concepts],
                "concept_codes": [x.get("BlockCode") for x in concepts],
                "styles": [x.get("BlockName") for x in styles],
                "indices": [x.get("BlockName") for x in indices],
                "relation_error": err,
                "raw_relation": rel,
            })
    finally:
        try: tq.close()
        except Exception as e: print(f"[WARN] tq.close() failed: {e}", file=sys.stderr)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    date = args.date or pd.Timestamp.now().strftime("%Y-%m-%d")
    out_json = OUT_DIR / f"{date}_holding_sector_mapping.json"
    out_csv = OUT_DIR / f"{date}_holding_sector_mapping.csv"
    out_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    pd.DataFrame(rows).drop(columns=["raw_relation"]).to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(out_json)
    print(out_csv)
    print(pd.DataFrame(rows)[["code","name","industry","concepts","position_pct","holding_pnl_pct"]].to_string(index=False))


if __name__ == "__main__":
    main()
