# -*- coding: utf-8 -*-
"""Generate theme_tracker daily sector trend report.

v0.142 起**取消人工主题映射表**（sector_code_map.json 已删除）：
持仓板块由自动解析链驱动（owner 指定 > 行业 > 概念共词优先取大 > 细分行业 > 无映射），
§1「主线」= 当日持仓相关板块中技术分最高者（口径写进 §1，不代表全市场主线）。

Reads:
- governance/strategy/_shared/holding_mainline_overrides.json（owner 持仓主线指定，解析链第①层）
- data/holdings/YYYY-MM-DD_holding_technical_summary.json
- data/holdings/*_holding_sector_mapping.json（≤报告日最近一份，行业层：TDX 行业名→880 行业板块）
- data/sectors/*_tq_sector_map.json（最新一份，概念/细分行业层反向成员关系）
- artifacts/reports/daily/YYYY-MM-DD/YYYY-MM-DD_market_timing_score.md

Writes:
- data/sectors/YYYY-MM-DD_sector_technical_summary.json
- artifacts/reports/daily/YYYY-MM-DD/YYYY-MM-DD_theme_tracker.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


from custos.core.paths import (
    HOLDING_MAINLINE_OVERRIDES_FILE,
    HOLDINGS_DIR,
    MARKET_DIR,
    PLANS,
    SECTORS_DIR,
    daily_report_dir,
)  # noqa: E402
from custos.core.contracts import require  # noqa: E402

SECTOR_DIR = SECTORS_DIR
OUT_DIR = PLANS

from custos.pipeline.market_timing import technical_monitor as tm  # noqa: E402


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def latest_holding_summary(date: str) -> list[dict[str, Any]]:
    p = HOLDINGS_DIR / f"{date}_holding_technical_summary.json"
    return load_json(p, []) or []


def _stage_inputs(
    a: dict[str, Any],
) -> tuple[Any, Any, Any, Any, Any]:
    trend = (a.get("trend") or {}).get("state")
    box20 = a.get("box_20d") or {}
    daily_kdj = (a.get("daily") or {}).get("kdj") or {}
    daily_macd = (a.get("daily") or {}).get("macd") or {}
    weekly_macd = (a.get("weekly") or {}).get("macd") or {}
    pos20 = box20.get("position")
    j = daily_kdj.get("j")
    macd_dir = daily_macd.get("hist_direction")
    weekly_hist = weekly_macd.get("hist")
    return trend, pos20, j, macd_dir, weekly_hist


def _classify_by_trend(trend: Any, pos20: Any, macd_dir: Any) -> tuple[str, str] | None:
    if trend == "上涨" and pos20 == "上沿/突破区" and macd_dir == "扩张":
        return "主升/加速", "趋势上涨、处于20日箱体上沿/突破区，日线MACD扩张。"
    if trend == "上涨":
        return "修复/上行", "趋势上涨，但需观察量能和是否有效突破。"
    if (
        trend == "横盘震荡"
        and pos20 in ("上沿/突破区", "箱体上半区")
        and macd_dir == "扩张"
    ):
        return "修复", "横盘震荡中向箱体上半区修复，日线MACD扩张。"
    if trend == "横盘震荡" and pos20 == "下沿/破位区":
        return "分歧/弱震荡", "横盘震荡但位于箱体下沿，若跌破需转入风控。"
    if trend == "下跌":
        return "退潮/下跌", "趋势下跌，板块不支持加仓。"
    return None


def classify_stage(a: dict[str, Any]) -> tuple[str, str]:
    if not a.get("available"):
        return "数据不足", a.get("error", "无K线数据")
    trend, pos20, j, macd_dir, weekly_hist = _stage_inputs(a)
    by_trend = _classify_by_trend(trend, pos20, macd_dir)
    if by_trend is not None:
        return by_trend
    if isinstance(j, (int, float)) and j > 90:
        return "高位分歧观察", "日线J值高位过热，追高风险上升。"
    if weekly_hist is not None and weekly_hist < 0:
        return "震荡", "日线信号一般，周线动能仍偏弱。"
    return "震荡", "趋势未形成明确主升或退潮，按震荡处理。"


def _trend_delta(state: Any) -> float:
    if state == "上涨":
        return 18
    if state == "下跌":
        return -20
    if state == "横盘震荡":
        return 0
    return 0


def _box_delta(position: Any) -> float:
    if position == "上沿/突破区":
        return 12
    if position == "箱体上半区":
        return 6
    if position == "下沿/破位区":
        return -12
    return 0


def _macd_delta(hist_direction: Any) -> float:
    if hist_direction == "扩张":
        return 8
    if hist_direction == "收缩":
        return -3
    return 0


def _kdj_delta(kdj: dict[str, Any]) -> float:
    j = kdj.get("j")
    if not isinstance(j, (int, float)):
        return 0
    if j > 95:
        return -5
    if j > 80:
        return 2
    if j < 12:
        return -3
    if j < 30 and kdj.get("j", 0) > kdj.get("j_prev", 0):
        return 5
    return 0


def _weekly_delta(hist: Any) -> float:
    if hist is None:
        return 0
    return 4 if hist > 0 else -4


def score_sector(a: dict[str, Any], priority: str) -> float:
    if not a.get("available"):
        return 0.0
    score = 50.0
    trend = a.get("trend") or {}
    box20 = a.get("box_20d") or {}
    kdj = (a.get("daily") or {}).get("kdj") or {}
    macd = (a.get("daily") or {}).get("macd") or {}
    weekly = (a.get("weekly") or {}).get("macd") or {}
    score += _trend_delta(trend.get("state"))
    score += _box_delta(box20.get("position"))
    score += _macd_delta(macd.get("hist_direction"))
    score += _kdj_delta(kdj)
    score += _weekly_delta(weekly.get("hist"))
    if priority == "high":
        score += 3
    return round(max(0, min(100, score)), 2)


def action_bias(stage: str, score: float, market_status: str = "震荡偏弱") -> str:
    if "退潮" in stage or score < 35:
        return "回避/禁止加仓"
    if "主升" in stage and score >= 70 and market_status in ("进攻", "震荡偏强"):
        return "可关注核心股"
    if score >= 65:
        return "观察核心低吸，不追高"
    if score >= 50:
        return "观察"
    return "谨慎观察"


def _sector_analysis_row(
    sector: dict[str, Any], source: str, member_codes: list[str]
) -> dict[str, Any]:
    """板块行：摊平 K 线分析关键字段 + 阶段/分数/操作倾向；K 线缺失标 quote_missing。

    theme_id/theme_name 沿用契约键（sector_technical_summary 有 3 个消费者、
    96 处读 available）——v0.142 起人工主题表删除，theme_id=板块代码、
    theme_name=板块名。板块已定位但行情缺失 ≠ 未定/无映射，文案必须分得开。
    """
    code = str(sector.get("code") or "")
    analysis = tm.analyze(tm.read_vipdoc(code), code)
    stage, reason = classify_stage(analysis)
    score = score_sector(analysis, "")
    trend = analysis.get("trend") or {}
    box20 = analysis.get("box_20d") or {}
    daily = analysis.get("daily") or {}
    weekly = analysis.get("weekly") or {}
    kdj = daily.get("kdj") or {}
    macd = daily.get("macd") or {}
    weekly_macd = weekly.get("macd") or {}
    row = {
        "theme_id": code,
        "theme_name": sector.get("name") or code,
        "primary_code": code,
        "category": sector.get("category"),
        "source": source,
        "representative_stocks": list(member_codes),
        "available": bool(analysis.get("available")),
        "latest_date": analysis.get("latest_date"),
        "trend_state": trend.get("state"),
        "close": trend.get("close"),
        "box20_position": box20.get("position"),
        "box20_upper": box20.get("upper"),
        "box20_lower": box20.get("lower"),
        "daily_j": kdj.get("j"),
        "daily_kdj_state": kdj.get("state"),
        "daily_macd_hist": macd.get("hist"),
        "daily_macd_direction": macd.get("hist_direction"),
        "weekly_macd_hist": weekly_macd.get("hist"),
        "stage": stage,
        "stage_reason": reason,
        "score": score,
        "action_bias": action_bias(stage, score),
        "analysis": analysis,
    }
    if not analysis.get("available"):
        row["quote_missing"] = True
        row["stage"] = "板块行情缺失"
        row["stage_reason"] = (
            f"已按{_SOURCE_LABELS[source]}定位板块 {code}，"
            f"但板块行情缺失（{analysis.get('error', '无K线数据')}）。"
        )
    return row


def build_sector_summary(
    date: str, holding_rows: dict[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """持仓板块技术摘要：同一板块多持仓合并代表股，可用在前、分数降序。"""
    if holding_rows is None:
        holding_rows = resolve_holding_rows(date)
    merged: dict[str, dict[str, Any]] = {}
    for hcode, row in holding_rows.items():
        if not row:
            continue
        code = row["primary_code"]
        if code in merged:
            merged[code]["representative_stocks"].append(hcode)
        else:
            merged[code] = row
    rows = list(merged.values())
    rows.sort(key=lambda r: (r.get("available") is not True, -(r.get("score") or 0)))
    return rows


def _shared_token(name: str, tokens: str) -> bool:
    """板块名与「股票名+行业名」有 ≥2 字公共子串（共词）。"""
    return len(name) >= 2 and any(
        name[i : i + 2] in tokens for i in range(len(name) - 1)
    )


# 持仓板块解析链（v0.145 owner 定稿；人工主题表 sector_code_map.json 已于 v0.142 删除）：
# ⓪ owner 指定层：governance/strategy/_shared/holding_mainline_overrides.json
#    —— owner 对持仓主线的指定，优先级高于一切自动解析；换仓时由 owner 增删
# ① 行业层：pick_industry_sector（holding_sector_mapping 的 tdxhy 行业名 →
#    名称表 tdx_type=2 精确匹配；tq_sector_map 没有行业 category，行业只能走这条）
# ② 概念层：反向成员关系反查，**共词优先**（板块名与股票名+行业名有 ≥2 字公共子串），
#    共词候选取成分股最多者；无共词再取成分股最大者
# ③ 细分行业（sub_industry）兜底
# ④ 都没有 ⇒ 无映射（与「行情缺失」两种文案分开）
# 区域/风格/统计指数不当主线。同层取大（最大共识板块，v0.140 定稿：
# 最具体≠最相关——迷你概念会把持仓从主共识板块上带偏），代码升序兜底确定性。
_SOURCE_LABELS = {
    "owner_override": "owner 指定",
    "industry": "行业映射",
    "reverse_membership": "反向成员关系",
}


def load_mainline_overrides(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """owner 持仓主线指定表 → {code6: {sector_code, sector_name, note, date}}。

    文件缺失返回 {}（行为不变，走自动解析链）。schema 钉测见
    tests/test_theme_tracker_report.py::TestMainlineOverrides。
    """
    data = load_json(path or HOLDING_MAINLINE_OVERRIDES_FILE, {}) or {}
    return dict(data.get("overrides") or {})


def latest_tq_sector_map() -> dict[str, Any]:
    """最新一份板块→成员股反向映射（*_tq_sector_map.json）；没有则返回 {}。"""
    files = sorted(SECTOR_DIR.glob("*_tq_sector_map.json"))
    if not files:
        return {}
    return load_json(files[-1], {}) or {}


def holding_industry_names(date: str) -> dict[str, str]:
    """持仓 → TDX 行业名（tdxhy 口径）。

    取 ≤ 报告日的最近一份 ``*_holding_sector_mapping.json``
    （holding_sector_mapper 每日产；同 positions_history 的回溯语义）。
    tq_sector_map 没有行业层（category 无 industry），行业必须走这份。
    文件缺失返回 {} —— 调用方行为不变（落概念反查）。
    """
    files = [
        f
        for f in sorted(HOLDINGS_DIR.glob("*_holding_sector_mapping.json"))
        if f.name.split("_")[0] <= date
    ]
    if not files:
        return {}
    records = load_json(files[-1], []) or []
    out: dict[str, str] = {}
    for r in records:
        code6 = str(r.get("code") or "").split(".")[0]
        industry = str(r.get("industry") or "").strip()
        if code6 and industry:
            out[code6] = industry
    return out


def pick_industry_sector(
    industry_name: str, sector_map: dict[str, Any]
) -> dict[str, Any] | None:
    """TDX 行业名 → 名称表精确匹配 tdx_type=2 的 880 行业板块。

    名称对不上、或候选板块全无 K 线 ⇒ 返回 None（落概念层，不硬报缺失）。
    同名歧义取「有 K 线且成分股最多」者（成分股数取自 tq_sector_map，缺则 0）。
    """
    from custos.datasource.local_tdx.tq_sector import (  # noqa: PLC0415 惰性：与 final_close_review 同例
        load_sector_names,
    )

    if not industry_name:
        return None
    name_map = load_sector_names()
    candidates = [
        code
        for code, info in name_map.items()
        if info.get("name") == industry_name and info.get("tdx_type") == "2"
    ]
    if not candidates:
        return None
    live = [c for c in candidates if not tm.read_vipdoc(f"{c}.SH").empty]
    if not live:
        return None
    counts = {
        str(s.get("code") or "").split(".")[0]: s.get("stock_count") or 0
        for s in (sector_map.get("sectors") or [])
    }
    best = max(live, key=lambda c: (counts.get(c, 0), c))
    return {
        "code": f"{best}.SH",
        "name": industry_name,
        "category": "industry",
        "stock_count": counts.get(best, 0),
    }


def pick_holding_sector(
    code: str, sector_map: dict[str, Any], name_tokens: str = ""
) -> dict[str, Any] | None:
    """概念/细分行业层：按反向成员关系为持仓选板块。

    概念层**共词优先**（板块名与「股票名+行业名」有 ≥2 字公共子串，如
    「融发核电」∩「核电核能」=「核电」）：共词候选取成分股最多者；
    无共词候选再取成分股最大者（v0.142 owner 定稿——机器人概念 1209 只
    这种泛化大板块不许盖过共词命中的核电核能）。细分行业兜底；
    区域/风格/统计指数不当主线，只剩这些时返回 None（= 无映射，
    区别于「有映射但行情缺失」）。代码升序兜底保证确定性。
    """
    code6 = str(code).split(".")[0]
    hits = [
        s
        for s in (sector_map.get("sectors") or [])
        if code6 in {str(x).split(".")[0] for x in s.get("stocks") or []}
    ]
    concepts = [s for s in hits if s.get("category") == "concept"]
    if concepts:
        shared = [
            s for s in concepts if _shared_token(str(s.get("name") or ""), name_tokens)
        ]
        pool = shared or concepts
        return min(
            pool, key=lambda s: (-(s.get("stock_count") or 0), str(s.get("code")))
        )
    subs = [s for s in hits if s.get("category") == "sub_industry"]
    if subs:
        return min(
            subs, key=lambda s: (-(s.get("stock_count") or 0), str(s.get("code")))
        )
    return None


def resolve_holding_sector(
    holding: dict[str, Any],
    sector_map: dict[str, Any],
    industry_name: str | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str] | None:
    """解析链：owner 指定 > 行业 > 概念（共词优先取大）> 细分行业；都无 ⇒ None（无映射）。

    ``overrides=None`` 表示无指定（纯函数语义，便于测试）；真实指定表由
    ``resolve_holding_rows`` 显式加载后传入。
    """
    code6 = str(holding.get("code") or "").split(".")[0]
    ov = (overrides or {}).get(code6)
    if ov:
        return {
            "code": ov["sector_code"],
            "name": ov.get("sector_name") or ov["sector_code"],
            "category": "owner_override",
            "note": ov.get("note", ""),
        }, "owner_override"
    tokens = str(holding.get("name") or "") + (industry_name or "")
    if industry_name:
        sector = pick_industry_sector(industry_name, sector_map)
        if sector is not None:
            return sector, "industry"
    sector = pick_holding_sector(code6, sector_map, tokens)
    if sector is not None:
        return sector, "reverse_membership"
    return None


def resolve_holding_rows(date: str) -> dict[str, dict[str, Any]]:
    """每持仓 → 板块行；解析不出板块的持仓值为 {}（§4 如实显示「未定/无映射」）。"""
    holdings = latest_holding_summary(date)
    if not holdings:
        return {}
    sector_map = latest_tq_sector_map()
    industry_names = holding_industry_names(date)
    overrides = load_mainline_overrides()
    out: dict[str, dict[str, Any]] = {}
    for h in holdings:
        code6 = str(h.get("code") or "").split(".")[0]
        resolved = resolve_holding_sector(
            h, sector_map, industry_names.get(code6), overrides
        )
        if resolved is None:
            out[str(h.get("code"))] = {}
            continue
        sector, source = resolved
        out[str(h.get("code"))] = _sector_analysis_row(sector, source, [code6])
    return out


def compare_holding_to_theme(
    holding: dict[str, Any], theme: dict[str, Any]
) -> tuple[str, str]:
    ht = holding.get("trend_state")
    tt = theme.get("trend_state")
    hp = holding.get("box20_position")
    tp = theme.get("box20_position")
    if theme and theme.get("quote_missing"):
        return "板块行情缺失", theme.get(
            "stage_reason", "板块已定位但行情缺失，无法对比。"
        )
    if not theme or not theme.get("available"):
        return "未定", "板块数据不足。"
    rank = {"上涨": 3, "横盘震荡": 2, "下跌": 1, None: 0}
    if rank.get(ht, 0) > rank.get(tt, 0):
        return "强于板块", f"个股趋势{ht}，板块趋势{tt}。"
    if rank.get(ht, 0) < rank.get(tt, 0):
        return "弱于板块", f"个股趋势{ht}，板块趋势{tt}。"
    if hp == "下沿/破位区" and tp != "下沿/破位区":
        return "弱于板块", f"个股在{hp}，板块在{tp}。"
    if hp in ("上沿/突破区", "箱体上半区") and tp in ("箱体下半区", "下沿/破位区"):
        return "强于板块", f"个股在{hp}，板块在{tp}。"
    return "同步", f"个股与板块均为{ht}/{tt}，箱体位置 {hp}/{tp}。"


def _section_mainline(date: str, top: dict[str, Any]) -> list[str]:
    """§1 今日主线（含报告头）。

    v0.142 口径：人工主题表已删除，「主线」= 当日持仓相关板块
    （行业>概念>细分四层自动解析）中技术分最高者——只覆盖持仓相关板块，
    不代表全市场主线；无可用板块时如实「未定」，不编主线。
    """
    lines = []
    lines.append("# theme_tracker 主线与板块跟踪\n")
    lines.append(f"日期：{date}\n")
    lines.append("## 1. 今日主线\n")
    mainline = top.get("theme_name") or "未定"
    lines.append(f"- 主线方向：**{mainline}**")
    lines.append(f"- 生命周期：**{top.get('stage', '未定')}**")
    lines.append(
        f"- 主线强度：**{'强' if (top.get('score') or 0) >= 75 else '中' if (top.get('score') or 0) >= 55 else '弱'}**"
    )
    evidence = (
        f"{top.get('stage_reason', '无')}；技术分 {top.get('score', 'NA')}。"
        if top
        else "无可用持仓板块（持仓均无板块映射或板块行情缺失），本节如实降级。"
    )
    lines.append(f"- 关键证据：{evidence}")
    lines.append(
        "- 口径：主线=持仓相关板块中技术分最高者（行业>概念>细分自动解析），"
        "仅覆盖持仓相关板块，不代表全市场主线。"
    )
    lines.append(
        "- 市场约束：market_timing 仍为震荡偏弱，允许低吸核心主线，但不支持追高和高频试错。\n"
    )
    return lines


def _section_strong(strong: list[dict[str, Any]]) -> list[str]:
    """§2 强势/可关注板块。"""
    lines = []
    lines.append("## 2. 强势/可关注板块\n")
    lines.append("| 板块 | 代码 | 状态 | 分数 | 代表股票 | 证据 | 风险 |")
    lines.append("|---|---|---|---:|---|---|---|")
    for r in strong[:8]:
        reps = ", ".join(r.get("representative_stocks", [])[:4])
        risk_note = (
            "J值过热需防追高"
            if isinstance((dj := r.get("daily_j")), (int, float)) and dj > 90
            else "大盘震荡偏弱，低吸优先"
        )
        lines.append(
            f"| {r.get('theme_name')} | {r.get('primary_code')} | {r.get('stage')} | {r.get('score')} | {reps} | {r.get('stage_reason')} | {risk_note} |"
        )
    if not strong:
        lines.append("| 无 | - | - | - | - | 当前无分数>=65的板块 | - |")
    lines.append("")
    return lines


def _section_risk(risk: list[dict[str, Any]]) -> list[str]:
    """§3 退潮/风险板块。"""
    lines = []
    lines.append("## 3. 退潮/风险板块\n")
    lines.append("| 板块 | 代码 | 风险状态 | 分数 | 风险原因 |")
    lines.append("|---|---|---|---:|---|")
    for r in risk[:8]:
        lines.append(
            f"| {r.get('theme_name')} | {r.get('primary_code', '')} | {r.get('stage', '数据不足')} | {r.get('score', 0)} | {r.get('stage_reason', r.get('reason', ''))} |"
        )
    if not risk:
        lines.append("| 无明显 | - | - | - | - |")
    lines.append("")
    return lines


def _section_holdings(
    holdings: list[dict[str, Any]], holding_themes: dict[str, dict[str, Any]]
) -> list[str]:
    """§4 持仓板块跟踪。"""
    lines = []
    lines.append("## 4. 持仓板块跟踪\n")
    lines.append(
        "| 代码 | 名称 | 最相关主线 | 板块状态 | 板块分数 | 个股相对板块 | 操作倾向 |"
    )
    lines.append("|---|---|---|---|---:|---|---|")
    for h in holdings:
        code = str(h.get("code"))
        theme = holding_themes.get(code) or {}
        rel, rel_reason = compare_holding_to_theme(h, theme)
        action = h.get("action") or theme.get("action_bias") or "观察"
        if rel == "弱于板块" and action == "观察":
            action = "风控观察"
        theme_name = theme.get("theme_name", "未定")
        source = theme.get("source")
        if source in _SOURCE_LABELS:
            theme_name += {"owner_override": "（指定）", "industry": "（行业）"}.get(
                source, "（反查）"
            )
        lines.append(
            f"| {code} | {h.get('name')} | {theme_name} | {theme.get('stage', '未定')} | {theme.get('score', 0)} | {rel}：{rel_reason} | {action} |"
        )
    lines.append("")
    return lines


def _section_market_consistency(
    date: str,
    market_status: str,
    strong: list[dict[str, Any]],
    risk: list[dict[str, Any]],
) -> list[str]:
    """§5 板块-大盘一致性。"""
    lines = []
    lines.append("## 5. 板块-大盘一致性\n")
    market = load_json(MARKET_DIR / f"{date}_market_timing_input.json", {}) or {}
    amv = market.get("amv_0", {})
    lines.append(
        f"- 大盘状态：{market_status}；0AMV当日 {amv.get('amv_change_pct', '缺失')}%，有效状态 **{amv.get('effective_state', amv.get('amv_zone', '未知'))}**。"
    )
    lines.append(
        "- 强于大盘的板块："
        + (
            "、".join([n for r in strong[:5] if (n := r.get("theme_name")) is not None])
            if strong
            else "暂不明确"
        )
    )
    weak_names = [
        n
        for r in risk[:5]
        if r.get("available") and (n := r.get("theme_name")) is not None
    ]
    lines.append(
        "- 弱于大盘/需回避板块："
        + ("、".join(weak_names) if weak_names else "暂无明确退潮，但低分板块需谨慎")
    )
    lines.append(
        "- 结构性机会仅来自上表中强于市场且获得交易许可的板块；低分或退潮方向不因长期逻辑直接加仓。\n"
    )
    return lines


def _section_chief_conclusion(
    strong: list[dict[str, Any]],
    holdings: list[dict[str, Any]],
    holding_themes: dict[str, dict[str, Any]],
) -> list[str]:
    """§6 给总控的结论。"""
    lines = []
    lines.append("## 6. 给总控的结论\n")
    focus = [n for r in strong[:3] if (n := r.get("theme_name")) is not None]
    lines.append("- 可关注方向：" + ("、".join(focus) if focus else "无明确可进攻方向"))
    lines.append("- 禁止方向：下跌/低分板块、弱于板块的个股、箱体破位个股。")
    weak_holdings = [
        str(h.get("name"))
        for h in holdings
        if compare_holding_to_theme(h, holding_themes.get(str(h.get("code"))) or {})[0]
        == "弱于板块"
    ]
    lines.append(
        "- 持仓需要重点风控："
        + (
            "、".join(weak_holdings)
            if weak_holdings
            else "按 portfolio_review 与 risk_control 动态识别。"
        )
    )
    lines.append("- 是否允许新开相关方向：仅允许核心主线小仓低吸观察；禁止追高接力。\n")
    lines.append(
        "> 风险提示：板块强弱是交易过滤器，不是直接买入信号；真实交易仍需 stock_pool、buy_strategy、risk_control、chief_decision 全链路确认。"
    )
    return lines


def make_report(
    date: str,
    rows: list[dict[str, Any]],
    holding_rows: dict[str, dict[str, Any]] | None = None,
) -> str:
    holdings = latest_holding_summary(date)
    market_status = "震荡偏弱"
    strong = [r for r in rows if r.get("available") and (r.get("score") or 0) >= 65]
    risk = [
        r
        for r in rows
        if (not r.get("available"))
        or "退潮" in str(r.get("stage"))
        or (r.get("score") or 0) < 45
    ]
    # §1 主线 = 第一个可用持仓板块（rows 已按可用+分数排序）；全不可用则如实降级
    top = next((r for r in rows if r.get("available")), {})
    if holding_rows is None:
        holding_rows = resolve_holding_rows(date)
    lines = (
        _section_mainline(date, top)
        + _section_strong(strong)
        + _section_risk(risk)
        + _section_holdings(holdings, holding_rows)
        + _section_market_consistency(date, market_status, strong, risk)
        + _section_chief_conclusion(strong, holdings, holding_rows)
    )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=pd.Timestamp.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    holding_rows = resolve_holding_rows(args.date)
    rows = build_sector_summary(args.date, holding_rows)
    SECTOR_DIR.mkdir(parents=True, exist_ok=True)
    out_dir = daily_report_dir(args.date, OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = SECTOR_DIR / f"{args.date}_sector_technical_summary.json"
    report_path = out_dir / f"{args.date}_theme_tracker.md"
    # ⚠️ 落盘前校验：3 个消费者、⛔硬失败链。消费端有 **96 处 `.get("available")`**
    # —— 那个布尔是全项目最常被读的分支键，必须保证它是真布尔。
    require("sector_technical_summary", rows)
    summary_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    report = make_report(args.date, rows, holding_rows)
    report_path.write_text(report, encoding="utf-8")
    print(summary_path)
    print(report_path)
    print(report[:5000])


if __name__ == "__main__":
    main()
