# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r'C:\new_tdx64\PYPlugins\user')
from tqcenter import tq
tq.initialize(__file__)
for code in ['000001.SH','399006.SZ','000688.SH','899050.BJ','880003.SH','880002.SH','880001.SH','880005.SH','880006.SH','880004.SH','880300.SH','880006.SZ']:
    print('\nSNAP', code)
    try:
        d=tq.get_market_snapshot(code)
        print(type(d), json.dumps(d, ensure_ascii=False)[:2000])
    except Exception as e:
        print('ERR', repr(e))
tq.close()
