# -*- coding: utf-8 -*-
"""Build normalized strategy contracts from existing artifacts and skill evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from skill_adapters import (
    BuyPlanAdapter,
    CandidateAdapter,
    RiskFlagAdapter,
    SkillEvidence,
    ThemeStageAdapter,
)

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "01_data"


def load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def dump(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    date = args.date

    sector_raw = load(DATA / "sectors" / f"{date}_sector_technical_summary.json", [])
    sector_states = [ThemeStageAdapter.adapt(x, date) for x in sector_raw if x.get("available", True)]
    dump(DATA / "sectors" / f"{date}_sector_state.json", sector_states)

    market_contract = load(DATA / "market" / f"{date}_market_state.json", {})
    market_permission = market_contract.get("new_position_permission")
    stock_raw = load(DATA / "stock_pool" / f"{date}_stock_pool.json", [])
    candidates = CandidateAdapter.adapt_many(stock_raw, sector_states, market_permission)
    dump(DATA / "stock_pool" / f"{date}_stock_pool_normalized.json", candidates)

    evidence_dir = DATA / "skill_evidence" / date
    evidence_files = sorted(evidence_dir.glob("*.json")) if evidence_dir.exists() else []
    evidences = []
    for path in evidence_files:
        raw = load(path, [])
        for item in raw if isinstance(raw, list) else [raw]:
            evidences.append(SkillEvidence.from_dict(item).to_dict())

    raw_plan_dir = DATA / "buy_strategy" / "raw_skill_plans" / date
    raw_plans = {}
    if raw_plan_dir.exists():
        for path in raw_plan_dir.glob("*.json"):
            raw = load(path, {})
            raw_plans[str(raw.get("code") or path.stem).split(".")[0]] = raw

    sector_by_id = {x.get("theme_id"): x for x in sector_states}
    plans = []
    for candidate in candidates:
        if candidate.get("bucket") not in {"A", "B"}:
            continue
        raw_plan = raw_plans.get(str(candidate.get("code")).split(".")[0])
        sector_permission = (sector_by_id.get(candidate.get("theme_id")) or {}).get("trade_permission")
        plans.append(BuyPlanAdapter.adapt(candidate, raw_plan, market_permission, sector_permission))
    dump(DATA / "buy_strategy" / f"{date}_buy_plan_normalized.json", plans)

    holding_reviews = load(DATA / "holdings" / f"{date}_holding_review.json", [])
    holding_risks = []
    for h in holding_reviews:
        action = h.get("action")
        b1 = h.get("b1_holding_state") or {}
        b1_priority = b1.get("final_priority")
        if action in {"减仓", "止损", "清仓"} or b1_priority in {"P0", "P1"}:
            normalized_action = action if action in {"减仓", "止损", "清仓"} else ("清仓" if b1_priority == "P0" else "减仓")
            holding_risks.append({
                "code": h.get("code"), "name": h.get("name", ""),
                "risk_type": "B1持仓结构" if b1_priority else ("破位" if "破位" in str(h.get("box_position")) else "亏损扩大"),
                "action": normalized_action, "priority": "高" if b1_priority == "P0" or normalized_action in {"止损", "清仓"} else "中",
                "reason": "；".join(h.get("reason") or ["portfolio_review触发风控"]),
                "evidence_ref": str(DATA / "holdings" / f"{date}_holding_review.json"),
                "b1_signal_refs": [x.get("signal") for x in b1.get("signals", [])],
            })
    risk = RiskFlagAdapter.adapt(date, evidences=evidences, candidates=candidates, buy_plans=plans, existing=holding_risks)
    dump(DATA / "risk" / f"{date}_risk_decision.json", risk)

    manifest = {
        "date": date,
        "candidate_discovery": "existing_theme_tracker_and_stock_pool",
        "disabled_skills": ["tdx-wxd-a", "tdx-wxd-bk"],
        "enabled_skill_evidence": [
            "tdx-agzxsb", "tdx-tczqcxx", "tdx-board-cpbd", "tdx-hot-topic",
            "tdx-company-info", "tdx-financials", "tdx-earnings-warning", "tdx-trade-plan",
            "tdx-czzdxfxjs",
        ],
        "counts": {
            "sector_states": len(sector_states), "candidates": len(candidates),
            "evidences": len(evidences), "buy_plans": len(plans),
            "holding_reviews": len(holding_reviews), "stock_risks": len(risk["stock_risks"]),
        },
        "outputs": {
            "sector_state": str(DATA / "sectors" / f"{date}_sector_state.json"),
            "stock_pool": str(DATA / "stock_pool" / f"{date}_stock_pool_normalized.json"),
            "buy_plan": str(DATA / "buy_strategy" / f"{date}_buy_plan_normalized.json"),
            "risk_decision": str(DATA / "risk" / f"{date}_risk_decision.json"),
        },
    }
    dump(DATA / "skill_evidence" / f"{date}_adapter_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
