# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse,json,re,sys
from pathlib import Path

if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8',errors='replace')

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import BASE  # noqa: E402

DATA=BASE/'01_data'; PLANS=BASE/'03_daily_plans'

def load(p,d): return json.loads(p.read_text(encoding='utf-8')) if p.exists() else d
def extract(pattern,text,default):
    m=re.search(pattern,text); return m.group(1).strip() if m else default

def classify(r):
    pnl=r.get('holding_pnl_pct'); trend=r.get('trend_state'); pos=str(r.get('box20_position') or ''); j=r.get('daily_j'); macd=r.get('daily_macd_hist_direction')
    reasons=[]; action='观察'
    if trend=='下跌' and '破位' in pos: action='止损'; reasons.append('下跌趋势且处于破位区')
    elif isinstance(pnl,(int,float)) and pnl<=-0.10: action='止损'; reasons.append('浮亏达到强制风控阈值')
    elif isinstance(pnl,(int,float)) and pnl<=-0.07: action='减仓'; reasons.append('浮亏超过-7%')
    elif trend=='下跌': action='减仓'; reasons.append('下跌趋势')
    elif trend=='横盘震荡' and '上半区' in pos: action='持有'; reasons.append('横盘上半区，保护利润且不追高')
    if isinstance(j,(int,float)) and j<12: reasons.append('J值低仅作观察，不构成加仓理由')
    if macd=='收缩': reasons.append('MACD动能收缩')
    if not reasons: reasons.append('暂无强触发信号')
    priority='P1' if action in {'止损','清仓'} else ('P2' if action=='减仓' else 'P3')
    return priority,action,reasons

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); a=ap.parse_args()
    tech=load(DATA/'holdings'/f'{a.date}_holding_technical_summary.json',[])
    b1_rows=load(DATA/'holdings'/f'{a.date}_b1_holding_state.json',[]); b1={str(x.get('code')):x for x in b1_rows}
    mt=(PLANS/f'{a.date}_market_timing_score.md').read_text(encoding='utf-8') if (PLANS/f'{a.date}_market_timing_score.md').exists() else ''
    state=extract(r'状态：\*\*(.*?)\*\*',mt,'未知'); position=extract(r'建议总仓位：\*\*(.*?)\*\*',mt,'待确认')
    reviews=[]
    for r in tech:
        state=b1.get(str(r.get('code')),{}); priority=state.get('final_priority'); action=state.get('final_action'); reasons=[]
        if not priority or not action:
            priority,action,reasons=classify(r)
        else:
            reasons=[state.get('final_reason')]+[x.get('reason') for x in state.get('signals',[])[1:3]]
        reviews.append({'code':str(r.get('code')),'name':r.get('name',''),'position_pct':r.get('position_pct'),'pnl_pct':r.get('holding_pnl_pct'),
          'holding_days':r.get('holding_days'),'sector':r.get('industry') or '、'.join(r.get('primary_themes') or []),'trend_state':r.get('trend_state'),
          'box_position':r.get('box20_position'),'daily_j':r.get('daily_j'),'macd_state':r.get('daily_macd_hist_direction'),
          'action':action,'priority':priority,'reason':[x for x in reasons if x], 'b1_holding_state':state})
    out_json=DATA/'holdings'/f'{a.date}_holding_review.json'; out_json.write_text(json.dumps(reviews,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# portfolio_review 每日持仓研判','',f'日期：{a.date}','', '## 1. 总体持仓风险','',f'- market_timing：**{state}**',f'- 建议总仓位：**{position}**','- 原则：低位指标不能覆盖趋势、板块与风险规则。','', '## 2. 持仓逐只研判','', '| 优先级 | 代码 | 名称 | 仓位 | 盈亏 | 趋势/位置 | 动作 | 理由 |','|---|---|---|---:|---:|---|---|---|']
    for x in sorted(reviews,key=lambda y:(y['priority'],y['code'])):
        lines.append(f"| {x['priority']} | {x['code']} | {x['name']} | {x['position_pct']} | {x['pnl_pct']} | {x['trend_state']}/{x['box_position']} | {x['action']} | {'；'.join(x['reason'])} |")
    lines += ['','## 3. 风控触发项','']
    risk=[x for x in reviews if x['priority'] in {'P1','P2'}]
    lines += [f"- **{x['name']}({x['code']})**：{x['action']}。{'；'.join(x['reason'])}" for x in risk] or ['- 暂无。']
    lines += ['','## 4. 数据声明','',f'- 结构化输出：`{out_json}`','- 本报告是策略辅助，不构成收益承诺。']
    out=PLANS/f'{a.date}_portfolio_review.md'; out.write_text('\n'.join(lines),encoding='utf-8'); print(out); print(out_json)
if __name__=='__main__': main()
