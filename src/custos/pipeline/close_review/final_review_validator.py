# -*- coding: utf-8 -*-
"""Validate final review artifacts before delivery."""

from __future__ import annotations

import argparse
import json


from custos.core.paths import REVIEWS, daily_report_dir  # noqa: E402

REV = REVIEWS / "daily"
REQUIRED_SECTIONS = [
    "今日计划、14:45建议与实际执行",
    "新闻、政策、风向与舆情",
    "大盘、资金与市场许可",
    # v0.136：§4 整节替换为客观事实节（原「主线生命周期」判定口径随 #26 撤下）；
    # §7 纪律偏差 / §8 数据时效 / §9 数据来源 三节同版删除（审计块在报告头部）。
    "板块题材涨跌幅榜与市场温度",
    "持仓逐只诊断与仓位审计",
    "下一交易日条件化交易计划",
]
REQUIRED_JSON_KEYS = [
    "date",
    "report_quality",
    "news_digest",
    "execution_review",
    "theme_lifecycles",
    "market_quality_checks",
    "revalued_positions",
    "next_day_plan",
    "rule_review",
    "unavailable",
]


def validate(day: str, markdown: str, payload: dict) -> list[str]:
    errors = []
    for section in REQUIRED_SECTIONS:
        if section not in markdown:
            errors.append(f"markdown section missing: {section}")
    for key in REQUIRED_JSON_KEYS:
        if key not in payload:
            errors.append(f"json key missing: {key}")
    if payload.get("date") != day:
        errors.append("json date mismatch")
    if payload.get("report_quality") not in {"complete", "degraded"}:
        errors.append("invalid report_quality")
    news = payload.get("news_digest") or {}
    if "cannot directly increase trading permissions" not in str(
        news.get("permission_rule")
    ):
        errors.append("news permission rule missing")
    execution = payload.get("execution_review") or {}
    if not isinstance(execution.get("rows"), list):
        errors.append("execution rows missing")
    plan = payload.get("next_day_plan") or {}
    if not isinstance(plan.get("holding_plans"), list):
        errors.append("next-day holding plans missing")
    if not isinstance(payload.get("unavailable"), list):
        errors.append("unavailable must be a list")
    if payload.get("report_quality") == "complete" and payload.get("unavailable"):
        errors.append("complete report cannot contain unavailable inputs")
    return errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    md_path = daily_report_dir(args.date, REV) / f"{args.date}_final_review.md"
    json_path = daily_report_dir(args.date, REV) / f"{args.date}_final_review.json"
    if not md_path.exists() or not json_path.exists():
        raise SystemExit("final review artifact missing")
    markdown = md_path.read_text(encoding="utf-8")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    errors = validate(args.date, markdown, payload)
    result = {
        "date": args.date,
        "status": "ok" if not errors else "failed",
        "errors": errors,
        "markdown": str(md_path),
        "json": str(json_path),
    }
    print(json.dumps(result, ensure_ascii=True))
    if errors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
