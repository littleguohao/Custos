# -*- coding: utf-8 -*-
"""Merge dynamic holding fields into an enriched mapping for a selected date."""
from __future__ import annotations
import argparse,json
from pathlib import Path
BASE=Path(__file__).resolve().parents[2]; HOLD=BASE/'01_data'/'holdings'
def load(p,d): return json.loads(p.read_text(encoding='utf-8')) if p.exists() else d

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); a=ap.parse_args()
    orig_path=HOLD/f'{a.date}_holding_sector_mapping.json'; enr_path=HOLD/f'{a.date}_holding_sector_mapping_enriched.json'
    orig=load(orig_path,[]); enriched=load(enr_path,orig); orig_by={str(x.get('code')):x for x in orig}; merged=[]
    fields=['holding_amount','holding_pnl','holding_pnl_pct','position_pct','holding_days']
    for e in enriched:
        o=orig_by.get(str(e.get('code')),{}); merged.append({**e,**{k:o.get(k) for k in fields}})
    enr_path.write_text(json.dumps(merged,ensure_ascii=False,indent=2,default=str),encoding='utf-8'); print(enr_path)
if __name__=='__main__': main()
