import json
p=r'C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team\01_data\market\2026-07-09_market_timing_input.json'
d=json.load(open(p,encoding='utf-8'))
print('collector', d['collector_version'])
print('indices', {k:v.get('intraday') for k,v in d['a_share_indices'].items()})
print('breadth', d['market_breadth'])
print('sentiment', d['sentiment'])
print('turnover', d['turnover'])
print('quality notes', d['data_quality'].get('notes'))
print('sc raw', d['data_quality'].get('sc_fields_raw'))
