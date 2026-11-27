# -*- coding: utf-8 -*-
"""新闻热度与重点行业影响的确定性评分（不读取持仓、不调用 LLM）。

热度只看时效、跨独立来源覆盖和源站推荐；行业影响只看重点行业命中、
政策／订单／产能／技术／监管驱动。分数仅用于信息排序，不改变交易权限。
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Any

FOCUS_INDUSTRIES = {
    "科技": {"科技", "半导体", "机器人"},
    "AI": {"AI算力"},
    "医疗医药": {"医疗医药", "医疗设备"},
    "电力": {"电力"},
    "电网": {"电网"},
}

IMPACT_DRIVERS = {
    "政策规划": (
        "十五五",
        "十四五",
        "规划",
        "指导意见",
        "实施方案",
        "行动方案",
        "若干措施",
        "政策",
        "批复",
        "核准",
        "征求意见",
    ),
    "需求订单": ("订单", "中标", "签约", "需求", "出口", "涨价", "降价"),
    "产能供给": ("产能", "投产", "扩产", "减产", "停产", "并购", "重组", "数据中心"),
    "技术产品": (
        "技术突破",
        "研发",
        "发布",
        "获批",
        "上市",
        "量产",
        "商业化",
        "国产替代",
        "大模型",
        "CANN",
        "昇腾",
    ),
    "监管风险": ("监管", "处罚", "禁令", "限制", "制裁", "调查", "风险"),
}

DRIVER_NARRATIVES = {
    "政策规划": "政策驱动，关注{industries}产业链需求、国产替代与落地节奏",
    "需求订单": "需求与订单驱动，关注{industries}景气度和业绩兑现",
    "产能供给": "产能与供给变化，关注{industries}供需格局和资本开支",
    "技术产品": "技术与产品驱动，关注{industries}迭代、国产替代和商业化",
    "监管风险": "监管与风险事件，关注{industries}供给约束和盈利分化",
}


def matched_industries(themes: list[str], cfg: dict[str, Any]) -> list[str]:
    """主题 → 重点行业；配置可覆盖默认映射，顺序固定便于审计。"""
    mapping = cfg.get("focus_industries") or FOCUS_INDUSTRIES
    theme_set = set(themes or [])
    return [
        name
        for name, mapped_themes in mapping.items()
        if theme_set.intersection(mapped_themes)
    ]


def industry_assessment(
    *, themes: list[str], category: str, text: str, cfg: dict[str, Any]
) -> dict[str, Any]:
    """返回重点行业、影响分、驱动事件和展示等级；非重点行业不硬贴影响标签。"""
    industries = matched_industries(themes, cfg)
    if not industries:
        return {
            "matched_industries": [],
            "industry_impact_score": 0,
            "industry_impact_level": "非重点",
            "impact_drivers": [],
        }
    lowered = text.lower()
    drivers = [
        label
        for label, words in IMPACT_DRIVERS.items()
        if any(word.lower() in lowered for word in words)
    ]
    score = min(40, 30 + max(0, len(industries) - 1) * 5)
    score += min(40, len(drivers) * 10)
    if "宏观政策" in themes:
        score += 10
    if "policy" in category:
        score += 10
    score = min(100, score)
    return {
        "matched_industries": industries,
        "industry_impact_score": score,
        "industry_impact_level": "高"
        if score >= 60
        else "中"
        if score >= 35
        else "一般",
        "impact_drivers": drivers,
    }


def _norm_title(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def apply_heat(items: list[dict[str, Any]], asof: datetime) -> list[dict[str, Any]]:
    """原地补充热度分并把它计入 relevance_score，返回原列表便于链式处理。"""
    groups: dict[str, set[str]] = defaultdict(set)
    for item in items:
        key = str(item.get("duplicate_group_id") or _norm_title(item.get("title")))
        groups[key].add(str(item.get("source_id") or "unknown"))
    for item in items:
        key = str(item.get("duplicate_group_id") or _norm_title(item.get("title")))
        source_count = len(groups[key])
        published = _parse_time(item.get("published_at"))
        age_hours = (
            max(0.0, (asof - published).total_seconds() / 3600) if published else 24.0
        )
        recency = (
            20
            if age_hours <= 3
            else 12
            if age_hours <= 8
            else 6
            if age_hours <= 24
            else 0
        )
        cross_source = min(30, max(0, source_count - 1) * 15)
        try:
            recommended = 10 if float(item.get("recommend") or 0) > 0 else 0
        except (TypeError, ValueError):
            recommended = 0
        heat = recency + cross_source + recommended
        item["cross_source_count"] = source_count
        item["heat_score"] = heat
        item["heat_level"] = "高" if heat >= 35 else "中" if heat >= 15 else "低"
        item["relevance_score"] = int(item.get("relevance_score") or 0) + heat
    return items


def impact_narrative(row: dict[str, Any]) -> str:
    """报告用的一句话影响判断；只给验证方向，不武断宣称利好或利空。"""
    industries = row.get("matched_industries") or []
    if not industries:
        return "非重点行业，仅作市场信息跟踪"
    names = "、".join(industries)
    drivers = row.get("impact_drivers") or []
    if drivers:
        template = DRIVER_NARRATIVES.get(str(drivers[0]))
        if template:
            return template.format(industries=names)
    return f"关注{names}景气度、资金反馈与后续验证"
