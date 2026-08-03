# -*- coding: utf-8 -*-
"""Build an auditable post-close news/policy/wind/sentiment digest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import BASE  # noqa: E402
from premarket_intel_schema import validate_premarket_intelligence  # noqa: E402
from daily_report import load_premarket_intelligence, premarket_intelligence_path  # noqa: E402

DATA = BASE / "01_data"


def load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def dump(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


# 政策分类口径。`A or B and C` 在 Python 里是 `A or (B and C)` —— 原写法
# `"policy" in category or "official" in category and "宏观政策" in themes`
# 靠优先级隐式表达分组，读者极易按 `(A or B) and C` 读反。这里显式加括号并把口径写下来。
#
# 当前口径（保持原行为，未擅改）：
#   ① category 含 "policy"（policy_official / policy_consultation）本身就是政策源 → 政策；
#   ② 其他官方源（macro_official / a_share_official / company_official ...）只有在
#      命中「宏观政策」主题时才算政策。
# ⚠️ 另一种读法是"所有政策/官方源都必须命中宏观政策主题"，会让 ① 收紧。两者的差别在于
# 一条不带宏观政策关键词的证监会公告算不算政策 —— 属策略口径，需人工裁定后再改。
POLICY_RULE_NOTE = ('政策 = ("policy" in category) or '
                    '("official" in category and "宏观政策" in matched_themes)')


def classify(item: dict) -> str:
    category = str(item.get("category") or "")
    themes = item.get("matched_themes") or []
    if ("policy" in category) or ("official" in category and "宏观政策" in themes):
        return "政策"
    if item.get("matched_market_keywords"):
        return "风向"
    if any(x in themes for x in ("AI算力", "半导体", "机器人", "船舶军工", "能源", "券商金融", "医疗设备")):
        return "信息"
    return "舆情"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    day = args.date
    rss_path = DATA / "news" / "rss" / "filtered" / f"{day}_postclose_rss_candidates.json"
    # 命名兼容与 daily_report 对齐:带连字符优先、无连字符回退
    intel_path = premarket_intelligence_path(day)
    rss = load(rss_path, [])
    intel = load_premarket_intelligence(day)
    events = []
    for item in rss:
        published = item.get("published_at")
        if not published:
            continue
        matched = item.get("matched_holdings_or_pool") or {}
        source_tier = item.get("source_tier")
        source_confirmed = bool(item.get("confirmed")) and source_tier in {"S", "A"}
        events.append({
            "category": classify(item),
            "published_at": published,
            "title": item.get("title"),
            "source_name": item.get("source_name"),
            "source_tier": source_tier,
            "source_url": item.get("source_url"),
            "fact_status": "source_confirmed" if source_confirmed else "candidate",
            "matched_holdings": matched.get("names") or [],
            "matched_codes": matched.get("codes") or [],
            "matched_themes": item.get("matched_themes") or [],
            "market_keywords": item.get("matched_market_keywords") or [],
            "direction": item.get("direction") or "uncertain",
            "impact_horizon": item.get("impact_horizon") or "unknown",
            "trade_meaning": "仅作事件发现；需由价格、成交或官方原文确认，不直接提高交易权限",
            "validation_condition": list(dict.fromkeys((item.get("validation_condition") or []) + ["核验官方原文和发布时间", "观察相关板块价格与成交反馈"])),
        })
    events.sort(key=lambda x: (bool(x["matched_holdings"] or x["matched_codes"]), x["source_tier"] in {"S", "A"}, x["published_at"]), reverse=True)
    events = events[:15]
    sections = {name: [x for x in events if x["category"] == name] for name in ("信息", "政策", "风向", "舆情")}
    missing = []
    if not rss_path.exists():
        missing.append("postclose_rss_candidates")
    if intel_path is None:
        missing.append("premarket_intelligence")
    elif not validate_premarket_intelligence(intel)["valid"]:
        missing.append("premarket_intelligence(schema_invalid)")
    if not sections["政策"]:
        missing.append("confirmed_high_priority_macro_policy")
    result = {
        "date": day,
        "status": "degraded" if missing else "complete",
        "sections": sections,
        "event_count": len(events),
        "premarket_market_event_count": len(intel.get("market_events") or []),
        "missing": missing,
        "permission_rule": "news may add validation or tighten risk; it cannot directly increase trading permissions",
        "sources": [str(rss_path), str(intel_path or (DATA / "news" / "premarket" / f"{day}_premarket_intelligence.json"))],
    }
    dump(DATA / "news" / "postclose" / f"{day}_postclose_news_digest.json", result)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
