# -*- coding: utf-8 -*-
"""Deterministic RSS/Atom collector with strict JSON and source-quality metadata."""
from __future__ import annotations
import argparse, hashlib, html, json, re, ssl, sys, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from source_name_overrides import fix_source_name

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import BASE, RSS_SOURCE_REGISTRY_FILE, cn_now  # noqa: E402
from contracts import require  # noqa: E402
from net_retry import retry_call  # noqa: E402

REG=RSS_SOURCE_REGISTRY_FILE
DATA=BASE/'01_data'/'news'/'rss'; LOG=BASE/'06_logs'/'rss'

# 单个 feed 的字节上限。国家统计局的 feed 实测约 4.5MB,所以上限不能定得太小;
# 但 r.read() 完全不设限意味着任何被劫持/故障的源都能把内存打爆,故设 16MB 硬顶。
MAX_FEED_BYTES = 16 * 1024 * 1024
# 只扫文件头：实体声明必须在 DOCTYPE 内部子集里，不可能出现在正文深处。
MAX_DTD_SCAN_BYTES = 64 * 1024
# source_id 会直接拼进落盘文件名,必须白名单,否则 registry 里一个 "../../x" 就能写到库外。
SAFE_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def _redact(text: str) -> str:
    """去掉异常信息里的 query string——feed URL 可能带 token/appkey。"""
    return re.sub(r'\?[^\s\'"]*', '?<redacted>', str(text))


def build_ssl_context(src: dict) -> tuple[ssl.SSLContext, bool]:
    """按源配置构造 SSL 上下文,返回 (ctx, transport_verified)。

    关闭校验必须**同时**显式写 ssl_insecure_ack:tier S 的政府源一旦关掉校验,
    中间人就能伪造"国务院发文"进入正式复盘并被标成 source_confirmed。三个政府源
    历史上带着 ssl_verify=false,而实测证书链完全正常——这属于无必要的历史遗留,
    已从 registry 移除。保留这条通道只为应对真实的证书故障,且必须留痕。
    """
    ctx = ssl.create_default_context()
    if src.get('ssl_verify', True) is False:
        if not src.get('ssl_insecure_ack'):
            raise ValueError(
                f"{src['id']}: ssl_verify=false 需同时设 ssl_insecure_ack=true 以显式承担中间人风险")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx, False
    return ctx, True


def _read_limited(resp) -> bytes:
    raw = resp.read(MAX_FEED_BYTES + 1)
    if len(raw) > MAX_FEED_BYTES:
        raise ValueError(f'feed exceeds {MAX_FEED_BYTES} bytes, refused')
    return raw


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

def _tier_quality(tier, transport_verified=True):
    """来源等级 → 证据质量。传输未经校验时**不得**给 confirmed。

    tier 表达的是"这个机构说的话有多权威",transport_verified 表达的是"这段字节
    真的来自那个机构吗"。后者不成立时前者无意义,故降级为 candidate 并由
    transport_verified 字段留痕,下游可据此拒绝把它当既成事实。
    """
    if not transport_verified:
        return 'candidate'
    return 'candidate' if tier in {'B','C'} else 'confirmed'

def refuse_entity_expansion(decoded: str) -> None:
    """拒收**嵌套实体声明**的 XML —— 这是「billion laughs」放大攻击的特征。

    ⚠️ 实测（2026-08-07）：`xml.etree.ElementTree` 对两类实体攻击的表现**不同**：

        外部实体（XXE, `<!ENTITY x SYSTEM "file:///etc/passwd">`）→ 已被拒 ✅
          ParseError: undefined entity —— 不必额外防。
        内部实体嵌套（`<!ENTITY b "&a;&a;...">`）→ **可行** ⚠️
          345 字节的 payload 4 层展开出 500 KB；再加两层就是 50 MB。
          `MAX_FEED_BYTES`（16 MB）只限**输入**大小，管不住展开后的内存。

    为什么这条路径值得防：这些 feed 是**远端不可信输入**，而
    `build_ssl_context` 按设计允许个别源 `ssl_verify=false`（需显式 ack 承担风险）。
    那类源上的中间人可以直接投递放大 payload，把 08:50 采集 OOM 掉。

    为什么**不**引 `defusedxml`：只为一条已知特征加一个依赖不划算。
    为什么只拒**嵌套**而不是所有 `<!ENTITY`：扁平声明（`<!ENTITY nbsp "&#160;">`）
    是真实 feed 的合法用法且无放大能力，一律拒会误杀正常源。
    放大的必要条件是「一个实体的值里引用了另一个被声明的实体」，只拦这个。
    """
    head = decoded[:MAX_DTD_SCAN_BYTES]
    if '<!ENTITY' not in head:
        return
    declared = dict(re.findall(r'<!ENTITY\s+(\S+)\s+["\']([^"\']*)["\']', head))
    for name, value in declared.items():
        for ref in re.findall(r'&(\w+);', value):
            if ref in declared:
                raise ValueError(
                    f'refused nested XML entity declaration: &{name}; references &{ref}; '
                    '(billion-laughs signature)')


def parse_feed(raw, src, fetched, transport_verified=True):
    # ElementTree rejects some valid legacy multibyte declarations (for
    # example GB2312). Decode explicitly and normalize the XML declaration.
    declaration=raw[:200].decode('ascii',errors='ignore')
    match=re.search(r'encoding=["\']([^"\']+)',declaration,re.I)
    encoding=(match.group(1) if match else 'utf-8').lower()
    if encoding in {'gb2312','gbk','gb_2312-80'}: encoding='gb18030'
    decoded=raw.decode(encoding,errors='replace')
    decoded=re.sub(r'(<\?xml[^>]*encoding=)["\'][^"\']+["\']',r'\1"utf-8"',decoded,count=1,flags=re.I)
    refuse_entity_expansion(decoded)
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
          'quality':_tier_quality(src['tier'],transport_verified),
          'confirmed':src['tier'] in {'S','A'} and transport_verified,
          'transport_verified':transport_verified,'duplicate_group_id':dup})
    return items

def parse_wscn_lives(raw, src, fetched, transport_verified=True):
    # WallstreetCN lives JSON API: {"code":20000,"data":{"items":[{id,content,display_time,uri,...}]}}
    data=json.loads(raw.decode('utf-8',errors='replace'))
    if not isinstance(data,dict) or data.get('code')!=20000:
        raise ValueError('wscn_lives bad response code: '+repr(data.get('code') if isinstance(data,dict) else type(data).__name__))
    entries=data.get('data',{}).get('items')
    if not isinstance(entries,list): raise ValueError('wscn_lives malformed payload: data.items missing')
    items=[]
    for e in entries:
        if not isinstance(e,dict): continue
        content=clean(e.get('content'))
        if not content: continue
        try: published=datetime.fromtimestamp(int(e.get('display_time')),timezone.utc).isoformat()
        except (TypeError,ValueError,OverflowError): published=None
        norm=re.sub(r'\W+','',content.lower())[:300]
        item_id=hashlib.sha256((src['id']+'|'+str(e.get('id'))).encode()).hexdigest()[:24]
        dup=hashlib.sha256(norm.encode()).hexdigest()[:20] if norm else item_id
        corrected_name = fix_source_name(src['id'], src['name'])
        title=content[:50]
        items.append({'item_id':item_id,'published_at':published,'fetched_at':fetched,
          'source_id':src['id'],'source_name':corrected_name,'source_tier':src['tier'],'category':src['category'],
          'title':title,'summary':content[:2000],'source_url':e.get('uri') or (f"https://wallstreetcn.com/livenews/{e.get('id')}" if e.get('id') is not None else ''),'feed_url':src['url'],
          'affected_entities':[],'affected_sectors':[],'direction':'uncertain','impact_horizon':'unknown',
          'fact':title,'inference':'','validation_condition':[],
          'quality':_tier_quality(src['tier'],transport_verified),
          'confirmed':src['tier'] in {'S','A'} and transport_verified,
          'transport_verified':transport_verified,'duplicate_group_id':dup})
    return items

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--timeout',type=int,default=15); ap.add_argument('--limit-per-feed',type=int,default=100); a=ap.parse_args()
    cfg=json.loads(REG.read_text(encoding='utf-8-sig')); fetched=cn_now().isoformat(timespec='seconds')
    day=DATA/'raw'/a.date; day.mkdir(parents=True,exist_ok=True); normalized=[]; log=[]
    for src in cfg['sources']:
        if not src.get('enabled') or not src.get('url'): continue
        row={'source_id':src['id'],'url':src['url'],'fetched_at':fetched}
        try:
            if not SAFE_ID.match(str(src.get('id',''))):
                raise ValueError(f"unsafe source id {src.get('id')!r}: 会拼进落盘文件名")
            req=urllib.request.Request(src['url'],headers={'User-Agent':'Mozilla/5.0 TdxClawRSS/1.0','Accept':'application/rss+xml,application/atom+xml,application/xml,text/xml'})
            ctx,verified=build_ssl_context(src)
            row['transport_verified']=verified
            with retry_call(lambda: urllib.request.urlopen(req,timeout=a.timeout,context=ctx)) as r: raw=_read_limited(r); row.update(http_status=r.status,final_url=r.geturl(),content_type=r.headers.get('content-type',''),bytes=len(raw))
            if src.get('type')=='wscn_lives':
                (day/f"{src['id']}.json").write_bytes(raw); items=parse_wscn_lives(raw,src,fetched,verified)[:a.limit_per_feed]
            else:
                (day/f"{src['id']}.xml").write_bytes(raw); items=parse_feed(raw,src,fetched,verified)[:a.limit_per_feed]
            normalized.extend(items); row.update(status='ok',items=len(items))
        except Exception as e: row.update(status='failed',error=_redact(repr(e)),items=0)
        log.append(row)
    # exact item IDs and normalized-title duplicate groups are deterministic.
    seen=set(); unique=[]
    for x in sorted(normalized,key=lambda z:(z.get('published_at') or '',z['item_id']),reverse=True):
        if x['item_id'] in seen: continue
        seen.add(x['item_id']); unique.append(x)
    require("rss_evidence", unique)
    out=DATA/'normalized'/f'{a.date}_rss_evidence.json'; out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(unique,ensure_ascii=False,indent=2),encoding='utf-8')
    LOG.mkdir(parents=True,exist_ok=True); lp=LOG/f'{a.date}_collection_log.json'; lp.write_text(json.dumps({'date':a.date,'fetched_at':fetched,'sources':log,'item_count':len(unique),'output':str(out)},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(out),'log':str(lp),'items':len(unique),'sources_ok':sum(x['status']=='ok' for x in log),'sources_failed':sum(x['status']!='ok' for x in log)},ensure_ascii=False))
if __name__=='__main__': main()
