# -*- coding: utf-8 -*-
"""每日持仓研判——只产结构化 `holding_review.json`（RiskDecision 的直接上游）。

v0.162 起人读的 `portfolio_review.md` 停产：展示层已并入 chief_decision.md
（§4 持仓处理优先级表带仓位/盈亏/持仓天数列）。本脚本仍是 daily_pipeline 的
硬失败 stage，落盘前 `require("holding_review", reviews)` 校验保留。
"""

from __future__ import annotations
import argparse, json, sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from custos.core.paths import DATA  # noqa: E402
from custos.core.paths import read_json as load  # noqa: E402
from custos.core.contracts import require  # noqa: E402
from custos.core.b1_thresholds import J_LOW_THRESHOLD  # noqa: E402
from custos.core.exit_rules import (  # noqa: E402  L0，止盈止损规则唯一来源
    HARD_LOSS_ENABLED,
    HARD_LOSS_PCT,
    LOSS_REDUCTION_ENABLED,
    LOSS_REDUCTION_PCT,
)


def classify(r):
    pnl = r.get("holding_pnl_pct")
    trend = r.get("trend_state")
    pos = str(r.get("box20_position") or "")
    j = r.get("daily_j")
    macd = r.get("daily_macd_hist_direction")
    reasons = []
    action = "观察"
    # 止损/减仓阈值唯一来源 = core/exit_rules（原硬编码 -0.10/-0.07，2026-08-19 收敛）
    if trend == "下跌" and "破位" in pos:
        action = "止损"
        reasons.append("下跌趋势且处于破位区")
    elif HARD_LOSS_ENABLED and isinstance(pnl, (int, float)) and pnl <= HARD_LOSS_PCT:
        action = "止损"
        reasons.append("浮亏达到强制风控阈值")
    elif (
        LOSS_REDUCTION_ENABLED
        and isinstance(pnl, (int, float))
        and pnl <= LOSS_REDUCTION_PCT
    ):
        action = "减仓"
        reasons.append(f"浮亏超过{LOSS_REDUCTION_PCT:.0%}")
    elif trend == "下跌":
        action = "减仓"
        reasons.append("下跌趋势")
    elif trend == "横盘震荡" and "上半区" in pos:
        action = "持有"
        reasons.append("横盘上半区，保护利润且不追高")
    # ⚠️ J 阈值原硬编码 12，与全仓其余判定点（13）不一致 —— 2026-08-19 修正为
    #    同源 b1_thresholds.J_LOW_THRESHOLD（v0.81，本 Phase 唯一有意行为修正）
    if isinstance(j, (int, float)) and j < J_LOW_THRESHOLD:
        reasons.append("J值低仅作观察，不构成加仓理由")
    if macd == "收缩":
        reasons.append("MACD动能收缩")
    if not reasons:
        reasons.append("暂无强触发信号")
    priority = (
        "P1" if action in {"止损", "清仓"} else ("P2" if action == "减仓" else "P3")
    )
    return priority, action, reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    a = ap.parse_args()
    tech = load(DATA / "holdings" / f"{a.date}_holding_technical_summary.json", [])
    b1_rows = load(DATA / "holdings" / f"{a.date}_b1_holding_state.json", [])
    b1 = {str(x.get("code")): x for x in b1_rows}
    reviews = []
    for r in tech:
        hold = b1.get(str(r.get("code")), {})
        priority = hold.get("final_priority")
        action = hold.get("final_action")
        reasons = []
        if not priority or not action:
            priority, action, reasons = classify(r)
        else:
            reasons = [hold.get("final_reason")] + [
                x.get("reason") for x in hold.get("signals", [])[1:3]
            ]
        reviews.append(
            {
                "code": str(r.get("code")),
                "name": r.get("name", ""),
                "position_pct": r.get("position_pct"),
                "pnl_pct": r.get("holding_pnl_pct"),
                "holding_days": r.get("holding_days"),
                "sector": r.get("industry") or "、".join(r.get("primary_themes") or []),
                "trend_state": r.get("trend_state"),
                "box_position": r.get("box20_position"),
                "daily_j": r.get("daily_j"),
                "macd_state": r.get("daily_macd_hist_direction"),
                "action": action,
                "priority": priority,
                "reason": [x for x in reasons if x],
                "b1_holding_state": hold,
            }
        )
    require("holding_review", reviews)
    out_json = DATA / "holdings" / f"{a.date}_holding_review.json"
    out_json.write_text(
        json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(out_json)


if __name__ == "__main__":
    main()
