# -*- coding: utf-8 -*-
"""盘前情报(premarket_intelligence) schema 校验。

生成方是仓库外的 OpenClaw cron LLM,schema 会漂移(如 20260717 文件只有
date/collected_at/holdings/data_quality)。消费端静默降级时报告中不可见,
因此加载后必须先校验,不合规时显式标注降级。
"""
from __future__ import annotations

from typing import Any

REQUIRED_LIST_KEYS = ("market_events", "holding_events")


def validate_premarket_intelligence(data: Any) -> dict[str, Any]:
    """校验盘前情报结构,返回 {"valid": bool, "errors": [...], "warnings": [...]}。

    必填: date(str)、market_events(list)、holding_events(list) —— 缺失或类型错误记 errors。
    推荐: window(dict) —— 缺失记 warnings。
    事件元素为 dict 时应有 title/direction 等基本字段,宽松检查只记 warnings。
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return {"valid": False, "errors": ["顶层必须是 JSON object"], "warnings": []}
    if not isinstance(data.get("date"), str) or not data.get("date"):
        errors.append("缺 date(str)")
    for key in REQUIRED_LIST_KEYS:
        if key not in data:
            errors.append(f"缺 {key}(list)")
        elif not isinstance(data[key], list):
            errors.append(f"{key} 应为 list,实际为 {type(data[key]).__name__}")
    if "window" not in data:
        warnings.append("缺推荐字段 window(dict)")
    elif not isinstance(data["window"], dict):
        warnings.append(f"window 应为 dict,实际为 {type(data['window']).__name__}")
    for key in REQUIRED_LIST_KEYS:
        items = data.get(key)
        if not isinstance(items, list):
            continue
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                warnings.append(f"{key}[{i}] 不是 object")
                continue
            if not item.get("title"):
                warnings.append(f"{key}[{i}] 缺 title")
            if "direction" not in item:
                warnings.append(f"{key}[{i}] 缺 direction")
    return {"valid": not errors, "errors": errors, "warnings": warnings}
