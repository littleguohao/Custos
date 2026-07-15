# -*- coding: utf-8 -*-
"""Presentation and risk signal for confirmed N-structure prior lows."""
from __future__ import annotations

from typing import Any


def _number(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "待确认"


def n_structure_basis(row: dict[str, Any], price: Any) -> dict[str, Any]:
    structure = row.get("n_structure") or {}
    prior_low = structure.get("prior_low", row.get("n_structure_prior_low"))
    prior_low_date = structure.get("prior_low_date", row.get("n_structure_prior_low_date"))
    if not structure.get("available") or prior_low is None:
        return {
            "available": False,
            "state": "N型前低待确认",
            "reminder": structure.get("reason") or "未识别到已完成的上升N型结构，不虚构结构清仓位",
            "signal": "unavailable",
        }
    try:
        current = float(price)
        level = float(prior_low)
    except (TypeError, ValueError):
        return {
            "available": False,
            "state": f"N型前低 {prior_low_date or '日期待确认'} {_number(prior_low)}；当前价格缺失",
            "reminder": "缺少当前价格，不判断是否失守",
            "signal": "unavailable",
        }
    distance = (current / level - 1) * 100 if level else None
    breached = current < level
    state = (
        f"N型前低 {prior_low_date or '日期待确认'} {_number(level)}；"
        f"当前价{'上方' if not breached else '下方'}（距离{_number(distance)}%）"
    )
    return {
        "available": True,
        "prior_low": level,
        "prior_low_date": prior_low_date,
        "breakout_level": structure.get("breakout_level"),
        "confirmed_date": structure.get("confirmed_date"),
        "current_price": current,
        "distance_pct": distance,
        "breached": breached,
        "state": state,
        "reminder": "N型前低已失守，结构失效，触发清仓/退出评估" if breached else "结构未失效；该位置是硬清仓位，不构成加仓理由",
        "signal": "structural_clear" if breached else "structure_hold",
    }
