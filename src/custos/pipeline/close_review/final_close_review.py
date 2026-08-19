# -*- coding: utf-8 -*-
"""Final close review with news, market, theme, holdings and execution audit."""

from __future__ import annotations

import argparse
import json
from typing import Optional

from custos.pipeline.holdings.b1_holding_state import evaluate as evaluate_b1_holding

from custos.pipeline.close_review.holding_bbi import intraday_bbi_basis
from custos.pipeline.close_review.holding_structure import n_structure_basis

from custos.core.paths import cn_now, DATA, REVIEWS, daily_report_dir  # noqa: E402
from custos.pipeline.close_review.loss_streak import format_lines as loss_streak_lines  # noqa: E402
from custos.pipeline.close_review.loss_streak import loss_streaks  # noqa: E402
from custos.pipeline.close_review.cooldowns import (  # noqa: E402
    format_cooldown_lines,
    stop_cooldowns,
)

# ⚠️ 台账解析与 FIFO 配平的唯一实现在 `weekly_review` —— 这里**单向**依赖它。
#    曾想把「加载→配平→连亏」包进 `loss_streak.from_ledger()` 做统一入口，
#    但 `weekly_review` 已经导入 `loss_streak` ⇒ 那会造成
#    `loss_streak ↔ weekly_review` 循环（`test_no_unexpected_cycles` 当场拦下）。
#    改成各调用方自己加载：这里两行、周报本来就有 `closings_all`，
#    共享的是 `parse_ledger`/`fifo_pair`/`loss_streaks` 三个函数，没有重复实现。
from custos.pipeline.close_review.weekly_review import fifo_pair, parse_ledger  # noqa: E402
from custos.core import report_audit  # noqa: E402
from custos.core.code_utils import market_of  # noqa: E402
from custos.core.paths import read_json as load  # noqa: E402
from custos.core.code_utils import bare_code as bare  # noqa: E402
from custos.core.code_utils import finite  # noqa: E402
from custos.core.code_utils import fnum as optional_finite  # noqa: E402
from custos.core.fmt import pct_text  # noqa: E402
from custos.core.contracts import require  # noqa: E402

REV = REVIEWS / "daily"


def ma_flag(value) -> str:
    """均线上下标记；`None` 渲染 `?` 而**不是「下」**。

    ⚠️ 2026-08-07 修：原写法 `"上" if value else "下"` 把 `None` 当假值 ⇒
    渲染成「下MA240」。而上游是**刻意**给 None 的：

        refresh_market_indices:124   `bool(close > ma240) if ma240 else None`
        market_timing_collector:129  同上
        technical_monitor:566        `c > ma240v if ma240v is not None else None`

    即**历史不足 240 日**（新股/次新）时根本算不出 MA240。
    把它显示成「下MA240」是一个未被支持的事实断言，而且方向偏空 ——
    同 `fmt.pct_text` 那条教训：不能把「不知道」渲染成一个具体读数。
    """
    if value is None:
        return "?"
    return "上" if value else "下"


def render_index_row(row: dict) -> str:
    """3.1 指数结构表的一行。"""
    close = row.get("close")
    close_text = "unavailable" if close is None else f"{close}"
    return (
        f"| {row['name']} | {close_text} | {pct_text(row.get('change_pct'))} | "
        f"{ma_flag(row.get('above_ma25'))}MA25 / {ma_flag(row.get('above_ma60'))}MA60 / "
        f"{ma_flag(row.get('above_ma144'))}MA144 / {ma_flag(row.get('above_ma240'))}MA240 |"
    )


def index_name(code):
    if code.startswith("688"):
        return "科创50（市场风格代理）"
    if code.startswith(("300", "301")):
        return "创业板指（市场风格代理）"
    market = market_of(code)
    if market == "BJ":
        return "北证50（市场风格代理）"
    if market == "SH":
        return "上证指数（市场风格代理）"
    return "深证成指（市场风格代理）"


def sector_for(code, sectors):
    for sector in sectors:
        linked = [
            bare(x)
            for x in (sector.get("holding_related") or [])
            + (sector.get("representative_stocks") or [])
        ]
        if code in linked:
            return sector
    return {}


def render_news(lines, news, hold_codes=None, hold_sectors=None):
    """§2（v0.57 角色定版）：盘后=复盘纠错+预案主产地 ⇒ 新闻节压缩为
    「与今日操作相关的事实核对」——只留与当日持仓/当日成交有交集的事实
    （matched_codes 命中代码 或 matched_themes 命中持仓板块），
    信息流式的全量罗列去掉。缺数据照常如实报（不静默）。

    ⚠️ 键的形状（2026-08-14 修）：生产者 postclose_news_digest 落的是
    `matched_holdings`=股票**名称**、`matched_codes`=**代码**——代码交集必须
    用 `matched_codes`（此前错用 matched_holdings 跟持仓代码相交，生产上恒为空，
    等于只剩主题一条腿、按代码命中的新闻被静默漏报）。fallback 到
    matched_holdings 只为兼容「直接装代码」的旧形状/手工构造输入。"""
    lines += [
        "",
        "## 2. 新闻、政策、风向与舆情（与今日操作相关的事实核对）",
        "",
        "> 复盘视角：本节只核对**与今日持仓/操作有交集**的新闻事实；"
        "信息流全量罗列已压缩（角色定版 v0.57）。",
        "",
    ]
    sections = news.get("sections") or {}
    hold_codes = set(hold_codes or [])
    hold_sectors = set(hold_sectors or [])
    rows = []
    for name in ("信息", "政策", "风向", "舆情"):
        for row in sections.get(name) or []:
            mc = set(row.get("matched_codes") or row.get("matched_holdings") or [])
            mt = set(row.get("matched_themes") or [])
            if (mc & hold_codes) or (mt & hold_sectors):
                rows.append((name, mc & hold_codes, mt & hold_sectors, row))
    if not sections:
        lines.append("- `unavailable`：当前窗口没有通过时效和来源质量门的证据。")
    elif not rows:
        lines.append("- 窗口内证据无与今日持仓/操作的交集（无需核对）。")
    else:
        lines += [
            "| 类别 | 时间 | 事件 | 来源/质量 | 与今日操作的交集 | 交易含义 |",
            "|---|---|---|---|---|---|",
        ]
        for name, codes_hit, themes_hit, row in rows[:8]:  # 有界：最多 8 条
            intersection = "、".join(sorted(codes_hit) + sorted(themes_hit))
            lines.append(
                f"| {name} | {row.get('published_at')} | {row.get('title')} | {row.get('source_name')}/{row.get('fact_status')} | {intersection} | {row.get('trade_meaning')} |"
            )
    if news.get("missing"):
        lines.append("\n- 新闻数据缺失：" + "、".join(news["missing"]))


def revalue_positions(
    day, ff_map, mfe_map, pmap, qmap, regime, sectors, tmap, total_assets
):
    """按当日行情重估每只持仓，返回逐票字典列表。

    2026-08-07 从 `main`（原 210 行）抽出。抽的是**数据计算**，与下面的
    `render_*` 分开 —— 重估逻辑因此可以单测，不必先铺一整份报告的上游产物。
    """
    revalued = []
    for code, position in pmap.items():
        technical = tmap.get(code, {})
        quote = qmap.get(code, {})
        close = optional_finite(quote.get("close", quote.get("price")))
        quantity = finite(position.get("持有数量"))
        cost = finite(position.get("单位成本"))
        market_value = close * quantity if close is not None else None
        pnl_pct = close / cost - 1 if close is not None and cost else None
        sector = sector_for(code, sectors)
        b1 = evaluate_b1_holding(
            {**technical, "holding_pnl_pct": pnl_pct},
            regime,
            close,
            quote.get("date") or day,
        )
        revalued.append(
            {
                "code": code,
                "name": position.get("名称"),
                "quantity": quantity,
                "cost": cost,
                "close": close,
                "price_date": quote.get("date"),
                "price_time": quote.get("time"),
                "technical_date": technical.get("latest_date"),
                "market_value": market_value,
                "pnl_pct": pnl_pct,
                "position_pct": market_value / total_assets
                if market_value is not None and total_assets
                else None,
                "trend": technical.get("trend_state"),
                "box": technical.get("box20_position"),
                "bbi": intraday_bbi_basis(
                    technical, close, technical.get("latest_date")
                ),
                "n_structure": n_structure_basis(technical, close),
                "b1_holding_state": b1,
                "sector": sector,
                "index": index_name(code),
                "mfe_pct": mfe_map.get(code, {}).get("mfe_pct"),
                "mae_pct": mfe_map.get(code, {}).get("mae_pct"),
                "main_net_inflow": ff_map.get(code, {}).get("main_net_inflow"),
                "main_net_pct": ff_map.get(code, {}).get("main_net_pct"),
            }
        )
    return revalued


def index_rows(market):
    """指数结构表的数据行（不含渲染）。"""
    indices = []
    for name, row in market.get("a_share_indices", {}).items():
        if not isinstance(row, dict) or not row.get("available", True):
            continue
        intraday = row.get("intraday") or {}
        # Prefer intraday change; fallback to daily_change_pct from vipdoc K-line
        change_pct = intraday.get("intraday_change_pct")
        if change_pct is None:
            change_pct = row.get("daily_change_pct")
        indices.append(
            {
                "name": name,
                "close": intraday.get("now", row.get("latest_close")),
                "change_pct": change_pct,
                "above_ma25": row.get("above_ma25"),
                "above_ma60": row.get("above_ma60"),
                "above_ma144": row.get("above_ma144"),
                "above_ma240": row.get("above_ma240"),
            }
        )
    return indices


def render_execution_rows(lines, execution):
    """§1 今日计划/建议/实际执行 的表格行（表头在 `lines` 初始化时给出）。"""
    for row in execution.get("rows") or []:
        actual = (
            "无成交"
            if not row.get("actual_trades")
            else "；".join(
                f"{x.get('交易类别')} {x.get('成交数量')}股@{x.get('成交价格')}"
                for x in row["actual_trades"]
            )
        )
        lines.append(
            f"| {row.get('code')} | {row.get('name')} | {row.get('premarket_action')}（参考：{row.get('premarket_reference_action')}） | {row.get('tail_priority')} {row.get('tail_action')} | {actual} | {row.get('execution_reason')} |"
        )


def render_market(lines, checks, chief, indices):
    """§3 大盘、资金与市场许可。"""
    lines += [
        "",
        "## 3. 大盘、资金与市场许可",
        "",
        "### 3.1 指数结构",
        "",
        "| 指数 | 收盘/最新 | 当日涨跌 | MA25/60/144/240状态 |",
        "|---|---:|---:|---|",
    ]
    for row in indices:
        lines.append(render_index_row(row))
    lines += ["", "### 3.2 宽度、成交与情绪", ""]
    for field, label in (
        ("market_breadth", "市场宽度"),
        ("turnover", "全市场成交额"),
        ("sentiment", "涨跌停与情绪"),
    ):
        check = checks.get(field, {})
        lines.append(
            f"- {label}：**{check.get('quality', 'unavailable')}**，数据日 {check.get('as_of') or 'unavailable'}；过期或缺失值不参与当日权限放宽。"
        )
    lines += [
        f"- 市场许可：新开仓 **{chief.get('new_position_permission')}**，总仓位建议 **{chief.get('total_position_range')}**。"
    ]


def render_themes(lines, enrichment):
    """§4 主线、题材生命周期与持续性（v0.57：压缩 + 标注待重设计）。"""
    lines += [
        "",
        "## 4. 主线、题材生命周期与持续性（⚠️ 判定口径待重设计，#26）",
        "",
        "> ⚠️ owner 2026-08-13：主线题材「目前不准，需要重新调整」——本节压缩为"
        "观察参考（最多 5 行），判定口径重设计归入 TODO #26（板块信息利用整体优化）。",
        "",
        "| 方向 | 生命周期 | 技术阶段 | 分数 | 事件证据 | 资金/龙头证据 | 持续性 | 次日验证 |",
        "|---|---|---|---:|---:|---|---|---|",
    ]
    for row in (enrichment.get("theme_lifecycles") or [])[:5]:
        lines.append(
            f"| {row.get('theme_name')} | {row.get('phase')} | {row.get('technical_stage')} | {row.get('score')} | {row.get('event_evidence_count')} | {row.get('fund_flow_evidence')}/{row.get('leader_structure')} | {row.get('continuity')} | {row.get('validation')} |"
        )


def render_holdings(lines, enrichment, revalued, day):
    """§5 持仓逐只诊断与仓位审计。"""
    lines += [
        "",
        "## 5. 持仓逐只诊断与仓位审计",
        "",
        "| 代码 | 名称 | 收盘/成本 | 盈亏 | 仓位 | MFE/MAE | 主力净流入 | 走势 | BBI/N型 | B1动作 | 原始逻辑/相对板块 |",
        "|---|---|---|---:|---:|---|---:|---|---|---|---|",
    ]
    diagnoses = {
        bare(x.get("code")): x for x in enrichment.get("holding_diagnoses") or []
    }
    for row in revalued:
        diagnosis = diagnoses.get(row["code"], {})
        close_text = (
            "缺失" if row["close"] is None else f"{row['close']:.2f}/{row['cost']:.3f}"
        )
        pnl_text = "缺失" if row["pnl_pct"] is None else f"{row['pnl_pct']:+.2%}"
        pos_text = (
            "缺失" if row["position_pct"] is None else f"{row['position_pct']:.1%}"
        )
        b1 = row["b1_holding_state"]
        mfe_text = (
            f"{row.get('mfe_pct', 'N/A')}%/{row.get('mae_pct', 'N/A')}%"
            if row.get("mfe_pct") is not None
            else "缺失"
        )
        ff_text = (
            f"{row.get('main_net_inflow', 'N/A')}"
            if row.get("main_net_inflow") is not None
            else "缺失"
        )
        lines.append(
            f"| {row['code']} | {row['name']} | {close_text} | {pnl_text} | {pos_text} | {mfe_text} | {ff_text} | {row['trend']}/{row['box']} | {row['bbi']['signal']}/{row['n_structure']['signal']} | {b1['final_priority']} {b1['final_action']}：{b1['final_reason']} | {diagnosis.get('original_holding_logic', 'B1策略')}/{diagnosis.get('relative_to_sector', 'unavailable')} |"
        )
    lines.append(
        "\n- 单票20%审计："
        + "；".join(
            f"{x['name']} {x['position_pct']:.1%}{'，超限' if x['position_pct'] > 0.2 else ''}"
            for x in revalued
            if x["position_pct"] is not None
        )
    )

    # 连亏检查（owner 2026-08-10：连亏冷却落在复盘环节，每日/每周都要统计并判断）。
    # ⚠️ 只报事实、不拦交易 —— 自动链里 `chief_decision.buy_actions` 恒为空表，
    #    没有买入决策可拦；作用是让复盘看见「这只票已连亏 N 次」。
    lines += loss_streak_lines(_loss_streak_today())

    # 止损冷却名单（owner 2026-08-12 #51：与连亏检查同落点、同「只提示不拦截」）。
    #    watch=当日在持 ⇒ 冷却期内的票还在持仓里会被点名提示。
    lines += format_cooldown_lines(
        _cooldown_today(day, watch={r["code"]: "当日在持" for r in revalued})
    )


def _load_closings() -> tuple[Optional[list], Optional[str]]:
    """读主台账 → FIFO 配平；失败返回 (None, 原因)。连亏/冷却两节共用。"""
    ledger = DATA / "trades" / "master_trade_ledger.csv"
    if not ledger.exists():
        return None, f"主台账不存在：{ledger}"
    trades = parse_ledger(ledger)
    if trades is None:
        return None, f"主台账解析失败：{ledger}"
    return fifo_pair(trades), None


def _cooldown_today(day: str, watch: Optional[dict] = None) -> dict:
    """当日止损冷却名单。台账缺失/解析失败时 available=False（不编「无冷却」）。"""
    closings, err = _load_closings()
    if err:
        return {"available": False, "reason": err}
    return stop_cooldowns(closings or [], as_of=day)


def _loss_streak_today() -> dict:
    """当日连亏检查：读主台账 → FIFO 配平 → 连亏聚合。

    台账缺失/解析失败时返回 `available=False` 并说明原因 ——
    **不返回「无连亏」**，那会把「没查」显示成「查了没有」。
    """
    closings, err = _load_closings()
    if err:
        return {"available": False, "reason": err}
    out = loss_streaks(closings or [])
    out["available"] = True
    return out


def render_next_day(lines, enrichment):
    """§6 下一交易日条件化交易计划；返回 `next_plan`（§8 还要用）。

    v0.57 角色定版：本节是**预案主产地**——明日预案以本节为准，
    盘前日报只做确认与信息刷新。"""
    next_plan = enrichment.get("next_day_plan") or {}
    lines += [
        "",
        "## 6. 下一交易日条件化交易计划（预案主产地）",
        "",
        "> 明日预案以本节为准；盘前日报只做**确认与信息刷新**（角色定版 v0.57）。",
        "",
        f"- 总仓位目标：**{next_plan.get('total_position_range')}**；新开仓权限：**{next_plan.get('new_position_permission')}**。",
        "",
        "| 代码 | 名称 | 方向/优先级 | 比例 | 触发条件 | 无效条件 | 开盘/盘中/14:45 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in next_plan.get("holding_plans") or []:
        reduction = row.get("reduction_pct_of_holding")
        reduction_text = (
            "unavailable"
            if not reduction
            else f"持仓的{reduction[0]}%-{reduction[-1]}%"
        )
        lines.append(
            f"| {row.get('code')} | {row.get('name')} | {row.get('priority')} {row.get('direction')} | {reduction_text} | {row.get('trigger')} | {row.get('invalidation')} | {row.get('open_scenario')} / {row.get('intraday_scenario')} / {row.get('tail_scenario')} |"
        )
    return next_plan


def render_discipline(lines, enrichment, execution):
    """§7 纪律偏差、规则有效性与待验证参数；返回 `rules`（落盘 payload 还要用）。"""
    rules = enrichment.get("rule_review") or {}
    behavior = execution.get("behavior_checks") or {}
    lines += ["", "## 7. 纪律偏差、规则有效性与待验证参数", "", "### 7.1 行为纪律", ""]
    lines += [f"- {key}: {value}" for key, value in behavior.items()]
    lines += ["", "### 7.2 有效规则", ""] + [
        f"- {x}" for x in rules.get("effective") or ["unavailable"]
    ]
    lines += ["", "### 7.3 失效/待验证规则", ""] + [
        f"- {x}"
        for x in (rules.get("failed") or []) + (rules.get("pending") or [])
        or ["unavailable"]
    ]
    return rules


def _input_paths(day: str) -> dict:
    """8 个强制输入 + 1 个可选新闻输入的落点。"""
    return {
        "chief": DATA / "decisions" / f"{day}_chief_decision.json",
        "market": DATA / "market" / f"{day}_market_timing_input.json",
        "gate": DATA / "quality" / f"{day}_runtime_gate.json",
        "tech": DATA / "holdings" / f"{day}_holding_technical_summary.json",
        "sectors": DATA / "sectors" / f"{day}_sector_technical_summary.json",
        "quotes": DATA / "market" / f"{day}_holding_quotes.json",
        "news": DATA / "news" / "postclose" / f"{day}_postclose_news_digest.json",
        "execution": DATA / "review_steps" / f"{day}_execution_review.json",
        "enrichment": DATA / "review_steps" / f"{day}_review_enrichment.json",
    }


def _check_mandatory(paths: dict) -> None:
    """⚠️ 8 个强制输入缺任一即硬失败 —— 产出「看起来完整但少了一节」的报告更危险。"""
    for key in (
        "chief",
        "market",
        "gate",
        "tech",
        "sectors",
        "quotes",
        "execution",
        "enrichment",
    ):
        if not paths[key].exists():
            raise SystemExit(f"mandatory close-review input missing: {paths[key]}")


def _load_inputs(paths: dict):
    """按 `paths` 全量加载；新闻是**可选**输入，缺失降级并留「missing」痕。"""
    chief = load(paths["chief"], {})
    market = load(paths["market"], {})
    gate = load(paths["gate"], {})
    tech = load(paths["tech"], [])
    sectors = load(paths["sectors"], [])
    quote_snapshot = load(paths["quotes"], {})
    news = load(
        paths["news"],
        {"status": "degraded", "sections": {}, "missing": ["postclose_news_digest"]},
    )
    execution = load(paths["execution"], {})
    enrichment = load(paths["enrichment"], {})
    return (
        chief,
        market,
        gate,
        tech,
        sectors,
        quote_snapshot,
        news,
        execution,
        enrichment,
    )


def _check_amv_gate(amv: dict, args, today: list):
    """⚠️ 盘后硬闸：0AMV 必须 confirmed + 有 regime + 有数值（v0.22 口径，语义级第 9 条强制）。"""
    value = amv.get("amv_change_pct")
    regime = amv.get("effective_state")
    if value is None or amv.get("quality") != "confirmed" or not regime:
        raise SystemExit("confirmed close 0AMV/regime missing")
    if args.no_trades_confirmed and today:
        raise SystemExit("no-trades confirmation conflicts with ledger")
    return value, regime


def _code_maps(tech: list, positions: list, quote_snapshot: dict):
    """技术面/持仓/当日行情三个按裸代码索引的字典（行情只收 available 的）。"""
    tmap = {bare(x.get("code")): x for x in tech}
    pmap = {bare(x.get("代码")): x for x in positions}
    qmap = {
        bare(x.get("code")): x
        for x in quote_snapshot.get("quotes", [])
        if x.get("available")
    }
    return tmap, pmap, qmap


def _side_maps(day: str):
    """可选旁路输入：MFE/MAE 与资金流排名（缺失即空表，不硬失败）。"""
    # Load MFE/MAE data
    mfe_path = DATA / "holdings" / f"{day}_mfe_mae.json"
    mfe_map = {}
    if mfe_path.exists():
        mfe_data = json.loads(mfe_path.read_text(encoding="utf-8"))
        mfe_map = {x["code"]: x for x in mfe_data.get("holdings", []) if "code" in x}
    # Load fund flow rank
    ff_path = DATA / "market" / f"{day}_fund_flow_rank.json"
    ff_map = {}
    if ff_path.exists():
        ff_data = json.loads(ff_path.read_text(encoding="utf-8"))
        ff_map = {x["code"]: x for x in ff_data.get("stock_rank", []) if "code" in x}
    return mfe_map, ff_map, mfe_path, ff_path


def _total_assets(positions: list) -> float:
    """用「持有金额/仓位占比」样本的中位数估计总资产。"""
    asset_samples = [
        finite(x.get("持有金额")) / finite(x.get("仓位占比"))
        for x in positions
        if finite(x.get("仓位占比")) > 0
    ]
    return sorted(asset_samples)[len(asset_samples) // 2] if asset_samples else 0


def _news_interest(pmap: dict, today: list, sectors: list):
    """§2 事实核对的交集面：持仓代码 ∪ 当日成交代码，外加这些票所属板块名
    （matched_themes 命中持仓板块也算交集）。"""
    hold_codes = set(pmap) | {bare(x.get("代码")) for x in today}
    hold_sectors = {
        s
        for c in hold_codes
        for s in (
            sector_for(c, sectors).get("sector") or sector_for(c, sectors).get("name"),
        )
        if s
    }
    return hold_codes, hold_sectors


def _render_tail(
    lines,
    quotes_current: bool,
    technical_dates: list,
    technical_current: bool,
    unavailable: list,
    paths: dict,
) -> None:
    """§8 数据时效、缺失项与风险提示 + §9 数据来源。"""
    lines += [
        "",
        "## 8. 数据时效、缺失项与风险提示",
        "",
        f"- 目标日行情完整：{quotes_current}；技术数据日：{','.join(technical_dates) or 'unavailable'}；目标日技术完整：{technical_current}。",
        "- 缺失项：" + ("、".join(unavailable) if unavailable else "无"),
        "- RSS仅用于事件发现；未确认候选不得直接形成交易授权。",
        "- 新闻、题材、技术信号均不得覆盖0AMV、运行质量门、RiskDecision和ChiefDecision。",
        "",
        "## 9. 数据来源",
        "",
    ]
    lines += [f"- `{path}`" for path in paths.values()]
    lines += [
        "- `data/trades/current_positions.json`",
        "- `data/trades/trades_stock.json`",
        "",
        "> 风险提示：本复盘用于策略纠偏，不构成收益承诺或无条件交易指令。",
    ]


def _position_summary(revalued: list, day: str):
    """收盘重估仓位：当日全持仓行情完整（日期对齐）才有总仓位，否则「缺失」。"""
    quotes_current = bool(revalued) and all(
        x["close"] is not None and x["price_date"] == day for x in revalued
    )
    actual_position = (
        sum(x["position_pct"] for x in revalued if x["position_pct"] is not None)
        if quotes_current
        else None
    )
    position_text = "缺失" if actual_position is None else f"{actual_position:.1%}"
    return quotes_current, actual_position, position_text


def build_payload(
    day: str,
    audit: dict,
    unavailable: list,
    amv: dict,
    news: dict,
    execution: dict,
    enrichment: dict,
    indices: list,
    quality: dict,
    revalued: list,
    next_plan: dict,
    rules: dict,
    today: list,
    actual_position,
    quotes_current: bool,
    technical_dates: list,
    technical_current: bool,
    gate: dict,
    out,
) -> dict:
    """落盘 JSON payload（`require("final_review")` 校验的形状）。"""
    return {
        "date": day,
        "audit": audit,
        "report_quality": "degraded" if unavailable else "complete",
        "amv": amv,
        "news_digest": news,
        "execution_review": execution,
        "theme_lifecycles": enrichment.get("theme_lifecycles") or [],
        "indices": indices,
        "market_quality_checks": quality.get("checks") or [],
        "revalued_positions": revalued,
        "next_day_plan": next_plan,
        "rule_review": rules,
        "unavailable": unavailable,
        "recorded_trade_count": len(today),
        "reference_position_pct": actual_position,
        "quotes_current": quotes_current,
        "technical_dates": technical_dates,
        "technical_current": technical_current,
        "precise_quantity_allowed": bool(
            gate.get("position_gate", {}).get("allow_precise_quantity")
        ),
        "output": str(out),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--no-trades-confirmed", action="store_true")
    args = ap.parse_args()
    day = args.date
    paths = _input_paths(day)
    _check_mandatory(paths)
    chief, market, gate, tech, sectors, quote_snapshot, news, execution, enrichment = (
        _load_inputs(paths)
    )
    positions = load(DATA / "trades" / "current_positions.json", [])
    trades = load(DATA / "trades" / "trades_stock.json", [])
    today = [x for x in trades if str(x.get("成交日期", "")).startswith(day)]
    amv = market.get("amv_0", {})
    value, regime = _check_amv_gate(amv, args, today)

    tmap, pmap, qmap = _code_maps(tech, positions, quote_snapshot)
    mfe_map, ff_map, mfe_path, ff_path = _side_maps(day)
    freshness = gate.get("position_freshness", {})
    technical_dates = sorted(
        {str(x.get("latest_date")) for x in tech if x.get("latest_date")}
    )
    technical_current = technical_dates == [day]
    total_assets = _total_assets(positions)
    revalued = revalue_positions(
        day, ff_map, mfe_map, pmap, qmap, regime, sectors, tmap, total_assets
    )
    quotes_current, actual_position, position_text = _position_summary(revalued, day)
    indices = index_rows(market)

    quality = chief.get("market_quality") or {}
    checks = {x.get("field"): x for x in quality.get("checks") or []}
    # 可审计块（原待办 #29，已实现）：8 个强制输入 + 持仓/台账 + 可选输入（缺失者留「缺失」标记）
    audit = report_audit.build(
        day,
        "close_review",
        [
            *paths.values(),
            DATA / "trades" / "current_positions.json",
            DATA / "trades" / "trades_stock.json",
            mfe_path,
            ff_path,
        ],
    )
    # ⚠️ 标题区角色行必须留在 main 本体：test_daily_report 用 inspect.getsource(main) 钉住它。
    lines = [
        f"# {day} 最终盘后复盘",
        "",
        "> 角色（v0.57 owner 定版）：**盘后=复盘纠错 + 条件化预案主产地** ｜ "
        "盘前=信息处理+预案确认 ｜ 盘中14:45=按规则的交易提醒。",
        f"> 生成时间：{cn_now().strftime('%Y-%m-%d %H:%M:%S')}",
        *report_audit.render_md(audit),
        f"> 报告质量：**{'complete' if not enrichment.get('unavailable') and news.get('status') == 'complete' else 'degraded'}**",
        f"> 0AMV当日变动：**{float(value):+.2f}%**；有效状态：**{regime}**",
        f"> 今日实际交易：**{'无交易动作' if not today else str(len(today)) + '笔'}**",
        f"> 持仓确认：**{freshness.get('status')}** — {freshness.get('reason')}",
        "",
        "## 1. 今日计划、14:45建议与实际执行",
        "",
        f"- 市场状态：**{chief.get('market_state')}**，建议仓位 **{chief.get('total_position_range')}**，收盘重估仓位 **{position_text}**。",
        f"- 执行对账质量：**{execution.get('status', 'unavailable')}**；成交记录 {execution.get('recorded_trade_count', 0)} 笔。",
        "",
        "| 代码 | 名称 | 盘前动作 | 14:45动作 | 实际动作 | 对账结论 |",
        "|---|---|---|---|---|---|",
    ]
    render_execution_rows(lines, execution)
    hold_codes, hold_sectors = _news_interest(pmap, today, sectors)
    render_news(lines, news, hold_codes=hold_codes, hold_sectors=hold_sectors)

    render_market(lines, checks, chief, indices)

    render_themes(lines, enrichment)

    render_holdings(lines, enrichment, revalued, day)

    next_plan = render_next_day(lines, enrichment)

    rules = render_discipline(lines, enrichment, execution)

    unavailable = list(
        dict.fromkeys(
            (enrichment.get("unavailable") or [])
            + (news.get("missing") or [])
            + (execution.get("missing") or [])
        )
    )
    _render_tail(
        lines, quotes_current, technical_dates, technical_current, unavailable, paths
    )

    out = daily_report_dir(day, REV) / f"{day}_final_review.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = build_payload(
        day,
        audit,
        unavailable,
        amv,
        news,
        execution,
        enrichment,
        indices,
        quality,
        revalued,
        next_plan,
        rules,
        today,
        actual_position,
        quotes_current,
        technical_dates,
        technical_current,
        gate,
        out,
    )
    json_out = daily_report_dir(day, REV) / f"{day}_final_review.json"
    require("final_review", payload)
    json_out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(out)
    print(json_out)


if __name__ == "__main__":
    main()

