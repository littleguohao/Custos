import json, glob, os

def load_summary(p):
    d = json.load(open(p, encoding='utf-8'))
    s = d.get('trade_summary') or d.get('summary') or d.get('trade_sim') or {}
    return s, d.get('portfolio')

rows = []
for p in sorted(glob.glob('06_logs/m2_sweep/*__*.json')):
    stem = os.path.basename(p)[:-5]
    g, name = stem.split('__', 1)
    s, pf = load_summary(p)
    if not s:
        continue
    rows.append({'group': g, 'name': name, 'n': s.get('n'),
                 'win': s.get('win_rate'), 'exp': s.get('expectancy'),
                 'expR': s.get('expectancy_R'), 'totR': s.get('total_R'),
                 'payoff': s.get('payoff_ratio'), 'avg_win': s.get('avg_win'),
                 'pf': pf})

print('=' * 80)
print('组内 R 对比')
print('=' * 80)
for g in ('A_stop_low', 'B_stop_pct'):
    print()
    print('【' + g + '】')
    hdr = ('方案', '笔数', '胜率', '期望%', '期望R', '累计R', '盈亏比', '均盈%')
    print(hdr)
    for r in [x for x in rows if x['group'] == g]:
        print((r['name'], r['n'] or 0,
               round((r['win'] or 0) * 100, 1),
               round((r['exp'] or 0) * 100, 2),
               round(r['expR'] or 0, 3),
               round(r['totR'] or 0, 1),
               round(r['payoff'] or 0, 3),
               round((r['avg_win'] or 0) * 100, 2)))

print()
print('=' * 80)
print('【C_portfolio】组合级')
print('=' * 80)
hdr = ('方案', '总收益', 'CAGR', '回撤', '成交', '限跳', '收益/回撤')
print(hdr)
for r in sorted([x for x in rows if x['group'] == 'C_portfolio'],
                key=lambda x: -((x['pf'] or {}).get('total_return') or 0)):
    d = r['pf'] or {}
    tr = d.get('total_return') or 0
    dd = d.get('max_drawdown') or 0
    ratio = tr / dd if dd else 0
    taken = d.get('n_taken') or d.get('filled') or 0
    skip = d.get('n_skipped') or d.get('skipped') or 0
    print((r['name'], round(tr * 100, 1), round((d.get('cagr') or 0) * 100, 1),
           round(dd * 100, 1), taken, skip, round(ratio, 2)))