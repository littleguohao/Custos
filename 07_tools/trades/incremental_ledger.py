# -*- coding: utf-8 -*-
"""Append daily transactions to the immutable master ledger.

The ledger is append-only. Existing rows are never edited or removed. New rows
are identified by a stable fingerprint. Buy/sell additions incrementally update
current position quantity and unit cost; market value/P&L remain pending until
the next close revaluation.
"""
from __future__ import annotations
import argparse,hashlib,json,math,sys
from datetime import datetime
from pathlib import Path
import pandas as pd

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import BASE  # noqa: E402

TD=BASE/'01_data'/'trades'
LEDGER=TD/'master_trade_ledger.csv'; AUDIT=TD/'ledger_append_audit.jsonl'; POS=TD/'current_positions.json'; CONFIRM=TD/'position_confirmations.json'
FIELDS=['成交日期','成交时间','代码','名称','交易类别','成交数量','成交价格','成交金额','发生金额','费用','备注']
KEY=['成交日期','成交时间','代码','名称','交易类别','成交数量','成交价格','成交金额','发生金额','费用']
def clean_code(v):
 s=str(v or '').strip().replace('.0',''); return s.split('.')[0].zfill(6) if s.split('.')[0].isdigit() else s.split('.')[0]
def finite(v,d=0.0):
 try:
  x=float(v); return d if math.isnan(x) else x
 except: return d
def norm(df):
 df=df.copy()
 for f in FIELDS:
  if f not in df: df[f]=''
 df=df[FIELDS]; df['成交日期']=pd.to_datetime(df['成交日期'],errors='coerce').dt.strftime('%Y-%m-%d'); df['成交时间']=df['成交时间'].astype(str).str.strip().str.replace(r'\.0$','',regex=True); df['代码']=df['代码'].map(clean_code)
 for f in ['成交数量','成交价格','成交金额','发生金额','费用']: df[f]=pd.to_numeric(df[f],errors='coerce')
 if df['成交日期'].isna().any() or df['代码'].eq('').any(): raise ValueError('新增记录存在无效成交日期或代码')
 return df
def fingerprint(row):
 vals=[]
 for k in KEY:
  v=row.get(k,''); v='' if pd.isna(v) else v; vals.append(str(v))
 return hashlib.sha256('|'.join(vals).encode('utf-8')).hexdigest()[:20]
def read_input(p):
 if p.suffix.lower()=='.csv': return pd.read_csv(p,dtype={'代码':str})
 if p.suffix.lower() in {'.xlsx','.xls'}: return pd.read_excel(p,dtype={'代码':str})
 if p.suffix.lower()=='.json': return pd.DataFrame(json.loads(p.read_text(encoding='utf-8')))
 raise ValueError('仅支持 csv/xlsx/json')
def apply_positions(new):
 rows=json.loads(POS.read_text(encoding='utf-8')) if POS.exists() else []; by={clean_code(x.get('代码')):x for x in rows}
 for _,t in new.iterrows():
  if t['交易类别'] not in {'买入','卖出'}: continue
  c=clean_code(t['代码']); q=finite(t['成交数量']); price=finite(t['成交价格']); fee=finite(t['费用']); p=by.get(c)
  if t['交易类别']=='买入':
   if p is None: p={'代码':c,'名称':t['名称'],'持有数量':0.0,'单位成本':0.0}; by[c]=p
   oldq=finite(p.get('持有数量')); oldcost=finite(p.get('单位成本')); nq=oldq+q
   p['持有数量']=nq; p['单位成本']=((oldq*oldcost)+(q*price)+fee)/nq if nq else 0; p['名称']=t['名称'] or p.get('名称')
  else:
   if p is None or finite(p.get('持有数量'))<q: raise ValueError(f'{c}卖出数量超过台账持仓')
   p['持有数量']=finite(p.get('持有数量'))-q
   if p['持有数量']<=0: del by[c]
 for p in by.values():
  p['snapshot_status']='pending_close_revaluation'; p['snapshot_note']='数量/成本已按增量成交更新；市值、盈亏、仓位须用最新收盘价重估'
 POS.write_text(json.dumps(list(by.values()),ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--input',required=True); ap.add_argument('--confirm-no-trades',action='store_true'); ap.add_argument('--date'); ap.add_argument('--allow-identical',action='store_true',help='允许追加与历史字段完全相同的真实分笔成交'); a=ap.parse_args(); src=Path(a.input)
 incoming=norm(read_input(src)); incoming['_fingerprint']=incoming.apply(fingerprint,axis=1)
 existing=pd.read_csv(LEDGER,dtype={'代码':str}) if LEDGER.exists() else pd.DataFrame(columns=FIELDS+['_fingerprint','transaction_id'])
 if '_fingerprint' not in existing: existing['_fingerprint']=norm(existing).apply(fingerprint,axis=1)
 known=set(existing['_fingerprint']); duplicate=set(incoming['_fingerprint']) & known
 new=incoming.copy() if a.allow_identical else incoming[~incoming['_fingerprint'].isin(known)].copy()
 counts=existing.groupby('_fingerprint').size().to_dict()
 ids=[]
 for fp in new['_fingerprint']:
  counts[fp]=counts.get(fp,0)+1; ids.append(f'{fp}-{counts[fp]:03d}')
 new['transaction_id']=ids
 if a.confirm_no_trades and len(incoming): raise ValueError('--confirm-no-trades 与非空输入冲突')
 if a.confirm_no_trades:
  if not a.date: raise ValueError('--confirm-no-trades 必须同时提供 --date')
  confirmations=json.loads(CONFIRM.read_text(encoding='utf-8')) if CONFIRM.exists() else {}
  confirmations[a.date]={'confirmed_at':datetime.now().isoformat(timespec='seconds'),'no_trades':True,'note':f'用户确认：{a.date} 今日无交易动作'}
  CONFIRM.write_text(json.dumps(confirmations,ensure_ascii=False,indent=2),encoding='utf-8')
 if len(new):
  apply_positions(new); merged=pd.concat([existing,new],ignore_index=True); merged=merged.sort_values(['成交日期','成交时间','transaction_id']); merged.to_csv(LEDGER,index=False,encoding='utf-8-sig')
  stock=merged[merged['交易类别'].isin(['买入','卖出'])].copy(); stock.to_json(TD/'trades_stock.json',orient='records',force_ascii=False,indent=2)
 audit={'appended_at':datetime.now().isoformat(timespec='seconds'),'source':str(src),'requested_date':a.date,'incoming_rows':len(incoming),'appended_rows':len(new),'duplicate_fingerprints':len(duplicate),'duplicate_rows_skipped':0 if a.allow_identical else int(incoming['_fingerprint'].isin(known).sum()),'allow_identical':a.allow_identical,'no_trades_confirmed':bool(a.confirm_no_trades),'transaction_ids':new['transaction_id'].tolist()}
 with AUDIT.open('a',encoding='utf-8') as f: f.write(json.dumps(audit,ensure_ascii=False)+'\n')
 print(json.dumps(audit,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
