# -*- coding: utf-8 -*-
"""Index E:\\O_DATA downloaded TDX CSV files."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = Path(r"E:\O_DATA")
OUT_JSON = BASE / "01_data" / "local_tdx" / "e_odata_index.json"
OUT_CSV = BASE / "01_data" / "local_tdx" / "e_odata_index.csv"


def main() -> None:
    rows = []
    for p in sorted(DATA_DIR.glob("*.csv")):
        code = p.name.split("-")[0]
        row = {
            "code": code,
            "path": str(p),
            "name": p.name,
            "size": p.stat().st_size,
            "last_write_time": pd.Timestamp.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "rows": 0,
            "min_date": None,
            "max_date": None,
            "ok": False,
            "error": None,
        }
        try:
            df = pd.read_csv(p, usecols=["Date"])
            row["rows"] = int(len(df))
            if len(df):
                row["min_date"] = str(df["Date"].min())
                row["max_date"] = str(df["Date"].max())
                row["ok"] = True
        except Exception as e:
            row["error"] = repr(e)
        rows.append(row)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame(rows).to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    ok = [r for r in rows if r["ok"]]
    empty = [r for r in rows if not r["ok"] and not r["error"]]
    print("files", len(rows))
    print("ok", len(ok))
    print("empty", len(empty))
    print("errors", len([r for r in rows if r["error"]]))
    print("min_date", min(r["min_date"] for r in ok if r["min_date"]) if ok else None)
    print("max_date", max(r["max_date"] for r in ok if r["max_date"]) if ok else None)
    print(OUT_JSON)
    print(OUT_CSV)


if __name__ == "__main__":
    main()
