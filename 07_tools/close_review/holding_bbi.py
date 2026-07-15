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


def intraday_bbi_basis(row: dict[str, Any], price: Any, technical_date: str | None) -> dict[str, Any]:
    """Compare a current quote with the latest confirmed BBI without rewriting history."""
    base = bbi_basis(row)
    if not base.get("available"):
        return base
    try:
        current = float(price)
        value = float(base["value"])
    except (TypeError, ValueError):
        return {
            "available": False,
            "state": "BBI待确认",
            "reminder": "缺少当日实时价，不据此调整持仓",
            "signal": "unavailable",
        }
    distance = (current / value - 1) * 100 if value else None
    above = current >= value
    as_of = technical_date or "日期待确认"
    state = f"当前价相对{as_of} BBI {_number(value)}：{'上方' if above else '下方'}（偏离{_signed_number(distance)}%）"
    signal = base["signal"]
    reminder = base["reminder"]
    if base.get("above") is True and not above:
        signal = "intraday_break_watch"
        reminder = "上一确认日收盘在BBI上方，当前价跌至BBI下方；等待收盘确认，未确认前不视为连续两日破位"
    elif base.get("signal") == "clear_review" and above:
        signal = "reclaim_in_progress"
        reminder = "历史连续跌破BBI，但当前价已回到BBI上方；等待收盘确认修复，不自动恢复加仓权限"
    return {
        **base,
        "current_price": current,
        "current_above": above,
        "current_distance_pct": distance,
        "technical_date": technical_date,
        "state": state,
        "reminder": reminder,
        "signal": signal,
    }
