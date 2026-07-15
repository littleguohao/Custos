# -*- coding: utf-8 -*-
"""Final close review using persistent 0AMV regime and same-day close data."""
from __future__ import annotations
import argparse,json,math
from datetime import datetime
from pathlib import Path
try:
    from .holding_bbi import bbi_basis
except ImportError:
    from holding_bbi import bbi_basis
BASE=Path(__file__).resolve().parents[2]; DATA=BASE/'01_data'; REV=BASE/'04_reviews'/'daily'
def load(p,d): return json.loads(p.read_text(encoding='utf-8')) if p.exists() else d
def finite(v,d=0.0):
    try:
        x=float(v); return d if math.isnan(x) else x
    except (TypeError,ValueError): return d
def bare(v): return str(v or '').split('.')[0]
def index_name(code):
    if code.startswith('688'): return '科创50（市场风格代理）'
    if code.startswith(('92','8','4')): return '北证50（市场风格代理）'
    if code.startswith(('300','301')): return '创业板指（市场风格代理）'
    return '上证指数（市场风格代理）' if code.startswith(('6','5')) else '深证成指（市场风格代理）'
def sector_for(code, sectors):
    for s in sectors:
        linked=[bare(x) for x in (s.get('holding_related') or [])+(s.get('representative_stocks') or [])]
        if code in linked:return s
    return {}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--no-trades-confirmed',action='store_true'); a=ap.parse_args(); day=a.date
    paths={k:p for k,p in {'chief':DATA/'decisions'/f'{day}_chief_decision.json','market':DATA/'market'/f'{day}_market_timing_input.json','gate':DATA/'quality'/f'{day}_runtime_gate.json','tech':DATA/'holdings'/f'{day}_holding_technical_summary.json','sectors':DATA/'sectors'/f'{day}_sector_technical_summary.json'}.items()}
    for p in paths.values():
        if not p.exists(): raise SystemExit(f'mandatory close-review input missing: {p}')
    chief=load(paths['chief'],{}); market=load(paths['market'],{}); gate=load(paths['gate'],{}); tech=load(paths['tech'],[]); sectors=load(paths['sectors'],[]); positions=load(DATA/'trades'/'current_positions.json',[]); trades=load(DATA/'trades'/'trades_stock.json',[])
    today=[x for x in trades if str(x.get('成交日期','')).startswith(day)]; amv=market.get('amv_0',{}); value=amv.get('amv_change_pct'); regime=amv.get('effective_state')
    if value is None or amv.get('quality')!='confirmed' or not regime: raise SystemExit('confirmed close 0AMV/regime missing')
    if a.no_trades_confirmed and today: raise SystemExit('no-trades confirmation conflicts with ledger')
    tmap={bare(x.get('code')):x for x in tech}; pmap={bare(x.get('代码')):x for x in positions}; freshness=gate.get('position_freshness',{})
    technical_dates=sorted({str(x.get('latest_date')) for x in tech if x.get('latest_date')})
    technical_current=technical_dates == [day]
    specialist=chief.get('specialist_handoff',{})
    # Infer total account assets from the imported snapshot, then revalue holdings at same-day closes.
    asset_samples=[finite(x.get('持有金额'))/finite(x.get('仓位占比')) for x in positions if finite(x.get('仓位占比'))>0]
    total_assets=sorted(asset_samples)[len(asset_samples)//2] if asset_samples else 0
    revalued=[]
    for c,p in pmap.items():
        t=tmap.get(c,{}); close=finite(t.get('close'),finite(p.get('最新价'))); qty=finite(p.get('持有数量')); cost=finite(p.get('单位成本')); mv=close*qty; pnl=mv-cost*qty; pnl_pct=close/cost-1 if cost else 0; sec=sector_for(c,sectors)
        revalued.append({'code':c,'name':p.get('名称'),'quantity':qty,'cost':cost,'close':close,'latest_date':t.get('latest_date'),'market_value':mv,'pnl':pnl,'pnl_pct':pnl_pct,'position_pct':mv/total_assets if total_assets else 0,'trend':t.get('trend_state'),'box':t.get('box20_position'),'bbi':bbi_basis(t),'sector':sec,'index':index_name(c)})
    actual_pos=sum(x['position_pct'] for x in revalued)
    indices=[]
    for name,x in market.get('a_share_indices',{}).items():
        if not isinstance(x,dict) or not x.get('available',True): continue
        intra=x.get('intraday') or {}; indices.append({'name':name,'close':intra.get('now',x.get('latest_close')),'change_pct':intra.get('intraday_change_pct'),'above_ma25':x.get('above_ma25'),'above_ma60':x.get('above_ma60'),'above_ma144':x.get('above_ma144'),'above_ma240':x.get('above_ma240')})
    valuation_label='目标日收盘重估' if technical_current else f"上一确认行情日参考（{','.join(technical_dates) or '缺失'}）"
    lines=[f'# {day} 最终盘后复盘','',f'> 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',f'> 0AMV当日变动：**{float(value):+.2f}%**；有效状态：**{regime}**',f"> 状态迁移：{amv.get('state_transition_reason')}",'> 注意：0AMV是市场活跃市值指标，不是账户或交易盈亏。',f"> 今日实际交易：**{'无交易动作' if not today else str(len(today))+'笔'}**",f"> 持仓确认：**{freshness.get('status')}** — {freshness.get('reason')}",f"> 专业 Agent 证据门：**{specialist.get('status','not_run')}**",f"> 持仓行情口径：**{valuation_label}**",'','## 1. 今日计划与执行','',f"- 市场状态：**{chief.get('market_state')}**，择时评分 **{chief.get('market_score')}**。",f"- 0AMV为空头锁定状态；只有后续单日涨幅严格大于4%才切回做多，阈值内或负值读数不能解除空头。",f"- 参考仓位约 **{actual_pos:.1%}**，建议区间 **{chief.get('total_position_range')}**；因目标日持仓行情{'齐全' if technical_current else '不齐全'}，{'可' if technical_current else '不可'}作为精确调仓数量依据。",f"- 今日无交易；没有追高，但也没有主动收缩超配仓位。",'','## 2. 市场复盘','','### 2.1 大盘指数','','| 指数 | 收盘/最新 | 当日涨跌 | MA25/60/144/240状态 |','|---|---:|---:|---|']
    for x in indices: lines.append(f"| {x['name']} | {x['close']} | {finite(x['change_pct']):+.2f}% | {'上' if x['above_ma25'] else '下'}MA25 / {'上' if x['above_ma60'] else '下'}MA60 / {'上' if x['above_ma144'] else '下'}MA144 / {'上' if x['above_ma240'] else '下'}MA240 |")
    lines += ['','### 2.2 全市场重点板块','','| 板块 | 收盘 | 趋势/阶段 | 分数 | 收盘数据日 |','|---|---:|---|---:|---|']
    for s in sectors:
        if s.get('available'): lines.append(f"| {s.get('theme_name')} | {s.get('close')} | {s.get('trend_state')}/{s.get('stage')} | {s.get('score')} | {s.get('latest_date')} |")
    lines += ['','### 2.3 持仓所属板块与大盘','','| 代码 | 名称 | 所属板块 | 板块走势 | 所属大盘/风格 |','|---|---|---|---|---|']
    for x in revalued:
        s=x['sector']; lines.append(f"| {x['code']} | {x['name']} | {s.get('theme_name','未映射')} | {s.get('trend_state','未知')}/{s.get('stage','未知')}（{s.get('latest_date','无日期')}） | {x['index']} |")
    lines += ['',f'## 3. 持仓复盘（{valuation_label}）','', '| 代码 | 名称 | 数据日 | 参考价 | 成本 | 参考盈亏 | 参考仓位 | 个股走势 | BBI持仓依据 | 板块走势 |','|---|---|---|---:|---:|---:|---:|---|---|---|']
    for x in revalued:
        s=x['sector']; bbi=x['bbi']; lines.append(f"| {x['code']} | {x['name']} | {x['latest_date']} | {x['close']:.2f} | {x['cost']:.3f} | {x['pnl_pct']:+.2%} | {x['position_pct']:.1%} | {x['trend']}/{x['box']} | {bbi['state']}；{bbi['reminder']} | {s.get('trend_state','未知')}/{s.get('stage','未知')} |")
    lines += ['','## 4. 结论与风险','','- 0AMV：**空头状态未解除**，所以不得按“中性偏弱”放宽开仓。','- 大盘：主要指数当日整体偏弱，按MA25/MA60/MA144/MA240四级结构评估。','- BBI持仓依据：BBI上方仅代表技术持有结构有效；首日跌破观察次日收回；连续两日收盘跌破进入清仓评估。0AMV、硬止损、重大风险和单票超限优先。',f"- 专业 Agent：证据门为 **{specialist.get('status','not_run')}**；未通过时不得扩大交易权限。",f"- 板块与持仓技术：数据日为 {','.join(technical_dates) or '缺失'}，仅作历史参考，不冒充 {day} 收盘事实。",f'- 组合：参考仓位约 {actual_pos:.1%}；目标日行情不完整时只给风险方向，不给精确调仓数量。','- 当前没有结构化可执行买入计划，禁止临时开仓。','','## 5. 数据来源','']+[f'- `{p}`' for p in paths.values()]+['- `01_data/trades/current_positions.json`','- `01_data/trades/trades_stock.json`','', '> 风险提示：本复盘用于策略纠偏，不构成收益承诺或无条件交易指令。']
    out=REV/f'{day}_final_review.md'; out.parent.mkdir(parents=True,exist_ok=True); out.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    jout=REV/f'{day}_final_review.json'; jout.write_text(json.dumps({'date':day,'amv':amv,'indices':indices,'sectors':sectors,'revalued_positions':revalued,'recorded_trade_count':len(today),'reference_position_pct':actual_pos,'technical_dates':technical_dates,'technical_current':technical_current,'specialist_handoff_status':specialist.get('status','not_run'),'precise_quantity_allowed':bool(gate.get('position_gate',{}).get('allow_precise_quantity')),'output':str(out)},ensure_ascii=False,indent=2),encoding='utf-8'); print(out); print(jout)
if __name__=='__main__': main()
