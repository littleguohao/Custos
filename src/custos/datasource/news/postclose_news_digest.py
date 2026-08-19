# -*- coding: utf-8 -*-
"""Build an auditable post-close news/policy/wind/sentiment digest."""

from __future__ import annotations

import argparse
import json


from custos.core.paths import DATA  # noqa: E402
from custos.core.paths import read_json as load  # noqa: E402
from custos.core.paths import write_json as dump  # noqa: E402
from custos.core.contracts import require  # noqa: E402

# ⚠️ **必须包限定导入** `news.premarket_intel_schema`。它持有可变的模块级状态
# （`PREMARKET_DIR`，测试要 monkeypatch）。`src` 与 `src/custos/datasource/news` 都在
# **第二个模块对象**，于是打桩打在另一个对象上、静默失效。
from custos.datasource.news.premarket_intel_schema import (  # noqa: E402
    load_premarket_intelligence,
    premarket_intelligence_path,
    validate_premarket_intelligence,
)


# 政策分类口径。`A or B and C` 在 Python 里是 `A or (B and C)` —— 原写法
# `"policy" in category or "official" in category and "宏观政策" in themes`
# 靠优先级隐式表达分组，读者极易按 `(A or B) and C` 读反。这里显式加括号并把口径写下来。
#
# 口径（2026-08-03 裁定，见 RSS_FILTER_CONFIG.rules）：
#   ① category 含 "policy"（policy_official / policy_consultation）本身就是政策源 → 政策；
#   ② 其他官方源（macro_official / a_share_official / company_official ...）只有在
#      命中「宏观政策」主题时才算政策；
#   ③ 但命中 policy_negative_keywords（人事任免/会见/文旅推介…）且**未**命中「宏观政策」
#      主题时不算政策 —— 政策源也会发非政策内容。
#
# 为什么不采用"所有源都必须命中宏观政策主题"的收紧读法：实测 9 条典型标题，收紧后
# 政策节 8→3，掉出的 5 条里有 3 条是真政策（国常会部署稳增长 / 专精特新扶持意见 /
# 证监会程序化交易征求意见），只有 2 条是真噪音（人事任免 / 文旅推介会）——误杀 3 条
# 换掉 2 条，净效果为负。根因是原「宏观政策」词表只有 10 个词、覆盖不足，故改为
# 扩充词表提全 + 负向词精确剔除，而不是加严 gate。
#
# ③ 里"未命中宏观政策主题"这个前置条件是必要的：正向证据优先，否则一条
# 「中美经贸磋商双方会见并讨论关税」会被"会见"误杀。
POLICY_RULE_NOTE = (
    '政策 = ("policy" in category) or '
    '("official" in category and "宏观政策" in matched_themes)；'
    "命中 matched_policy_negative 且未命中「宏观政策」主题时不计政策"
)


def is_policy(item: dict) -> bool:
    """是否计入「政策」节。纯函数，判据全部来自 rss_filter 落痕的字段。"""
    category = str(item.get("category") or "")
    themes = item.get("matched_themes") or []
    is_macro = "宏观政策" in themes
    if not (("policy" in category) or ("official" in category and is_macro)):
        return False
    # 负向词只在没有正向证据时生效
    if (item.get("matched_policy_negative") or []) and not is_macro:
        return False
    return True


def classify(item: dict) -> str:
    themes = item.get("matched_themes") or []
    if is_policy(item):
        return "政策"
    if item.get("matched_market_keywords"):
        return "风向"
    if any(
        x in themes
        for x in (
            "AI算力",
            "半导体",
            "机器人",
            "船舶军工",
            "能源",
            "券商金融",
            "医疗设备",
        )
    ):
        return "信息"
    return "舆情"


def _fact_status(item: dict, source_tier) -> str:
    """confirmed 且信源为 S/A 级才算 source_confirmed，否则只是候选。"""
    source_confirmed = bool(item.get("confirmed")) and source_tier in {"S", "A"}
    return "source_confirmed" if source_confirmed else "candidate"


def _validation_condition(item: dict) -> list:
    """原有的核验条件 + 两条固定补充，按序去重。"""
    return list(
        dict.fromkeys(
            (item.get("validation_condition") or [])
            + ["核验官方原文和发布时间", "观察相关板块价格与成交反馈"]
        )
    )


def _event_from_item(item: dict) -> dict | None:
    """把一条 RSS 候选条目规整成事件 dict；无发布时间则丢弃（返回 None）。"""
    published = item.get("published_at")
    if not published:
        return None
    matched = item.get("matched_holdings_or_pool") or {}
    return {
        "category": classify(item),
        "published_at": published,
        "title": item.get("title"),
        "source_name": item.get("source_name"),
        "source_tier": item.get("source_tier"),
        "source_url": item.get("source_url"),
        "fact_status": _fact_status(item, item.get("source_tier")),
        "matched_holdings": matched.get("names") or [],
        "matched_codes": matched.get("codes") or [],
        "matched_themes": item.get("matched_themes") or [],
        "market_keywords": item.get("matched_market_keywords") or [],
        "direction": item.get("direction") or "uncertain",
        "impact_horizon": item.get("impact_horizon") or "unknown",
        "trade_meaning": "仅作事件发现；需由价格、成交或官方原文确认，不直接提高交易权限",
        "validation_condition": _validation_condition(item),
    }


def _collect_events(rss: list) -> list:
    """规整、排序（持仓命中 > 高信源 > 发布时间，倒序）并截断到 15 条。"""
    events = [e for e in (_event_from_item(item) for item in rss) if e is not None]
    events.sort(
        key=lambda x: (
            bool(x["matched_holdings"] or x["matched_codes"]),
            x["source_tier"] in {"S", "A"},
            x["published_at"],
        ),
        reverse=True,
    )
    return events[:15]


def _collect_missing(rss_path, intel_path, intel: dict, sections: dict) -> list:
    """汇总缺失/降级标记：输入文件缺、schema 不合法、政策节为空。"""
    missing = []
    if not rss_path.exists():
        missing.append("postclose_rss_candidates")
    if intel_path is None:
        missing.append("premarket_intelligence")
    elif not validate_premarket_intelligence(intel)["valid"]:
        missing.append("premarket_intelligence(schema_invalid)")
    if not sections["政策"]:
        missing.append("confirmed_high_priority_macro_policy")
    return missing


def _build_result(
    day: str,
    rss_path,
    intel_path,
    intel: dict,
    sections: dict,
    events: list,
    missing: list,
) -> dict:
    return {
        "date": day,
        "status": "degraded" if missing else "complete",
        "sections": sections,
        "event_count": len(events),
        "premarket_market_event_count": len(intel.get("market_events") or []),
        "missing": missing,
        "permission_rule": "news may add validation or tighten risk; it cannot directly increase trading permissions",
        "sources": [
            str(rss_path),
            str(
                intel_path
                or (DATA / "news" / "premarket" / f"{day}_premarket_intelligence.json")
            ),
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    day = args.date
    rss_path = (
        DATA / "news" / "rss" / "filtered" / f"{day}_postclose_rss_candidates.json"
    )
    # 命名兼容与 daily_report 对齐:带连字符优先、无连字符回退
    intel_path = premarket_intelligence_path(day)
    rss = load(rss_path, [])
    intel = load_premarket_intelligence(day)
    events = _collect_events(rss)
    sections = {
        name: [x for x in events if x["category"] == name]
        for name in ("信息", "政策", "风向", "舆情")
    }
    missing = _collect_missing(rss_path, intel_path, intel, sections)
    result = _build_result(day, rss_path, intel_path, intel, sections, events, missing)
    require("postclose_news_digest", result)
    dump(DATA / "news" / "postclose" / f"{day}_postclose_news_digest.json", result)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
