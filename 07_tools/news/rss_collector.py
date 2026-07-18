# -*- coding: utf-8 -*-
"""Deterministic RSS/Atom collector with strict JSON and source-quality metadata."""
from __future__ import annotations
import argparse, hashlib, html, json, re, ssl, sys, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from source_name_overrides import fix_source_name

BASE=Path(__file__).resolve().parents[2]
REG=BASE/'00_governance'/'RSS_SOURCE_REGISTRY.json'
DATA=BASE/'01_data'/'news'/'rss'; LOG=BASE/'06_logs'/'rss'
sys.path.insert(0,str(BASE/'07_tools'))
from net_retry import retry_call

def text(node, names):
    for child in node.iter():
        tag=child.tag.rsplit('}',1)[-1].lower()
        if tag in names and child.text: return child.text.strip()
    return ''

def clean(s): return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',s or ''))).strip()
def iso_date(s):
    if not s: return None
    try:
        d=parsedate_to_datetime(s); return d.astimezone(timezone.utc).isoformat()
    except Exception:
        try: return datetime.fromisoformat(s.replace('Z','+00:00')).astimezone(timezone.utc).isoformat()
        except Exception: return None

def parse_feed(raw, src, fetched):
    # ElementTree rejects some valid legacy multibyte declarations (for
    # example GB2312). Decode explicitly and normalize the XML declaration.
    declaration=raw[:200].decode('ascii',errors='ignore')
    match=re.search(r'encoding=["\']([^"\']+)',declaration,re.I)
    encoding=(match.group(1) if match else 'utf-8').lower()
    if encoding in {'gb2312','gbk','gb_2312-80'}: encoding='gb18030'
    decoded=raw.decode(encoding,errors='replace')
    decoded=re.sub(r'(<\?xml[^>]*encoding=)["\'][^"\']+["\']',r'\1"utf-8"',decoded,count=1,flags=re.I)
    root=ET.fromstring(decoded); nodes=[]
    for e in root.iter():
        if e.tag.rsplit('}',1)[-1].lower() in {'item','entry'}: nodes.append(e)
    items=[]
    for e in nodes:
        title=clean(text(e,{'title'})); summary=clean(text(e,{'description','summary','content'})); link=text(e,{'link'})
        if not link:
            for c in e.iter():
                if c.tag.rsplit('}',1)[-1].lower()=='link' and c.attrib.get('href'): link=c.attrib['href']; break
        published=text(e,{'pubdate','published','updated','date'}); guid=text(e,{'guid','id'})
        norm=re.sub(r'\W+','',title.lower())[:300]
        item_id=hashlib.sha256((src['id']+'|'+(guid or link or norm)).encode()).hexdigest()[:24]
        dup=hashlib.sha256(norm.encode()).hexdigest()[:20] if norm else item_id
        corrected_name = fix_source_name(src['id'], src['name'])
        items.append({'item_id':item_id,'published_at':iso_date(published),'fetched_at':fetched,
          'source_id':src['id'],'source_name':corrected_name,'source_tier':src['tier'],'category':src['category'],
          'title':title,'summary':summary[:2000],'source_url':link,'feed_url':src['url'],
          'affected_entities':[],'affected_sectors':[],'direction':'uncertain','impact_horizon':'unknown',
          'fact':title,'inference':'','validation_condition':[],
          'quality':'candidate' if src['tier'] in {'B','C'} else 'confirmed',
          'confirmed':src['tier'] in {'S','A'},'duplicate_group_id':dup})
    return items

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--timeout',type=int,default=15); ap.add_argument('--limit-per-feed',type=int,default=100); a=ap.parse_args()
    cfg=json.loads(REG.read_text(encoding='utf-8-sig')); fetched=datetime.now().astimezone().isoformat(timespec='seconds')
    day=DATA/'raw'/a.date; day.mkdir(parents=True,exist_ok=True); normalized=[]; log=[]
    for src in cfg['sources']:
        if not src.get('enabled') or not src.get('url'): continue
        row={'source_id':src['id'],'url':src['url'],'fetched_at':fetched}
        try:
            req=urllib.request.Request(src['url'],headers={'User-Agent':'Mozilla/5.0 TdxClawRSS/1.0','Accept':'application/rss+xml,application/atom+xml,application/xml,text/xml'})
            ctx=ssl.create_default_context()
            if src.get('ssl_verify',True) is False: ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
            with retry_call(lambda: urllib.request.urlopen(req,timeout=a.timeout,context=ctx)) as r: raw=r.read(3_000_000); row.update(http_status=r.status,final_url=r.geturl(),content_type=r.headers.get('content-type',''))
            (day/f"{src['id']}.xml").write_bytes(raw); items=parse_feed(raw,src,fetched)[:a.limit_per_feed]; normalized.extend(items); row.update(status='ok',items=len(items))
        except Exception as e: row.update(status='failed',error=repr(e),items=0)
        log.append(row)
    # exact item IDs and normalized-title duplicate groups are deterministic.
    seen=set(); unique=[]
    for x in sorted(normalized,key=lambda z:(z.get('published_at') or '',z['item_id']),reverse=True):
        if x['item_id'] in seen: continue
        seen.add(x['item_id']); unique.append(x)
    out=DATA/'normalized'/f'{a.date}_rss_evidence.json'; out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(unique,ensure_ascii=False,indent=2),encoding='utf-8')
    LOG.mkdir(parents=True,exist_ok=True); lp=LOG/f'{a.date}_collection_log.json'; lp.write_text(json.dumps({'date':a.date,'fetched_at':fetched,'sources':log,'item_count':len(unique),'output':str(out)},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(out),'log':str(lp),'items':len(unique),'sources_ok':sum(x['status']=='ok' for x in log),'sources_failed':sum(x['status']!='ok' for x in log)},ensure_ascii=False))
if __name__=='__main__': main()
