# -*- coding: utf-8 -*-
"""market_timing scorer v1.

Reads strategy_team/01_data/market/YYYY-MM-DD_market_timing_input.json
and generates a markdown decision report.

Scoring modules:
- macro_policy: 15
- 0AMV: 15
- overseas_market: 10
- index_trend: 15
- market_breadth: 15
- sentiment: 15
- turnover: 8
- theme: 7
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parents[2]
IN_DIR = BASE / "01_data" / "market"
OUT_DIR = BASE / "03_daily_plans"
QUALITY_DIR = BASE / "01_data" / "quality"


def fnum(x):
    try:
        return float(x) if x is not None else None
    except Exception:
        return None


def score_macro(d: dict) -> tuple[float, str]:
    mp = d.get("macro_policy", {})
    if not any(mp.get(k) for k in ["monetary_policy", "fiscal_policy", "credit_environment", "regulation_environment"]):
        return 7.5, "宏观政策未填，按中性半分处理；需人工补充货币/财政/信用/监管判断。"
    score = 0
    notes = []
    if mp.get("monetary_policy") == "宽松": score += 4
    elif mp.get("monetary_policy") == "中性": score += 2
    else: notes.append("货币政策非宽松")
    if mp.get("fiscal_policy") == "积极": score += 4
    elif mp.get("fiscal_policy") == "中性": score += 2
    else: notes.append("财政政策非积极")
    if mp.get("credit_environment") == "扩张": score += 3
    elif mp.get("credit_environment") == "稳定": score += 1.5
    else: notes.append("信用环境偏收缩")
    if mp.get("regulation_environment") == "呵护市场": score += 4
    elif mp.get("regulation_environment") == "中性": score += 2
    else: notes.append("监管环境压制风险偏好")
    return min(score, 15), "；".join(notes) or "宏观环境偏友好。"


def score_amv(d: dict) -> tuple[float, str]:
    amv = d.get("amv_0", {})
    v = fnum(amv.get("amv_change_pct"))
    effective = amv.get("effective_state") or amv.get("amv_zone")
    reason = amv.get("state_transition_reason") or ""
    if effective == "空头":
        return 0, f"0AMV有效状态为空头。{reason} 当日值={v if v is not None else '缺失'}%。"
    if effective == "做多":
        return 15, f"0AMV有效状态为做多。{reason} 当日值={v if v is not None else '缺失'}%。"
    if v is None:
        return 7.5, "0AMV 未填且无锁定状态，按中性半分处理。"
    if v > 0:
        return 9, f"0AMV {v:.2f}%，无锁定前态下中性偏多。"
    return 5, f"0AMV {v:.2f}%，无锁定前态下中性偏弱。"


def score_overseas(d: dict) -> tuple[float, str]:
    om = d.get("overseas_market", {})
    vals = [fnum(om.get(k)) for k in ["nasdaq_change_pct", "sp500_change_pct", "sox_change_pct", "nikkei_change_pct", "kospi_change_pct", "hstech_change_pct"]]
    vals = [v for v in vals if v is not None]
    if not vals:
        return 5, "外围市场未填，按中性半分处理。"
    avg = sum(vals)/len(vals)
    if avg >= 1.0: return 10, f"外围平均涨幅 {avg:.2f}%，利多风险偏好。"
    if avg >= 0.2: return 7, f"外围平均涨幅 {avg:.2f}%，中性偏多。"
    if avg <= -1.0: return 1, f"外围平均跌幅 {avg:.2f}%，明显利空。"
    if avg < -0.2: return 3, f"外围平均跌幅 {avg:.2f}%，中性偏弱。"
    return 5, f"外围平均 {avg:.2f}%，中性。"


def score_indices(d: dict) -> tuple[float, str]:
    idx = d.get("a_share_indices", {})
    items = []
    for name, x in idx.items():
        if not x.get("available"):
            continue
        intraday = (x.get("intraday") or {}).get("intraday_change_pct")
        ch20 = fnum(x.get("change_20d_pct"))
        above25 = x.get("above_ma25")
        above60 = x.get("above_ma60")
        above144 = x.get("above_ma144")
        above240 = x.get("above_ma240")
        s = 0
        if intraday is not None:
            if intraday > 1: s += 1.5
            elif intraday > 0: s += 1
            elif intraday < -1: s -= 1
        if ch20 is not None:
            if ch20 > 3: s += 1.5
            elif ch20 > 0: s += 1
            elif ch20 < -3: s -= 1.5
            elif ch20 < 0: s -= 0.8
        if above25 is True: s += 0.8
        elif above25 is False: s -= 0.6
        if above60 is True: s += 1
        elif above60 is False: s -= 0.8
        if above144 is True: s += 0.6
        elif above144 is False: s -= 0.5
        if above240 is True: s += 0.6
        elif above240 is False: s -= 0.5
        items.append((name, s, intraday, ch20, above25, above60, above144, above240))
    if not items:
        return 7.5, "指数数据缺失，按中性处理。"
    raw = sum(x[1] for x in items) / (len(items) * 6)  # approx -1~1
    score = max(0, min(15, 7.5 + raw * 7.5))
    strong = [x[0] for x in items if (x[2] or 0) > 1 or (x[3] or 0) > 3]
    weak = [x[0] for x in items if (x[2] or 0) < -1 or (x[3] or 0) < -3]
    note = f"多指数结构：强={strong or '无明显'}，弱={weak or '无明显'}；四指数分化时按结构性行情处理。"
    return round(score, 2), note


def score_breadth(d: dict) -> tuple[float, str]:
    b = d.get("market_breadth", {})
    up, down = fnum(b.get("up_count")), fnum(b.get("down_count"))
    q = b.get("quality")
    if up is None or down is None or down == 0:
        return 7.5, "涨跌家数缺失，按中性处理。"
    ratio = up/down
    if ratio >= 2: s = 15
    elif ratio >= 1.2: s = 11
    elif ratio >= 0.8: s = 8
    elif ratio >= 0.5: s = 5
    else: s = 2
    if q == "candidate":
        s = (s + 7.5) / 2
        return round(s, 2), f"涨跌比 {ratio:.2f}，但字段为候选口径，折中处理。"
    return s, f"涨跌比 {ratio:.2f}。"


def score_sentiment(d: dict) -> tuple[float, str]:
    snt = d.get("sentiment", {})
    lu = fnum(snt.get("limit_up_count"))
    ld = fnum(snt.get("limit_down_count"))
    blow = fnum(snt.get("blowup_rate"))
    height = fnum(snt.get("market_height"))
    if lu is None or ld is None:
        return 7.5, "涨跌停数据缺失，按中性处理。"
    score = 7.5
    if lu >= 80: score += 4
    elif lu >= 50: score += 2
    elif lu < 30: score -= 1.5
    if ld >= 40: score -= 4
    elif ld >= 20: score -= 2
    elif ld <= 5: score += 1
    if blow is not None:
        if blow > 0.45: score -= 2.5
        elif blow > 0.3: score -= 1.5
        elif blow < 0.15: score += 1
    if height is not None:
        if height >= 5: score += 2
        elif height >= 3: score += 1
        elif height <= 2: score -= 1
    score = max(0, min(15, score))
    return round(score, 2), f"涨停 {lu:.0f}、跌停 {ld:.0f}、炸板率 {blow if blow is not None else 'NA'}、高度 {height if height is not None else 'NA'}。"


def score_turnover(d: dict) -> tuple[float, str]:
    t = d.get("turnover", {})
    chg = fnum(t.get("turnover_change_pct"))
    if chg is None:
        return 4, "成交额变化率未确认；Amount候选已采集但单位/口径待确认，按半分处理。"
    if chg > 15: return 8, f"成交额放量 {chg:.2f}%。"
    if chg > 5: return 6, f"成交额温和放量 {chg:.2f}%。"
    if chg < -15: return 1, f"成交额明显缩量 {chg:.2f}%。"
    if chg < -5: return 3, f"成交额缩量 {chg:.2f}%。"
    return 4, f"成交额变化 {chg:.2f}%，中性。"


def score_theme(d: dict) -> tuple[float, str]:
    th = d.get("theme", {})
    clarity = th.get("theme_clarity")
    if clarity == "强": return 7, th.get("theme_summary") or "主线清晰。"
    if clarity == "中": return 4.5, th.get("theme_summary") or "主线一般。"
    if clarity == "弱": return 2, th.get("theme_summary") or "主线弱。"
    # Infer a little from sentiment details, but keep conservative.
    details = (d.get("sentiment") or {}).get("details") or {}
    leaders = details.get("sample_leaders") or []
    if leaders:
        return 4, "主线字段未填；从涨停样本看 AI/半导体有活跃线索，暂按中性偏弱。"
    return 3.5, "主线清晰度未填，按半分处理。"


def status_from_score(score: float) -> tuple[str, str, str, str]:
    if score >= 80: return "进攻", "60%-80%", "允许", "普通"
    if score >= 60: return "震荡偏强", "40%-60%", "允许，但精选", "普通"
    if score >= 40: return "震荡偏弱", "30%-50%", "仅低吸 / 小仓核心主线", "提高"
    if score >= 20: return "防守", "20%-40%", "原则上不新开", "提高"
    return "冰点", "0%-20%", "禁止追涨", "强风控"


def make_report(d: dict, module_scores: list[tuple[str,int,float,str]], quality_gate: dict | None = None) -> str:
    total = round(sum(x[2] for x in module_scores), 2)
    status, position, open_perm, risk = status_from_score(total)
    q = quality_gate or {}
    q_status = (q.get("market_quality") or {}).get("status")
    if q_status == "blocked":
        open_perm, risk = "禁止", "强风控"
        position = "0%-20%"
    elif q_status == "degraded":
        if open_perm.startswith("允许"):
            open_perm = "仅观察 / 小仓待确认"
        risk = "提高"
    lines = []
    lines.append(f"# market_timing 自动评分报告\n")
    lines.append(f"日期：{d.get('date')}\n")
    lines.append("## 1. 市场状态\n")
    lines.append(f"- 状态：**{status}**")
    lines.append(f"- 择时评分：**{total}/100**")
    lines.append(f"- 建议总仓位：**{position}**")
    lines.append(f"- 今日是否允许开新仓：**{open_perm}**")
    lines.append(f"- 风控等级：**{risk}**\n")
    lines.append("## 2. 模块评分\n")
    lines.append("| 模块 | 权重 | 得分 | 判断 |")
    lines.append("|---|---:|---:|---|")
    for name, weight, score, note in module_scores:
        lines.append(f"| {name} | {weight} | {score:.2f} | {note} |")
    lines.append(f"| 合计 | 100 | {total:.2f} | |\n")
    lines.append("## 3. 交易指令\n")
    if status in ("震荡偏弱", "防守", "冰点"):
        lines.append("- 不适合高频短线试错。")
        lines.append("- 不适合追高接力。")
        lines.append("- 优先处理弱持仓和风控项。")
    else:
        lines.append("- 可围绕核心主线精选参与。")
        lines.append("- 仍需遵守个股服从板块、板块服从大盘。")
    lines.append("- 若 0AMV 未填，最终仓位不得上调到进攻档。")
    lines.append("\n## 4. 数据质量提示\n")
    for n in (d.get("data_quality") or {}).get("notes", []):
        lines.append(f"- {n}")
    if not (d.get("data_quality") or {}).get("notes"):
        lines.append("- 无特殊数据质量提示。")
    if q:
        lines.append(f"- 运行时质量门：{q_status or '未知'}，评分 {(q.get('market_quality') or {}).get('quality_score', 'NA')}。candidate/partial 数据不得上调交易权限。")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--input", default="")
    args = ap.parse_args()
    inp = Path(args.input) if args.input else IN_DIR / f"{args.date}_market_timing_input.json"
    d = json.loads(inp.read_text(encoding="utf-8"))
    modules = [
        ("宏观政策环境", 15, *score_macro(d)),
        ("0AMV 活跃市值", 15, *score_amv(d)),
        ("外围市场影响", 10, *score_overseas(d)),
        ("指数趋势", 15, *score_indices(d)),
        ("市场宽度", 15, *score_breadth(d)),
        ("情绪强度", 15, *score_sentiment(d)),
        ("成交量能", 8, *score_turnover(d)),
        ("主线清晰度", 7, *score_theme(d)),
    ]
    # tuple shape: name, weight, score, note after star expansion
    modules = [(m[0], m[1], float(m[2]), str(m[3])) for m in modules]
    gate_path = QUALITY_DIR / f"{d.get('date')}_runtime_gate.json"
    quality_gate = json.loads(gate_path.read_text(encoding="utf-8")) if gate_path.exists() else {}
    report = make_report(d, modules, quality_gate)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{d.get('date')}_market_timing_score.md"
    out.write_text(report, encoding="utf-8")
    print(out)
    print(report)


if __name__ == "__main__":
    main()
