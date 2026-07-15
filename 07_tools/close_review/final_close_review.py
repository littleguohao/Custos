# -*- coding: utf-8 -*-
"""Final close review with news, market, theme, holdings and execution audit."""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_timing.b1_holding_state import evaluate as evaluate_b1_holding

try:
    from .holding_bbi import intraday_bbi_basis
    from .holding_structure import n_structure_basis
except ImportError:
    from holding_bbi import intraday_bbi_basis
    from holding_structure import n_structure_basis

BASE = Path(__file__).resolve().parents[2]
DATA = BASE / "01_data"
REV = BASE / "04_reviews" / "daily"


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def finite(value, default=0.0):
    try:
        number = float(value)
        return default if not math.isfinite(number) else number
    except (TypeError, ValueError):
        return default


def optional_finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def bare(value):
    return str(value or "").split(".")[0]


def index_name(code):
    if code.startswith("688"):
        return "科创50（市场风格代理）"
    if code.startswith(("92", "8", "4")):
        return "北证50（市场风格代理）"
    if code.startswith(("300", "301")):
        return "创业板指（市场风格代理）"
    return "上证指数（市场风格代理）" if code.startswith(("6", "5")) else "深证成指（市场风格代理）"


def sector_for(code, sectors):
    for sector in sectors:
        linked = [bare(x) for x in (sector.get("holding_related") or []) + (sector.get("representative_stocks") or [])]
        if code in linked:
            return sector
    return {}


def render_news(lines, news):
    lines += ["", "## 2. 新闻、政策、风向与舆情", ""]
    sections = news.get("sections") or {}
    for name in ("信息", "政策", "风向", "舆情"):
        lines += [f"### 2.{['信息', '政策', '风向', '舆情'].index(name) + 1} {name}", ""]
        rows = sections.get(name) or []
        if not rows:
            lines.append("- `unavailable`：当前窗口没有通过时效和来源质量门的证据。")
            continue
        lines += ["| 时间 | 事件 | 来源/质量 | 影响对象 | 交易含义 |", "|---|---|---|---|---|"]
        for row in rows[:5]:
            affected = "、".join((row.get("matched_holdings") or []) + (row.get("matched_themes") or [])) or "待确认"
            lines.append(f"| {row.get('published_at')} | {row.get('title')} | {row.get('source_name')}/{row.get('fact_status')} | {affected} | {row.get('trade_meaning')} |")
    if news.get("missing"):
        lines.append("\n- 新闻数据缺失：" + "、".join(news["missing"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--no-trades-confirmed", action="store_true")
    args = ap.parse_args()
    day = args.date
    paths = {
        "chief": DATA / "decisions" / f"{day}_chief_decision.json",
        "market": DATA / "market" / f"{day}_market_timing_input.json",
        "gate": DATA / "quality" / f"{day}_runtime_gate.json",
        "tech": DATA / "holdings" / f"{day}_holding_technical_summary.json",
        "sectors": DATA / "sectors" / f"{day}_sector_technical_summary.json",
        "quotes": DATA / "market" / f"{day}_holding_quotes.json",
        "news": DATA / "news" / "postclose" / f"{day}_postclose_news_digest.json",
        "execution": DATA / "reviews" / f"{day}_execution_review.json",
        "enrichment": DATA / "reviews" / f"{day}_review_enrichment.json",
    }
    for key in ("chief", "market", "gate", "tech", "sectors", "quotes", "execution", "enrichment"):
        if not paths[key].exists():
            raise SystemExit(f"mandatory close-review input missing: {paths[key]}")
    chief = load(paths["chief"], {})
    market = load(paths["market"], {})
    gate = load(paths["gate"], {})
    tech = load(paths["tech"], [])
    sectors = load(paths["sectors"], [])
    quote_snapshot = load(paths["quotes"], {})
    news = load(paths["news"], {"status": "degraded", "sections": {}, "missing": ["postclose_news_digest"]})
    execution = load(paths["execution"], {})
    enrichment = load(paths["enrichment"], {})
    positions = load(DATA / "trades" / "current_positions.json", [])
    trades = load(DATA / "trades" / "trades_stock.json", [])
    today = [x for x in trades if str(x.get("成交日期", "")).startswith(day)]
    amv = market.get("amv_0", {})
    value = amv.get("amv_change_pct")
    regime = amv.get("effective_state")
    if value is None or amv.get("quality") != "confirmed" or not regime:
        raise SystemExit("confirmed close 0AMV/regime missing")
    if args.no_trades_confirmed and today:
        raise SystemExit("no-trades confirmation conflicts with ledger")

    tmap = {bare(x.get("code")): x for x in tech}
    pmap = {bare(x.get("代码")): x for x in positions}
    qmap = {bare(x.get("code")): x for x in quote_snapshot.get("quotes", []) if x.get("date") == day}
    freshness = gate.get("position_freshness", {})
    technical_dates = sorted({str(x.get("latest_date")) for x in tech if x.get("latest_date")})
    technical_current = technical_dates == [day]
    asset_samples = [finite(x.get("持有金额")) / finite(x.get("仓位占比")) for x in positions if finite(x.get("仓位占比")) > 0]
    total_assets = sorted(asset_samples)[len(asset_samples) // 2] if asset_samples else 0
    revalued = []
    for code, position in pmap.items():
        technical = tmap.get(code, {})
        quote = qmap.get(code, {})
        close = optional_finite(quote.get("price"))
        quantity = finite(position.get("持有数量"))
        cost = finite(position.get("单位成本"))
        market_value = close * quantity if close is not None else None
        pnl_pct = close / cost - 1 if close is not None and cost else None
        sector = sector_for(code, sectors)
        b1 = evaluate_b1_holding({**technical, "holding_pnl_pct": pnl_pct}, regime, close, quote.get("date") or day)
        revalued.append({
            "code": code,
            "name": position.get("名称"),
            "quantity": quantity,
            "cost": cost,
            "close": close,
            "price_date": quote.get("date"),
            "price_time": quote.get("time"),
            "technical_date": technical.get("latest_date"),
            "market_value": market_value,
            "pnl_pct": pnl_pct,
            "position_pct": market_value / total_assets if market_value is not None and total_assets else None,
            "trend": technical.get("trend_state"),
            "box": technical.get("box20_position"),
            "bbi": intraday_bbi_basis(technical, close, technical.get("latest_date")),
            "n_structure": n_structure_basis(technical, close),
            "b1_holding_state": b1,
            "sector": sector,
            "index": index_name(code),
        })
    quotes_current = bool(revalued) and all(x["close"] is not None and x["price_date"] == day for x in revalued)
    actual_position = sum(x["position_pct"] for x in revalued if x["position_pct"] is not None) if quotes_current else None
    position_text = "缺失" if actual_position is None else f"{actual_position:.1%}"
    indices = []
    for name, row in market.get("a_share_indices", {}).items():
        if not isinstance(row, dict) or not row.get("available", True):
            continue
        intraday = row.get("intraday") or {}
        indices.append({
            "name": name,
            "close": intraday.get("now", row.get("latest_close")),
            "change_pct": intraday.get("intraday_change_pct"),
            "above_ma25": row.get("above_ma25"),
            "above_ma60": row.get("above_ma60"),
            "above_ma144": row.get("above_ma144"),
            "above_ma240": row.get("above_ma240"),
        })

    quality = chief.get("market_quality") or {}
    checks = {x.get("field"): x for x in quality.get("checks") or []}
    lines = [
        f"# {day} 最终盘后复盘", "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 报告质量：**{'complete' if not enrichment.get('unavailable') and news.get('status') == 'complete' else 'degraded'}**",
        f"> 0AMV当日变动：**{float(value):+.2f}%**；有效状态：**{regime}**",
        f"> 今日实际交易：**{'无交易动作' if not today else str(len(today)) + '笔'}**",
        f"> 持仓确认：**{freshness.get('status')}** — {freshness.get('reason')}",
        "",
        "## 1. 今日计划、14:45建议与实际执行", "",
        f"- 市场状态：**{chief.get('market_state')}**，建议仓位 **{chief.get('total_position_range')}**，收盘重估仓位 **{position_text}**。",
        f"- 执行对账质量：**{execution.get('status', 'unavailable')}**；成交记录 {execution.get('recorded_trade_count', 0)} 笔。",
        "", "| 代码 | 名称 | 盘前动作 | 14:45动作 | 实际动作 | 对账结论 |", "|---|---|---|---|---|---|",
    ]
    for row in execution.get("rows") or []:
        actual = "无成交" if not row.get("actual_trades") else "；".join(f"{x.get('交易类别')} {x.get('成交数量')}股@{x.get('成交价格')}" for x in row["actual_trades"])
        lines.append(f"| {row.get('code')} | {row.get('name')} | {row.get('premarket_action')}（参考：{row.get('premarket_reference_action')}） | {row.get('tail_priority')} {row.get('tail_action')} | {actual} | {row.get('execution_reason')} |")
    render_news(lines, news)

    lines += ["", "## 3. 大盘、资金与市场许可", "", "### 3.1 指数结构", "", "| 指数 | 收盘/最新 | 当日涨跌 | MA25/60/144/240状态 |", "|---|---:|---:|---|"]
    for row in indices:
        lines.append(f"| {row['name']} | {row['close']} | {finite(row['change_pct']):+.2f}% | {'上' if row['above_ma25'] else '下'}MA25 / {'上' if row['above_ma60'] else '下'}MA60 / {'上' if row['above_ma144'] else '下'}MA144 / {'上' if row['above_ma240'] else '下'}MA240 |")
    lines += ["", "### 3.2 宽度、成交与情绪", ""]
    for field, label in (("market_breadth", "市场宽度"), ("turnover", "全市场成交额"), ("sentiment", "涨跌停与情绪")):
        check = checks.get(field, {})
        lines.append(f"- {label}：**{check.get('quality', 'unavailable')}**，数据日 {check.get('as_of') or 'unavailable'}；过期或缺失值不参与当日权限放宽。")
    lines += [f"- 市场许可：新开仓 **{chief.get('new_position_permission')}**，总仓位建议 **{chief.get('total_position_range')}**。"]

    lines += ["", "## 4. 主线、题材生命周期与持续性", "", "| 方向 | 生命周期 | 技术阶段 | 分数 | 事件证据 | 资金/龙头证据 | 持续性 | 次日验证 |", "|---|---|---|---:|---:|---|---|---|"]
    for row in enrichment.get("theme_lifecycles") or []:
        lines.append(f"| {row.get('theme_name')} | {row.get('phase')} | {row.get('technical_stage')} | {row.get('score')} | {row.get('event_evidence_count')} | {row.get('fund_flow_evidence')}/{row.get('leader_structure')} | {row.get('continuity')} | {row.get('validation')} |")

    lines += ["", "## 5. 持仓逐只诊断与仓位审计", "", "| 代码 | 名称 | 收盘/成本 | 盈亏 | 仓位 | 走势 | BBI/N型 | B1动作 | 原始逻辑/相对板块 |", "|---|---|---|---:|---:|---|---|---|---|"]
    diagnoses = {bare(x.get("code")): x for x in enrichment.get("holding_diagnoses") or []}
    for row in revalued:
        diagnosis = diagnoses.get(row["code"], {})
        close_text = "缺失" if row["close"] is None else f"{row['close']:.2f}/{row['cost']:.3f}"
        pnl_text = "缺失" if row["pnl_pct"] is None else f"{row['pnl_pct']:+.2%}"
        pos_text = "缺失" if row["position_pct"] is None else f"{row['position_pct']:.1%}"
        b1 = row["b1_holding_state"]
        lines.append(f"| {row['code']} | {row['name']} | {close_text} | {pnl_text} | {pos_text} | {row['trend']}/{row['box']} | {row['bbi']['signal']}/{row['n_structure']['signal']} | {b1['final_priority']} {b1['final_action']}：{b1['final_reason']} | {diagnosis.get('original_holding_logic', 'unavailable')}/{diagnosis.get('relative_to_sector', 'unavailable')} |")
    lines.append("\n- 单票20%审计：" + "；".join(f"{x['name']} {x['position_pct']:.1%}{'，超限' if x['position_pct'] > .2 else ''}" for x in revalued if x["position_pct"] is not None))

    next_plan = enrichment.get("next_day_plan") or {}
    lines += ["", "## 6. 下一交易日条件化交易计划", "", f"- 总仓位目标：**{next_plan.get('total_position_range')}**；新开仓权限：**{next_plan.get('new_position_permission')}**。", "", "| 代码 | 名称 | 方向/优先级 | 比例 | 触发条件 | 无效条件 | 开盘/盘中/14:45 |", "|---|---|---|---|---|---|---|"]
    for row in next_plan.get("holding_plans") or []:
        reduction = row.get("reduction_pct_of_holding")
        reduction_text = "unavailable" if not reduction else f"持仓的{reduction[0]}%-{reduction[-1]}%"
        lines.append(f"| {row.get('code')} | {row.get('name')} | {row.get('priority')} {row.get('direction')} | {reduction_text} | {row.get('trigger')} | {row.get('invalidation')} | {row.get('open_scenario')} / {row.get('intraday_scenario')} / {row.get('tail_scenario')} |")

    rules = enrichment.get("rule_review") or {}
    behavior = execution.get("behavior_checks") or {}
    lines += ["", "## 7. 纪律偏差、规则有效性与待验证参数", "", "### 7.1 行为纪律", ""]
    lines += [f"- {key}: {value}" for key, value in behavior.items()]
    lines += ["", "### 7.2 有效规则", ""] + [f"- {x}" for x in rules.get("effective") or ["unavailable"]]
    lines += ["", "### 7.3 失效/待验证规则", ""] + [f"- {x}" for x in (rules.get("failed") or []) + (rules.get("pending") or []) or ["unavailable"]]

    unavailable = list(dict.fromkeys((enrichment.get("unavailable") or []) + (news.get("missing") or []) + (execution.get("missing") or [])))
    lines += ["", "## 8. 数据时效、缺失项与风险提示", "", f"- 目标日行情完整：{quotes_current}；技术数据日：{','.join(technical_dates) or 'unavailable'}；目标日技术完整：{technical_current}。", "- 缺失项：" + ("、".join(unavailable) if unavailable else "无"), "- RSS仅用于事件发现；未确认候选不得直接形成交易授权。", "- 新闻、题材、技术信号均不得覆盖0AMV、运行质量门、RiskDecision和ChiefDecision。", "", "## 9. 数据来源", ""]
    lines += [f"- `{path}`" for path in paths.values()]
    lines += ["- `01_data/trades/current_positions.json`", "- `01_data/trades/trades_stock.json`", "", "> 风险提示：本复盘用于策略纠偏，不构成收益承诺或无条件交易指令。"]

    out = REV / f"{day}_final_review.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = {
        "date": day,
        "report_quality": "degraded" if unavailable else "complete",
        "amv": amv,
        "news_digest": news,
        "execution_review": execution,
        "theme_lifecycles": enrichment.get("theme_lifecycles") or [],
        "indices": indices,
        "market_quality_checks": quality.get("checks") or [],
        "revalued_positions": revalued,
        "next_day_plan": next_plan,
        "rule_review": rules,
        "unavailable": unavailable,
        "recorded_trade_count": len(today),
        "reference_position_pct": actual_position,
        "quotes_current": quotes_current,
        "technical_dates": technical_dates,
        "technical_current": technical_current,
        "precise_quantity_allowed": bool(gate.get("position_gate", {}).get("allow_precise_quantity")),
        "output": str(out),
    }
    json_out = REV / f"{day}_final_review.json"
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(out)
    print(json_out)


if __name__ == "__main__":
    main()
