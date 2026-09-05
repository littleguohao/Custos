# -*- coding: utf-8 -*-
"""Render the unified daily report from structured ChiefDecision.

ChiefDecision is the only final-action authority. Upstream market, sector and
position artifacts may add evidence, but cannot override its permissions.
"""

from __future__ import annotations
import argparse, math
from datetime import datetime
from pathlib import Path
from typing import Any


from custos.pipeline.close_review.holding_structure import n_structure_basis
from custos.datasource.news.premarket_intel_schema import (
    validate_premarket_intelligence,
)

from custos.core.paths import DATA, PLANS, REVIEW_DIR, REVIEWS, cn_now, daily_report_dir
from custos.core.paths import read_json as load
from custos.core import report_audit

# 2026-08-07 架构审查：这两个访问器已移到 `news/premarket_intel_schema`——
# 它们读的是 `data/news/premarket/`，而 `news/postclose_news_digest`
# 也要用；放在本模块（根层报告生成器）里会让 news/（L1）反向依赖根层。
from custos.datasource.news.premarket_intel_schema import (  # noqa: E402
    load_premarket_intelligence,
    premarket_intelligence_path,
)

PLAN = PLANS
# 前日 final_review.json 的新落点（v0.179 起 data/review/，与报告分层）——模块常量，
# 测试 monkeypatch 改道 tmp；直接读 paths.REVIEW_DIR 会绕过改道、读真实 data/。
REVIEW_JSON_DIR = REVIEW_DIR
WEEKDAY = "一二三四五六日"


def clean(v: Any, d="待确认"):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return d
    s = str(v).strip()
    return s if s else d


def pct(v: Any):
    try:
        return f"{float(v) * 100:+.1f}%"
    except (TypeError, ValueError):
        return "待确认"


def ratio(v: Any):
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "待确认"


def code(v: Any):
    return str(v or "").split(".")[0]


def num(v: Any, digits=2):
    try:
        return f"{float(v):,.{digits}f}"
    except (TypeError, ValueError):
        return "待确认"


def pct_point(v: Any):
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "待确认"


ACTION_LABELS = {
    "no_add_and_1445_reduce_review": "禁止加仓；14:45复核降至20%以内",
    "priority_no_add_and_1445_reduce_review": "禁止加仓；14:45优先复核降至20%以内",
    "observe_no_add_low_j_is_not_buy_signal": "观察、禁止加仓；低J不是买点",
    "hold_conditionally_no_add": "条件持有、禁止加仓",
    "no_chasing_or_averaging_down_review_divergence": "禁止追涨或补跌；复核量价背离",
    "no_add; reduce 20%-25% of holding on unconfirmed rebound, 14:45 review": "禁止加仓；反弹未获确认时减持20%-25%，14:45复核",
    "no_add; if still over cap on rebound, reduce about 5% of holding at 14:45": "禁止加仓；反弹后仍超单票上限时，14:45减持约5%",
    "reduce 10%-20% on rally without sector confirmation": "反弹无板块确认时减持10%-20%",
    "no_add; reduce 10%-20% at 14:45 if rebound fails to repair structure": "禁止加仓；反弹未修复结构时14:45减持10%-20%",
    "no dip-buy; reduce 10%-20% on weak rebound or renewed reversal": "禁止逢跌补仓；弱反弹或再次转弱时减持10%-20%",
}


def premarket_schema_note(check: dict[str, Any]) -> str:
    # schema 不合规时的降级标注,插在 2.1 节首行,让情报失效在报告中显式可见
    if check.get("valid"):
        return ""
    return f"> ⚠️ 盘前情报 schema 不合规（{'；'.join(check.get('errors') or ['未知'])}），已降级为 RSS 候选展示"


def premarket_schema_marker(check: dict[str, Any]) -> str:
    # 第 7 节"盘前情报"行的 schema 状态标记;valid 且无 warnings 时为空串,输出逐字不变
    if not check.get("valid"):
        return f"（schema invalid: {'；'.join(check.get('errors') or ['未知'])}）"
    if check.get("warnings"):
        return f"（schema warnings: {len(check['warnings'])}）"
    return ""


def previous_review(day: str) -> dict[str, Any]:
    candidates = []
    # v0.179 起 .json 机器接口落 data/review/（glob 后缀 *_final_review.json，
    # 新路径优先）；找不到再回退 reports/daily/ 旧位置——历史产物不搬，
    # 搬目录次日读前一交易日预案不能断。
    for path in REVIEW_JSON_DIR.glob("*_final_review.json"):
        file_day = path.name[:10]
        if file_day < day:
            candidates.append((file_day, path))
    if not candidates:
        review_dir = REVIEWS / "daily"
        # 旧位置回退：{day}/{day}_1700_final_review.json + 旧名 {day}_final_review.json
        # （2026-08-29 文件名带时点标记前）+ 旧平铺 *_final_review.json（迁移期兼容，
        # 读历史日前一批是旧布局）——glob 后缀 *_final_review.json 对新旧名都命中，
        # 同一文件会被两种模式各命中一次的形态不存在，放心并集。
        for path in list(review_dir.glob("*/*_final_review.json")) + list(
            review_dir.glob("*_final_review.json")
        ):
            file_day = path.name[:10]
            if file_day < day:
                candidates.append((file_day, path))
    return load(max(candidates)[1], {}) if candidates else {}


def previous_holding_actions(review: dict[str, Any]) -> dict[str, dict[str, Any]]:
    # Current schema (v3+): next_day_plan.holding_plans
    rows = review.get("next_day_plan", {}).get("holding_plans")
    if not isinstance(rows, list):
        # Legacy v3: position_audit
        rows = review.get("position_audit")
    if not isinstance(rows, list):
        # Legacy v2: step_4_holdings.holdings
        rows = (review.get("step_4_holdings") or {}).get("holdings") or []
    return {code(x.get("code")): x for x in rows if isinstance(x, dict)}


def technical_relation(row: dict[str, Any]) -> str:
    above = [str(n) for n in (25, 60, 144, 240) if row.get(f"above_ma{n}") is True]
    below = [str(n) for n in (25, 60, 144, 240) if row.get(f"above_ma{n}") is False]
    parts = []
    if above:
        parts.append("站上MA" + "/".join(above))
    if below:
        parts.append("低于MA" + "/".join(below))
    return "；".join(parts) or "四均线待确认"


def bbi_holding_reminder(row: dict[str, Any]) -> tuple[str, str]:
    value = row.get("bbi")
    above = row.get("above_bbi")
    below_days = row.get("consecutive_closes_below_bbi")
    if value is None or above is None:
        return "BBI待确认", "缺少BBI数据，不据此调整持仓"
    try:
        days = int(below_days or 0)
    except (TypeError, ValueError):
        days = 0
    distance = pct_point(row.get("bbi_distance_pct"))
    state = f"BBI {num(value)}；收盘{'上方' if above else '下方'}（偏离{distance}）"
    if above:
        reminder = "仅技术维度持有结构有效；继续拿住，若BBI上方连续两根中大阳则分批止盈；更高优先级风控仍有效"
    elif days >= 2:
        reminder = f"连续{days}日收盘跌破BBI；按B1进入清仓评估，硬风险优先"
    else:
        reminder = "首日收盘跌破BBI；先看次日能否快速收回，未收回则升级清仓评估"
    return state, reminder


def direction_label(v: Any) -> str:
    s = str(v or "").lower()
    if s in {"positive", "bullish", "利好"}:
        return "利好"
    if s in {"negative", "bearish", "利空"}:
        return "利空"
    if s in {"neutral", "中性"}:
        return "中性"
    return "待确认"


def fallback_rss_events(day: str) -> list[dict[str, Any]]:
    # 新源体系(S/A tier 官方源)相关性分普遍偏高且多数不命中市场关键词,
    # 旧规则(matched_market_keywords 且 >=80)基本恒空;改为 relevance_score>=60 排序取 top3
    path = DATA / "news" / "rss" / "filtered" / f"{day}_premarket_rss_candidates.json"
    items = load(path, [])
    ranked = sorted(
        items, key=lambda x: int(x.get("relevance_score") or 0), reverse=True
    )
    selected = []
    for item in ranked:
        if int(item.get("relevance_score") or 0) >= 60:
            selected.append(
                {
                    "published_at": item.get("published_at"),
                    "title": item.get("title"),
                    "direction": item.get("direction"),
                    "impact": "仅作候选风险证据",
                    "source": item.get("source_name"),
                    "quality": item.get("quality", "candidate"),
                }
            )
    return selected[:3]


def _plan_level_lines(plan: dict, chief: dict, prior_day: str) -> list[str]:
    """计划级两行（总仓位目标/新开仓权限）盘后 vs 盘前刷新值逐条确认。"""
    if not plan:
        return [
            "- ⚠️ 盘后条件化预案**缺失**（上一交易日 final_review 无 next_day_plan）"
            "——无预案可确认；以下为盘前信息刷新值。"
        ]
    lines = [f"- 预案来源：**{prior_day}** 盘后复盘 §6。"]
    lines += [
        "",
        "| 预案项 | 盘后计划 | 盘前确认 |",
        "|---|---|---|",
    ]
    for label, pv, cv in (
        (
            "总仓位目标",
            plan.get("total_position_range"),
            chief.get("total_position_range"),
        ),
        (
            "新开仓权限",
            plan.get("new_position_permission"),
            chief.get("new_position_permission"),
        ),
    ):
        if not pv:
            state = "盘后缺值"
        elif str(pv) == str(cv):
            state = "✅ 确认（一致）"
        else:
            state = f"⚠️ 变化：盘前为 {cv}"
        lines.append(f"| {label} | {pv or '缺失'} | {state} |")
    return lines


def _holding_action_row(
    x: dict,
    tech: dict,
    prior_actions: dict,
    plan_by_code: dict,
    holding_event_map: dict,
    plan: dict,
) -> str:
    """逐票行：持仓状态 × 盘后预案 × 盘前确认（原 §4 表 + §6 逐票确认合并）。"""
    c = code(x.get("code"))
    t = tech.get(c, {})
    p = prior_actions.get(c, {})
    prow = plan_by_code.get(c)
    event = holding_event_map.get(c)
    tech_state = f"{clean(t.get('latest_date'))} 收{num(t.get('close'))}；{clean(t.get('trend_state'))}；仓位{ratio(t.get('position_pct'))}"
    ma_j = f"{technical_relation(t)}；日J={num(t.get('daily_j'), 1)}"
    bbi_state, bbi_reminder = bbi_holding_reminder(t)
    structure = n_structure_basis(t, t.get("close"))
    current_action = f"{x.get('priority', 'P3')} {clean(x.get('action'), '观察')}；{'；'.join(x.get('reasons') or [])}"
    if prow:
        plan_cell = (
            f"{prow.get('priority')} {prow.get('direction')}"
            f"；触发：{prow.get('trigger')}；无效：{prow.get('invalidation')}"
        )
        confirm = "✅ 确认（盘前动作在列）"
    else:
        # 预案缺该票：回退到上次复盘动作口径（原 §4 列），确认态如实标注
        plan_cell = ACTION_LABELS.get(
            p.get("action") or "",
            clean(p.get("action") or p.get("direction"), "无可用计划"),
        )
        confirm = "盘后预案无该票条目" if plan else "-"
    new_evidence = (
        f"{direction_label(event.get('direction'))}：{clean(event.get('title'))}"
        if event
        else "无新增持仓事件"
    )
    return f"| {c} {x.get('name') or (prow or {}).get('name') or ''} | {tech_state} | {ma_j} | {bbi_state}；{bbi_reminder}；{structure['state']}；{structure['reminder']} | {current_action} | {plan_cell} | {confirm} | {new_evidence} |"


def _plan_only_warning_lines(plan: dict, action_codes: set) -> list[str]:
    """盘后预案在列、盘前动作缺位的票（原 §6「盘前无该票动作条目」口径保留）。"""
    return [
        f"- ⚠️ {code(prow.get('code'))} {prow.get('name')}：盘后预案在列"
        "（触发："
        f"{prow.get('trigger')}）但**盘前无该票动作条目**"
        for prow in plan.get("holding_plans") or []
        if code(prow.get("code")) not in action_codes
    ]


def _premarket_refresh_lines(chief: dict) -> list[str]:
    """盘前信息刷新（隔夜信息后的当前执行规则）。"""
    return [
        "",
        "- 盘前信息刷新："
        f"风控优先={'；'.join(chief.get('allowed_actions') or ['仅观察'])}"
        f"；新开仓={chief.get('new_position_permission', '禁止')}"
        f"；仓位管理=建议 {chief.get('total_position_range', '待确认')}"
        "（持仓快照、目标日行情或市场质量未全部通过时只给方向，不给精确数量）",
        "- 开盘验证：先验证隔夜利好/利空是否被价格与成交确认，再决定是否收紧计划；"
        "利好不得自动放宽权限。",
        f"- 下一验证点：{'；'.join(chief.get('tomorrow_validation') or []) or '无'}",
    ]


def holdings_plan_section(
    chief: dict,
    tech: dict,
    prior_actions: dict,
    holding_event_map: dict,
    prior: dict,
    prior_day: str,
) -> list[str]:
    """§4 持仓与预案确认（v0.100 owner：合并原 §4「持仓状态与上次计划调整」与
    §6「预案确认」——两节都是「盘后计划 vs 盘前动作」的逐票对照，拆开必然重复）。

    数据流：逐票行 = chief.holding_actions（盘前动作）× 前一交易日 final_review 的
    `next_day_plan.holding_plans`（盘后预案：触发/无效条件）；计划级两行
    （总仓位目标/新开仓权限）盘后 vs 盘前刷新值逐条确认。盘后预案缺失时如实报
    （fail-closed 惯例：不编一份预案）。
    """
    plan = (prior or {}).get("next_day_plan") or {}
    plan_by_code = {code(x.get("code")): x for x in plan.get("holding_plans") or []}
    lines = [
        "",
        f"## 4. 持仓与预案确认（上次复盘：{prior_day}）",
        "",
        "> 角色定版（v0.57）：盘前=信息处理 + 预案确认；**明日预案的主产地在"
        "盘后复盘 §6**。本节确认预案在隔夜信息后是否仍成立，不是新出预案。",
        "",
    ]
    lines += _plan_level_lines(plan, chief, prior_day)
    # 逐票：持仓状态 × 盘后预案 × 盘前确认（原 §4 表 + §6 逐票确认合并）
    lines += [
        "",
        "| 代码/名称 | 最新技术状态 | 四均线/J值 | BBI与N型前低 | B1/总控动作 | 盘后预案（触发/无效） | 预案确认 | 隔夜新证据 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    action_codes = set()
    for x in chief.get("holding_actions", []):
        action_codes.add(code(x.get("code")))
        lines.append(
            _holding_action_row(
                x, tech, prior_actions, plan_by_code, holding_event_map, plan
            )
        )
    if not chief.get("holding_actions"):
        lines.append(
            "| - | 持仓数据缺失 | - | BBI/N型前低待确认 | 不提高交易权限 | - | - | - |"
        )
    # 盘后预案在列、盘前动作缺位的票（原 §6「盘前无该票动作条目」口径保留）
    lines += _plan_only_warning_lines(plan, action_codes)
    # 盘前信息刷新（隔夜信息后的当前执行规则）：
    lines += _premarket_refresh_lines(chief)
    return lines


def _gather_inputs(a, day: str) -> dict[str, Any]:
    """输入装载段：ChiefDecision/市场/技术/前次复盘/盘前情报。

    模块级常量 DATA/REVIEWS 与被 monkeypatch 的访问器一律**运行时**读取，
    不得在函数默认值里捕获。
    """
    chief = load(DATA / "decisions" / f"{day}_chief_decision.json", {})
    market = load(DATA / "market" / f"{day}_market_timing_input.json", {})
    technical = load(DATA / "holdings" / f"{day}_holding_technical_summary.json", [])
    tech = {code(x.get("code")): x for x in technical}
    prior = previous_review(day)
    prior_day = prior.get("date", "待确认")
    prior_actions = previous_holding_actions(prior)
    intel_path = premarket_intelligence_path(day)
    intel = load_premarket_intelligence(day)
    intel_check = (
        validate_premarket_intelligence(intel)
        if intel_path
        else {"valid": True, "errors": [], "warnings": []}
    )
    if not intel_check["valid"]:
        intel = {}
    market_events = intel.get("market_events") or fallback_rss_events(day)
    holding_events = intel.get("holding_events") or []
    holding_event_map = {code(x.get("code")): x for x in holding_events}
    window = intel.get("window") or {}
    window_start = window.get("start") or f"{prior_day} 15:00"
    window_end = window.get("end") or f"{a.date} 09:00"
    # 可审计块（原待办 #29，已实现）：本报告实际读过的输入；盘前情报缺失时也登记为缺失项
    audit_inputs = [
        DATA / "decisions" / f"{day}_chief_decision.json",
        DATA / "market" / f"{day}_market_timing_input.json",
        DATA / "trades" / "current_positions.json",
        DATA / "holdings" / f"{day}_holding_technical_summary.json",
        intel_path
        or (DATA / "news" / "premarket" / f"{day}_premarket_intelligence.json"),
    ]
    return {
        "chief": chief,
        "market": market,
        "tech": tech,
        "prior": prior,
        "prior_day": prior_day,
        "prior_actions": prior_actions,
        "intel_path": intel_path,
        "intel_check": intel_check,
        "market_events": market_events,
        "holding_events": holding_events,
        "holding_event_map": holding_event_map,
        "quality": chief.get("market_quality", {}),
        "freshness": chief.get("position_freshness", {}),
        "pgate": chief.get("position_gate", {}),
        "window_start": window_start,
        "window_end": window_end,
        "audit_inputs": audit_inputs,
    }


def _section_overnight_news(
    market_events: list[dict[str, Any]],
    holding_events: list[dict[str, Any]],
    intel_check: dict[str, Any],
) -> list[str]:
    """§2 隔夜重大消息与持仓公告。"""
    lines = [
        "",
        "## 2. 隔夜重大消息与持仓公告",
        "",
        "### 2.1 市场重大消息",
        "",
    ]
    schema_note = premarket_schema_note(intel_check)
    if schema_note:
        lines += [schema_note, ""]
    lines += [
        "| 时间 | 事件 | 方向 | 对A股/持仓的影响 | 来源/质量 |",
        "|---|---|---|---|---|",
    ]
    for e in market_events:
        lines.append(
            f"| {clean(e.get('published_at'))} | {clean(e.get('title'))} | {direction_label(e.get('direction'))} | {clean(e.get('impact'))} | {clean(e.get('source'))}/{clean(e.get('quality'), 'candidate')} |"
        )
    if not market_events:
        lines.append(
            "| - | 信息窗口内未发现达到展示门槛的重大消息 | 中性 | 不据此调整交易计划 | 检索完成 |"
        )
    lines += [
        "",
        "### 2.2 持仓相关消息与公告",
        "",
        "| 代码/名称 | 时间 | 事件 | 方向 | 计划影响 | 来源/质量 |",
        "|---|---|---|---|---|---|",
    ]
    for e in holding_events:
        lines.append(
            f"| {code(e.get('code'))} {clean(e.get('name'))} | {clean(e.get('published_at'))} | {clean(e.get('title'))} | {direction_label(e.get('direction'))} | {clean(e.get('impact'))} | {clean(e.get('source'))}/{clean(e.get('quality'), 'candidate')} |"
        )
    if not holding_events:
        lines.append(
            "| 全部持仓 | - | 信息窗口内未检索到持仓相关公告或高相关消息 | 中性 | 维持上次复盘计划 | 公告检索完成 |"
        )
    return lines


def _section_overseas(overseas: dict[str, Any]) -> list[str]:
    """§3 美国、日本、韩国市场。"""
    lines = [
        "",
        "## 3. 美国、日本、韩国市场",
        "",
        "| 市场 | 指数 | 点位 | 涨跌幅 | 行情性质 | 数据时间 |",
        "|---|---|---:|---:|---|---|",
    ]
    details = overseas.get("details") or {}
    for key, market_name in [
        ("dow", "美国"),
        ("sp500", "美国"),
        ("nasdaq", "美国"),
        ("nikkei", "日本"),
        ("kospi", "韩国"),
    ]:
        item = details.get(key) or {}
        lines.append(
            f"| {market_name} | {clean(item.get('name'))} | {num(item.get('price'))} | {pct_point(item.get('change_pct'))} | {clean(item.get('data_kind'), '待确认')} | {clean(item.get('last_time_local_hint'))} |"
        )
    lines += [
        "",
        f"- 外围综合判断：**{clean(overseas.get('overall_signal'))}**。{clean(overseas.get('overseas_summary'))}",
        "- 美国已收盘数据与日韩开盘后最新数据必须分开标注；缺值不得用历史数据替代。",
    ]
    return lines


def _section_data_freshness(
    day: str,
    chief_path: Path,
    freshness: dict[str, Any],
    quality: dict[str, Any],
    intel_path: Any,
    intel_check: dict[str, Any],
) -> list[str]:
    """§7 数据时效与声明。"""
    snapshot_date = freshness.get("snapshot_date") or "未知"
    return [
        "## 5. 数据时效与声明",
        "",
        f"- ChiefDecision：`{chief_path}`",
        f"- 持仓新鲜度：{freshness.get('status', '未知')}；快照日期 {snapshot_date}；导入时间 {freshness.get('imported_at', '未知')}；源文件时间 {freshness.get('source_mtime', '未知')}",
        f"- 市场质量门：{quality.get('status', '未知')}；candidate/partial/stale/missing 数据不得上调交易权限。",
        f"- 盘前情报：{intel_path or (DATA / 'news' / 'premarket' / f'{day}_premarket_intelligence.json')}；缺失时仅使用RSS候选降级展示。{premarket_schema_marker(intel_check)}",
        "- 本报告仅渲染 ChiefDecision 的最终动作，不以消息、技术指标或上游技能覆盖风险否决。",
        "- 本简报用于策略辅助，不构成收益承诺或无条件交易指令。",
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--data-date")
    ap.add_argument("--session", default="")
    ap.add_argument("--output")
    a = ap.parse_args()
    day = a.data_date or a.date
    dt = datetime.strptime(a.date, "%Y-%m-%d")
    chief_path = DATA / "decisions" / f"{day}_chief_decision.json"
    if not chief_path.exists():
        raise SystemExit(f"mandatory ChiefDecision missing: {chief_path}")
    inp = _gather_inputs(a, day)
    chief = inp["chief"]
    quality = inp["quality"]
    freshness = inp["freshness"]
    pgate = inp["pgate"]
    audit = report_audit.build(a.date, a.session or "premarket", inp["audit_inputs"])
    lines = [
        f"# 每日投研简报｜{dt.year}年{dt.month}月{dt.day}日（星期{WEEKDAY[dt.weekday()]}）"
        + (f"｜{a.session}" if a.session else ""),
        "",
        "> 角色（v0.57 owner 定版）：**盘前=信息处理 + 预案确认** ｜ "
        "盘中14:45=按规则的交易提醒 ｜ 盘后=复盘纠错 + 条件化预案主产地。",
        f"> 信息窗口：{inp['window_start']} 至 {inp['window_end']}（Asia/Shanghai）  ",
        f"> 生成时间：{cn_now().strftime('%Y-%m-%d %H:%M:%S')} Asia/Shanghai",
        *report_audit.render_md(audit),
        "",
        "## 1. 今日核心结论",
        "",
        f"**{chief.get('market_state', '未知')}，总仓位建议 {chief.get('total_position_range', '待确认')}；新开仓权限：{chief.get('new_position_permission', '禁止')}。**",
        "",
        f"- 择时评分：{chief.get('market_score', '待确认')}",
        f"- 风控等级：{chief.get('risk_level', '提高')}",
        f"- 市场数据质量：{quality.get('status', '未知')}（{quality.get('quality_score', 'NA')}）",
        f"- 持仓快照：{freshness.get('status', '未知')}——{freshness.get('reason', '')}",
        f"- 精确数量权限：{'允许' if pgate.get('allow_precise_quantity') else '禁止'}",
    ]
    lines += _section_overnight_news(
        inp["market_events"], inp["holding_events"], inp["intel_check"]
    )
    lines += _section_overseas(inp["market"].get("overseas_market", {}))
    # v0.100（owner）：原 §5（主线题材观察节，口径 TODO #26 待重设计，一直挂着
    # 「仅观察参考」——不下决策的节是噪声）整节下线；原 §4+§6 合并为
    # 「持仓与预案确认」（两节都是盘后计划 vs 盘前动作的逐票对照，拆开必重复）。
    lines += holdings_plan_section(
        chief,
        inp["tech"],
        inp["prior_actions"],
        inp["holding_event_map"],
        inp["prior"],
        inp["prior_day"],
    )
    lines += [
        "",
        *_section_data_freshness(
            day, chief_path, freshness, quality, inp["intel_path"], inp["intel_check"]
        ),
    ]
    out = (
        Path(a.output)
        if a.output
        else daily_report_dir(a.date, PLAN) / f"{a.date}_0905_daily_report.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
