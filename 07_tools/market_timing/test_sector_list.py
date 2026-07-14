# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r'C:\new_tdx64\PYPlugins\user')
from tqcenter import tq
tq.initialize(__file__)
try:
    sectors=tq.get_sector_list()
    print(type(sectors), len(sectors) if sectors else 0)
    print(json.dumps(sectors[:20] if isinstance(sectors,list) else sectors, ensure_ascii=False)[:4000])
    for kw in ['人工智能','半导体','液冷','算力','机器人','CPO','光模块','数据中心']:
        matches=[x for x in sectors if kw in str(x)] if isinstance(sectors,list) else []
        print('\nKW',kw,'count',len(matches))
        print(json.dumps(matches[:10], ensure_ascii=False)[:2000])
finally:
    try: tq.close()
    except Exception: pass
