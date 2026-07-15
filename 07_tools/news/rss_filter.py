# -*- coding: utf-8 -*-
"""Filter normalized RSS evidence into a bounded, relevant, auditable candidate set."""
from __future__ import annotations
import argparse, hashlib, json, math, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo

BASE=Path(__file__).resolve().parents[2]; DATA=BASE/'01_data'; GOV=BASE/'00_governance'; LOG=BASE/'06_logs'/'rss'
CFG=GOV/'RSS_FILTER_CONFIG.json'; REG=GOV/'RSS_SOURCE_REGISTRY.json'
CAL=GOV/'CN_TRADING_CALENDAR.json'; CAL_CACHE=DATA/'market'/'CN_TRADING_CALENDAR_CACHE.json'; SH=ZoneInfo('Asia/Shanghai')

def load(p,default):
 try:return json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else default
 except Exception:return default

def dump(p,x): p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
def norm_text(s): return re.sub(r'[^0-9a-z\u4e00-\u9fff]+','',str(s or '').lower())
def canonical_url(u):
 try:
  z=urlsplit(u); q=[(k,v) for k,v in parse_qsl(z.query,keep_blank_values=True) if k.lower() not in {'utm_source','utm_medium','utm_campaign','utm_term','utm_content','source','ref'}]
  return urlunsplit((z.scheme.lower(),z.netloc.lower(),z.path.rstrip('/'),urlencode(q),'')).lower()
 except Exception:return str(u or '')
def parse_dt(s):
 try:return datetime.fromisoformat(str(s).replace('Z','+00:00')).astimezone(timezone.utc)
 except Exception:return None
def bare(code): return str(code or '').split('.')[0]

def premarket_window(day, asof, fallback_hours):
 cal=load(CAL,{})
 cache=load(CAL_CACHE,{})
 confirmed=cache.get('trading_days') or []
 confirmed += [d for d,v in cal.get('overrides',{}).items() if v.get('is_trading_day') is True]
 confirmed=sorted(set(confirmed))
 previous=max((x for x in confirmed if x<day),default=None)
 if not previous:return asof-timedelta(hours=fallback_hours),None
 start=datetime.fromisoformat(previous+'T15:00:00').replace(tzinfo=SH).astimezone(timezone.utc)
 return start,previous

def entities(date):
 positions=load(DATA/'trades'/'current_positions.json',[]); pool=load(DATA/'stock_pool'/f'{date}_stock_pool_normalized.json',[])
 names=set(); codes=set()
 for x in positions+pool:
  code=x.get('代码') or x.get('code'); name=x.get('名称') or x.get('name')
  if code: codes.add(bare(code))
  if name: names.add(str(name))
 return names,codes

def dedupe(items):
 # First exact/canonical URL, then near-identical normalized titles.
 out=[]; url_seen=set(); title_seen=[]
 for x in items:
  cu=canonical_url(x.get('source_url')); nt=norm_text(x.get('title'))
  if cu and cu in url_seen: continue
  duplicate=False
  if nt:
   for old in title_seen:
    shorter=min(len(nt),len(old)); longer=max(len(nt),len(old))
    if shorter>=12 and (nt in old or old in nt) and shorter/longer>=0.82: duplicate=True; break
  if duplicate: continue
  if cu:url_seen.add(cu)
  if nt:title_seen.append(nt)
  out.append(x)
 return out

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--session-type',required=True,choices=['premarket','intraday_1445','postclose','weekly','monthly','ad_hoc']); ap.add_argument('--as-of'); a=ap.parse_args()
 cfg=load(CFG,{}); reg={x['id']:x for x in load(REG,{}).get('sources',[])}; raw=load(DATA/'news'/'rss'/'normalized'/f'{a.date}_rss_evidence.json',[])
 asof=datetime.fromisoformat(a.as_of).astimezone(timezone.utc) if a.as_of else datetime.now(timezone.utc); hours=cfg['session_windows_hours'][a.session_type]; cutoff=asof-timedelta(hours=hours); previous_close_date=None
 if a.session_type=='premarket': cutoff,previous_close_date=premarket_window(a.date,asof,hours)
 limit=cfg['limits'][a.session_type]; per_source_limit=cfg.get('per_source_limits',{}).get(a.session_type,limit)
 names,codes=entities(a.date); scored=[]; excluded={}
 for x in raw:
  pub=parse_dt(x.get('published_at')); text=(str(x.get('title') or '')+' '+str(x.get('summary') or '')).lower(); tier=x.get('source_tier','C'); cat=x.get('category','')
  if pub and (pub>asof+timedelta(minutes=10) or pub<cutoff): excluded['outside_window']=excluded.get('outside_window',0)+1; continue
  hits_names=sorted(n for n in names if n and n.lower() in text); hits_codes=sorted(c for c in codes if c in text)
  themes=[]
  for theme,words in cfg.get('theme_keywords',{}).items():
   if any(w.lower() in text for w in words): themes.append(theme)
  market_hits=[w for w in cfg.get('market_keywords',[]) if w.lower() in text]
  spam=[w for w in cfg.get('negative_spam_keywords',[]) if w.lower() in text]
  score=cfg['tier_weight'].get(tier,0)+cfg['category_weight'].get(cat,0)+(45 if hits_names or hits_codes else 0)+min(36,len(themes)*12)+min(18,len(market_hits)*6)-(18 if spam else 0)-(10 if pub is None else 0)
  if tier=='C' and not (hits_names or hits_codes or themes or market_hits): excluded['c_tier_irrelevant']=excluded.get('c_tier_irrelevant',0)+1; continue
  y=dict(x); y.update({'relevance_score':score,'matched_holdings_or_pool':{'names':hits_names,'codes':hits_codes},'matched_themes':themes,'matched_market_keywords':market_hits,'filter_session':a.session_type,'filter_cutoff':cutoff.isoformat()})
  src=reg.get(x.get('source_id'),{}); y['policy_stage']=src.get('policy_stage');
  if y['policy_stage']=='consultation_not_effective': y['confirmed']=False; y['quality']='candidate'; y['validation_condition']=list(dict.fromkeys((y.get('validation_condition') or [])+['核验正式文件、实施日期和配套细则']))
  scored.append(y)
 scored.sort(key=lambda x:(bool(x['matched_holdings_or_pool']['names'] or x['matched_holdings_or_pool']['codes']),x.get('source_tier') in {'S','A'},x['relevance_score'],x.get('published_at') or ''),reverse=True); unique=dedupe(scored)
 selected=[]; source_selected={}
 for x in unique:
  source=x.get('source_id','unknown')
  if source_selected.get(source,0)>=per_source_limit:continue
  selected.append(x); source_selected[source]=source_selected.get(source,0)+1
  if len(selected)>=limit:break
 out=DATA/'news'/'rss'/'filtered'/f'{a.date}_{a.session_type}_rss_candidates.json'; dump(out,selected)
 report={'date':a.date,'session_type':a.session_type,'as_of':asof.isoformat(),'window_start':cutoff.isoformat(),'previous_close_date':previous_close_date,'window_hours_actual':round((asof-cutoff).total_seconds()/3600,2),'input_count':len(raw),'within_window_and_relevant':len(scored),'after_dedupe':len(unique),'selected_count':len(selected),'limit':limit,'per_source_limit':per_source_limit,'excluded':excluded,'tier_counts':{},'theme_counts':{},'source_counts':{},'output':str(out),'permission_rule':'RSS candidates cannot directly increase trading permissions'}
 for x in selected:
  report['tier_counts'][x['source_tier']]=report['tier_counts'].get(x['source_tier'],0)+1; report['source_counts'][x['source_id']]=report['source_counts'].get(x['source_id'],0)+1
  for t in x['matched_themes']:report['theme_counts'][t]=report['theme_counts'].get(t,0)+1
 rp=LOG/f'{a.date}_{a.session_type}_filter_log.json'; dump(rp,report); print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
