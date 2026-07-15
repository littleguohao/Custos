# -*- coding: utf-8 -*-
"""Render the unified daily report from structured ChiefDecision.

ChiefDecision is the only final-action authority. Upstream market, sector and
position artifacts may add evidence, but cannot override its permissions.
"""
from __future__ import annotations
import argparse, json, math
from datetime import datetime
from pathlib import Path
from typing import Any

from close_review.holding_structure import n_structure_basis

BASE=Path(__file__).resolve().parents[1]; DATA=BASE/'01_data'; PLAN=BASE/'03_daily_plans'; WEEKDAY='一二三四五六日'
def load(p:Path,d:Any): return json.loads(p.read_text(encoding='utf-8')) if p.exists() else d
def clean(v:Any,d='待确认'):
    if v is None or (isinstance(v,float) and math.isnan(v)): return d
    s=str(v).strip(); return s if s else d
def pct(v:Any):
    try: return f'{float(v)*100:+.1f}%'
    except (TypeError,ValueError): return '待确认'
def ratio(v:Any):
    try: return f'{float(v)*100:.1f}%'
    except (TypeError,ValueError): return '待确认'
def code(v:Any): return str(v or '').split('.')[0]
def num(v:Any,digits=2):
    try: return f'{float(v):,.{digits}f}'
    except (TypeError,ValueError): return '待确认'
def pct_point(v:Any):
    try: return f'{float(v):+.2f}%'
    except (TypeError,ValueError): return '待确认'

ACTION_LABELS={
    'no_add_and_1445_reduce_review':'禁止加仓；14:45复核降至20%以内',
    'priority_no_add_and_1445_reduce_review':'禁止加仓；14:45优先复核降至20%以内',
    'observe_no_add_low_j_is_not_buy_signal':'观察、禁止加仓；低J不是买点',
    'hold_conditionally_no_add':'条件持有、禁止加仓',
    'no_chasing_or_averaging_down_review_divergence':'禁止追涨或补跌；复核量价背离',
    'no_add; reduce 20%-25% of holding on unconfirmed rebound, 14:45 review':'禁止加仓；反弹未获确认时减持20%-25%，14:45复核',
    'no_add; if still over cap on rebound, reduce about 5% of holding at 14:45':'禁止加仓；反弹后仍超单票上限时，14:45减持约5%',
    'reduce 10%-20% on rally without sector confirmation':'反弹无板块确认时减持10%-20%',
    'no_add; reduce 10%-20% at 14:45 if rebound fails to repair structure':'禁止加仓；反弹未修复结构时14:45减持10%-20%',
    'no dip-buy; reduce 10%-20% on weak rebound or renewed reversal':'禁止逢跌补仓；弱反弹或再次转弱时减持10%-20%',
}

def previous_review(day:str)->dict[str,Any]:
    review_dir=BASE/'04_reviews'/'daily'
    candidates=[]
    for path in review_dir.glob('*_final_review.json'):
        file_day=path.name[:10]
        if file_day < day:
            candidates.append((file_day,path))
    return load(max(candidates)[1],{}) if candidates else {}

def previous_holding_actions(review:dict[str,Any])->dict[str,dict[str,Any]]:
    rows=review.get('position_audit')
    if not isinstance(rows,list):
        rows=((review.get('step_4_holdings') or {}).get('holdings') or [])
    return {code(x.get('code')):x for x in rows if isinstance(x,dict)}

def technical_relation(row:dict[str,Any])->str:
    above=[str(n) for n in (25,60,144,240) if row.get(f'above_ma{n}') is True]
    below=[str(n) for n in (25,60,144,240) if row.get(f'above_ma{n}') is False]
    parts=[]
    if above: parts.append('站上MA'+'/'.join(above))
    if below: parts.append('低于MA'+'/'.join(below))
    return '；'.join(parts) or '四均线待确认'

def bbi_holding_reminder(row:dict[str,Any])->tuple[str,str]:
    value=row.get('bbi'); above=row.get('above_bbi'); below_days=row.get('consecutive_closes_below_bbi')
    if value is None or above is None:
        return 'BBI待确认','缺少BBI数据，不据此调整持仓'
    try: days=int(below_days or 0)
    except (TypeError,ValueError): days=0
    distance=pct_point(row.get('bbi_distance_pct'))
    state=f"BBI {num(value)}；收盘{'上方' if above else '下方'}（偏离{distance}）"
    if above:
        reminder='仅技术维度持有结构有效；继续拿住，若BBI上方连续两根中大阳则分批止盈；更高优先级风控仍有效'
    elif days >= 2:
        reminder=f'连续{days}日收盘跌破BBI；按B1进入清仓评估，硬风险优先'
    else:
        reminder='首日收盘跌破BBI；先看次日能否快速收回，未收回则升级清仓评估'
    return state,reminder

def adjustment_with_bbi(row:dict[str,Any],event:dict[str,Any]|None)->str:
    base=plan_adjustment('',event)
    structure=n_structure_basis(row,row.get('close'))
    if structure.get('signal')=='structural_clear':
        return '结构风控：收盘失守N型前低，结构失效，进入清仓/退出评估；优先级高于BBI'
    if structure.get('signal')=='pullback_failure':
        return '结构收紧：更高回踩低点失守，N型尝试失败；主结构前低未破，进入减仓/清仓评估'
    above=row.get('above_bbi'); below_days=row.get('consecutive_closes_below_bbi')
    try: days=int(below_days or 0)
    except (TypeError,ValueError): days=0
    if above is False and days >= 2:
        return f'技术提醒收紧：连续{days}日收盘跌破BBI，进入清仓评估；最终动作服从总控'
    if above is False:
        return '新增观察：首日跌破BBI，验证次日能否快速收回；最终动作服从总控'
    return base

def direction_label(v:Any)->str:
    s=str(v or '').lower()
    if s in {'positive','bullish','利好'}: return '利好'
    if s in {'negative','bearish','利空'}: return '利空'
    if s in {'neutral','中性'}: return '中性'
    return '待确认'

def fallback_rss_events(day:str)->list[dict[str,Any]]:
    path=DATA/'news'/'rss'/'filtered'/f'{day}_premarket_rss_candidates.json'
    items=load(path,[])
    selected=[]
    for item in items:
        if item.get('matched_market_keywords') and int(item.get('relevance_score') or 0)>=80:
            selected.append({
                'published_at':item.get('published_at'), 'title':item.get('title'),
                'direction':item.get('direction'), 'impact':'仅作候选风险证据',
                'source':item.get('source_name'), 'quality':item.get('quality','candidate'),
            })
    return selected[:3]

def plan_adjustment(prior_action:str,event:dict[str,Any]|None)->str:
    direction=direction_label((event or {}).get('direction'))
    if direction=='利空': return '收紧：提高开盘风险复核优先级；不得放宽原计划'
    if direction=='利好': return '不放宽：观察利好兑现强度，原风控计划继续有效'
    return '不调整：维持上次复盘计划'

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--data-date'); ap.add_argument('--session',default=''); ap.add_argument('--output'); a=ap.parse_args()
    day=a.data_date or a.date; dt=datetime.strptime(a.date,'%Y-%m-%d')
    chief_path=DATA/'decisions'/f'{day}_chief_decision.json'
    if not chief_path.exists(): raise SystemExit(f'mandatory ChiefDecision missing: {chief_path}')
    chief=load(chief_path,{}); market=load(DATA/'market'/f'{day}_market_timing_input.json',{}); positions=load(DATA/'trades'/'current_positions.json',[]); sectors=load(DATA/'sectors'/f'{day}_sector_state.json',[]); pool=load(DATA/'stock_pool'/f'{day}_stock_pool_normalized.json',[])
    technical=load(DATA/'holdings'/f'{day}_holding_technical_summary.json',[]); tech={code(x.get('code')):x for x in technical}
    prior=previous_review(day); prior_day=prior.get('date','待确认'); prior_actions=previous_holding_actions(prior)
    intel=load(DATA/'news'/'premarket'/f'{day}_premarket_intelligence.json',{}); market_events=intel.get('market_events') or fallback_rss_events(day); holding_events=intel.get('holding_events') or []
    holding_event_map={code(x.get('code')):x for x in holding_events}
    pos={code(x.get('代码')):x for x in positions}; quality=chief.get('market_quality',{}); freshness=chief.get('position_freshness',{}); pgate=chief.get('position_gate',{}); specialist=chief.get('specialist_handoff',{})
    window=intel.get('window') or {}; window_start=window.get('start') or f'{prior_day} 15:00'; window_end=window.get('end') or f'{a.date} 09:00'
    lines=[f'# 每日投研简报｜{dt.year}年{dt.month}月{dt.day}日（星期{WEEKDAY[dt.weekday()]}）'+(f'｜{a.session}' if a.session else ''),'',f'> 信息窗口：{window_start} 至 {window_end}（Asia/Shanghai）  ',f'> 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")} Asia/Shanghai','', '## 1. 今日核心结论','',f"**{chief.get('market_state','未知')}，总仓位建议 {chief.get('total_position_range','待确认')}；新开仓权限：{chief.get('new_position_permission','禁止')}。**",'',f"- 择时评分：{chief.get('market_score','待确认')}",f"- 风控等级：{chief.get('risk_level','提高')}",f"- 市场数据质量：{quality.get('status','未知')}（{quality.get('quality_score','NA')}）",f"- 持仓快照：{freshness.get('status','未知')}——{freshness.get('reason','')}",f"- 异步专业研究增强：{specialist.get('status','not_run')}（不阻断正式报告，不提高交易权限）",f"- 精确数量权限：{'允许' if pgate.get('allow_precise_quantity') else '禁止'}",'', '## 2. 隔夜重大消息与持仓公告','', '### 2.1 市场重大消息','']
    overseas=market.get('overseas_market',{}); amv=market.get('amv_0',{})
    lines += ['| 时间 | 事件 | 方向 | 对A股/持仓的影响 | 来源/质量 |','|---|---|---|---|---|']
    for e in market_events: lines.append(f"| {clean(e.get('published_at'))} | {clean(e.get('title'))} | {direction_label(e.get('direction'))} | {clean(e.get('impact'))} | {clean(e.get('source'))}/{clean(e.get('quality'),'candidate')} |")
    if not market_events: lines.append('| - | 信息窗口内未发现达到展示门槛的重大消息 | 中性 | 不据此调整交易计划 | 检索完成 |')
    lines += ['', '### 2.2 持仓相关消息与公告','', '| 代码/名称 | 时间 | 事件 | 方向 | 计划影响 | 来源/质量 |','|---|---|---|---|---|---|']
    for e in holding_events: lines.append(f"| {code(e.get('code'))} {clean(e.get('name'))} | {clean(e.get('published_at'))} | {clean(e.get('title'))} | {direction_label(e.get('direction'))} | {clean(e.get('impact'))} | {clean(e.get('source'))}/{clean(e.get('quality'),'candidate')} |")
    if not holding_events: lines.append('| 全部持仓 | - | 信息窗口内未检索到持仓相关公告或高相关消息 | 中性 | 维持上次复盘计划 | 公告检索完成 |')
    lines += ['', '## 3. 美国、日本、韩国市场','', '| 市场 | 指数 | 点位 | 涨跌幅 | 行情性质 | 数据时间 |','|---|---|---:|---:|---|---|']
    details=overseas.get('details') or {}
    for key,market_name in [('dow','美国'),('sp500','美国'),('nasdaq','美国'),('nikkei','日本'),('kospi','韩国')]:
        item=details.get(key) or {}
        lines.append(f"| {market_name} | {clean(item.get('name'))} | {num(item.get('price'))} | {pct_point(item.get('change_pct'))} | {clean(item.get('data_kind'),'待确认')} | {clean(item.get('last_time_local_hint'))} |")
    lines += ['',f"- 外围综合判断：**{clean(overseas.get('overall_signal'))}**。{clean(overseas.get('overseas_summary'))}",'- 美国已收盘数据与日韩开盘后最新数据必须分开标注；缺值不得用历史数据替代。']
    lines += ['', f'## 4. 持仓状态与上次计划调整（上次复盘：{prior_day}）','', '| 代码/名称 | 最新技术状态 | 四均线/J值 | BBI与N型前低 | B1/总控动作 | 上次复盘计划 | 隔夜新证据 |','|---|---|---|---|---|---|---|']
    for x in chief.get('holding_actions',[]):
        c=code(x.get('code')); t=tech.get(c,{}); p=prior_actions.get(c,{}); event=holding_event_map.get(c)
        prior_action=ACTION_LABELS.get(p.get('action'),clean(p.get('action'),'无可用计划'))
        tech_state=f"{clean(t.get('latest_date'))} 收{num(t.get('close'))}；{clean(t.get('trend_state'))}；仓位{ratio(t.get('position_pct'))}"
        ma_j=f"{technical_relation(t)}；日J={num(t.get('daily_j'),1)}"
        bbi_state,bbi_reminder=bbi_holding_reminder(t)
        structure=n_structure_basis(t,t.get('close'))
        new_evidence=f"{direction_label(event.get('direction'))}：{clean(event.get('title'))}" if event else '无新增持仓事件'
        current_action=f"{x.get('priority','P3')} {clean(x.get('action'),'观察')}；{'；'.join(x.get('reasons') or [])}"
        lines.append(f"| {c} {x.get('name')} | {tech_state} | {ma_j} | {bbi_state}；{bbi_reminder}；{structure['state']}；{structure['reminder']} | {current_action} | {prior_action} | {new_evidence} |")
    if not chief.get('holding_actions'): lines.append('| - | 持仓数据缺失 | - | BBI/N型前低待确认 | 不提高交易权限 | - | - |')
    lines += ['', '## 5. 主线、机会与风险','', '| 方向 | 阶段 | 交易许可 | 理由 |','|---|---|---|---|']
    supported=[x for x in sectors if x.get('trade_permission')=='支持']
    for x in supported[:5]: lines.append(f"| {clean(x.get('sector'))} | {clean(x.get('stage'))} | 支持 | {clean(x.get('reason'))} |")
    if not supported: lines.append('| 暂无 | 待确认 | 仅观察 | 没有获得结构化交易许可的板块 |')
    lines += ['', '### 风险提示','']
    for x in chief.get('forbidden_actions',[]): lines.append(f'- **禁止**：{x}')
    if not chief.get('forbidden_actions'): lines.append('- 无新增禁止项；仍须遵守基础风控。')
    snapshot_date=freshness.get('snapshot_date') or '未知'
    lines += ['', '### 候选审核','', '| 分层 | 代码 | 名称 | 总控结论 | 风控否决 |','|---|---|---|---|---|']
    pool_bucket={code(x.get('code')):x.get('bucket') for x in pool}
    for x in chief.get('buy_actions',[]): lines.append(f"| {clean(pool_bucket.get(code(x.get('code'))),'-')} | {code(x.get('code'))} | {x.get('name')} | {x.get('conclusion')} | {'是' if x.get('blocked_by_risk') else '否'} |")
    if not chief.get('buy_actions'): lines.append('| - | - | 暂无可审核计划 | 禁止临时开仓 | - |')
    lines += ['', '## 6. 当日行动建议','', '| 决策项 | 执行规则 |','|---|---|',f"| 风控优先 | {'；'.join(chief.get('allowed_actions') or ['仅观察'])} |",f"| 新开仓 | {chief.get('new_position_permission','禁止')} |",f"| 仓位管理 | 建议 {chief.get('total_position_range','待确认')}；持仓快照、目标日行情或市场质量未全部通过时只给方向，不给精确数量 |",f"| 开盘验证 | 先验证隔夜利好/利空是否被价格与成交确认，再决定是否收紧计划；利好不得自动放宽权限 |",f"| 下一验证点 | {'；'.join(chief.get('tomorrow_validation') or [])} |",'', '## 7. 数据时效与声明','',f'- ChiefDecision：`{chief_path}`',f"- 持仓新鲜度：{freshness.get('status','未知')}；快照日期 {snapshot_date}；导入时间 {freshness.get('imported_at','未知')}；源文件时间 {freshness.get('source_mtime','未知')}",f"- 市场质量门：{quality.get('status','未知')}；candidate/partial/stale/missing 数据不得上调交易权限。",f"- 盘前情报：{DATA/'news'/'premarket'/f'{day}_premarket_intelligence.json'}；缺失时仅使用RSS候选降级展示。",'- 本报告仅渲染 ChiefDecision 的最终动作，不以消息、技术指标或上游技能覆盖风险否决。','- 本简报用于策略辅助，不构成收益承诺或无条件交易指令。']
    out=Path(a.output) if a.output else PLAN/f'{a.date}_daily_report.md'; out.parent.mkdir(parents=True,exist_ok=True); out.write_text('\n'.join(lines)+'\n',encoding='utf-8'); print(out)
if __name__=='__main__': main()
