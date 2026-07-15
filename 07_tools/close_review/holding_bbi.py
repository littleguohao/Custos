# -*- coding: utf-8 -*-
"""Shared BBI presentation and decision basis for close-review reports."""
from __future__ import annotations

from typing import Any


def _number(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "待确认"


def _signed_number(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):+.{digits}f}"
    except (TypeError, ValueError):
        return "待确认"


def bbi_basis(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("bbi")
    above = row.get("above_bbi")
    distance = row.get("bbi_distance_pct")
    below_days = row.get("consecutive_closes_below_bbi")
    if value is None or above is None:
        return {
            "available": False,
            "state": "BBI待确认",
            "reminder": "缺少BBI数据，不据此调整持仓",
            "signal": "unavailable",
        }
    try:
        days = int(below_days or 0)
    except (TypeError, ValueError):
        days = 0
    state = f"BBI {_number(value)}；收盘{'上方' if above else '下方'}（偏离{_signed_number(distance)}%）"
    if above:
        reminder = "仅技术维度持有结构有效；连续两根中大阳时分批止盈；更高优先级风控仍有效"
        signal = "technical_hold"
    elif days >= 2:
        reminder = f"连续{days}日收盘跌破BBI；按B1进入清仓评估，最终动作服从总控"
        signal = "clear_review"
    else:
        reminder = "首日收盘跌破BBI；验证下一交易日能否快速收回，未收回则升级清仓评估"
        signal = "reclaim_watch"
    return {
        "available": True,
        "value": value,
        "above": bool(above),
        "distance_pct": distance,
        "consecutive_closes_below": days,
        "state": state,
        "reminder": reminder,
        "signal": signal,
    }
