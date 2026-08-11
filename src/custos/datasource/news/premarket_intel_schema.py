# -*- coding: utf-8 -*-
"""盘前情报(premarket_intelligence) schema 校验。

生成方是仓库外的 OpenClaw cron LLM,schema 会漂移(如 20260717 文件只有
date/collected_at/holdings/data_quality)。消费端静默降级时报告中不可见,
因此加载后必须先校验,不合规时显式标注降级。
"""
from __future__ import annotations

from custos.core.paths import BASE, read_json, NEWS_DIR

from pathlib import Path

from typing import Any

REQUIRED_LIST_KEYS = ("market_events", "holding_events")

# ⚠️ 独立的模块常量而不是在函数里拼 `BASE / ...`：测试要能 monkeypatch 它。
# 2026-08-07 从 daily_report 搬这两个函数时踩过 —— 既有测试打桩
# `daily_report.DATA`，函数搬走后桩就失效了（打在旧模块上）。
PREMARKET_DIR = NEWS_DIR / "premarket"


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

# ══ 文件定位与加载（2026-08-07 从 `daily_report.py` 移来）
#
# 为什么移：它们读 `data/news/premarket/`，而 `news/postclose_news_digest`
# 与 `daily_report` **都要用**。原先放在 `daily_report.py`（根层报告生成器）里，
# 导致 `news/`（L1 数据/采集层）反向依赖根层编排 —— 分层反转。
# 放这里则与它们加载的 schema 同处一个模块。

def premarket_intelligence_path(day: str) -> Path | None:
    """定位当日盘前情报文件。

    ⚠️ 生成方是**仓库外**的 OpenClaw cron，存在两种命名（带连字符
    `2026-07-16_...` 与无连字符 `20260717_...`），加载端必须兼容两种 ——
    这不是我们能单方面统一的口径。
    """
    for name in (f"{day}_premarket_intelligence.json",
                 f"{day.replace('-', '')}_premarket_intelligence.json"):
        path = PREMARKET_DIR / name
        if path.exists():
            return path
    return None


def load_premarket_intelligence(day: str) -> dict:
    """读当日盘前情报；文件不存在返回 `{}`（盘前情报是可选证据层）。"""
    path = premarket_intelligence_path(day)
    return read_json(path, {}) if path else {}
