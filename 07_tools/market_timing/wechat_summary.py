# -*- coding: utf-8 -*-
"""Render concise summary exclusively from structured ChiefDecision."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import BASE  # noqa: E402

DATA=BASE/'01_data'; OUT=BASE/'03_daily_plans'
def load(p,d): return json.loads(p.read_text(encoding='utf-8')) if p.exists() else d

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); a=ap.parse_args()
    p=DATA/'decisions'/f'{a.date}_chief_decision.json'
    if not p.exists(): raise SystemExit(f'mandatory ChiefDecision missing: {p}')
    d=load(p,{})
    lines=[f"【策略Team日报 {a.date}】",f"市场：{d.get('market_state','未知')}（{d.get('market_score','')}）",f"仓位：{d.get('total_position_range','待确认')}；新开仓：{d.get('new_position_permission','禁止')}",f"风控：{d.get('risk_level','提高')}；数据质量：{(d.get('market_quality') or {}).get('status','未知')}",f"持仓快照：{(d.get('position_freshness') or {}).get('status','未知')}（未确认时不输出精确数量）",'', '持仓处理：']
    actions=d.get('holding_actions') or []
    for x in actions[:8]: lines.append(f"- {x.get('priority')} {x.get('name')}({x.get('code')})：{x.get('action')}；{'；'.join(x.get('reasons') or [])}")
    if not actions: lines.append('- 暂无经过总控确认的持仓动作')
    lines += ['', '禁止动作：']+[f'- {x}' for x in (d.get('forbidden_actions') or ['无计划追高','绕过风控开仓'])]
    lines += ['', '下一交易日验证：']+[f'- {x}' for x in (d.get('tomorrow_validation') or ['市场与主线状态'])]
    lines.append('仅供策略辅助，不构成交易指令。')
    out=OUT/f'{a.date}_wechat_summary.txt'; out.write_text('\n'.join(lines)+'\n',encoding='utf-8'); print(out); print('\n'.join(lines))
if __name__=='__main__': main()
