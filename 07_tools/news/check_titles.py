# -*- coding: utf-8 -*-
"""Check RSS raw XML channel titles with correct encoding."""
import os, re
import xml.etree.ElementTree as ET

raw_dir = r'C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team\01_data\news\rss\raw\2026-07-17'

files = [
    'eeo_all.xml', 'eeo_finance.xml', 'eeo_industry.xml',
    'newtimespace_finance.xml', 'newtimespace_research.xml',
    'newtimespace_etf.xml', 'newtimespace_ipo.xml',
    'newtimespace_overseas.xml', 'newtimespace_tech.xml',
    'ftchinese_all.xml', 'oreilly_ai_ml.xml'
]

for fname in files:
    fpath = os.path.join(raw_dir, fname)
    if not os.path.exists(fpath):
        print(f'{fname}: NOT FOUND')
        continue
    raw = open(fpath, 'rb').read(5000)
    decl = raw[:200].decode('ascii', errors='ignore')
    m = re.search(r'encoding=["\']([^"\']+)', decl, re.I)
    enc = m.group(1) if m else 'utf-8'
    if enc.lower() in ('gb2312', 'gbk', 'gb_2312-80'):
        enc = 'gb18030'
    try:
        decoded = raw.decode(enc, errors='replace')
        # Fix XML declaration for ET
        decoded = re.sub(r'(<\?xml[^>]*encoding=)["\'][^"\']+["\']', r'\1"utf-8"', decoded, count=1, flags=re.I)
        root = ET.fromstring(decoded)
        # RSS 2.0: channel/title
        ch = root.find('channel')
        if ch is not None:
            title = ch.findtext('title', '')
            print(f'{fname} [{enc}]: {title}')
        else:
            # Atom
            ns = '{http://www.w3.org/2005/Atom}'
            title = root.findtext(f'{ns}title', '')
            print(f'{fname} [{enc}] (atom): {title}')
    except Exception as e:
        print(f'{fname} [{enc}]: ERROR {e}')
