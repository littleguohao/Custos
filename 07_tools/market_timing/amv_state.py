# -*- coding: utf-8 -*-
"""Persistent 0AMV regime state machine.

Once the regime enters 空头 it remains 空头 until a later confirmed daily
0AMV change is strictly greater than +4%. A daily reading between thresholds
must not reset the regime to neutral.
"""
from __future__ import annotations
import argparse,json,sys
from datetime import datetime
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import BASE  # noqa: E402

MARKET=BASE/'01_data'/'market'; STATE=MARKET/'0amv_regime_history.json'; LEDGER=MARKET/'0amv_observations.jsonl'
def load(p,d): return json.loads(p.read_text(encoding='utf-8')) if p.exists() else d

def append_observation(day:str, amv:dict):
    value=amv.get('amv_change_pct')
    if value is None: return None
    record={'date':day,'amv_change_pct':float(value),'as_of':amv.get('as_of') or day,
            'quality':amv.get('quality') or 'candidate','source':amv.get('source') or 'market_timing_input',
            'recorded_at':datetime.now().astimezone().isoformat(timespec='seconds')}
    existing=[]
    if LEDGER.exists():
        existing=[json.loads(line) for line in LEDGER.read_text(encoding='utf-8').splitlines() if line.strip()]
    same=[x for x in existing if x.get('date')==day and x.get('amv_change_pct')==record['amv_change_pct'] and x.get('source')==record['source']]
    if same: return same[-1]
    LEDGER.parent.mkdir(parents=True,exist_ok=True)
    with LEDGER.open('a',encoding='utf-8',newline='\n') as f: f.write(json.dumps(record,ensure_ascii=False)+'\n')
    return record

def compute(day:str, initial:str|None=None):
    hist=load(STATE,{})
    market_path=MARKET/f'{day}_market_timing_input.json'; d=load(market_path,{})
    amv=d.setdefault('amv_0',{}); value=amv.get('amv_change_pct')
    append_observation(day,amv)
    prior_dates=sorted(k for k in hist if k<day)
    prior=hist[prior_dates[-1]]['effective_state'] if prior_dates else (initial or amv.get('prior_effective_state') or '未知')
    if value is None: state=prior; transition='缺值，延续前态'
    elif float(value)>4: state='做多'; transition='单日涨幅>4%，切换/维持做多'
    elif float(value)<-2.3: state='空头'; transition='单日跌幅<-2.3%，切换/维持空头'
    elif prior=='空头': state='空头'; transition='空头锁定；未达到>4%，继续空头'
    elif prior=='做多': state='做多'; transition='做多延续；未触发空头阈值'
    else: state='中性'; transition='无已知锁定前态，处于阈值之间'
    rec={'date':day,'daily_change_pct':value,'prior_state':prior,'effective_state':state,'transition_reason':transition,'confirmed':amv.get('quality')=='confirmed'}
    hist[day]=rec; STATE.write_text(json.dumps(hist,ensure_ascii=False,indent=2),encoding='utf-8')
    amv.update({'daily_zone':'做多触发' if value is not None and float(value)>4 else ('空头触发' if value is not None and float(value)<-2.3 else '阈值内'),'prior_effective_state':prior,'effective_state':state,'state_transition_reason':transition})
    market_path.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8'); return rec

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--initial-state',choices=['空头','做多','中性']); a=ap.parse_args(); print(json.dumps(compute(a.date,a.initial_state),ensure_ascii=False,indent=2))
if __name__=='__main__': main()
