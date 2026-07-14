# -*- coding: utf-8 -*-
"""Record explicit manual position updates for a date; no embedded securities."""
from __future__ import annotations
import argparse,json
from datetime import datetime
from pathlib import Path
BASE=Path(__file__).resolve().parents[2]; HOLD=BASE/'01_data'/'holdings'
def bare(v): return str(v or '').split('.')[0]
def load(p,d): return json.loads(p.read_text(encoding='utf-8')) if p.exists() else d

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--code',action='append',required=True,help='updated/cleared security code; repeatable'); ap.add_argument('--action',choices=['已清仓','减仓','加仓'],default='已清仓'); ap.add_argument('--name',action='append',default=[]); a=ap.parse_args()
    updates=[]
    for i,c in enumerate(a.code): updates.append({'code':bare(c),'name':a.name[i] if i<len(a.name) else '', 'action':a.action,'source':'user_manual_update','recorded_at':datetime.now().isoformat(timespec='seconds')})
    state=HOLD/f'{a.date}_manual_position_updates.json'; old=load(state,{'date':a.date,'updates':[]}); by={(bare(x.get('code')),x.get('action')):x for x in old.get('updates',[])}
    for x in updates: by[(x['code'],x['action'])]=x
    old['updates']=list(by.values()); state.write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf-8'); print(state)
if __name__=='__main__': main()
