# -*- coding: utf-8 -*-
"""Generate theme_tracker daily sector trend report.

Reads:
- 01_data/sectors/sector_code_map.json
- 01_data/holdings/YYYY-MM-DD_holding_technical_summary.json
- 03_daily_plans/YYYY-MM-DD_market_timing_score.md

Writes:
- 01_data/sectors/YYYY-MM-DD_sector_technical_summary.json
- 03_daily_plans/YYYY-MM-DD_theme_tracker.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent.parent
SECTOR_MAP = BASE / "01_data" / "sectors" / "sector_code_map.json"
SECTOR_DIR = BASE / "01_data" / "sectors"
HOLDINGS_DIR = BASE / "01_data" / "holdings"
OUT_DIR = BASE / "03_daily_plans"
TOOLS_DIR = BASE / "07_tools" / "market_timing"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import technical_monitor as tm  # noqa: E402

def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def latest_holding_summary(date: str) -> list[dict[str, Any]]:
    p = HOLDINGS_DIR / f"{date}_holding_technical_summary.json"
    return load_json(p, []) or []


def classify_stage(a: dict[str, Any]) -> tuple[str, str]:
    if not a.get("available"):
        return "数据不足", a.get("error", "无K线数据")
    trend = (a.get("trend") or {}).get("state")
    box20 = (a.get("box_20d") or {})
    box60 = (a.get("box_60d") or {})
    daily_kdj = ((a.get("daily") or {}).get("kdj") or {})
    daily_macd = ((a.get("daily") or {}).get("macd") or {})
    weekly_macd = ((a.get("weekly") or {}).get("macd") or {})
    pos20 = box20.get("position")
    j = daily_kdj.get("j")
    macd_dir = daily_macd.get("hist_direction")
    weekly_hist = weekly_macd.get("hist")

    if trend == "上涨" and pos20 == "上沿/突破区" and macd_dir == "扩张":
        return "主升/加速", "趋势上涨、处于20日箱体上沿/突破区，日线MACD扩张。"
    if trend == "上涨":
        return "修复/上行", "趋势上涨，但需观察量能和是否有效突破。"
    if trend == "横盘震荡" and pos20 in ("上沿/突破区", "箱体上半区") and macd_dir == "扩张":
        return "修复", "横盘震荡中向箱体上半区修复，日线MACD扩张。"
    if trend == "横盘震荡" and pos20 == "下沿/破位区":
        return "分歧/弱震荡", "横盘震荡但位于箱体下沿，若跌破需转入风控。"
    if trend == "下跌":
        return "退潮/下跌", "趋势下跌，板块不支持加仓。"
    if isinstance(j, (int, float)) and j > 90:
        return "高位分歧观察", "日线J值高位过热，追高风险上升。"
    if weekly_hist is not None and weekly_hist < 0:
        return "震荡", "日线信号一般，周线动能仍偏弱。"
    return "震荡", "趋势未形成明确主升或退潮，按震荡处理。"


def score_sector(a: dict[str, Any], priority: str) -> float:
    if not a.get("available"):
        return 0.0
    score = 50.0
    trend = (a.get("trend") or {})
    box20 = (a.get("box_20d") or {})
    kdj = ((a.get("daily") or {}).get("kdj") or {})
    macd = ((a.get("daily") or {}).get("macd") or {})
    weekly = ((a.get("weekly") or {}).get("macd") or {})
    if trend.get("state") == "上涨": score += 18
    elif trend.get("state") == "下跌": score -= 20
    elif trend.get("state") == "横盘震荡": score += 0
    if box20.get("position") == "上沿/突破区": score += 12
    elif box20.get("position") == "箱体上半区": score += 6
    elif box20.get("position") == "下沿/破位区": score -= 12
    if macd.get("hist_direction") == "扩张": score += 8
    elif macd.get("hist_direction") == "收缩": score -= 3
    j = kdj.get("j")
    if isinstance(j, (int, float)):
        if j > 95: score -= 5
        elif j > 80: score += 2
        elif j < 12: score -= 3
        elif j < 30 and kdj.get("j", 0) > kdj.get("j_prev", 0): score += 5
    if weekly.get("hist") is not None:
        score += 4 if weekly.get("hist") > 0 else -4
    if priority == "high": score += 3
    return round(max(0, min(100, score)), 2)


def action_bias(stage: str, score: float, market_status: str = "震荡偏弱") -> str:
    if "退潮" in stage or score < 35:
        return "回避/禁止加仓"
    if "主升" in stage and score >= 70 and market_status in ("进攻", "震荡偏强"):
        return "可关注核心股"
    if score >= 65:
        return "观察核心低吸，不追高"
    if score >= 50:
        return "观察"
    return "谨慎观察"


def build_sector_summary(date: str) -> list[dict[str, Any]]:
    m = load_json(SECTOR_MAP, {})
    rows = []
    for th in m.get("themes", []):
        codes = th.get("primary_sector_codes") or []
        if not codes:
            rows.append({
                "theme_id": th.get("theme_id"),
                "theme_name": th.get("theme_name"),
                "priority": th.get("priority"),
                "available": False,
                "reason": "no primary sector code",
                "representative_stocks": th.get("representative_stocks", []),
                "semantic_tags": th.get("semantic_tags", []),
            })
            continue
        code = codes[0]
        df = tm.read_vipdoc(code)
        analysis = tm.analyze(df)
        stage, reason = classify_stage(analysis)
        score = score_sector(analysis, th.get("priority", ""))
        rows.append({
            "theme_id": th.get("theme_id"),
            "theme_name": th.get("theme_name"),
            "priority": th.get("priority"),
            "primary_code": code,
            "candidate_codes": th.get("candidate_sector_codes", []),
            "representative_stocks": th.get("representative_stocks", []),
            "holding_related": th.get("holding_related", []),
            "semantic_tags": th.get("semantic_tags", []),
            "confidence": th.get("confidence"),
            "available": bool(analysis.get("available")),
            "latest_date": analysis.get("latest_date"),
            "trend_state": (analysis.get("trend") or {}).get("state"),
            "close": (analysis.get("trend") or {}).get("close"),
            "box20_position": (analysis.get("box_20d") or {}).get("position"),
            "box20_upper": (analysis.get("box_20d") or {}).get("upper"),
            "box20_lower": (analysis.get("box_20d") or {}).get("lower"),
            "daily_j": (((analysis.get("daily") or {}).get("kdj") or {}).get("j")),
            "daily_kdj_state": (((analysis.get("daily") or {}).get("kdj") or {}).get("state")),
            "daily_macd_hist": (((analysis.get("daily") or {}).get("macd") or {}).get("hist")),
            "daily_macd_direction": (((analysis.get("daily") or {}).get("macd") or {}).get("hist_direction")),
            "weekly_macd_hist": (((analysis.get("weekly") or {}).get("macd") or {}).get("hist")),
            "stage": stage,
            "stage_reason": reason,
            "score": score,
            "action_bias": action_bias(stage, score),
            "analysis": analysis,
        })
    rows.sort(key=lambda r: (r.get("available") is not True, -(r.get("score") or 0)))
    return rows


def match_holding_theme(holding: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Match by explicit holding/representative code first, then semantic tags."""
    code = str(holding.get("code") or "").split(".")[0]
    for row in rows:
        linked = [str(x).split(".")[0] for x in (row.get("holding_related") or []) + (row.get("representative_stocks") or [])]
        if code and code in linked:
            return row
    tokens = set(holding.get("primary_themes") or [])
    industry = holding.get("industry")
    if industry and str(industry).lower() != "nan":
        tokens.add(str(industry))
    best: tuple[int, dict[str, Any]] = (0, {})
    for row in rows:
        hay = "|".join([str(row.get("theme_name") or ""), *[str(x) for x in row.get("semantic_tags", [])]])
        score = sum(1 for token in tokens if token and (token in hay or any(part and part in hay for part in str(token).replace("/", "|").split("|"))))
        if score > best[0]:
            best = (score, row)
    return best[1]


def compare_holding_to_theme(holding: dict[str, Any], theme: dict[str, Any]) -> tuple[str, str]:
    ht = holding.get("trend_state")
    tt = theme.get("trend_state")
    hp = holding.get("box20_position")
    tp = theme.get("box20_position")
    if not theme or not theme.get("available"):
        return "未定", "板块数据不足。"
    rank = {"上涨": 3, "横盘震荡": 2, "下跌": 1, None: 0}
    if rank.get(ht, 0) > rank.get(tt, 0):
        return "强于板块", f"个股趋势{ht}，板块趋势{tt}。"
    if rank.get(ht, 0) < rank.get(tt, 0):
        return "弱于板块", f"个股趋势{ht}，板块趋势{tt}。"
    if hp == "下沿/破位区" and tp != "下沿/破位区":
        return "弱于板块", f"个股在{hp}，板块在{tp}。"
    if hp in ("上沿/突破区", "箱体上半区") and tp in ("箱体下半区", "下沿/破位区"):
        return "强于板块", f"个股在{hp}，板块在{tp}。"
    return "同步", f"个股与板块均为{ht}/{tt}，箱体位置 {hp}/{tp}。"


def make_report(date: str, rows: list[dict[str, Any]]) -> str:
    holdings = latest_holding_summary(date)
    market_status = "震荡偏弱"
    strong = [r for r in rows if r.get("available") and (r.get("score") or 0) >= 65]
    risk = [r for r in rows if (not r.get("available")) or "退潮" in str(r.get("stage")) or (r.get("score") or 0) < 45]
    top = rows[0] if rows else {}

    lines = []
    lines.append("# theme_tracker 主线与板块跟踪\n")
    lines.append(f"日期：{date}\n")
    lines.append("## 1. 今日主线\n")
    mainline = top.get("theme_name") or "未定"
    lines.append(f"- 主线方向：**{mainline}**")
    lines.append(f"- 生命周期：**{top.get('stage', '未定')}**")
    lines.append(f"- 主线强度：**{'强' if (top.get('score') or 0) >= 75 else '中' if (top.get('score') or 0) >= 55 else '弱'}**")
    lines.append(f"- 关键证据：{top.get('stage_reason', '无')}；技术分 {top.get('score', 'NA')}。")
    lines.append("- 市场约束：market_timing 仍为震荡偏弱，允许低吸核心主线，但不支持追高和高频试错。\n")

    lines.append("## 2. 强势/可关注板块\n")
    lines.append("| 板块 | 代码 | 状态 | 分数 | 代表股票 | 证据 | 风险 |")
    lines.append("|---|---|---|---:|---|---|---|")
    for r in strong[:8]:
        reps = ", ".join(r.get("representative_stocks", [])[:4])
        risk_note = "J值过热需防追高" if isinstance(r.get("daily_j"), (int, float)) and r.get("daily_j") > 90 else "大盘震荡偏弱，低吸优先"
        lines.append(f"| {r.get('theme_name')} | {r.get('primary_code')} | {r.get('stage')} | {r.get('score')} | {reps} | {r.get('stage_reason')} | {risk_note} |")
    if not strong:
        lines.append("| 无 | - | - | - | - | 当前无分数>=65的板块 | - |")
    lines.append("")

    lines.append("## 3. 退潮/风险板块\n")
    lines.append("| 板块 | 代码 | 风险状态 | 分数 | 风险原因 |")
    lines.append("|---|---|---|---:|---|")
    for r in risk[:8]:
        lines.append(f"| {r.get('theme_name')} | {r.get('primary_code', '')} | {r.get('stage', '数据不足')} | {r.get('score', 0)} | {r.get('stage_reason', r.get('reason', ''))} |")
    if not risk:
        lines.append("| 无明显 | - | - | - | - |")
    lines.append("")

    lines.append("## 4. 持仓板块跟踪\n")
    lines.append("| 代码 | 名称 | 最相关主线 | 板块状态 | 板块分数 | 个股相对板块 | 操作倾向 |")
    lines.append("|---|---|---|---|---:|---|---|")
    for h in holdings:
        code = str(h.get("code"))
        theme = match_holding_theme(h, rows)
        rel, rel_reason = compare_holding_to_theme(h, theme)
        action = h.get("action") or theme.get("action_bias") or "观察"
        if rel == "弱于板块" and action == "观察":
            action = "风控观察"
        lines.append(f"| {code} | {h.get('name')} | {theme.get('theme_name', '未定')} | {theme.get('stage', '未定')} | {theme.get('score', 0)} | {rel}：{rel_reason} | {action} |")
    lines.append("")

    lines.append("## 5. 板块-大盘一致性\n")
    market = load_json(BASE / "01_data" / "market" / f"{date}_market_timing_input.json", {}) or {}
    amv = market.get("amv_0", {})
    lines.append(f"- 大盘状态：{market_status}；0AMV当日 {amv.get('amv_change_pct', '缺失')}%，有效状态 **{amv.get('effective_state', amv.get('amv_zone', '未知'))}**。")
    lines.append("- 强于大盘的板块：" + ("、".join([r.get("theme_name") for r in strong[:5]]) if strong else "暂不明确"))
    weak_names = [r.get("theme_name") for r in risk[:5] if r.get("available")]
    lines.append("- 弱于大盘/需回避板块：" + ("、".join(weak_names) if weak_names else "暂无明确退潮，但低分板块需谨慎"))
    lines.append("- 结构性机会仅来自上表中强于市场且获得交易许可的板块；低分或退潮方向不因长期逻辑直接加仓。\n")

    lines.append("## 6. 给总控的结论\n")
    focus = [r.get("theme_name") for r in strong[:3]]
    lines.append("- 可关注方向：" + ("、".join(focus) if focus else "无明确可进攻方向"))
    lines.append("- 禁止方向：下跌/低分板块、弱于板块的个股、箱体破位个股。")
    weak_holdings = [str(h.get('name')) for h in holdings if compare_holding_to_theme(h, match_holding_theme(h, rows))[0] == '弱于板块']
    lines.append("- 持仓需要重点风控：" + ("、".join(weak_holdings) if weak_holdings else "按 portfolio_review 与 risk_control 动态识别。"))
    lines.append("- 是否允许新开相关方向：仅允许核心主线小仓低吸观察；禁止追高接力。\n")
    lines.append("> 风险提示：板块强弱是交易过滤器，不是直接买入信号；真实交易仍需 stock_pool、buy_strategy、risk_control、chief_decision 全链路确认。")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=pd.Timestamp.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    rows = build_sector_summary(args.date)
    SECTOR_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = SECTOR_DIR / f"{args.date}_sector_technical_summary.json"
    report_path = OUT_DIR / f"{args.date}_theme_tracker.md"
    summary_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    report = make_report(args.date, rows)
    report_path.write_text(report, encoding="utf-8")
    print(summary_path)
    print(report_path)
    print(report[:5000])


if __name__ == "__main__":
    main()
