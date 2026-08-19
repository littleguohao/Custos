# -*- coding: utf-8 -*-
"""Build a data-backed 14:45 review from positions, quotes and market state."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from custos.pipeline.holdings.b1_holding_state import evaluate as evaluate_b1_holding

from custos.pipeline.close_review.holding_bbi import intraday_bbi_basis
from custos.pipeline.close_review.holding_structure import n_structure_basis

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from custos.core.paths import (
    cn_today,
    cn_now,
    HOLDINGS_DIR,
    LOGS,
    MARKET_DIR,
    PLANS,
    QUALITY_DIR,
    RISK_DIR,
    TRADES_DIR,
    daily_report_dir,
)  # noqa: E402
from custos.core.paths import read_json as load  # noqa: E402
from custos.core import report_audit  # noqa: E402
from custos.core.code_utils import finite  # noqa: E402
from custos.core.code_utils import fnum as optional_finite  # noqa: E402
from custos.core.fmt import pct_text as _fmt_pct_text  # noqa: E402

TRADES = TRADES_DIR
HOLDINGS = HOLDINGS_DIR
RISK = RISK_DIR
MARKET = MARKET_DIR
QUALITY = QUALITY_DIR
PLANS.mkdir(parents=True, exist_ok=True)
LOGS.mkdir(parents=True, exist_ok=True)


def latest(pattern: str, folder: Path) -> Path | None:
    files = sorted(folder.glob(pattern))
    return files[-1] if files else None


def price_text(value, digits=2):
    return "缺失" if value is None else f"{value:.{digits}f}"


def pct_text(value, digits=2):
    """本报告的缺数占位用中文「缺失」（与表格里其他列一致）；口径同 `fmt.pct_text`。

    只在措辞上与共享实现不同，**有限性判定单一来源** ——
    收敛前这份自己判 `is None`，NaN 会渲染成 `+nan%`。
    """
    return _fmt_pct_text(value, digits, missing="缺失")


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


def _validate_snapshot_meta(
    target_date: str, snapshot: dict, errors: list[str]
) -> None:
    if snapshot.get("as_of_date") != target_date:
        errors.append(
            f"snapshot_date={snapshot.get('as_of_date')!r}, expected={target_date}"
        )
    captured_at = str(snapshot.get("captured_at") or "")
    if not captured_at.startswith(target_date):
        errors.append("captured_at missing or not on target date")
    if str(snapshot.get("source") or "").lower() in {"", "missing", "缺失"}:
        errors.append("quote source missing")


def _validate_holding_quotes(
    target_date: str, positions: list[dict], snapshot: dict, errors: list[str]
) -> None:
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


def _validate_index_quotes(target_date: str, snapshot: dict, errors: list[str]) -> None:
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


def validate_quote_snapshot(
    target_date: str, positions: list[dict], snapshot: dict
) -> list[str]:
    errors: list[str] = []
    _validate_snapshot_meta(target_date, snapshot, errors)
    _validate_holding_quotes(target_date, positions, snapshot, errors)
    _validate_index_quotes(target_date, snapshot, errors)
    return errors


def validate_report(
    target_date: str, positions: list[dict], report: str, gate: dict
) -> list[str]:
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
    index_text = (
        "；".join(
            f"{item.get('name') or index_names.get(normalized_code(item.get('code')), item.get('code', '未知'))}{price_text(optional_finite(item.get('price')))}({pct_text(optional_finite(item.get('change_pct')))})"
            for item in indices
        )
        or "缺失"
    )
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
        f"持仓说明：{snap.get('reason', '缺失')}；{snap.get('assumption', '14:45按当前行情评估持仓操作建议')}。完整报告：strategy_team/artifacts/reports/daily/{target_date}/{target_date}_1445_review.md",
    ]
    return "\n".join(lines)


def snapshot_state(target_date: str) -> dict:
    gate = load(QUALITY / f"{target_date}_runtime_gate.json", {})
    state = gate.get("position_freshness", {})
    return {
        "status": state.get("status", "未知"),
        "reason": state.get("reason", "缺少运行门控"),
        "assumption": state.get("assumption"),
        # 继承标记必须整组透出:只带 inherited_from 时,"这是继承来的基线"这个**布尔事实**
        # 在报告层就没了,下游只能靠解析 reason 文本猜。
        "inherited": state.get("inherited"),
        "inherited_from": state.get("inherited_from"),
    }


def quote_map(target_date: str) -> tuple[dict[str, dict], dict]:
    snapshot = load(MARKET / f"{target_date}_holding_quotes.json", {})
    return {str(x.get("code")): x for x in snapshot.get("quotes", [])}, snapshot


def technical_map(target_date: str) -> dict[str, dict]:
    path = HOLDINGS / f"{target_date}_holding_technical_summary.json"
    if not path.exists():
        alt = latest("*_holding_technical_summary.json", HOLDINGS)
        if alt is not None:
            path = alt
    # 找不到时 load 一个不存在的路径 → read_json 返回 default，与原先 path=None 同结果
    rows = load(path, []) if path.exists() else []
    return {str(x.get("code")): x for x in rows}


def regime_advice(regime: str) -> str:
    """按**实际 0AMV regime** 生成操作建议口径。

    原先硬编码"0AMV处于实质空头区间",做多/中性时报告会与第3节的 regime 自相矛盾。
    """
    return {
        "空头": "0AMV处于实质空头区间，所有反弹优先按减仓机会处理，不作为加仓、摊低成本或趋势反转依据。",
        "做多": "0AMV处于做多区间，持仓按结构持有，加仓仍受运行权限与单票上限约束；空头级减仓规则不适用。",
        "中性": "0AMV处于中性区间，不主动加仓，按个股结构与硬风险处理；不得把中性当作做多信号。",
    }.get(
        str(regime),
        f"0AMV状态为「{regime}」（未确认），按保守口径处理：不加仓，硬风险优先。",
    )


def risk_source_date(target_date: str) -> tuple[Path | None, str]:
    """定位 risk_decision 的实际来源文件与其日期。当日缺失时会回退到最近一份,
    **必须把该日期标进报告**——否则读者无法分辨风控依据是今天的还是几天前的。"""
    path = RISK / f"{target_date}_risk_decision.json"
    if path.exists():
        return path, target_date
    alt = latest("*_risk_decision.json", RISK)
    if alt is None:
        return None, ""
    return alt, alt.name[:10]


def risk_map(target_date: str) -> dict[str, list[dict]]:
    path, _src = risk_source_date(target_date)
    data = load(path, {}) if path else {}
    out: dict[str, list[dict]] = {}
    for x in data.get("stock_risks", []):
        # ⚠️ 不能写 `str(x.get("code", ""))`：key 存在而值为 `None` 时 `.get` 返回
        # **None 而不是默认值**，`str(None)` == "None" 是真值 ⇒ 建出一个叫 "None"
        # 的幽灵持仓键，下游按代码查风险时永远查不到、还多一条无主风险。
        code = str(x.get("code") or "").split(".")[0]
        if code:
            out.setdefault(code, []).append(x)
    return out


def _b1_short_circuit(
    b1_state: dict | None, high_risk: bool, risks: list[dict]
) -> tuple[str, str, str] | None:
    if not (b1_state and b1_state.get("final_priority") in {"P0", "P1", "P2"}):
        return None
    # ⚠️ B1 状态短路在 high_risk 判定**之前**，所以要在这里把高优先风险的理由
    # 补回来，否则它在整份 14:45 报告里**一个字都看不到**（`risks` 只经由
    # `classify` 影响输出，别处不渲染）——而「所有计划必须可复盘」。
    #
    # 举例：RiskDecision 说「已触发止损线」（高），而 B1 按实时价重算只判 P2
    # 「尾盘跌破BBI待收盘确认」。修前该行显示 P2 且止损理由消失。
    #
    # 只补理由、**不动优先级**：B1 用的是 14:45 实时价、RiskDecision 可能来自
    # 前一日 17:00 —— 谁压过谁已定案（v0.35，owner 拍板）：盘中以**证据新鲜度**
    # 为准，14:45 实时行情重算的 B1 压过同日期标签的 RiskDecision
    # （README 写「risk_control 拥有否决权」，那条没区分依据新鲜度，v0.35 补上）。
    reason = b1_state["final_reason"]
    if high_risk:
        outstanding = "；".join(
            str(x.get("reason") or x.get("risk_type"))
            for x in risks
            if x.get("priority") == "高"
        )
        reason = f"{reason}；⚠️未消化的高优先风控依据：{outstanding}"
    return b1_state["final_priority"], b1_state["final_action"], reason


def _structure_signal(
    structure: dict, structure_reason: str
) -> tuple[str, str, str] | None:
    if structure.get("signal") == "structural_clear":
        return "P0", "N型前低清仓评估", structure_reason
    if structure.get("signal") == "pullback_failure":
        return "P1", "N型回踩低点失守评估", structure_reason
    return None


def _hard_risk_signal(
    high_risk: bool,
    box: str,
    pnl: float,
    risks: list[dict],
    trend: str,
    bbi_reason: str,
) -> tuple[str, str, str] | None:
    if not (high_risk or "破位" in box or pnl <= -0.07):
        return None
    reasons = [
        str(x.get("reason") or x.get("risk_type"))
        for x in risks
        if x.get("priority") == "高"
    ]
    return (
        "P1",
        "减仓/止损评估",
        ("；".join(reasons) or f"趋势{trend}、位置{box}、盈亏{pnl:+.1%}")
        + f"；{bbi_reason}",
    )


def _bbi_signal(bbi: dict, bbi_reason: str) -> tuple[str, str, str] | None:
    if bbi.get("signal") == "clear_review" and bbi.get("current_above") is not True:
        return "P1", "BBI清仓评估", bbi_reason
    if bbi.get("signal") == "intraday_break_watch":
        return "P2", "尾盘跌破BBI待收盘确认", bbi_reason
    if bbi.get("signal") == "reclaim_in_progress":
        return "P2", "BBI修复待收盘确认", bbi_reason
    return None


def _bearish_rebound(
    bearish_regime: bool, quote: dict, bbi_reason: str
) -> tuple[str, str, str] | None:
    if bearish_regime and finite(quote.get("change_pct")) > 0:
        priority = "P1" if finite(quote.get("change_pct")) >= 5 else "P2"
        return (
            priority,
            "反弹减仓评估",
            f"0AMV空头区间，当日反弹{finite(quote.get('change_pct')):+.2f}%优先用于降低仓位；{bbi_reason}",
        )
    return None


def _classify_context(
    position: dict, tech: dict, price: float
) -> tuple[float, str, str, dict, dict]:
    """price 已确认非 None 后的公共输入：盈亏、趋势/箱体口径、BBI 与 N型结构。"""
    cost = finite(position.get("单位成本"))
    pnl = price / cost - 1 if cost else finite(position.get("持有盈亏率"), 0)
    trend = str(tech.get("trend_state") or "待确认")
    box = str(tech.get("box20_position") or "待确认")
    bbi = intraday_bbi_basis(tech, price, str(tech.get("latest_date") or "") or None)
    structure = n_structure_basis(tech, price)
    return pnl, trend, box, bbi, structure


def classify(
    position: dict,
    tech: dict,
    risks: list[dict],
    quote: dict,
    bearish_regime: bool,
    b1_state: dict | None = None,
) -> tuple[str, str, str]:
    price = optional_finite(quote.get("price"))
    if price is None:
        return (
            "P1",
            "等待当日行情/仅风险收缩",
            "当日实时行情缺失；禁止使用持仓快照旧价生成尾盘动作",
        )
    pnl, trend, box, bbi, structure = _classify_context(position, tech, price)
    bbi_reason = f"{bbi['state']}；{bbi['reminder']}"
    structure_reason = f"{structure['state']}；{structure['reminder']}"
    high_risk = any(x.get("priority") == "高" for x in risks)
    # 分支顺序即判定优先级，不得调整：B1 短路 → N型结构 → 硬风险 →
    # BBI 信号 → 空头反弹减仓（各 helper 未命中时返回 None 继续往下）。
    for decision in (
        _b1_short_circuit(b1_state, high_risk, risks),
        _structure_signal(structure, structure_reason),
        _hard_risk_signal(high_risk, box, pnl, risks, trend, bbi_reason),
        _bbi_signal(bbi, bbi_reason),
        _bearish_rebound(bearish_regime, quote, bbi_reason),
    ):
        if decision is not None:
            return decision
    if trend == "下跌" or pnl < 0:
        return (
            "P2",
            "观察、不加仓",
            f"趋势{trend}、位置{box}、盈亏{pnl:+.1%}；{bbi_reason}",
        )
    return "P3", "持有观察", f"趋势{trend}、位置{box}、盈亏{pnl:+.1%}；{bbi_reason}"


def risk_date_note(target_date: str, risk_src_date: str, risk_obj: dict) -> str:
    """风控依据数据日的报告措辞。

    ⚠️ 同时看**文件日**与**证据日**。2026-08-07 修：此前只按文件日判，
    于是 09:05 盘前产出的 risk_decision（文件日=当日、依据=前一交易日收盘）
    在 14:45 报告里被标成「当日」—— 读者会以为风控依据是今天的。
    `evidence_date` 由 `generate_risk_and_sectors` 从技术面 `latest_date` 取得。
    """
    _evidence = str(risk_obj.get("evidence_date") or "")
    if not risk_src_date:
        return "缺失（无 risk_decision，按无风控依据处理）"
    if risk_src_date != target_date:
        return (
            f"**{risk_src_date}**（⚠️非当日，当日 risk_decision 缺失，"
            f"已回退最近一份，不得据此放宽任何权限）"
        )
    if _evidence and _evidence != target_date:
        return (
            f"**{risk_src_date}**（文件为当日，但**证据日是 {_evidence}** —— "
            f"盘前生成、依据前一交易日收盘；盘中动作以 14:45 实时行情"
            f"重算的 B1 为准（v0.35 已定案：盘中风控依据按证据新鲜度取））"
        )
    if not _evidence:
        return f"**{risk_src_date}**（当日；证据日未标注，无法确认依据新鲜度）"
    return f"**{risk_src_date}**（当日，证据日同为当日）"


def estimate_total_assets(positions: list[dict]) -> float:
    """用「持有金额/仓位占比」样本的中位数估计总资产。"""
    asset_samples = [
        finite(x.get("持有金额")) / finite(x.get("仓位占比"))
        for x in positions
        if finite(x.get("仓位占比")) > 0
    ]
    return sorted(asset_samples)[len(asset_samples) // 2] if asset_samples else 0


def revalue_and_plan(
    target_date: str,
    positions: list[dict],
    tech: dict[str, dict],
    risks: dict[str, list[dict]],
    quotes: dict[str, dict],
    regime: str,
    total_assets: float,
) -> tuple[list[dict], list[dict]]:
    """按 14:45 实时行情逐票重估并分类出优先级动作（§1/§2 的数据源）。"""
    revalued: list[dict] = []
    actions = []
    for p in positions:
        code = str(p.get("代码", "")).split(".")[0]
        quote = quotes.get(code, {})
        price = optional_finite(quote.get("price"))
        qty = finite(p.get("持有数量"))
        cost = finite(p.get("单位成本"))
        market_value = price * qty if price is not None else None
        pnl_pct = price / cost - 1 if price is not None and cost else None
        position_pct = (
            market_value / total_assets
            if market_value is not None and total_assets
            else None
        )
        b1_input = {**tech.get(code, {}), "holding_pnl_pct": pnl_pct}
        b1_state = evaluate_b1_holding(
            b1_input, regime, price, str(quote.get("date") or target_date)
        )
        revalued.append(
            {
                "code": code,
                "price": price,
                "pnl_pct": pnl_pct,
                "position_pct": position_pct,
                "market_value": market_value,
                "bbi": intraday_bbi_basis(
                    tech.get(code, {}),
                    price,
                    str(tech.get(code, {}).get("latest_date") or "") or None,
                ),
                "n_structure": n_structure_basis(tech.get(code, {}), price),
                "b1_holding_state": b1_state,
            }
        )
        priority, action, reason = classify(
            p,
            tech.get(code, {}),
            risks.get(code, []),
            quote,
            regime == "空头",
            b1_state,
        )
        actions.append(
            {
                "priority": priority,
                "code": code,
                "name": p.get("名称", ""),
                "action": action,
                "reason": reason,
                "b1_holding_state": b1_state,
            }
        )
    actions.sort(key=lambda x: (x["priority"], x["code"]))
    return revalued, actions


def render_header(
    lines: list[str],
    target_date: str,
    audit: dict,
    snap: dict,
    quote_snapshot: dict,
    amv_display: str,
    regime: str,
    market_quality: dict,
) -> None:
    """报告头：角色定版、生成时间、可审计块与行情/状态摘要。"""
    lines += [
        f"# 14:45 收盘前操作建议 — {target_date}",
        "",
        "> 角色（v0.57 owner 定版）：**盘中14:45=按规则的交易提醒** ｜ "
        "盘前=信息处理+预案确认 ｜ 盘后=复盘纠错+条件化预案主产地。",
        f"> 生成时间：{cn_now().strftime('%Y-%m-%d %H:%M:%S')}",
        *report_audit.render_md(audit),
        f"> 持仓状态：**{snap['status']}**｜{snap['reason']}",
        f"> 持仓基线：{snap.get('assumption') or '按当前已确认持仓基线评估14:45操作建议'}",
        f"> 行情来源：{quote_snapshot.get('source', '缺失')}｜行情日期：{quote_snapshot.get('as_of_date', '缺失')}｜采集时间：{quote_snapshot.get('captured_at', '缺失')}",
        "> 口径说明：持仓价格使用上述行情快照；BBI与其他技术指标单独标注最近确认数据日，不把历史技术状态冒充当日收盘事实。",
        f"> 0AMV当日变动：**{amv_display}**｜有效状态：**{regime}**；盘中市场质量：**{market_quality.get('status', '未知')}**（{market_quality.get('quality_score', 'NA')}）",
        "",
    ]


def render_indices(lines: list[str], indices: list[dict]) -> None:
    """§0 主要指数快照。"""
    index_lines = [
        f"| {x.get('name', x.get('code', '未知'))} | {price_text(optional_finite(x.get('price')), 2)} | {pct_text(optional_finite(x.get('change_pct')))} | {x.get('date', '缺失')} {x.get('time', '缺失')} |"
        for x in indices
    ] or ["| 缺失 | 缺失 | 缺失 | 缺失 |"]
    lines += [
        "## 0. 主要指数快照",
        "",
        "| 指数 | 点位 | 涨跌幅 | 行情时间 |",
        "|---|---:|---:|---|",
        *index_lines,
        "",
    ]


def render_revalued(
    lines: list[str],
    positions: list[dict],
    revalued_map: dict[str, dict],
    quotes: dict[str, dict],
    total_position: float | None,
) -> None:
    """§1 当日行情重估持仓（含重估总仓位行）。"""
    lines += [
        "## 1. 当日行情重估持仓",
        "",
        "| 代码 | 名称 | 数量 | 成本 | 当日价格 | 持有盈亏 | 重估仓位 | 当日涨跌 | BBI状态 | N型前低清仓点 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for p in positions:
        code = str(p.get("代码", "")).split(".")[0]
        value = revalued_map[code]
        quote = quotes.get(code, {})
        pnl_display = "缺失" if value["pnl_pct"] is None else f"{value['pnl_pct']:+.2%}"
        position_display = (
            "缺失" if value["position_pct"] is None else f"{value['position_pct']:.1%}"
        )
        lines.append(
            f"| {code} | {p.get('名称')} | {finite(p.get('持有数量')):.0f} | {finite(p.get('单位成本')):.3f} | {price_text(value['price'])} | {pnl_display} | {position_display} | {pct_text(optional_finite(quote.get('change_pct')))} | {value['bbi']['state']} | {value['n_structure']['state']} |"
        )
    total_position_display = (
        "缺失（当日全持仓行情不完整）"
        if total_position is None
        else f"{total_position:.1%}"
    )
    lines += [
        "",
        f"- 当日行情重估总仓位：**{total_position_display}**",
        "",
    ]


def render_actions(lines: list[str], actions: list[dict]) -> None:
    """§2 动态持仓优先级。"""
    lines += [
        "## 2. 动态持仓优先级",
        "",
        "| 优先级 | 代码 | 名称 | 操作倾向 | 依据 |",
        "|---|---|---|---|---|",
    ]
    for x in actions:
        lines.append(
            f"| {x['priority']} | {x['code']} | {x['name']} | {x['action']} | {x['reason']} |"
        )
    lines.append("")


def render_market_state(
    lines: list[str],
    amv_display: str,
    regime: str,
    market_quality: dict,
    tech: dict[str, dict],
    risk_note: str,
) -> None:
    """§3 市场状态与数据日期。"""
    lines += [
        "## 3. 市场状态与数据日期",
        "",
        f"- 0AMV：当日 **{amv_display}**；缺值时只延续上一确认状态，不把缺失格式化为0。当前有效状态为 **{regime}**。",
        f"- 盘中市场质量：{market_quality.get('status', '未知')}；盘中缺失项按最近有效交易日继承并在门控中逐项标注。",
        f"- 个股技术数据日：{', '.join(sorted({str(x.get('latest_date')) for x in tech.values() if x.get('latest_date')})) or '缺失'}；仅作技术参考，不冒充当日行情。",
        f"- 风控依据数据日：{risk_note}",
        "",
    ]


def render_advice(lines: list[str], advice_line: str) -> None:
    """§4 操作建议。"""
    lines += [
        "## 4. 操作建议",
        "",
        f"- {advice_line}",
        "- BBI持仓依据：BBI上方仅代表技术持有结构有效；首日跌破观察次日收回；连续两日收盘跌破进入清仓评估。0AMV、硬止损、重大风险和单票超限优先。",
        "- N型结构：L1是主结构硬清仓位，L2是更高回踩结构位；L2失守表示N型尝试失败，不等同于L1硬位失守。",
        "- B1统一持仓状态：动作由硬止损、N型L1/L2、BBI、趋势箱体、量价、利润保护依次裁决；空头0AMV不得被个股信号放宽。",
        "- 精确减仓数量：B1默认盘中不交易，持仓基线确认且当日全持仓行情齐全时允许评估；若用户告知或成交台账出现目标日成交，立即改用最新持仓。",
        "- 加仓/新开仓：继续禁止；需0AMV退出空头且大盘、板块、个股结构修复，并通过完整市场质量门。",
        "",
    ]


def render_permissions(lines: list[str], gate: dict) -> None:
    """§5 运行权限 + 文末风险提示。"""
    lines += [
        "## 5. 运行权限",
        "",
        f"- 精确数量权限：{'允许' if gate.get('position_gate', {}).get('allow_precise_quantity') else '禁止'}。",
        f"- 减仓权限：{'允许' if gate.get('position_gate', {}).get('allow_position_reduction') else '禁止'}。",
        f"- 提高仓位权限：{'允许' if gate.get('position_gate', {}).get('allow_position_increase') else '禁止'}。",
        "",
        "> 风险提示：本报告用于收盘前风险决策，不构成收益承诺；继承的盘后指标不得用于放宽加仓权限。",
        "",
    ]


def _strict_check(strict: bool, errors: list[str], what: str) -> None:
    """--strict 模式下校验失败即硬失败（不发布带病报告）。"""
    if strict and errors:
        raise SystemExit(
            f"[close_review] strict {what} validation failed:\n- " + "\n- ".join(errors)
        )


def _total_position(revalued: list[dict]) -> float | None:
    """重估总仓位：任一持仓缺当日行情则为 None（报「缺失」而非部分求和）。"""
    return (
        sum(x["position_pct"] for x in revalued if x["position_pct"] is not None)
        if all(x["position_pct"] is not None for x in revalued)
        else None
    )


def emit_output(
    args,
    target_date: str,
    report: str,
    snap: dict,
    gate: dict,
    quote_snapshot: dict,
    indices: list[dict],
    positions: list[dict],
    revalued_map: dict[str, dict],
    quotes: dict[str, dict],
    actions: list[dict],
    total_position: float | None,
    amv_display: str,
    regime: str,
) -> None:
    """stdout 投递三种形态：--emit-digest 有界摘要 / --emit-report 报告正文 / 默认 JSON 摘要。"""
    if args.emit_digest:
        digest = build_delivery_digest(
            target_date,
            quote_snapshot,
            indices,
            positions,
            revalued_map,
            quotes,
            actions,
            total_position,
            snap,
            gate,
            amv_display,
            regime,
        )
        if args.strict and len(digest) > 3500:
            raise SystemExit(
                f"[close_review] delivery digest too long: {len(digest)} chars"
            )
        print(digest)
    elif args.emit_report:
        print(f"\n【14:45尾盘操作建议｜{target_date}】\n")
        print(report)
    else:
        print(
            json.dumps(
                {
                    "position_snapshot": snap,
                    "total_position": total_position,
                    "actions": actions,
                },
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=cn_today().strftime("%Y-%m-%d"))
    ap.add_argument(
        "--strict",
        action="store_true",
        help="fail instead of publishing when required quote/report fields are invalid",
    )
    ap.add_argument(
        "--emit-report",
        action="store_true",
        help="print the validated report body for cron delivery",
    )
    ap.add_argument(
        "--emit-digest",
        action="store_true",
        help="print a bounded delivery digest containing all execution-critical fields",
    )
    args = ap.parse_args()
    target_date = args.date
    positions = load(TRADES / "current_positions.json", [])
    if not positions:
        raise SystemExit("[close_review] no positions found")

    snap = snapshot_state(target_date)
    tech = technical_map(target_date)
    risks = risk_map(target_date)
    _risk_path, risk_src_date = risk_source_date(target_date)
    _risk_obj = load(_risk_path, {}) if _risk_path else {}
    risk_note = risk_date_note(target_date, risk_src_date, _risk_obj)
    quotes, quote_snapshot = quote_map(target_date)
    gate = load(QUALITY / f"{target_date}_runtime_gate.json", {})
    input_errors = validate_quote_snapshot(target_date, positions, quote_snapshot)
    _strict_check(args.strict, input_errors, "input")
    market = load(MARKET / f"{target_date}_market_timing_input.json", {})
    regime = market.get("amv_0", {}).get("effective_state") or "未知"
    amv_value = market.get("amv_0", {}).get("amv_change_pct")
    total_assets = estimate_total_assets(positions)
    revalued, actions = revalue_and_plan(
        target_date, positions, tech, risks, quotes, regime, total_assets
    )
    revalued_map = {x["code"]: x for x in revalued}
    total_position = _total_position(revalued)
    market_quality = gate.get("market_quality", {})
    indices = (
        quote_snapshot.get("indices", []) if isinstance(quote_snapshot, dict) else []
    )
    amv_numeric = optional_finite(amv_value)
    amv_display = "缺失" if amv_numeric is None else f"{amv_numeric:+.2f}%"
    advice_line = regime_advice(regime)

    # 可审计块（原待办 #29，已实现）：本报告实际读过的输入；风控依据回退旧文件时按实际来源登记
    audit = report_audit.build(
        target_date,
        "1445",
        [
            p
            for p in [
                TRADES / "current_positions.json",
                MARKET / f"{target_date}_holding_quotes.json",
                HOLDINGS / f"{target_date}_holding_technical_summary.json",
                _risk_path,
                QUALITY / f"{target_date}_runtime_gate.json",
                MARKET / f"{target_date}_market_timing_input.json",
            ]
            if p is not None
        ],
    )
    lines: list[str] = []
    render_header(
        lines,
        target_date,
        audit,
        snap,
        quote_snapshot,
        amv_display,
        regime,
        market_quality,
    )
    render_indices(lines, indices)
    render_revalued(lines, positions, revalued_map, quotes, total_position)
    render_actions(lines, actions)
    render_market_state(lines, amv_display, regime, market_quality, tech, risk_note)
    render_advice(lines, advice_line)
    render_permissions(lines, gate)

    report = "\n".join(lines)
    report_errors = validate_report(target_date, positions, report, gate)
    _strict_check(args.strict, report_errors, "report")
    out_dir = daily_report_dir(target_date, PLANS)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{target_date}_1445_review.md"
    out.write_text(report, encoding="utf-8")
    log = {
        "date": target_date,
        "generated_at": cn_now().isoformat(timespec="seconds"),
        "audit": audit,
        "position_snapshot": snap,
        "total_position": total_position,
        "positions": positions,
        "revalued_positions": revalued,
        "actions": actions,
        "quote_snapshot": quote_snapshot,
        "live_quotes_pending": not all(x["price"] is not None for x in revalued),
        "position_gate": gate.get("position_gate", {}),
    }
    (LOGS / f"{target_date}_1445_review.json").write_text(
        json.dumps(json_safe(log), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(out)
    emit_output(
        args,
        target_date,
        report,
        snap,
        gate,
        quote_snapshot,
        indices,
        positions,
        revalued_map,
        quotes,
        actions,
        total_position,
        amv_display,
        regime,
    )


if __name__ == "__main__":
    main()
