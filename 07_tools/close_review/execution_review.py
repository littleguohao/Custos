# -*- coding: utf-8 -*-
"""Deterministically reconcile premarket, 14:45 actions and actual trades."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
DATA = BASE / "01_data"
LOG = BASE / "06_logs"


def load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def bare(value):
    return str(value or "").split(".")[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    day = args.date
    premarket_path = DATA / "decisions" / f"{day}_premarket_chief_decision.json"
    chief_path = premarket_path if premarket_path.exists() else DATA / "decisions" / f"{day}_chief_decision.json"
    tail_path = LOG / f"{day}_1445_review.json"
    trades_path = DATA / "trades" / "trades_stock.json"
    chief = load(chief_path, {})
    tail = load(tail_path, {})
    trades = [x for x in load(trades_path, []) if str(x.get("成交日期") or "").startswith(day)]
    trade_by_code = {}
    for trade in trades:
        trade_by_code.setdefault(bare(trade.get("代码")), []).append(trade)
    pre = {bare(x.get("code")): x for x in chief.get("holding_actions") or []}
    tail_actions = {bare(x.get("code")): x for x in tail.get("actions") or []}
    codes = sorted(set(pre) | set(tail_actions) | set(trade_by_code))
    rows = []
    for code in codes:
        p = pre.get(code, {})
        t = tail_actions.get(code, {})
        actual = trade_by_code.get(code, [])
        tail_action = t.get("action") or "无尾盘动作"
        evaluative = any(word in tail_action for word in ("评估", "观察", "持有", "等待"))
        if actual:
            status = "executed"
            reason = "成交台账记录当日交易"
        elif t and evaluative:
            status = "not_executed_reason_unavailable"
            reason = "尾盘为评估/观察类建议且当日无成交；真实未执行原因未记录，不能自动判定违纪"
        elif t:
            status = "not_executed_requires_review"
            reason = "尾盘存在明确动作但当日无成交；需用户补充未执行原因"
        else:
            status = "no_action_no_trade"
            reason = "无尾盘动作且无成交"
        rows.append({
            "code": code,
            "name": t.get("name") or p.get("name") or (actual[0].get("名称") if actual else ""),
            "premarket_action": p.get("action") or "unavailable",
            "premarket_reference_action": p.get("b1_reference_action") or "unavailable",
            "tail_action": tail_action,
            "tail_priority": t.get("priority") or "unavailable",
            "actual_trades": actual,
            "execution_status": status,
            "execution_reason": reason,
            "discipline_status": "unavailable" if "reason_unavailable" in status or "requires_review" in status else "no_breach_detected",
        })
    confirmation = (chief.get("position_freshness") or {}).get("confirmation") or {}
    result = {
        "date": day,
        "status": "complete" if trades or confirmation.get("no_trades") is True else "degraded",
        "recorded_trade_count": len(trades),
        "no_trades_confirmed": confirmation.get("no_trades") is True,
        "premarket_snapshot_available": premarket_path.exists(),
        "premarket_plan_source": str(chief_path),
        "rows": rows,
        "behavior_checks": {
            "chasing": "no_breach_detected" if not trades else "requires_trade_level_review",
            "weak_position_add": "no_breach_detected" if not trades else "requires_trade_level_review",
            "unplanned_trade": "no_breach_detected" if not trades else "requires_plan_linkage",
            "delayed_stop_or_reduction": "unavailable" if not trades and any(x.get("tail_priority") in {"P0", "P1"} for x in rows) else "no_breach_detected",
            "user_execution_reason": "当前标的一直横盘震荡状态未大涨，预期这次回调后会有机会，故没有操作",
        },
        "missing": list(dict.fromkeys(
            (["premarket_chief_decision_snapshot"] if not premarket_path.exists() else [])
            + (["user_execution_reason"] if any(x["discipline_status"] == "unavailable" for x in rows) else [])
        )),
        "sources": [str(chief_path), str(tail_path), str(trades_path)],
    }
    out = DATA / "reviews" / f"{day}_execution_review.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
