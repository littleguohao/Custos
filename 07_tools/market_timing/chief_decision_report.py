# -*- coding: utf-8 -*-
"""Build ChiefDecision JSON first, then render Markdown. RiskDecision is mandatory."""
from __future__ import annotations
import argparse,json,re,sys
from pathlib import Path

if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8',errors='replace')
BASE=Path(r'C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team'); DATA=BASE/'01_data'; PLANS=BASE/'03_daily_plans'

def load(p,d): return json.loads(p.read_text(encoding='utf-8')) if p.exists() else d
def extract(pattern,text,default):
    m=re.search(pattern,text); return m.group(1).strip() if m else default
def dedupe(xs): return list(dict.fromkeys(x for x in xs if x))
def bare(code): return str(code or '').split('.')[0]

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); a=ap.parse_args()
    mt_path=PLANS/f'{a.date}_market_timing_score.md'
    if not mt_path.exists(): mt_path=PLANS/'_supporting'/a.date/f'{a.date}_market_timing_score.md'
    risk_path=DATA/'risk'/f'{a.date}_risk_decision.json'
    if not risk_path.exists(): raise SystemExit(f'mandatory RiskDecision missing: {risk_path}')
    mt=mt_path.read_text(encoding='utf-8') if mt_path.exists() else ''
    risk=load(risk_path,{}); holdings=load(DATA/'holdings'/f'{a.date}_holding_review.json',[]); plans=load(DATA/'buy_strategy'/f'{a.date}_buy_plan_normalized.json',[]); sectors=load(DATA/'sectors'/f'{a.date}_sector_state.json',[]); gate=load(DATA/'quality'/f'{a.date}_runtime_gate.json',{})
    b1_rows=load(DATA/'holdings'/f'{a.date}_b1_holding_state.json',[]); b1_by_code={bare(x.get('code')):x for x in b1_rows}
    handoff_gate=load(DATA/'agent_handoffs'/a.date/'handoff_gate.json',{})
    specialist_evidence={}
    expected_request_id=handoff_gate.get('request_id')
    for agent in ('market-intelligence','theme-sector','portfolio-execution'):
        evidence=load(DATA/'agent_handoffs'/a.date/'validated'/f'{agent}.json',{})
        specialist_evidence[agent]=evidence if evidence.get('request_id')==expected_request_id else {}
    state=extract(r'状态：\*\*(.*?)\*\*',mt,'未知'); score=extract(r'择时评分：\*\*(.*?)\*\*',mt,'待确认'); position=extract(r'建议总仓位：\*\*(.*?)\*\*',mt,'待确认'); permission=extract(r'今日是否允许开新仓：\*\*(.*?)\*\*',mt,'原则不允许')
    risk_by_code={}
    for x in risk.get('stock_risks',[]): risk_by_code.setdefault(bare(x.get('code')),[]).append(x)
    holding_actions=[]
    technical_status=gate.get('technical_freshness',{}).get('status','missing')
    for h in holdings:
        code=bare(h.get('code')); rlist=risk_by_code.get(code,[]); high=[x for x in rlist if x.get('priority')=='高']
        b1=b1_by_code.get(code,{}); action=b1.get('final_action') or h.get('action','观察'); priority=b1.get('final_priority') or h.get('priority','P3'); reasons=[b1.get('final_reason')] if b1.get('final_reason') else list(h.get('reason') or [])
        if high:
            priority='P1'; actions=[x.get('action') for x in high]
            if '清仓' in actions: action='清仓'
            elif '止损' in actions: action='止损'
            elif '减仓' in actions: action='减仓'
            else: action='禁止加仓'
            reasons += [str(x.get('reason') or x.get('risk_type')) for x in high]
        elif technical_status!='confirmed':
            action='等待行情更新'
            reasons=['目标日持仓技术行情未确认，不沿用旧技术动作']
        holding_actions.append({'priority':priority,'code':code,'name':h.get('name',''),'action':action,'reasons':dedupe(reasons),'risk_refs':rlist,'b1_holding_state':b1,'b1_reference_action':b1.get('final_action'),'b1_reference_priority':b1.get('final_priority'),'execution_status':'current' if technical_status=='confirmed' else 'waiting_for_current_technical'})
    holding_actions.sort(key=lambda x:(x['priority'],x['code']))
    buy_actions=[]
    for p in plans:
        code=bare(p.get('code')); blocked=bool(risk_by_code.get(code)) or p.get('conclusion') in {'禁止','仅观察'}
        conclusion='禁止' if risk_by_code.get(code) else p.get('conclusion','仅观察')
        buy_actions.append({'code':code,'name':p.get('name',''),'conclusion':conclusion,'blocked_by_risk':bool(risk_by_code.get(code)),'source_conclusion':p.get('conclusion')})
    market_quality_status=gate.get('market_quality',{}).get('status')
    position_gate=gate.get('position_gate',{})
    effective_risk=risk.get('risk_level','提高')
    if risk.get('risk_level')=='强风控' or market_quality_status=='blocked':
        permission='禁止'; effective_risk='强风控'
    elif market_quality_status=='degraded' and effective_risk=='普通':
        effective_risk='提高'
    if position_gate.get('allow_position_increase') is False:
        permission='禁止' if permission=='禁止' else '仅观察，不得加仓'
    # Specialist evidence is additive and cannot weaken deterministic gates.
    # Missing/invalid/partial Agent output must never increase permissions.
    specialist_status=handoff_gate.get('status','not_run')
    specialist_agents=handoff_gate.get('agents',{})
    if specialist_status!='pass':
        permission='禁止' if permission=='禁止' else '仅观察，不得加仓'
        effective_risk='提高' if effective_risk=='普通' else effective_risk
    allowed=['处理P1/P2风险持仓','观察支持交易的主线和A/B池条件']
    forbidden=dedupe(risk.get('forbidden_actions',[])+['无计划追高','因J值低直接补仓','绕过risk_control开仓'])
    if market_quality_status=='blocked': forbidden.append('市场数据质量blocked时新开仓')
    if position_gate.get('allow_position_increase') is False: forbidden.append('持仓快照、目标日技术行情或市场质量未全部通过时加仓或输出精确交易数量')
    if specialist_status!='pass': forbidden.append('专业Agent证据不完整或校验失败时扩大交易权限')
    # Prefer validated theme-sector evidence for watchlist; retain deterministic
    # sector_state only as a dated fallback, never as permission escalation.
    ts=specialist_evidence.get('theme-sector') or {}
    agent_sectors=[x.get('sector_name') for x in ts.get('sector_states',[]) if x.get('quality')=='confirmed' and x.get('confirmed')]
    main_sectors=dedupe(agent_sectors)[:3] if specialist_status=='pass' else []
    mi=specialist_evidence.get('market-intelligence') or {}
    event_evidence=[]
    for event_type in ('notice_evidence','news_evidence'):
        for item in mi.get(event_type,[]):
            if item.get('quality')!='confirmed' or not item.get('confirmed'):
                continue
            if item.get('confidence') not in {'high','medium'}:
                continue
            event_evidence.append({
                'type':'公告' if event_type=='notice_evidence' else '新闻',
                'evidence_id':item.get('evidence_id'), 'title':item.get('title'),
                'published_at':item.get('published_at'), 'source_name':item.get('source_name'),
                'source_ref':item.get('source_ref'), 'fact_summary':item.get('fact_summary'),
                'affected_entities':item.get('affected_entities') or [],
                'impact_direction':item.get('impact_direction','unknown'),
                'impact_horizon':item.get('impact_horizon'),
                'validation_condition':item.get('validation_condition'),
                'confidence':item.get('confidence'), 'quality':item.get('quality'), 'confirmed':True,
            })
    event_evidence=event_evidence[:5]
    decision={'date':a.date,'market_state':state,'market_score':score,'total_position_range':position,'new_position_permission':permission,
      'risk_level':effective_risk,'position_freshness':gate.get('position_freshness',{}),'position_gate':position_gate,'market_quality':gate.get('market_quality',{}),
      'specialist_handoff':{'status':specialist_status,'agents':specialist_agents},
      'event_evidence':event_evidence,
      'allowed_actions':allowed,'forbidden_actions':forbidden,'holding_actions':holding_actions,'buy_actions':buy_actions,
      'watchlist':main_sectors,'tomorrow_validation':['市场数据质量是否改善','主线是否形成并保持支持状态','风险持仓是否修复关键结构'], 
      'risk_notice':'RiskDecision为强制输入；B1持仓状态只可在硬风险优先级下裁决；专业Agent证据只可追加，任何风险否决均不得被覆盖。','sources':{'risk_decision':str(risk_path),'b1_holding_state':str(DATA/'holdings'/f'{a.date}_b1_holding_state.json'),'runtime_gate':str(DATA/'quality'/f'{a.date}_runtime_gate.json'),'specialist_handoff_gate':str(DATA/'agent_handoffs'/a.date/'handoff_gate.json')}}
    out_json=DATA/'decisions'/f'{a.date}_chief_decision.json'; out_json.parent.mkdir(parents=True,exist_ok=True); out_json.write_text(json.dumps(decision,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# chief_decision 每日总控交易计划','',f'日期：{a.date}','', '## 1. 总控结论','',f'- 市场状态：**{state}**（{score}）',f'- 总仓位建议：**{position}**',f'- 新开仓权限：**{permission}**',f"- 风控等级：**{decision['risk_level']}**",f"- 持仓时效：**{decision['position_freshness'].get('status','未知')}** — {decision['position_freshness'].get('reason','')}",'', '## 2. 持仓处理优先级','', '| 优先级 | 代码 | 名称 | 动作 | 理由 |','|---|---|---|---|---|']
    for x in holding_actions: lines.append(f"| {x['priority']} | {x['code']} | {x['name']} | {x['action']} | {'；'.join(x['reasons'])} |")
    lines += ['','## 3. 买入计划审核','', '| 代码 | 名称 | 上游结论 | 总控结论 | 风控否决 |','|---|---|---|---|---|']
    for x in buy_actions: lines.append(f"| {x['code']} | {x['name']} | {x['source_conclusion']} | {x['conclusion']} | {'是' if x['blocked_by_risk'] else '否'} |")
    if not buy_actions: lines.append('| - | 暂无 | - | - | - |')
    lines += ['','## 4. 允许动作','']+[f'- {x}' for x in allowed]+['','## 5. 禁止动作','']+[f'- {x}' for x in forbidden]+['','## 6. 观察方向','']+[f'- {x}' for x in main_sectors or ['暂无经过许可的方向']]+['','## 7. 下一交易日验证点','']+[f'- {x}' for x in decision['tomorrow_validation']]+['','## 8. 数据与风险声明','',f'- 结构化总控：`{out_json}`',f"- 市场数据质量：{decision['market_quality'].get('status','未知')}（{decision['market_quality'].get('quality_score','NA')}）",'- 本计划是策略辅助，不构成收益承诺。']
    out=PLANS/f'{a.date}_chief_decision.md'; out.write_text('\n'.join(lines),encoding='utf-8'); print(out); print(out_json)
if __name__=='__main__': main()
