# -*- coding: utf-8 -*-
"""批量计算持仓技术面 → {date}_holding_technical_summary.json。

**为什么改成进程内计算**：原实现对每一只持仓 fork 一个 `uv run python
technical_monitor.py` 子进程（N 只持仓 = N 次解释器启动 + N 次 pandas/mootdx
导入），持仓十来只时光是进程与导入开销就是秒级，且每次失败只能靠 stderr 尾巴归因。
现在默认在进程内调 technical_monitor.analyze，并按代码 memoize（同一代码重复出现
在 mapping 里不重复算）。缓存**可失效可注入**：clear_analysis_cache() 显式清理，
analyze_code 是模块级函数便于替换；`--subprocess` 保留旧的逐股 fork 路径作为兜底。
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parents[1]
for _bp in (TOOLS_DIR, TOOLS_DIR.parent / "core"):  # core/: paths 等 L0 模块
    if str(_bp) not in sys.path:
        sys.path.insert(0, str(_bp))

from paths import BASE, HOLDINGS_DIR, TRADES_DIR  # noqa: E402
from paths import read_json as load  # noqa: E402
from contracts import require  # noqa: E402

PY=Path(sys.executable)
TECH=BASE/'src'/"pipeline" / "market_timing"/'technical_monitor.py'
HOLD=HOLDINGS_DIR
TRADES=TRADES_DIR / "current_positions.json"

# code -> analysis dict（或 {"__error__": msg}）。一次运行内的 memo，避免重复计算。
_ANALYSIS_CACHE: dict[str, dict] = {}


def clear_analysis_cache() -> None:
    """显式失效技术面分析缓存（测试 / 长驻进程用）。"""
    _ANALYSIS_CACHE.clear()



def pos_to_row(p):
    code=str(p.get('代码','')).split('.')[0]
    return {'code':code,'name':p.get('名称',''),'holding_amount':p.get('持有金额'),
            'holding_pnl':p.get('持有盈亏'),'holding_pnl_pct':p.get('持有盈亏率'),
            'position_pct':p.get('仓位占比'),'holding_days':p.get('持仓天数'),
            'industry':p.get('关联板块') or '', 'concepts':[], 'industry_chain':'', 'primary_themes':[]}


def analyze_code(code: str, name: str = "") -> dict:
    """进程内计算单只代码的技术面（返回 technical_monitor.analyze 的 analysis 段）。"""
    from market_timing import technical_monitor as tm  # 延迟导入：只在真的要算时才付 pandas 代价
    from code_utils import norm_code
    tcode = norm_code(code)
    return tm.analyze(tm.read_vipdoc(tcode), tcode)


def _analysis_via_subprocess(code: str, name: str, date: str, out: Path) -> dict:
    """旧路径：逐股 fork technical_monitor.py（--subprocess 兜底用）。"""
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    p = subprocess.run([str(PY), str(TECH), '--code', code, '--name', name,
                        '--date', date, '--out', str(out)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', env=env)
    if p.returncode != 0:
        return {"available": False, "error": (p.stderr or "")[-1000:]}
    return load(out, {}).get('analysis', {})


def _analysis_for(code: str, name: str, date: str, use_subprocess: bool) -> dict:
    if code in _ANALYSIS_CACHE:
        return _ANALYSIS_CACHE[code]
    out = HOLD / f'{date}_technical_{code}.json'
    if use_subprocess:
        an = _analysis_via_subprocess(code, name, date, out)
    else:
        try:
            an = analyze_code(code, name)
        except Exception as e:      # 单只失败只降级这一只，不影响其余持仓
            an = {"available": False, "error": f"{type(e).__name__}: {e}"}
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"code": code, "name": name, "analysis": an},
                                      ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError as e:
            print(f"[WARN] 单股技术面落盘失败 {code}: {e}", file=sys.stderr)
    _ANALYSIS_CACHE[code] = an
    return an


def _row_from_analysis(it: dict, code: str, an: dict) -> dict:
    if not an.get('available'):
        return {**it, 'code': code, 'technical_available': False,
                'technical_error': an.get('error')}
    return {**it, 'code':code,'technical_available':True,'latest_date':an.get('latest_date'),
            'trend_state':(an.get('trend') or {}).get('state'),'close':(an.get('trend') or {}).get('close'),
            'ma25':(an.get('trend') or {}).get('ma25'),'ma60':(an.get('trend') or {}).get('ma60'),
            'ma144':(an.get('trend') or {}).get('ma144'),'ma240':(an.get('trend') or {}).get('ma240'),
            'above_ma25':(an.get('trend') or {}).get('above_ma25'),'above_ma60':(an.get('trend') or {}).get('above_ma60'),
            'above_ma144':(an.get('trend') or {}).get('above_ma144'),'above_ma240':(an.get('trend') or {}).get('above_ma240'),
            'bbi':(an.get('bbi') or {}).get('value'),'above_bbi':(an.get('bbi') or {}).get('close_above'),
            'bbi_distance_pct':(an.get('bbi') or {}).get('distance_pct'),
            'consecutive_closes_below_bbi':(an.get('bbi') or {}).get('consecutive_closes_below'),
            'n_structure':an.get('n_structure') or {'available':False},
            'descending_n_structure':an.get('descending_n_structure') or {'available':False},
            'n_structure_prior_low':(an.get('n_structure') or {}).get('prior_low'),
            'n_structure_prior_low_date':(an.get('n_structure') or {}).get('prior_low_date'),
            'n_structure_origin_extreme_low':(an.get('n_structure') or {}).get('origin_extreme_low'),
            'n_structure_pullback_low':(an.get('n_structure') or {}).get('pullback_low'),
            'n_structure_pullback_low_date':(an.get('n_structure') or {}).get('pullback_low_date'),
            'n_structure_breakout_level':(an.get('n_structure') or {}).get('breakout_level'),
            'n_structure_confirmed_date':(an.get('n_structure') or {}).get('confirmed_date'),
            'box20_upper':(an.get('box_20d') or {}).get('upper'),'box20_lower':(an.get('box_20d') or {}).get('lower'),
            'box20_mid':(an.get('box_20d') or {}).get('mid'),'box20_position':(an.get('box_20d') or {}).get('position'),
            'box60_upper':(an.get('box_60d') or {}).get('upper'),'box60_lower':(an.get('box_60d') or {}).get('lower'),
            'box60_mid':(an.get('box_60d') or {}).get('mid'),'box60_position':(an.get('box_60d') or {}).get('position'),
            'daily_j':(((an.get('daily') or {}).get('kdj') or {}).get('j')),
            'daily_kdj_golden_cross':(((an.get('daily') or {}).get('kdj') or {}).get('golden_cross')),
            'daily_kdj_death_cross':(((an.get('daily') or {}).get('kdj') or {}).get('death_cross')),
            'daily_kdj_state':(((an.get('daily') or {}).get('kdj') or {}).get('state')),
            'daily_macd_hist':(((an.get('daily') or {}).get('macd') or {}).get('hist')),
            'daily_macd_hist_direction':(((an.get('daily') or {}).get('macd') or {}).get('hist_direction')),
            'daily_macd_golden_cross':(((an.get('daily') or {}).get('macd') or {}).get('golden_cross')),
            'daily_macd_death_cross':(((an.get('daily') or {}).get('macd') or {}).get('death_cross')),
            'weekly_j':(((an.get('weekly') or {}).get('kdj') or {}).get('j')),
            'weekly_kdj_state':(((an.get('weekly') or {}).get('kdj') or {}).get('state')),
            'weekly_macd_hist':(((an.get('weekly') or {}).get('macd') or {}).get('hist')),
            'weekly_macd_hist_direction':(((an.get('weekly') or {}).get('macd') or {}).get('hist_direction')),
            'monthly_j':(((an.get('monthly') or {}).get('kdj') or {}).get('j')),
            'monthly_kdj_state':(((an.get('monthly') or {}).get('kdj') or {}).get('state')),
            'monthly_macd_hist':(((an.get('monthly') or {}).get('macd') or {}).get('hist')),
            'monthly_macd_hist_direction':(((an.get('monthly') or {}).get('macd') or {}).get('hist_direction')),
            'price_volume':an.get('price_volume') or {'available':False}}


def build_summary(items: list[dict], date: str, use_subprocess: bool = False) -> list[dict]:
    """按持仓列表构建技术面 summary 行（同一代码只算一次）。"""
    summary = []
    for it in items:
        code = str(it['code']).split('.')[0]
        name = it.get('name', '')
        an = _analysis_for(code, name, date, use_subprocess)
        summary.append(_row_from_analysis(it, code, an))
    return summary


def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument('--date',required=True)
    ap.add_argument('--mapping',default='')
    ap.add_argument('--subprocess',action='store_true',
                    help='逐股 fork technical_monitor.py（旧路径，进程内计算异常时兜底）')
    a=ap.parse_args(argv)
    mapping=Path(a.mapping) if a.mapping else HOLD/f'{a.date}_holding_sector_mapping_enriched.json'
    if mapping.exists(): items=load(mapping,[])
    else: items=[pos_to_row(x) for x in load(TRADES,[]) if x.get('代码')]
    if not items: raise SystemExit('no current holdings or mapping')
    summary=build_summary(items, a.date, use_subprocess=a.subprocess)
    dest=HOLD/f'{a.date}_holding_technical_summary.json'
    dest.parent.mkdir(parents=True, exist_ok=True)
    # ⚠️ 落盘前校验：11 个消费者，其中 8 处读 latest_date 做陈旧判定。
    require("holding_technical_summary", summary)
    dest.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(dest)
    return 0

if __name__=='__main__': raise SystemExit(main())
