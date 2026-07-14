# -*- coding: utf-8 -*-
"""Build a dynamic 14:45 review skeleton from the latest position snapshot.

The agent layer enriches this skeleton with live quotes. No stock, price or
trigger is hard-coded here.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path("C:/Users/gh/.openclaw-tdxclaw/workspace/strategy_team")
TRADES = BASE / "01_data" / "trades"
HOLDINGS = BASE / "01_data" / "holdings"
RISK = BASE / "01_data" / "risk"
PLANS = BASE / "03_daily_plans"
LOGS = BASE / "06_logs"
PLANS.mkdir(parents=True, exist_ok=True)
LOGS.mkdir(parents=True, exist_ok=True)


def load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def latest(pattern: str, folder: Path) -> Path | None:
    files = sorted(folder.glob(pattern))
    return files[-1] if files else None


def finite(value, default=0.0):
    try:
        v = float(value)
        return default if math.isnan(v) else v
    except (TypeError, ValueError):
        return default


def snapshot_state(target_date: str) -> dict:
    meta = load(TRADES / "_import_meta.json", {})
    imported = meta.get("imported_at")
    source_mtime = meta.get("source_mtime")
    stale = True
    if imported:
        try:
            stale = datetime.fromisoformat(imported).date().isoformat() != target_date
        except ValueError:
            pass
    return {
        "imported_at": imported or "未知",
        "source_mtime": source_mtime or "未知",
        "stale_for_trade_date": stale,
        "warning": "持仓为最近一次Excel导入快照，盘中交易若未补录则不会反映" if stale else "持仓快照当日已导入；仍需确认导入后是否发生盘中交易",
    }


def technical_map(target_date: str) -> dict[str, dict]:
    path = HOLDINGS / f"{target_date}_holding_technical_summary.json"
    if not path.exists():
        path = latest("*_holding_technical_summary.json", HOLDINGS)
    rows = load(path, []) if path else []
    return {str(x.get("code")): x for x in rows}


def risk_map(target_date: str) -> dict[str, list[dict]]:
    path = RISK / f"{target_date}_risk_decision.json"
    if not path.exists():
        path = latest("*_risk_decision.json", RISK)
    data = load(path, {}) if path else {}
    out: dict[str, list[dict]] = {}
    for x in data.get("stock_risks", []):
        code = str(x.get("code", "")).split(".")[0]
        if code:
            out.setdefault(code, []).append(x)
    return out


def classify(position: dict, tech: dict, risks: list[dict]) -> tuple[str, str, str]:
    pnl = finite(position.get("持有盈亏率"), 0)
    trend = str(tech.get("trend_state") or "待确认")
    box = str(tech.get("box20_position") or "待确认")
    high_risk = any(x.get("priority") == "高" for x in risks)
    if high_risk or "破位" in box or pnl <= -0.07:
        reasons = [str(x.get("reason") or x.get("risk_type")) for x in risks if x.get("priority") == "高"]
        return "P1", "减仓/止损评估", "；".join(reasons) or f"趋势{trend}、位置{box}、盈亏{pnl:+.1%}"
    if trend == "下跌" or pnl < 0:
        return "P2", "观察、不加仓", f"趋势{trend}、位置{box}、盈亏{pnl:+.1%}"
    return "P3", "持有观察", f"趋势{trend}、位置{box}、盈亏{pnl:+.1%}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    target_date = args.date
    positions = load(TRADES / "current_positions.json", [])
    if not positions:
        raise SystemExit("[close_review] no positions found")

    snap = snapshot_state(target_date)
    tech = technical_map(target_date)
    risks = risk_map(target_date)
    total_position = sum(finite(x.get("仓位占比")) for x in positions)
    actions = []
    for p in positions:
        code = str(p.get("代码", "")).split(".")[0]
        priority, action, reason = classify(p, tech.get(code, {}), risks.get(code, []))
        actions.append({"priority": priority, "code": code, "name": p.get("名称", ""), "action": action, "reason": reason})
    actions.sort(key=lambda x: (x["priority"], x["code"]))

    lines = [
        f"# 14:45 收盘前操作建议 — {target_date}", "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 持仓快照导入：{snap['imported_at']}｜源文件更新：{snap['source_mtime']}",
        f"> ⚠️ {snap['warning']}", "",
        "## 1. 当前持仓快照", "",
        "| 代码 | 名称 | 数量 | 成本 | 快照价格 | 快照盈亏 | 仓位 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for p in positions:
        lines.append(f"| {p.get('代码')} | {p.get('名称')} | {finite(p.get('持有数量')):.0f} | {finite(p.get('单位成本')):.3f} | {finite(p.get('最新价')):.2f} | {finite(p.get('持有盈亏率')):+.2%} | {finite(p.get('仓位占比')):.1%} |")
    lines += ["", f"- 快照总仓位：**{total_position:.1%}**", "- 14:45 实时价格与实时盈亏：待 agent 使用 `tdx_quotes` 更新。", "",
              "## 2. 动态持仓优先级", "", "| 优先级 | 代码 | 名称 | 操作倾向 | 依据 |", "|---|---|---|---|---|"]
    for x in actions:
        lines.append(f"| {x['priority']} | {x['code']} | {x['name']} | {x['action']} | {x['reason']} |")
    lines += ["", "## 3. 14:45 市场与板块状态", "",
              "- 指数实时行情：待更新。", "- 持仓所属板块及主线状态：待更新。", "- 0AMV 盘中状态：若无可靠数据，不作推测。", "",
              "## 4. 操作建议", "",
              "- P1：实时验证关键价位，触发风险规则时优先执行。", "- P2：不加仓，弱于板块或跌破关键位时降低风险。", "- P3：持有观察，不追高；冲高回落时保护利润。",
              "- 新开仓：由实时 market_timing、板块许可、A池计划及 risk_control 共同决定。", "",
              "## 5. 数据质量与待确认", "", f"- 持仓快照是否过期：{'是' if snap['stale_for_trade_date'] else '否/当日已导入'}",
              "- 盘中发生但未补录的交易：需要用户确认。", "- 实时行情：由 14:45 任务补充。", ""]

    out = PLANS / f"{target_date}_1445_review.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    log = {"date": target_date, "generated_at": datetime.now().isoformat(timespec="seconds"), "position_snapshot": snap,
           "total_position": total_position, "positions": positions, "actions": actions, "live_quotes_pending": True}
    (LOGS / f"{target_date}_1445_review.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
    print(json.dumps({"position_snapshot": snap, "total_position": total_position, "actions": actions}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
