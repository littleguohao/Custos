# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r'C:\new_tdx64\PYPlugins\user')
from tqcenter import tq
tq.initialize(__file__)
fields=['SC03','SC04','SC23','SC24','SC30','SC31','SC35','SC36']
for kwargs in [
    {'field_list':fields},
    {'field_list':fields,'start_time':'20260708','end_time':'20260709'},
    {'field_list':fields,'start_time':'20260708','end_time':'20260708'},
]:
    print('\nCALL', kwargs)
    try:
        data=tq.get_scjy_value(**kwargs)
        print(json.dumps(data, ensure_ascii=False)[:2000])
    except Exception as e:
        print('ERR', repr(e))
try:
    print('\nBY DATE')
    data=tq.get_scjy_value_by_date(field_list=fields, year=2026, mmdd=708)
    print(json.dumps(data, ensure_ascii=False)[:2000])
except Exception as e:
    print('ERR date', repr(e))
tq.close()
