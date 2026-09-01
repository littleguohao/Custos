# -*- coding: utf-8 -*-
"""Generate theme_tracker daily sector technical summary (JSON only).

v0.142 起**取消人工主题映射表**（sector_code_map.json 已删除）；v0.149 起
**owner 指定层也撤掉**（holding_mainline_overrides.json 已删除）——
持仓板块归属只有**走势贴合**一档（60 日日收益 Pearson，贴合最高者胜；
贴合无有效数据如实「未定」，无兜底猜谜）。
v0.156（owner 拍板 2026-08-28）候选侧人工主题匹配链（enrich_candidates
build_stock_theme_map）也随之整段废弃——人工判断路径全仓零残留，
记录在案见 governance/data/TDX_LOCAL_INTERFACES.md §3。
v0.162 起人读的 `theme_tracker.md` 停产：强势/退潮板块展示并入
chief_decision.md §3（`_section_strong`/`_section_risk` 被
chief_decision_report 复用）；本脚本只产结构化 JSON（3 个消费者）。

Reads:
- data/holdings/YYYY-MM-DD_holding_technical_summary.json
- data/holdings/*_holding_sector_mapping.json（≤报告日最近一份，行业名→880 行业板块贴合候选）
- data/sectors/*_tq_sector_map.json（最新一份，概念/细分反向成员关系贴合候选）

Writes:
- data/sectors/YYYY-MM-DD_sector_technical_summary.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


from custos.core.paths import (  # noqa: E402
    HOLDINGS_DIR,
    SECTORS_DIR,
)
from custos.core.contracts import require  # noqa: E402
from custos.core.code_utils import market_of  # noqa: E402

SECTOR_DIR = SECTORS_DIR

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
    """板块行：摊平 K 线分析关键字段 + 阶段/分数/操作倾向。

    theme_id/theme_name 沿用契约键（sector_technical_summary 有 3 个消费者、
    96 处读 available）——v0.142 起人工主题表删除，theme_id=板块代码、
    theme_name=板块名。贴合选出的板块必然有 K 线（贴合有效的前提），
    分析不可用只是防御性分支（classify_stage 如实报「数据不足」）。
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
        "fit": sector.get("fit"),
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
    return row


def build_sector_summary(
    date: str, holding_rows: dict[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """持仓板块技术摘要：同一板块多持仓合并代表股，可用在前、分数降序。"""
    if holding_rows is None:
        holding_rows = resolve_holding_rows(date)
    merged: dict[str, dict[str, Any]] = {}
    for hcode, row in holding_rows.items():
        if not row or "primary_code" not in row:
            # {}（无映射）与 {"fit_insufficient": True}（贴合不足）都不是板块行
            continue
        code = row["primary_code"]
        if code in merged:
            merged[code]["representative_stocks"].append(hcode)
        else:
            merged[code] = row
    rows = list(merged.values())
    rows.sort(key=lambda r: (r.get("available") is not True, -(r.get("score") or 0)))
    return rows


# 持仓板块解析（v0.149 owner 定稿：指定层也撤掉，全部按贴合）：
# 只有**走势贴合**一档——全部所属板块（反向成员关系命中的 概念/细分 +
# tdxhy 行业名匹配出的 880 行业板块）入池，有 K 线且贴合有效者取相关最高
# （`_sector_fit_map` 60 日日收益 Pearson、inner join ≥20 根）；
# 贴合无有效数据（全候选 <20 根/无 K 线/个股无 K 线）⇒ 未定，如实报
# 「贴合数据不足」，与「无映射」区分开——不猜（分层兜底链 v0.148 已删，
# owner 指定层 v0.149 已删）。区域/风格/统计指数不当主线（也不入贴合池）。


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
    industry_name: str,
    sector_map: dict[str, Any],
    cache: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """TDX 行业名 → 名称表精确匹配 tdx_type=2 的 880 行业板块。

    名称对不上、或候选板块全无 K 线 ⇒ 返回 None（落概念层，不硬报缺失）。
    同名歧义取「有 K 线且成分股最多」者（成分股数取自 tq_sector_map，缺则 0）。
    ``cache``（贴合收益缓存，v0.147）让 K 线存在性检查不重读板块文件——
    与 ``_sector_fit`` 共用同一缓存（≥20 根收益才算有行情）。
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
    live = [c for c in candidates if _cached_returns(f"{c}.SH", cache) is not None]
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


def _cached_returns(code: str, cache: dict[str, Any] | None) -> Any:
    """读板块 60 日收益并（可选）写入缓存；无缓存时直读。"""
    if cache is not None and code in cache:
        return cache[code]
    ret = _daily_returns(tm.read_vipdoc(code), 60)
    if cache is not None:
        cache[code] = ret
    return ret


def _daily_returns(df: Any, window: int) -> Any:
    """日收益序列（截尾 window 根）；不足 20 根或缺 date/close 列返回 None。"""
    if df is None or getattr(df, "empty", True):
        return None
    if "date" not in df.columns or "close" not in df.columns:
        return None
    ret = df.set_index("date")["close"].pct_change().dropna().tail(window)
    return ret if len(ret) >= 20 else None


def _sector_fit_map(
    stock_code: str,
    candidate_codes: list[str],
    window: int = 60,
    cache: dict[str, Any] | None = None,
) -> dict[str, float]:
    """个股与候选板块指数的 60 日日收益 Pearson 相关 → {板块码: 相关系数}。

    只含贴合有效者（inner join 后 ≥20 根）；个股无 K 线或全部候选数据不足 ⇒ {}。
    ``cache``（{板块码: 收益序列或 None}）让同一报告内
    每个板块文件只读一次（持仓 ≤10 × 候选 ≤20，逐对算可接受，重读文件不行）。

    ⚠️ NaN 守卫（2026-08-31 review 低优先项）：任一侧收益序列方差为 0
    （停牌后恢复交易的常数段等）时 Pearson=NaN —— NaN 相关=数据无效，不进候选；
    否则 ``max()`` 遇 NaN 选择不确定，且 NaN 会漏进 JSON 落盘（json 默认放行 NaN）。
    """
    stock_ret = _daily_returns(tm.read_vipdoc(stock_code), window)
    if stock_ret is None:
        return {}
    out: dict[str, float] = {}
    for c in candidate_codes:
        sret = _cached_returns(c, cache)
        if sret is None:
            continue
        joined = pd.concat([stock_ret, sret], axis=1, join="inner").dropna()
        if len(joined) < 20:
            continue
        r = float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))
        if math.isnan(r):
            continue
        out[c] = r
    return out


def _fit_pool_sectors(
    code6: str, sector_map: dict[str, Any], industry_sector: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """贴合池（v0.147）：反向成员关系命中的 概念/细分 全部 + 行业名匹配出的
    行业板块——不再分层，贴合就是唯一标准。区域/风格/统计指数不入池。"""
    pool = [
        s
        for s in (sector_map.get("sectors") or [])
        if s.get("category") in ("concept", "sub_industry")
        and code6 in {str(x).split(".")[0] for x in s.get("stocks") or []}
    ]
    if industry_sector is not None and all(
        str(s.get("code")) != industry_sector["code"] for s in pool
    ):
        pool.append(industry_sector)
    return pool


def resolve_holding_sector(
    holding: dict[str, Any],
    sector_map: dict[str, Any],
    industry_name: str | None = None,
    fit_cache: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """解析（v0.149 owner 定稿）：只有走势贴合一档，没有指定/兜底。

    返回 (sector, status)：status ∈ fit / no_mapping（无候选板块）
    / fit_insufficient（有候选但贴合全无效）。sector 为 None 时是后两种。
    """
    code6 = str(holding.get("code") or "").split(".")[0]
    industry_sector = (
        pick_industry_sector(industry_name, sector_map, fit_cache)
        if industry_name
        else None
    )
    pool = _fit_pool_sectors(code6, sector_map, industry_sector)
    suffix = market_of(code6)
    fits = (
        _sector_fit_map(
            f"{code6}.{suffix}", [str(s.get("code")) for s in pool], cache=fit_cache
        )
        if suffix and pool
        else {}
    )
    if not pool:
        return None, "no_mapping"
    if not fits:
        return None, "fit_insufficient"
    best_code = max(fits, key=lambda c: fits[c])
    chosen = next(s for s in pool if str(s.get("code")) == best_code)
    return {**chosen, "fit": round(fits[best_code], 3)}, "fit"


def resolve_holding_rows(date: str) -> dict[str, dict[str, Any]]:
    """每持仓 → 板块行；解析不出板块的持仓：无映射为 {}、贴合无有效数据为
    {"fit_insufficient": True}（§4 分别如实显示「未定/无映射」「未定/贴合数据不足」）。"""
    holdings = latest_holding_summary(date)
    if not holdings:
        return {}
    sector_map = latest_tq_sector_map()
    industry_names = holding_industry_names(date)
    fit_cache: dict[str, Any] = {}  # 同一报告内板块收益序列只读一次
    out: dict[str, dict[str, Any]] = {}
    for h in holdings:
        code6 = str(h.get("code") or "").split(".")[0]
        sector, source = resolve_holding_sector(
            h, sector_map, industry_names.get(code6), fit_cache
        )
        if sector is None:
            out[str(h.get("code"))] = (
                {"fit_insufficient": True} if source == "fit_insufficient" else {}
            )
            continue
        out[str(h.get("code"))] = _sector_analysis_row(sector, source, [code6])
    return out


def _section_strong(
    strong: list[dict[str, Any]], heading: str = "## 2. 强势/可关注板块"
) -> list[str]:
    """强势/可关注板块表（v0.162 起被 chief_decision_report §3 复用，heading 可换）。"""
    lines = []
    lines.append(heading + "\n")
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


def _section_risk(
    risk: list[dict[str, Any]], heading: str = "## 3. 退潮/风险板块"
) -> list[str]:
    """退潮/风险板块表（v0.162 起被 chief_decision_report §3 复用，heading 可换）。"""
    lines = []
    lines.append(heading + "\n")
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=pd.Timestamp.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    holding_rows = resolve_holding_rows(args.date)
    rows = build_sector_summary(args.date, holding_rows)
    SECTOR_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = SECTOR_DIR / f"{args.date}_sector_technical_summary.json"
    # ⚠️ 落盘前校验：3 个消费者、⛔硬失败链。消费端有 **96 处 `.get("available")`**
    # —— 那个布尔是全项目最常被读的分支键，必须保证它是真布尔。
    require("sector_technical_summary", rows)
    summary_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(summary_path)


if __name__ == "__main__":
    main()
