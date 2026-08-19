# -*- coding: utf-8 -*-
"""Deterministically reconcile premarket, 14:45 actions and actual trades.

用户当日未执行原因补录位置：data/trades/position_confirmations.json 中
对应日期的 "execution_reason" 字段；未补录时字段留空并计入 missing。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


from custos.core.paths import DATA, LOGS  # noqa: E402
from custos.core.paths import read_json as load  # noqa: E402
from custos.core.code_utils import bare_code as bare  # noqa: E402
from custos.core.contracts import require  # noqa: E402

LOG = LOGS


def _row_status(t: dict, tail_action: str, actual: list) -> tuple[str, str]:
    """单代码执行状态判定（status, reason）。"""
    evaluative = any(word in tail_action for word in ("评估", "观察", "持有", "等待"))
    if actual:
        return "executed", "成交台账记录当日交易"
    if t and evaluative:
        return (
            "not_executed_reason_unavailable",
            "尾盘为评估/观察类建议且当日无成交；真实未执行原因未记录，不能自动判定违纪",
        )
    if t:
        return (
            "not_executed_requires_review",
            "尾盘存在明确动作但当日无成交；需用户补充未执行原因",
        )
    return "no_action_no_trade", "无尾盘动作且无成交"


def _reconcile_row(
    code: str, pre: dict, tail_actions: dict, trade_by_code: dict
) -> dict:
    """单代码对账行：盘前动作 × 14:45动作 × 实际成交。"""
    p = pre.get(code, {})
    t = tail_actions.get(code, {})
    actual = trade_by_code.get(code, [])
    tail_action = t.get("action") or "无尾盘动作"
    status, reason = _row_status(t, tail_action, actual)
    return {
        "code": code,
        "name": t.get("name")
        or p.get("name")
        or (actual[0].get("名称") if actual else ""),
        "premarket_action": p.get("action") or "unavailable",
        "premarket_reference_action": p.get("b1_reference_action") or "unavailable",
        "tail_action": tail_action,
        "tail_priority": t.get("priority") or "unavailable",
        "actual_trades": actual,
        "execution_status": status,
        "execution_reason": reason,
        "discipline_status": "unavailable"
        if "reason_unavailable" in status or "requires_review" in status
        else "no_breach_detected",
    }


def _reconcile_rows(pre: dict, tail_actions: dict, trade_by_code: dict) -> list:
    """逐代码对账行：盘前动作 × 14:45动作 × 实际成交。

    ⚠️ 最要紧的一条：**尾盘是评估/观察类建议且当日无成交，不得自动判违纪**
    （真实未执行原因未记录时，无从判断）。
    """
    codes = sorted(set(pre) | set(tail_actions) | set(trade_by_code))
    return [_reconcile_row(code, pre, tail_actions, trade_by_code) for code in codes]


def _behavior_checks(trades: list, rows: list, user_reason: str) -> dict:
    """行为纪律检查结论（只提示不拦截）。"""
    return {
        "chasing": "no_breach_detected"
        if not trades
        else "requires_trade_level_review",
        "weak_position_add": "no_breach_detected"
        if not trades
        else "requires_trade_level_review",
        "unplanned_trade": "no_breach_detected"
        if not trades
        else "requires_plan_linkage",
        "delayed_stop_or_reduction": "unavailable"
        if not trades and any(x.get("tail_priority") in {"P0", "P1"} for x in rows)
        else "no_breach_detected",
        "user_execution_reason": user_reason,
    }


def _input_paths(day: str) -> tuple[Path, Path, Path, Path]:
    """输入文件路径：盘前快照（有则用）/chief 决策、14:45 复盘、成交台账。"""
    premarket_path = DATA / "decisions" / f"{day}_premarket_chief_decision.json"
    chief_path = (
        premarket_path
        if premarket_path.exists()
        else DATA / "decisions" / f"{day}_chief_decision.json"
    )
    tail_path = LOG / f"{day}_1445_review.json"
    trades_path = DATA / "trades" / "trades_stock.json"
    return premarket_path, chief_path, tail_path, trades_path


def _day_trades(trades_path: Path, day: str) -> tuple[list, dict[str, list]]:
    """当日成交列表 + 按 bare 代码分组。"""
    trades = [
        x for x in load(trades_path, []) if str(x.get("成交日期") or "").startswith(day)
    ]
    trade_by_code: dict[str, list] = {}
    for trade in trades:
        trade_by_code.setdefault(bare(trade.get("代码")), []).append(trade)
    return trades, trade_by_code


def _no_trades_confirmation(chief: dict) -> dict:
    """chief 决策里的「今日无交易」确认块。"""
    return (chief.get("position_freshness") or {}).get("confirmation") or {}


def _user_execution_reason(day: str) -> str:
    """用户补录的当日未执行原因（未补录为空串）。"""
    confirmations = load(DATA / "trades" / "position_confirmations.json", {})
    return str((confirmations.get(day) or {}).get("execution_reason") or "").strip()


def _missing_entries(
    premarket_available: bool, needs_reason: bool, user_reason: str
) -> list:
    """数据缺口清单（去重保序）。"""
    return list(
        dict.fromkeys(
            (["premarket_chief_decision_snapshot"] if not premarket_available else [])
            + (["user_execution_reason"] if needs_reason and not user_reason else [])
        )
    )


def build_review(day: str) -> dict:
    """加载输入 → 对账 → 组装 execution_review payload（不落盘、不校验）。"""
    premarket_path, chief_path, tail_path, trades_path = _input_paths(day)
    chief = load(chief_path, {})
    tail = load(tail_path, {})
    trades, trade_by_code = _day_trades(trades_path, day)
    pre = {bare(x.get("code")): x for x in chief.get("holding_actions") or []}
    tail_actions = {bare(x.get("code")): x for x in tail.get("actions") or []}
    rows = _reconcile_rows(pre, tail_actions, trade_by_code)
    confirmation = _no_trades_confirmation(chief)
    user_reason = _user_execution_reason(day)
    needs_reason = any(x["discipline_status"] == "unavailable" for x in rows)
    premarket_available = premarket_path.exists()
    return {
        "date": day,
        "status": "complete"
        if trades or confirmation.get("no_trades") is True
        else "degraded",
        "recorded_trade_count": len(trades),
        "no_trades_confirmed": confirmation.get("no_trades") is True,
        "premarket_snapshot_available": premarket_available,
        "premarket_plan_source": str(chief_path),
        "rows": rows,
        "behavior_checks": _behavior_checks(trades, rows, user_reason),
        "missing": _missing_entries(premarket_available, needs_reason, user_reason),
        "sources": [str(chief_path), str(tail_path), str(trades_path)],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    day = args.date
    result = build_review(day)
    out = DATA / "review_steps" / f"{day}_execution_review.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # ⚠️ 落盘前校验：⛔硬失败链。`behavior_checks`（纪律结论）与 `missing`
    # （数据缺口）必须都在 —— 混淆会让「缺文件」看起来像「违纪」。
    require("execution_review", result)
    out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(out)


if __name__ == "__main__":
    main()
