# -*- coding: utf-8 -*-
"""Build a data-backed 14:45 review from positions, quotes and market state."""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_timing.b1_holding_state import evaluate as evaluate_b1_holding

try:
    from .holding_bbi import bbi_basis, intraday_bbi_basis
    from .holding_structure import n_structure_basis
except ImportError:
    from holding_bbi import bbi_basis, intraday_bbi_basis
    from holding_structure import n_structure_basis

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[2]
TRADES = BASE / "01_data" / "trades"
HOLDINGS = BASE / "01_data" / "holdings"
RISK = BASE / "01_data" / "risk"
MARKET = BASE / "01_data" / "market"
QUALITY = BASE / "01_data" / "quality"
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
        return default if not math.isfinite(v) else v
    except (TypeError, ValueError):
        return default


def optional_finite(value):
    try:
        v = float(value)
        return None if not math.isfinite(v) else v
    except (TypeError, ValueError):
        return None


def price_text(value, digits=2):
    return "缺失" if value is None else f"{value:.{digits}f}"


def pct_text(value, digits=2):
    return "缺失" if value is None else f"{value:+.{digits}f}%"


def normalized_code(value) -> str:
    return str(value or "").split(".")[0]


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def validate_quote_snapshot(target_date: str, positions: list[dict], snapshot: dict) -> list[str]:
    errors: list[str] = []
    if snapshot.get("as_of_date") != target_date:
        errors.append(f"snapshot_date={snapshot.get('as_of_date')!r}, expected={target_date}")
    captured_at = str(snapshot.get("captured_at") or "")
    if not captured_at.startswith(target_date):
        errors.append("captured_at missing or not on target date")
    if str(snapshot.get("source") or "").lower() in {"", "missing", "缺失"}:
        errors.append("quote source missing")

    quotes = {normalized_code(x.get("code")): x for x in snapshot.get("quotes", [])}
    for position in positions:
        code = normalized_code(position.get("代码"))
        quote = quotes.get(code)
        if not quote:
            errors.append(f"holding quote missing: {code}")
            continue
        if quote.get("date") != target_date:
            errors.append(f"holding quote date invalid: {code}")
        if not str(quote.get("time") or ""):
            errors.append(f"holding quote time missing: {code}")
        for field in ("price", "previous_close", "change_pct"):
            if optional_finite(quote.get(field)) is None:
                errors.append(f"holding quote {field} missing: {code}")

    indices = {normalized_code(x.get("code")): x for x in snapshot.get("indices", [])}
    for code in ("000001", "399001", "399006"):
        quote = indices.get(code)
        if not quote:
            errors.append(f"index quote missing: {code}")
            continue
        if quote.get("date") != target_date:
            errors.append(f"index quote date invalid: {code}")
        if not str(quote.get("time") or ""):
            errors.append(f"index quote time missing: {code}")
        for field in ("price", "change_pct"):
            if optional_finite(quote.get(field)) is None:
                errors.append(f"index quote {field} missing: {code}")
    return errors


def validate_report(target_date: str, positions: list[dict], report: str, gate: dict) -> list[str]:
    errors: list[str] = []
    required_text = [
        f"# 14:45 收盘前操作建议 — {target_date}",
        "## 0. 主要指数快照",
        "## 1. 当日行情重估持仓",
        "## 2. 动态持仓优先级",
        "## 5. 运行权限",
    ]
    for text in required_text:
        if text not in report:
            errors.append(f"report section missing: {text}")
    for position in positions:
        code = normalized_code(position.get("代码"))
        if f"| {code} |" not in report:
            errors.append(f"holding missing from report: {code}")
    if gate.get("position_gate", {}).get("quotes_current") is not True:
        errors.append("runtime gate does not confirm current holding quotes")
    return errors


def build_delivery_digest(
    target_date: str,
    quote_snapshot: dict,
    indices: list[dict],
    positions: list[dict],
    revalued_map: dict[str, dict],
    quotes: dict[str, dict],
    actions: list[dict],
    total_position: float | None,
    snap: dict,
    gate: dict,
    amv_display: str,
    regime: str,
) -> str:
    action_map = {item["code"]: item for item in actions}
    index_names = {"000001": "上证指数", "399001": "深证成指", "399006": "创业板指"}
    index_text = "；".join(
        f"{item.get('name') or index_names.get(normalized_code(item.get('code')), item.get('code', '未知'))}{price_text(optional_finite(item.get('price')))}({pct_text(optional_finite(item.get('change_pct')))})"
        for item in indices
    ) or "缺失"
    lines = [
        f"【14:45尾盘操作建议｜{target_date}】",
        f"数据：{quote_snapshot.get('source', '缺失')}｜行情日{quote_snapshot.get('as_of_date', '缺失')}｜采集{quote_snapshot.get('captured_at', '缺失')}",
        f"指数：{index_text}",
        f"组合：重估仓位{'缺失' if total_position is None else f'{total_position:.1%}'}｜持仓{snap.get('status', '未知')}｜0AMV {amv_display}，有效状态{regime}",
        "逐股：",
    ]
    for position in positions:
        code = normalized_code(position.get("代码"))
        value = revalued_map[code]
        quote = quotes.get(code, {})
        action = action_map[code]
        lines.append(
            f"- {position.get('名称')}({code}) {price_text(value['price'])} {pct_text(optional_finite(quote.get('change_pct')))}；"
            f"{value['bbi']['state']}；{value['n_structure']['state']}；{action['priority']} {action['action']}"
        )
    position_gate = gate.get("position_gate", {})
    lines += [
        "权限："
        f"精确数量{'允许' if position_gate.get('allow_precise_quantity') else '禁止'}；"
        f"减仓执行{'允许' if position_gate.get('allow_position_reduction') else '禁止'}；"
        f"提高仓位{'允许' if position_gate.get('allow_position_increase') else '禁止'}。",
        "禁止动作：旧持仓价代替实时价、用历史技术或缺失0AMV放宽权限、空头区间补仓/追高、绕过风险否决。",
        f"持仓说明：{snap.get('reason', '缺失')}；{snap.get('assumption', '14:45按当前行情评估持仓操作建议')}。完整报告：strategy_team/03_daily_plans/{target_date}_1445_review.md",
    ]
    return "\n".join(lines)


def snapshot_state(target_date: str) -> dict:
    gate = load(QUALITY / f"{target_date}_runtime_gate.json", {})
    state = gate.get("position_freshness", {})
    return {
        "status": state.get("status", "未知"),
        "reason": state.get("reason", "缺少运行门控"),
        "assumption": state.get("assumption"),
        "inherited_from": state.get("inherited_from"),
    }


def quote_map(target_date: str) -> tuple[dict[str, dict], dict]:
    snapshot = load(MARKET / f"{target_date}_holding_quotes.json", {})
    return {str(x.get("code")): x for x in snapshot.get("quotes", [])}, snapshot


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


def classify(position: dict, tech: dict, risks: list[dict], quote: dict, bearish_regime: bool, b1_state: dict | None = None) -> tuple[str, str, str]:
    price = optional_finite(quote.get("price"))
    if price is None:
        return "P1", "等待当日行情/仅风险收缩", "当日实时行情缺失；禁止使用持仓快照旧价生成尾盘动作"
    cost = finite(position.get("单位成本"))
    pnl = price / cost - 1 if cost else finite(position.get("持有盈亏率"), 0)
    trend = str(tech.get("trend_state") or "待确认")
    box = str(tech.get("box20_position") or "待确认")
    bbi = intraday_bbi_basis(tech, price, str(tech.get("latest_date") or "") or None)
    bbi_reason = f"{bbi['state']}；{bbi['reminder']}"
    structure = n_structure_basis(tech, price)
    structure_reason = f"{structure['state']}；{structure['reminder']}"
    high_risk = any(x.get("priority") == "高" for x in risks)
    if b1_state and b1_state.get("final_priority") in {"P0", "P1", "P2"}:
        return b1_state["final_priority"], b1_state["final_action"], b1_state["final_reason"]
    if structure.get("signal") == "structural_clear":
        return "P0", "N型前低清仓评估", structure_reason
    if structure.get("signal") == "pullback_failure":
        return "P1", "N型回踩低点失守评估", structure_reason
    if high_risk or "破位" in box or pnl <= -0.07:
        reasons = [str(x.get("reason") or x.get("risk_type")) for x in risks if x.get("priority") == "高"]
        return "P1", "减仓/止损评估", ("；".join(reasons) or f"趋势{trend}、位置{box}、盈亏{pnl:+.1%}") + f"；{bbi_reason}"
    if bbi.get("signal") == "clear_review" and bbi.get("current_above") is not True:
        return "P1", "BBI清仓评估", bbi_reason
    if bbi.get("signal") == "intraday_break_watch":
        return "P2", "尾盘跌破BBI待收盘确认", bbi_reason
    if bbi.get("signal") == "reclaim_in_progress":
        return "P2", "BBI修复待收盘确认", bbi_reason
    if bearish_regime and finite(quote.get("change_pct")) > 0:
        priority = "P1" if finite(quote.get("change_pct")) >= 5 else "P2"
        return priority, "反弹减仓评估", f"0AMV空头区间，当日反弹{finite(quote.get('change_pct')):+.2f}%优先用于降低仓位；{bbi_reason}"
    if trend == "下跌" or pnl < 0:
        return "P2", "观察、不加仓", f"趋势{trend}、位置{box}、盈亏{pnl:+.1%}；{bbi_reason}"
    return "P3", "持有观察", f"趋势{trend}、位置{box}、盈亏{pnl:+.1%}；{bbi_reason}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
    ap.add_argument("--strict", action="store_true", help="fail instead of publishing when required quote/report fields are invalid")
    ap.add_argument("--emit-report", action="store_true", help="print the validated report body for cron delivery")
    ap.add_argument("--emit-digest", action="store_true", help="print a bounded delivery digest containing all execution-critical fields")
    args = ap.parse_args()
    target_date = args.date
    positions = load(TRADES / "current_positions.json", [])
    if not positions:
        raise SystemExit("[close_review] no positions found")

    snap = snapshot_state(target_date)
    tech = technical_map(target_date)
    risks = risk_map(target_date)
    quotes, quote_snapshot = quote_map(target_date)
    gate = load(QUALITY / f"{target_date}_runtime_gate.json", {})
    input_errors = validate_quote_snapshot(target_date, positions, quote_snapshot)
    if args.strict and input_errors:
        raise SystemExit("[close_review] strict input validation failed:\n- " + "\n- ".join(input_errors))
    market = load(MARKET / f"{target_date}_market_timing_input.json", {})
    regime = market.get("amv_0", {}).get("effective_state") or "未知"
    amv_value = market.get("amv_0", {}).get("amv_change_pct")
    asset_samples = [finite(x.get("持有金额")) / finite(x.get("仓位占比")) for x in positions if finite(x.get("仓位占比")) > 0]
    total_assets = sorted(asset_samples)[len(asset_samples) // 2] if asset_samples else 0
    revalued = []
    actions = []
    for p in positions:
        code = str(p.get("代码", "")).split(".")[0]
        quote = quotes.get(code, {})
        price = optional_finite(quote.get("price"))
        qty = finite(p.get("持有数量"))
        cost = finite(p.get("单位成本"))
        market_value = price * qty if price is not None else None
        pnl_pct = price / cost - 1 if price is not None and cost else None
        position_pct = market_value / total_assets if market_value is not None and total_assets else None
        b1_input = {**tech.get(code, {}), "holding_pnl_pct": pnl_pct}
        b1_state = evaluate_b1_holding(b1_input, regime, price, str(quote.get("date") or target_date))
        revalued.append({"code": code, "price": price, "pnl_pct": pnl_pct, "position_pct": position_pct, "market_value": market_value, "bbi": intraday_bbi_basis(tech.get(code, {}), price, str(tech.get(code, {}).get("latest_date") or "") or None), "n_structure": n_structure_basis(tech.get(code, {}), price), "b1_holding_state": b1_state})
        priority, action, reason = classify(p, tech.get(code, {}), risks.get(code, []), quote, regime == "空头", b1_state)
        actions.append({"priority": priority, "code": code, "name": p.get("名称", ""), "action": action, "reason": reason, "b1_holding_state": b1_state})
    actions.sort(key=lambda x: (x["priority"], x["code"]))
    revalued_map = {x["code"]: x for x in revalued}
    total_position = sum(x["position_pct"] for x in revalued if x["position_pct"] is not None) if all(x["position_pct"] is not None for x in revalued) else None
    market_quality = gate.get("market_quality", {})
    indices = quote_snapshot.get("indices", []) if isinstance(quote_snapshot, dict) else []
    amv_numeric = optional_finite(amv_value)
    amv_display = "缺失" if amv_numeric is None else f"{amv_numeric:+.2f}%"
    index_lines = [
        f"| {x.get('name', x.get('code', '未知'))} | {price_text(optional_finite(x.get('price')), 2)} | {pct_text(optional_finite(x.get('change_pct')))} | {x.get('date', '缺失')} {x.get('time', '缺失')} |"
        for x in indices
    ] or ["| 缺失 | 缺失 | 缺失 | 缺失 |"]

    lines = [
        f"# 14:45 收盘前操作建议 — {target_date}", "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 持仓状态：**{snap['status']}**｜{snap['reason']}",
        f"> 持仓基线：{snap.get('assumption') or '按当前已确认持仓基线评估14:45操作建议'}",
        f"> 行情来源：{quote_snapshot.get('source', '缺失')}｜行情日期：{quote_snapshot.get('as_of_date', '缺失')}｜采集时间：{quote_snapshot.get('captured_at', '缺失')}",
        "> 口径说明：持仓价格使用上述行情快照；BBI与其他技术指标单独标注最近确认数据日，不把历史技术状态冒充当日收盘事实。",
        f"> 0AMV当日变动：**{amv_display}**｜有效状态：**{regime}**；盘中市场质量：**{market_quality.get('status', '未知')}**（{market_quality.get('quality_score', 'NA')}）", "",
        "## 0. 主要指数快照", "",
        "| 指数 | 点位 | 涨跌幅 | 行情时间 |", "|---|---:|---:|---|",
        *index_lines, "",
        "## 1. 当日行情重估持仓", "",
        "| 代码 | 名称 | 数量 | 成本 | 当日价格 | 持有盈亏 | 重估仓位 | 当日涨跌 | BBI状态 | N型前低清仓点 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for p in positions:
        code = str(p.get("代码", "")).split(".")[0]
        value = revalued_map[code]
        quote = quotes.get(code, {})
        pnl_display = "缺失" if value["pnl_pct"] is None else f"{value['pnl_pct']:+.2%}"
        position_display = "缺失" if value["position_pct"] is None else f"{value['position_pct']:.1%}"
        lines.append(f"| {code} | {p.get('名称')} | {finite(p.get('持有数量')):.0f} | {finite(p.get('单位成本')):.3f} | {price_text(value['price'])} | {pnl_display} | {position_display} | {pct_text(optional_finite(quote.get('change_pct')))} | {value['bbi']['state']} | {value['n_structure']['state']} |")
    total_position_display = "缺失（当日全持仓行情不完整）" if total_position is None else f"{total_position:.1%}"
    lines += ["", f"- 当日行情重估总仓位：**{total_position_display}**", "",
              "## 2. 动态持仓优先级", "", "| 优先级 | 代码 | 名称 | 操作倾向 | 依据 |", "|---|---|---|---|---|"]
    for x in actions:
        lines.append(f"| {x['priority']} | {x['code']} | {x['name']} | {x['action']} | {x['reason']} |")
    lines += ["", "## 3. 市场状态与数据日期", "",
              f"- 0AMV：当日 **{amv_display}**；缺值时只延续上一确认状态，不把缺失格式化为0。当前有效状态为 **{regime}**。",
              f"- 盘中市场质量：{market_quality.get('status', '未知')}；盘中缺失项按最近有效交易日继承并在门控中逐项标注。",
              f"- 个股技术数据日：{', '.join(sorted({str(x.get('latest_date')) for x in tech.values() if x.get('latest_date')})) or '缺失'}；仅作技术参考，不冒充当日行情。", "",
              "## 4. 操作建议", "",
              "- 0AMV处于实质空头区间，所有反弹优先按减仓机会处理，不作为加仓、摊低成本或趋势反转依据。",
              "- BBI持仓依据：BBI上方仅代表技术持有结构有效；首日跌破观察次日收回；连续两日收盘跌破进入清仓评估。0AMV、硬止损、重大风险和单票超限优先。",
              "- N型结构：L1是主结构硬清仓位，L2是更高回踩结构位；L2失守表示N型尝试失败，不等同于L1硬位失守。",
              "- B1统一持仓状态：动作由硬止损、N型L1/L2、BBI、趋势箱体、量价、利润保护依次裁决；空头0AMV不得被个股信号放宽。",
              "- 精确减仓数量：B1默认盘中不交易，持仓基线确认且当日全持仓行情齐全时允许评估；若用户告知或成交台账出现目标日成交，立即改用最新持仓。",
              "- 加仓/新开仓：继续禁止；需0AMV退出空头且大盘、板块、个股结构修复，并通过完整市场质量门。", "",
              "## 5. 运行权限", "",
              f"- 精确数量权限：{'允许' if gate.get('position_gate', {}).get('allow_precise_quantity') else '禁止'}。",
              f"- 减仓权限：{'允许' if gate.get('position_gate', {}).get('allow_position_reduction') else '禁止'}。",
              f"- 提高仓位权限：{'允许' if gate.get('position_gate', {}).get('allow_position_increase') else '禁止'}。", "",
              "> 风险提示：本报告用于收盘前风险决策，不构成收益承诺；继承的盘后指标不得用于放宽加仓权限。", ""]

    report = "\n".join(lines)
    report_errors = validate_report(target_date, positions, report, gate)
    if args.strict and report_errors:
        raise SystemExit("[close_review] strict report validation failed:\n- " + "\n- ".join(report_errors))
    out = PLANS / f"{target_date}_1445_review.md"
    out.write_text(report, encoding="utf-8")
    log = {"date": target_date, "generated_at": datetime.now().isoformat(timespec="seconds"), "position_snapshot": snap,
           "total_position": total_position, "positions": positions, "revalued_positions": revalued,
           "actions": actions, "quote_snapshot": quote_snapshot, "live_quotes_pending": not all(x["price"] is not None for x in revalued),
           "position_gate": gate.get("position_gate", {})}
    (LOGS / f"{target_date}_1445_review.json").write_text(json.dumps(json_safe(log), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(out)
    if args.emit_digest:
        digest = build_delivery_digest(target_date, quote_snapshot, indices, positions, revalued_map, quotes, actions, total_position, snap, gate, amv_display, regime)
        if args.strict and len(digest) > 3500:
            raise SystemExit(f"[close_review] delivery digest too long: {len(digest)} chars")
        print(digest)
    elif args.emit_report:
        print(f"\n【14:45尾盘操作建议｜{target_date}】\n")
        print(report)
    else:
        print(json.dumps({"position_snapshot": snap, "total_position": total_position, "actions": actions}, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
