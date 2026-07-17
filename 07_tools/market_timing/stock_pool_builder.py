# -*- coding: utf-8 -*-
"""Build stock_pool from technical formula candidates + sector heat filter.

First implementation consumes B1 output and theme_tracker sector summary.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent.parent
STOCK_POOL_DIR = BASE / "01_data" / "stock_pool"
SECTOR_DIR = BASE / "01_data" / "sectors"
PLAN_DIR = BASE / "03_daily_plans"
TOOLS_DIR = BASE / "07_tools" / "market_timing"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import technical_monitor as tm  # noqa: E402

# Initial mapping for the first runnable version. Later this should be replaced
# by concept/sector lookup + sector_code_map matching per stock.
RUN_THEME_BY_LABEL = {
    "semiconductor": "semiconductor_chip_memory_packaging",
    "ai_compute": "ai_compute_server_liquid_cooling",
}

THEME_STOCK_NAMES = {
    "603986.SH": "兆易创新",
    "002185.SZ": "华天科技",
    "000977.SZ": "浪潮信息",
    "600584.SH": "长电科技",
    "002414.SZ": "高德红外",
    "000021.SZ": "深科技",
    "002384.SZ": "东山精密",
    "600206.SH": "有研新材",
    "002281.SZ": "光迅科技",
    "002409.SZ": "雅克科技",
    "688416.SH": "恒烁股份",
    "688536.SH": "思瑞浦",
    "301611.SZ": "珂玛科技",
    "603290.SH": "斯达半导",
    "600460.SH": "士兰微",
    "688110.SH": "东芯股份",
    "300223.SZ": "北京君正",
    "300706.SZ": "阿石创",
    "301308.SZ": "江波龙",
    "300327.SZ": "中颖电子",
    "688525.SH": "佰维存储",
    "688766.SH": "普冉股份",
    "920181.BJ": "赛英电子",
    "688381.SH": "帝奥微",
    "300077.SZ": "国民技术",
    "688797.SH": "臻宝科技",
    "600666.SH": "奥瑞德",
    "300831.SZ": "ST派瑞",
    "301581.SZ": "黄山谷捷",
}


def normalize_code(code: str) -> str:
    s = str(code).strip().upper()
    if "." in s:
        return s
    if s.startswith(("920", "8", "4")):
        return f"{s}.BJ"
    if s.startswith(("6", "5", "9")):
        return f"{s}.SH"
    if s.startswith(("0", "1", "2", "3")):
        return f"{s}.SZ"
    return s


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def find_b1_top_files(date: str) -> list[Path]:
    root = STOCK_POOL_DIR / "b1_candidates"
    if not root.exists():
        return []
    files = []
    for p in root.rglob("B1_top10*.csv"):
        if date in str(p):
            files.append(p)
    return sorted(files)


def infer_run_label(path: Path) -> str:
    s = str(path).lower()
    if "semiconductor" in s:
        return "semiconductor"
    if "ai" in s or "compute" in s:
        return "ai_compute"
    return "unknown"


def technical_level(code: str) -> tuple[str, dict[str, Any], list[str]]:
    df = tm.read_vipdoc(code)
    a = tm.analyze(df)
    flags = []
    if not a.get("available"):
        return "弱", a, ["技术数据不足"]
    trend = (a.get("trend") or {}).get("state")
    box = (a.get("box_20d") or {}).get("position")
    kdj = ((a.get("daily") or {}).get("kdj") or {})
    macd = ((a.get("daily") or {}).get("macd") or {})
    score = 0
    if trend == "上涨": score += 35
    elif trend == "横盘震荡": score += 20
    elif trend == "下跌": score -= 10
    if box == "上沿/突破区": score += 25
    elif box == "箱体上半区": score += 15
    elif box == "箱体下半区": score += 8
    elif box == "下沿/破位区": score -= 15
    if macd.get("hist_direction") == "扩张": score += 15
    elif macd.get("hist_direction") == "收缩": score -= 5
    j = kdj.get("j")
    if isinstance(j, (int, float)):
        if j < 12:
            score += 10
            flags.append("日线J低位")
        elif j > 95:
            score -= 10
            flags.append("日线J过热")
        elif j > kdj.get("j_prev", j):
            score += 5
    if trend == "下跌" or box == "下沿/破位区":
        flags.append("技术结构偏弱/破位观察")
    if score >= 60:
        lvl = "强"
    elif score >= 35:
        lvl = "中"
    else:
        lvl = "弱"
    return lvl, a, flags


def heat_level(sector: dict[str, Any]) -> str:
    score = sector.get("score") or 0
    stage = str(sector.get("stage") or "")
    if score >= 70 or "主升" in stage:
        return "强"
    if score >= 45 or "震荡" in stage or "修复" in stage:
        return "中"
    return "弱"


def resonance_bucket(tech: str, heat: str, sector_stage: str) -> tuple[str, str, str]:
    if "退潮" in sector_stage or "下跌" in sector_stage:
        if tech == "强":
            return "C", "observe_only", "技术面较强但板块退潮/下跌，最多长期观察"
        return "D", "reject_A", "板块退潮/下跌且技术不强"
    if tech == "强" and heat == "强":
        return "B", "allow_B", "技术面与板块热度共振；market_timing震荡偏弱，先入B池观察"
    if tech == "强" and heat == "中":
        return "B", "allow_B", "技术面强，板块热度待加强"
    if tech == "强" and heat == "弱":
        return "C", "observe_only", "技术面强但板块热度弱"
    if tech == "中" and heat == "强":
        return "B", "allow_B", "板块热度强，等待个股技术进一步确认"
    if tech == "中" and heat == "中":
        return "C", "observe_only", "技术和板块均未强共振"
    return "D", "reject_A", "技术面和板块热度不支持"


def build(date: str) -> tuple[list[dict[str, Any]], str]:
    sector_rows = load_json(SECTOR_DIR / f"{date}_sector_technical_summary.json", []) or []
    sector_by_theme = {r.get("theme_id"): r for r in sector_rows}
    top_files = find_b1_top_files(date)
    candidates: list[dict[str, Any]] = []
    seen = set()
    for f in top_files:
        label = infer_run_label(f)
        theme_id = RUN_THEME_BY_LABEL.get(label, "unknown")
        sector = sector_by_theme.get(theme_id, {})
        df = pd.read_csv(f)
        if df.empty or "Code" not in df.columns:
            continue
        for i, row in df.reset_index(drop=True).iterrows():
            code = normalize_code(row.get("Code"))
            key = (code, theme_id)
            if key in seen:
                continue
            seen.add(key)
            tech_lvl, analysis, tech_flags = technical_level(code)
            h_lvl = heat_level(sector)
            bucket, pass_level, reason = resonance_bucket(tech_lvl, h_lvl, str(sector.get("stage", "")))
            sim = row.get("Similarity") if "Similarity" in row else None
            raw_rank = i + 1
            base_score = 0
            base_score += {"强": 35, "中": 20, "弱": 5}.get(tech_lvl, 0)
            base_score += {"强": 35, "中": 20, "弱": 5}.get(h_lvl, 0)
            if isinstance(sim, (int, float)):
                base_score += max(0, min(20, float(sim) * 20))
            base_score -= max(0, raw_rank - 1) * 0.5
            risk_flags = list(tech_flags)
            if pass_level in ("reject_A", "reject_all"):
                risk_flags.append("不得进入A池")
            if sector.get("stage") and ("退潮" in str(sector.get("stage")) or "下跌" in str(sector.get("stage"))):
                risk_flags.append("所属板块退潮/下跌")
            candidates.append({
                "code": code,
                "name": THEME_STOCK_NAMES.get(code, ""),
                "sector": sector.get("theme_name", theme_id),
                "theme_id": theme_id,
                "source": ["formula_screen", "B1", "theme_tracker"],
                "technical_sources": [{
                    "source_id": "B1_low_j_factor_similarity",
                    "signal": "KDJ低位+因子相似度",
                    "technical_score": None,
                    "similarity": None if pd.isna(sim) else round(float(sim), 4),
                    "raw_rank": raw_rank,
                    "source_file": str(f),
                }],
                "sector_heat_filter": {
                    "sector_state": sector.get("stage", "未知"),
                    "sector_score": sector.get("score", 0),
                    "heat_level": h_lvl,
                    "pass_level": pass_level,
                    "reason": reason,
                },
                "resonance": {
                    "technical_level": tech_lvl,
                    "sector_heat_level": h_lvl,
                    "market_permission": "仅低吸/小仓核心主线",
                    "resonance_level": "强共振" if tech_lvl == "强" and h_lvl == "强" else "弱共振" if tech_lvl in ("强", "中") and h_lvl in ("强", "中") else "无共振",
                },
                "stock_role": "未定",
                "relative_strength": "未定",
                "score": round(base_score, 2),
                "bucket": bucket,
                "entry_reason": [reason, f"B1 rank={raw_rank}", f"技术={tech_lvl}", f"板块热度={h_lvl}"],
                "risk_flags": risk_flags,
                "next_step": "observe_price" if bucket == "B" else "long_term_track" if bucket == "C" else "avoid" if bucket == "D" else "generate_buy_plan",
                "technical_snapshot": {
                    "latest_date": analysis.get("latest_date"),
                    "trend_state": (analysis.get("trend") or {}).get("state"),
                    "box20_position": (analysis.get("box_20d") or {}).get("position"),
                    "daily_j": (((analysis.get("daily") or {}).get("kdj") or {}).get("j")),
                    "daily_macd_direction": (((analysis.get("daily") or {}).get("macd") or {}).get("hist_direction")),
                },
            })
    candidates.sort(key=lambda x: (x["bucket"], -x["score"]))
    return candidates, ", ".join(str(p) for p in top_files)


def make_report(date: str, candidates: list[dict[str, Any]], source_files: str) -> str:
    buckets = {b: [c for c in candidates if c.get("bucket") == b] for b in ["A", "B", "C", "D"]}
    lines = []
    lines.append("# stock_pool 选股池\n")
    lines.append(f"日期：{date}\n")
    lines.append("## 1. 今日选股结论\n")
    lines.append("- 市场是否支持选股：支持观察和小仓核心低吸，但不支持追高。")
    lines.append("- 今日可进入买入计划的方向：暂无直接A池；半导体强主线候选先进入B池。")
    lines.append("- 今日只观察的方向：半导体/芯片/存储/封测 B1 技术候选。")
    lines.append("- 今日回避方向：板块退潮/下跌且技术不共振的候选。")
    lines.append(f"- B1来源文件：{source_files or '无'}\n")

    def table(title: str, rows: list[dict[str, Any]], empty: str):
        lines.append(f"## {title}\n")
        lines.append("| 代码 | 名称 | 板块 | 技术 | 板块热度 | 分数 | 入池理由 | 风险点 | 下一步 |")
        lines.append("|---|---|---|---|---|---:|---|---|---|")
        if not rows:
            lines.append(f"| {empty} | - | - | - | - | - | - | - | - |")
        for c in rows[:20]:
            reason = "；".join(c.get("entry_reason", [])[:3])
            risks = "；".join(c.get("risk_flags", [])[:3]) or "-"
            lines.append(f"| {c.get('code')} | {c.get('name')} | {c.get('sector')} | {c.get('resonance',{}).get('technical_level')} | {c.get('resonance',{}).get('sector_heat_level')} | {c.get('score')} | {reason} | {risks} | {c.get('next_step')} |")
        lines.append("")

    table("2. A池：可进入买入计划", buckets["A"], "暂无A池")
    table("3. B池：重点观察", buckets["B"], "暂无B池")
    table("4. C池：长期跟踪", buckets["C"], "暂无C池")
    table("5. D池：回避/冷却", buckets["D"], "暂无D池")

    lines.append("## 6. 传递给 buy_strategy\n")
    a_codes = [c["code"] for c in buckets["A"]]
    b_codes = [c["code"] for c in buckets["B"]]
    lines.append("- 可生成买入计划：" + ("、".join(a_codes) if a_codes else "无"))
    lines.append("- 仅生成观察价位：" + ("、".join(b_codes) if b_codes else "无"))
    lines.append("- 禁止生成买入计划：C/D 池全部候选。\n")
    lines.append("> 风险提示：B1 是技术候选来源，不是买入信号；真实交易必须经过 buy_strategy、risk_control、chief_decision。")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=pd.Timestamp.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    candidates, source_files = build(args.date)
    STOCK_POOL_DIR.mkdir(parents=True, exist_ok=True)
    out_json = STOCK_POOL_DIR / f"{args.date}_stock_pool.json"
    out_md = PLAN_DIR / f"{args.date}_stock_pool.md"
    out_json.write_text(json.dumps(candidates, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    report = make_report(args.date, candidates, source_files)
    out_md.write_text(report, encoding="utf-8")
    print(out_json)
    print(out_md)
    print(report[:5000])


if __name__ == "__main__":
    main()
