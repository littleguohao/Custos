# -*- coding: utf-8 -*-
import json
from pathlib import Path
p=Path(r'C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team\01_data\sectors\sector_member_probe.json')
d=json.loads(p.read_text(encoding='utf-8'))
summary={}
for theme, m in d['theme_hits'].items():
    sets=[set(v) for v in m.values() if v]
    common=set.intersection(*sets) if sets else set()
    cnt={}
    for code, secs in m.items():
        for s in secs:
            cnt[s]=cnt.get(s,0)+1
    ranked=sorted(cnt.items(), key=lambda x:(-x[1], x[0]))[:20]
    summary[theme]={"common":sorted(common),"ranked":[{"sector":s,"hit_count":c} for s,c in ranked]}
print(json.dumps(summary,ensure_ascii=False,indent=2)[:12000])
Path(r'C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team\01_data\sectors\sector_probe_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
