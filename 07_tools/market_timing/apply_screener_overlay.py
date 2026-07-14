# -*- coding: utf-8 -*-
"""Apply tdx_screener sentiment overlay to market_timing input.

This script is intentionally deterministic: it writes the latest screener values
that were verified during the daily run. The upstream tdx_screener tool is an
OpenClaw tool, so the actual queries are run by the assistant, then persisted
here as the day's overlay payload for reproducible reruns.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

BASE = Path(r"C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team")
IN_DIR = BASE / "01_data" / "market"
LOG_DIR = BASE / "06_logs"

# 2026-07-09 close-session tdx_screener verified values.
# Query evidence:
# - 今日涨停: total=75
# - 今日跌停股票数量: answerText="今日跌停家数为14"
# - 今日炸板股票: total=29
# - 今日连板股票: total=6, max 连续涨停天数0#=8
# - 今日2连板股票: total=4
# - 今日3连板股票: total=1
# - 今日4连板股票: total=0
# - 今日5连板股票: total=0
DAILY_OVERLAYS: dict[str, dict[str, Any]] = {
    "2026-07-09": {
        "limit_up_count": 75,
        "limit_down_count": 14,
        "blowup_count": 29,
        "board_2_count": 4,
        "board_3_count": 1,
        "board_4_count": 0,
        "board_5_count": 0,
        "market_height": 8,
        "limit_up_query_total": 75,
        "limit_down_query_answer": "今日跌停家数为14",
        "blowup_query_total": 29,
        "continuous_board_query_total": 6,
        "sample_leaders": [
            "恒尚节能: 8连板/近期复牌/存储器资产收购/BIPV",
            "视源股份: 3连板/业绩预升/人工智能/消费电子/机器人",
            "浪潮信息: 2连板/AI服务器/算力租赁/液冷服务器",
            "华天科技: 芯片/半导体/机器人概念",
            "兆易创新: 芯片/半导体/长鑫科技映射"
        ],
        "theme_clues": [
            "半导体/芯片/长鑫科技映射",
            "AI算力/液冷服务器",
            "机器人/消费电子",
            "局部复牌重组高标"
        ],
        "quality_note": "tdx_screener close-session overlay; 涨停查询使用'今日涨停'口径，'今日涨停股票数量'口径异常返回全市场数，已弃用。"
    }
}


def apply_overlay(date: str, input_path: Path | None = None) -> Path:
    if date not in DAILY_OVERLAYS:
        raise SystemExit(f"No screener overlay payload configured for {date}")
    p = input_path or IN_DIR / f"{date}_market_timing_input.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    ov = DAILY_OVERLAYS[date]

    limit_up = ov["limit_up_count"]
    limit_down = ov["limit_down_count"]
    blowup = ov["blowup_count"]
    once_up = limit_up + blowup
    blowup_rate = round(blowup / once_up, 4) if once_up else None

    d.setdefault("sentiment", {}).update({
        "limit_up_count": limit_up,
        "limit_down_count": limit_down,
        "once_limit_up_count": once_up,
        "once_limit_down_count": None,
        "blowup_count": blowup,
        "blowup_rate": blowup_rate,
        "market_height": ov["market_height"],
        "above_2_board_count": ov["board_2_count"],
        "board_2_count": ov["board_2_count"],
        "board_3_count": ov["board_3_count"],
        "board_4_count": ov["board_4_count"],
        "board_5_count": ov["board_5_count"],
        "continuous_board_count": ov["continuous_board_query_total"],
        "source": "tdx_screener_tool",
        "quality": "auto_overlay_close_verified",
        "details": {
            "涨停_total": limit_up,
            "跌停_total": limit_down,
            "炸板_total": blowup,
            "曾涨停_total": once_up,
            "炸板率": blowup_rate,
            "连板_total": ov["continuous_board_query_total"],
            "2连板_total": ov["board_2_count"],
            "3连板_total": ov["board_3_count"],
            "4连板_total": ov["board_4_count"],
            "5连板_total": ov["board_5_count"],
            "市场高度": ov["market_height"],
            "sample_leaders": ov["sample_leaders"],
            "theme_clues": ov["theme_clues"],
            "query_evidence": {
                "limit_up_total": ov["limit_up_query_total"],
                "limit_down_answer": ov["limit_down_query_answer"],
                "blowup_total": ov["blowup_query_total"],
                "continuous_board_total": ov["continuous_board_query_total"]
            }
        }
    })

    notes = d.setdefault("data_quality", {}).setdefault("notes", [])
    notes = [n for n in notes if "涨跌停、炸板、连板高度由 tdx_screener" not in n]
    notes.append("涨跌停、炸板、连板高度由 tdx_screener 收盘口径补齐；优先级高于 TQ SC raw_only 字段。")
    notes.append(ov["quality_note"])
    d["data_quality"]["notes"] = notes
    sources = d.setdefault("data_quality", {}).setdefault("sources", [])
    for src in ["tdx_screener", "tdx_screener_close_overlay"]:
        if src not in sources:
            sources.append(src)

    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = {
        "date": date,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "input_path": str(p),
        "overlay": d["sentiment"],
    }
    (LOG_DIR / f"{date}_screener_overlay_update.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--input", default="")
    args = ap.parse_args()
    p = apply_overlay(args.date, Path(args.input) if args.input else None)
    print(p)


if __name__ == "__main__":
    main()
