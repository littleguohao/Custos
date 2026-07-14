# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse, json
from runtime_guards import write_runtime_gate

p=argparse.ArgumentParser()
p.add_argument('--date', required=True)
p.add_argument('--require-trading-day', action='store_true')
a=p.parse_args()
r=write_runtime_gate(a.date)
print(json.dumps(r,ensure_ascii=False,indent=2))
if a.require_trading_day and r['calendar']['is_trading_day'] is not True:
    raise SystemExit(3)
