# -*- coding: utf-8 -*-
import sys, os, json
sys.path.insert(0, r'C:\new_tdx64\PYPlugins\user')
from tqcenter import tq
print('init')
tq.initialize(__file__)
fields=['SC03','SC04','SC23','SC24','SC30','SC31','SC35','SC36']
try:
    data=tq.get_scjy_value(field_list=fields)
    print(type(data))
    print(json.dumps(data, ensure_ascii=False)[:3000])
finally:
    try: tq.close()
    except Exception as e: print('close err', e)
