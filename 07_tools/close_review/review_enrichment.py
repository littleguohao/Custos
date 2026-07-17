# -*- coding: utf-8 -*-
"""Build theme lifecycle, holding diagnosis, next-day plans and rule review."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
DATA = BASE / "01_data"


def load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def bare(value):
    return str(value or "").split(".")[0]


def lifecycle(row: dict, event_count: int) -> dict:
    raw = str(row.get("raw_stage") or row.get("state") or "数据不足")
    if "退潮" in raw or row.get("trend") == "下跌":
        phase = "退潮"
    elif "主升" in raw:
        phase = "主升"
    elif "修复" in raw:
        phase = "修复"
    elif "分歧" in raw:
        phase = "分歧"
    else:
        phase = "震荡/待确认"
    return {
        "theme_id": row.get("theme_id"),
        "theme_name": row.get("sector"),
        "phase": phase,
        "technical_stage": raw,
        "score": row.get("score"),
        "trend": row.get("trend"),
        "event_evidence_count": event_count,
        "fund_flow_evidence": "unavailable",
        "leader_structure": "unavailable",
        "continuity": "weak" if phase == "退潮" else "unavailable",
        "validation": "观察板块价格、成交、核心锚点和事件是否继续共振",
        "invalidation": "核心锚点破位、板块转弱或催化未获价格确认",
        "permission_rule": "theme lifecycle is a filter, not a direct trade authorization",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    day = args.date
    chief = load(DATA / "decisions" / f"{day}_chief_decision.json", {})
    sectors = load(DATA / "sectors" / f"{day}_sector_state.json", [])
    tech = load(DATA / "holdings" / f"{day}_holding_technical_summary.json", [])
    execution = load(DATA / "review_steps" / f"{day}_execution_review.json", {})
    news = load(DATA / "news" / "postclose" / f"{day}_postclose_news_digest.json", {})
    event_counts = {}
    for values in (news.get("sections") or {}).values():
        for event in values:
            for theme in event.get("matched_themes") or []:
                event_counts[theme] = event_counts.get(theme, 0) + 1
    lifecycles = [lifecycle(x, event_counts.get(str(x.get("sector") or "").split("/")[0], 0)) for x in sectors]
    exec_by_code = {bare(x.get("code")): x for x in execution.get("rows") or []}
    action_by_code = {bare(x.get("code")): x for x in chief.get("holding_actions") or []}
    diagnoses = []
    plans = []
    for row in tech:
        code = bare(row.get("code"))
        action = action_by_code.get(code, {})
        b1 = action.get("b1_holding_state") or row.get("b1_holding_state") or {}
        facts = b1.get("facts") or {}
        n = facts.get("n_structure") or {}
        exec_row = exec_by_code.get(code, {})
        diagnosis = {
            "code": code,
            "name": row.get("name"),
            "original_holding_logic": "unavailable",
            "trend": facts.get("trend_state") or row.get("trend_state"),
            "box": facts.get("box20_position") or row.get("box20_position"),
            "relative_to_sector": row.get("relative_to_sector") or "unavailable",
            "b1_priority": b1.get("final_priority") or action.get("priority"),
            "b1_action": b1.get("final_action") or action.get("b1_reference_action") or action.get("action"),
            "b1_reason": b1.get("final_reason") or ";".join(action.get("reasons") or []),
            "n_l1": n.get("prior_low"),
            "n_l2": n.get("pullback_low"),
            "execution_status": exec_row.get("execution_status") or "unavailable",
            "max_favorable_excursion": "unavailable",
            "trade_feedback": "unavailable" if not exec_row.get("actual_trades") else "recorded",
            "risk_flags": list(dict.fromkeys([x.get("signal") for x in b1.get("signals") or [] if x.get("signal")])),
        }
        diagnoses.append(diagnosis)
        action_plan = b1.get("action_plan") or {}
        plans.append({
            "code": code,
            "name": row.get("name"),
            "direction": diagnosis["b1_action"] or "观察",
            "priority": diagnosis["b1_priority"] or "P3",
            "reduction_pct_of_holding": action_plan.get("suggested_reduction_pct_of_holding"),
            "exact_quantity": None,
            "trigger": diagnosis["b1_reason"] or "等待目标日技术确认",
            "invalidation": "若目标日收盘重新修复关键结构，则重新评估；不得由单一低位指标放宽权限",
            "open_scenario": "仅观察，不因集合竞价或单条消息直接执行",
            "intraday_scenario": "监控硬止损、重大风险和异常流动性；普通波段动作等待14:45确认",
            "tail_scenario": "按目标日行情重算B1并服从RiskDecision/ChiefDecision",
        })
    market_quality = chief.get("market_quality") or {}
    unavailable = [x.get("field") for x in market_quality.get("checks") or [] if x.get("quality") in {"missing", "candidate", "stale"}]
    result = {
        "date": day,
        "theme_lifecycles": lifecycles,
        "holding_diagnoses": diagnoses,
        "next_day_plan": {
            "total_position_range": chief.get("total_position_range"),
            "new_position_permission": chief.get("new_position_permission"),
            "holding_plans": plans,
            "global_validation": chief.get("tomorrow_validation") or [],
            "forbidden_actions": chief.get("forbidden_actions") or [],
        },
        "rule_review": {
            "effective": ["0AMV风险上限已进入总控", "B1按硬风险、N型、BBI、趋势箱体统一排序"],
            "failed": [],
            "pending": ["主线持续性缺资金流和龙头结构证据", "单日结果不足以调整B1、BBI或N型参数", "计划未执行原因需用户确认"],
        },
        "unavailable": list(dict.fromkeys(unavailable + ["market_turnover", "current_market_sentiment", "fund_flow_rank", "original_holding_logic", "max_favorable_excursion"])),
        "permission_rule": "enrichment cannot override RiskDecision or ChiefDecision",
    }
    out = DATA / "review_steps" / f"{day}_review_enrichment.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
