# -*- coding: utf-8 -*-
"""Generate risk_decision.json and sector_state.json from deterministic pipeline outputs.

Replaces build_skill_contracts.py + skill_adapters.py in the pure-script pipeline.
Reads: holding_review.json, sector_technical_summary.json
Writes: risk_decision.json, sector_state.json
"""
from __future__ import annotations

import argparse
import json


from custos.core.paths import DATA
from custos.core.paths import read_json as load
from custos.core.paths import write_json as dump
from custos.core.code_utils import bare_code as bare, fnum
from custos.core.runtime_guards import normalize_regime
from custos.core.contracts import require


# ── Sector state normalization (from ThemeStageAdapter) ──

STAGE_RULES = (
    (("主升", "加速", "高潮"), "主升"),
    (("修复", "回流", "弱转强", "启动", "发酵"), "修复"),
    (("分歧", "扩散", "高位震荡"), "分歧"),
    (("退潮", "衰退", "下跌", "冰点"), "退潮"),
    (("震荡", "盘整", "整理"), "震荡"))


def normalize_stage(raw_stage: str, trend: str) -> str:
    text = f"{raw_stage or ''}/{trend or ''}"
    for keys, state in STAGE_RULES:
        if any(key in text for key in keys):
            return state
    return "震荡"


def build_sector_state(date: str) -> list[dict]:
    sector_raw = load(DATA / "sectors" / f"{date}_sector_technical_summary.json", [])
    result = []
    for row in sector_raw:
        if not row.get("available", True):
            continue
        raw_stage = row.get("raw_stage", row.get("stage", row.get("state", "")))
        trend = row.get("trend", row.get("trend_state", "横盘震荡"))
        state = normalize_stage(raw_stage, trend)
        # ⚠️ 判定读**原值**、落盘写 `fnum(score)`，两者刻意不同：
        #   · 判定：NaN 时 `float(nan) >= 60` 为 False ⇒ 落到「观察」，这是**保守**的。
        #     不能顺手换成 fnum —— 那会让 NaN 变 None，命中下面 `score is None`
        #     （「没打分不算减分项」）⇒ 从观察**放宽成支持**，方向正好反了。
        #   · 落盘：NaN 不是合法 JSON，且 `paths.write_json` 的 `allow_nan=False`
        #     会当场崩；这是硬失败 stage，不能因一个板块的脏分数拖垮整条 17:00 链。
        score = row.get("score")
        action = str(row.get("action_bias") or "")
        if state == "退潮" or "回避" in action or "禁止" in action:
            permission = "回避"
        elif state in {"主升", "修复"} and (score is None or float(score) >= 60):
            permission = "支持"
        else:
            permission = "观察"
        result.append({
            "date": date,
            "sector": row.get("theme_name") or row.get("sector") or "未知板块",
            "theme_id": row.get("theme_id"),
            "raw_stage": raw_stage or "未知",
            "state": state,
            "trend": trend if trend in {"上涨", "横盘震荡", "下跌"} else "横盘震荡",
            "relative_strength": row.get("relative_strength", "待确认"),
            "support": row.get("box20_lower", row.get("support")),
            "resistance": row.get("box20_upper", row.get("resistance")),
            "trade_permission": permission,
            "score": fnum(score),
            "risk_flags": list(dict.fromkeys(x for x in (row.get("risk_flags") or []) if x)),
        })
    return result


# ── Risk decision (from RiskFlagAdapter + holding risk extraction) ──

def build_risk_decision(date: str) -> dict:
    holding_reviews = load(DATA / "holdings" / f"{date}_holding_review.json", [])
    # ⚠️ **证据日 ≠ 运行日。** 09:05 盘前也会跑这条链（`daily_pipeline` 里
    # `generate_risk_and_sectors` 不受 session_type 限制），那时当日 K 线还不存在，
    # 所以盘前产出的 risk_decision 打着当日日期、依据却是**前一交易日收盘**。
    # 不把这件事写进产物，14:45 报告只能按文件名判「当日」，读者会以为
    # 风控依据是今天的 —— 同「把缺数渲染成读数」那一类失真。
    _tech = load(DATA / "holdings" / f"{date}_holding_technical_summary.json", [])
    _tech_dates = sorted({str(x.get("latest_date")) for x in _tech if x.get("latest_date")})
    evidence_date = _tech_dates[-1] if _tech_dates else ""
    risks = []
    for h in holding_reviews:
        action = h.get("action")
        b1 = h.get("b1_holding_state") or {}
        b1_priority = b1.get("final_priority")
        if action in {"减仓", "止损", "清仓"} or b1_priority in {"P0", "P1"}:
            normalized_action = action if action in {"减仓", "止损", "清仓"} else ("清仓" if b1_priority == "P0" else "减仓")
            risks.append({
                "code": h.get("code"),
                "name": h.get("name", ""),
                "risk_type": "B1持仓结构" if b1_priority else ("破位" if "破位" in str(h.get("box_position")) else "亏损扩大"),
                "action": normalized_action,
                "priority": "高" if b1_priority == "P0" or normalized_action in {"止损", "清仓"} else "中",
                "reason": "；".join(h.get("reason") or ["portfolio_review触发风控"]),
                "evidence_ref": str(DATA / "holdings" / f"{date}_holding_review.json"),
                "b1_signal_refs": [x.get("signal") for x in b1.get("signals", [])],
            })

    # Dedupe by (code, risk_type, reason)
    unique: dict[tuple, dict] = {}
    for risk in risks:
        key = (bare(risk.get("code")), str(risk.get("risk_type")), str(risk.get("reason")))
        unique[key] = {**risk, "code": key[0]}
    ordered = sorted(unique.values(), key=lambda x: ({"高": 0, "中": 1, "低": 2}.get(x.get("priority"), 9), x.get("code", "")))

    level = "强风控" if any(x.get("priority") == "高" for x in ordered) else ("提高" if ordered else "普通")
    forbidden = list(dict.fromkeys(x.get("action") for x in ordered if x.get("action") in {"禁止加仓", "止损", "清仓"}))

    market = load(DATA / "market" / f"{date}_market_timing_input.json", {})
    # 补 amv_zone 兜底并归一:此前只读 effective_state 且精确等值,
    # "空头触发"会让 risk_decision 漏置 allow_add=False(审计 B1)
    _amv = market.get("amv_0") or {}
    market_regime = normalize_regime(_amv.get("effective_state") or _amv.get("amv_zone") or "未知")
    if market_regime == "空头":
        regime_directive = {
            "reduce_top_priority": True,
            "allow_add": False,
            "note": "0AMV空头区间:降低仓位为最高优先级,任何反弹都是卖出机会",
        }
    else:
        regime_directive = {"reduce_top_priority": False}

    return {"date": date, "evidence_date": evidence_date, "market_regime": market_regime, "regime_directive": regime_directive, "risk_level": level, "forbidden_actions": forbidden, "stock_risks": ordered}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()

    sector_states = build_sector_state(args.date)
    # ⚠️ 落盘前校验：6 个消费者。`score` 的 NaN 曾让 `nan >= 60` 恒为 False
    # ⇒ 板块**静默降级**成「观察」；上面已过 fnum，这里把它钉住。
    require("sector_state", sector_states)
    dump(DATA / "sectors" / f"{args.date}_sector_state.json", sector_states)

    risk = build_risk_decision(args.date)
    require("risk_decision", risk)
    dump(DATA / "risk" / f"{args.date}_risk_decision.json", risk)

    print(json.dumps({
        "date": args.date,
        "sector_states": len(sector_states),
        "stock_risks": len(risk["stock_risks"]),
        "risk_level": risk["risk_level"],
        "outputs": {
            "sector_state": str(DATA / "sectors" / f"{args.date}_sector_state.json"),
            "risk_decision": str(DATA / "risk" / f"{args.date}_risk_decision.json"),
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
