# -*- coding: utf-8 -*-
import os
from __future__ import annotations
import argparse, json, os, subprocess, sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = Path(__file__).resolve().parents[2]
PY=Path(sys.executable)
TECH=BASE/'07_tools'/'market_timing'/'technical_monitor.py'
HOLD=BASE/'01_data'/'holdings'
TRADES=BASE/'01_data'/'trades'/'current_positions.json'


def load(path, default): return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

def pos_to_row(p):
    code=str(p.get('代码','')).split('.')[0]
    return {'code':code,'name':p.get('名称',''),'holding_amount':p.get('持有金额'),
            'holding_pnl':p.get('持有盈亏'),'holding_pnl_pct':p.get('持有盈亏率'),
            'position_pct':p.get('仓位占比'),'holding_days':p.get('持仓天数'),
            'industry':p.get('关联板块') or '', 'concepts':[], 'industry_chain':'', 'primary_themes':[]}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--mapping',default=''); a=ap.parse_args()
    mapping=Path(a.mapping) if a.mapping else HOLD/f'{a.date}_holding_sector_mapping_enriched.json'
    if mapping.exists(): items=load(mapping,[])
    else: items=[pos_to_row(x) for x in load(TRADES,[]) if x.get('代码')]
    if not items: raise SystemExit('no current holdings or mapping')
    summary=[]
    for it in items:
        code=str(it['code']).split('.')[0]; name=it.get('name','')
        out=HOLD/f'{a.date}_technical_{code}.json'
        env={**os.environ,'PYTHONIOENCODING':'utf-8'}
        p=subprocess.run([str(PY),str(TECH),'--code',code,'--name',name,'--date',a.date,'--out',str(out)],capture_output=True,text=True,encoding='utf-8',errors='replace',env=env)
        if p.returncode != 0:
            summary.append({**it,'code':code,'technical_available':False,'technical_error':p.stderr[-1000:]}); continue
        data=load(out,{}); an=data.get('analysis',{})
        if not an.get('available'):
            row={**it,'code':code,'technical_available':False,'technical_error':an.get('error')}
        else:
            row={**it,'code':code,'technical_available':True,'latest_date':an.get('latest_date'),
                 'trend_state':(an.get('trend') or {}).get('state'),'close':(an.get('trend') or {}).get('close'),
                 'ma25':(an.get('trend') or {}).get('ma25'),'ma60':(an.get('trend') or {}).get('ma60'),
                 'ma144':(an.get('trend') or {}).get('ma144'),'ma240':(an.get('trend') or {}).get('ma240'),
                 'above_ma25':(an.get('trend') or {}).get('above_ma25'),'above_ma60':(an.get('trend') or {}).get('above_ma60'),
                 'above_ma144':(an.get('trend') or {}).get('above_ma144'),'above_ma240':(an.get('trend') or {}).get('above_ma240'),
                 'bbi':(an.get('bbi') or {}).get('value'),'above_bbi':(an.get('bbi') or {}).get('close_above'),
                 'bbi_distance_pct':(an.get('bbi') or {}).get('distance_pct'),
                 'consecutive_closes_below_bbi':(an.get('bbi') or {}).get('consecutive_closes_below'),
                 'n_structure':an.get('n_structure') or {'available':False},
                'descending_n_structure':an.get('descending_n_structure') or {'available':False},
                 'n_structure_prior_low':(an.get('n_structure') or {}).get('prior_low'),
                 'n_structure_prior_low_date':(an.get('n_structure') or {}).get('prior_low_date'),
                 'n_structure_origin_extreme_low':(an.get('n_structure') or {}).get('origin_extreme_low'),
                 'n_structure_pullback_low':(an.get('n_structure') or {}).get('pullback_low'),
                 'n_structure_pullback_low_date':(an.get('n_structure') or {}).get('pullback_low_date'),
                 'n_structure_breakout_level':(an.get('n_structure') or {}).get('breakout_level'),
                 'n_structure_confirmed_date':(an.get('n_structure') or {}).get('confirmed_date'),
                 'box20_upper':(an.get('box_20d') or {}).get('upper'),'box20_lower':(an.get('box_20d') or {}).get('lower'),
                 'box20_mid':(an.get('box_20d') or {}).get('mid'),'box20_position':(an.get('box_20d') or {}).get('position'),
                 'box60_upper':(an.get('box_60d') or {}).get('upper'),'box60_lower':(an.get('box_60d') or {}).get('lower'),
                 'box60_mid':(an.get('box_60d') or {}).get('mid'),'box60_position':(an.get('box_60d') or {}).get('position'),
                 'daily_j':(((an.get('daily') or {}).get('kdj') or {}).get('j')),
                 'daily_kdj_golden_cross':(((an.get('daily') or {}).get('kdj') or {}).get('golden_cross')),
                 'daily_kdj_death_cross':(((an.get('daily') or {}).get('kdj') or {}).get('death_cross')),
                 'daily_kdj_state':(((an.get('daily') or {}).get('kdj') or {}).get('state')),
                 'daily_macd_hist':(((an.get('daily') or {}).get('macd') or {}).get('hist')),
                 'daily_macd_hist_direction':(((an.get('daily') or {}).get('macd') or {}).get('hist_direction')),
                 'daily_macd_golden_cross':(((an.get('daily') or {}).get('macd') or {}).get('golden_cross')),
                 'daily_macd_death_cross':(((an.get('daily') or {}).get('macd') or {}).get('death_cross')),
                 'weekly_j':(((an.get('weekly') or {}).get('kdj') or {}).get('j')),
                 'weekly_kdj_state':(((an.get('weekly') or {}).get('kdj') or {}).get('state')),
                 'weekly_macd_hist':(((an.get('weekly') or {}).get('macd') or {}).get('hist')),
                 'weekly_macd_hist_direction':(((an.get('weekly') or {}).get('macd') or {}).get('hist_direction')),
                 'monthly_j':(((an.get('monthly') or {}).get('kdj') or {}).get('j')),
                 'monthly_kdj_state':(((an.get('monthly') or {}).get('kdj') or {}).get('state')),
                 'monthly_macd_hist':(((an.get('monthly') or {}).get('macd') or {}).get('hist')),
                 'monthly_macd_hist_direction':(((an.get('monthly') or {}).get('macd') or {}).get('hist_direction')),
                 'price_volume':an.get('price_volume') or {'available':False}}
        summary.append(row)
    dest=HOLD/f'{a.date}_holding_technical_summary.json'; dest.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(dest)

if __name__=='__main__': main()
