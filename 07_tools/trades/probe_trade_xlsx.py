# -*- coding: utf-8 -*-
"""Probe trade record xlsx fields."""
import sys
import pandas as pd
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
p = Path(r"C:\Users\gh\Downloads\交易记录.xlsx")
xls = pd.ExcelFile(p)
print("sheets:", xls.sheet_names)
for s in xls.sheet_names[:5]:
    df = pd.read_excel(p, sheet_name=s)
    print("\n=== sheet:", s, "shape:", df.shape)
    print("columns:", list(df.columns))
    print(df.head(10).to_string(max_cols=20))
    print(df.tail(5).to_string(max_cols=20))
