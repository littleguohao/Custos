# -*- coding: utf-8 -*-
"""Inspect trade history xlsx and dump headers + sample rows."""
import os
from __future__ import annotations
import sys
import json
from pathlib import Path
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(os.environ.get("TRADE_SOURCE", "trades.xlsx"))
OUT_DIR = Path(__file__).resolve().parent.parent / "01_data" / "trades"
OUT_DIR.mkdir(parents=True, exist_ok=True)

sheets = pd.read_excel(SRC, sheet_name=None)
summary = {}
for name, df in sheets.items():
    sample = df.head(5).copy()
    sample.columns = [str(c) for c in sample.columns]
    summary[name] = {
        "rows": int(len(df)),
        "cols": [str(c) for c in df.columns],
        "dtypes": {str(c): str(df[c].dtype) for c in df.columns},
        "head": sample.fillna("").to_dict(orient="records"),
    }

out = OUT_DIR / "_inspect_raw.json"
out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(out)
print(json.dumps(summary, ensure_ascii=False, indent=2, default=str)[:8000])
