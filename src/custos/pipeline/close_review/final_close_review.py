# -*- coding: utf-8 -*-
"""Final close review with news, market, theme, holdings and execution audit."""

from __future__ import annotations

import argparse
import json

from custos.pipeline.holdings.b1_holding_state import evaluate as evaluate_b1_holding
from custos.pipeline.holdings.b1_holding_state import shadow_compare_line

from custos.pipeline.close_review.holding_bbi import intraday_bbi_basis
from custos.pipeline.close_review.holding_structure import n_structure_basis
from custos.pipeline.market_timing.sector_daily_rank import (  # noqa: E402  L3 同层，#26 采集器口径复用
    pct_on,
    read_close_series,
)

from custos.core.paths import cn_now, DATA, REVIEWS, daily_report_dir  # noqa: E402
from custos.core import report_audit  # noqa: E402
from custos.core.b1_thresholds import J_LOW_THRESHOLD  # noqa: E402  L0，J<13 硬门槛唯一来源
from custos.core.code_utils import market_of  # noqa: E402
from custos.core.paths import read_json as load  # noqa: E402
from custos.core.code_utils import bare_code as bare  # noqa: E402
from custos.core.code_utils import finite  # noqa: E402
from custos.core.code_utils import fnum as optional_finite  # noqa: E402
from custos.core.fmt import pct_text  # noqa: E402
from custos.core.contracts import require  # noqa: E402
from custos.core.exit_rules import LOSS_REDUCTION_PCT  # noqa: E402  L0，−7% 减仓线唯一来源
from custos.core.trades.position_plans import load_plans  # noqa: E402  L2，持仓计划影子读取

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


def _digest_rows(news: dict, hold_codes: set, hold_sectors: set) -> list:
    """digest 内的交集行（优先层）。返回 (类别, 命中代码, 命中主题, 行) 列表。

    ⚠️ 键的形状（2026-08-14 修）：生产者 postclose_news_digest 落的是
    `matched_holdings`=股票**名称**、`matched_codes`=**代码**——代码交集必须
    用 `matched_codes`（此前错用 matched_holdings 跟持仓代码相交，生产上恒为空，
    等于只剩主题一条腿、按代码命中的新闻被静默漏报）。fallback 到
    matched_holdings 只为兼容「直接装代码」的旧形状/手工构造输入。"""
    sections = news.get("sections") or {}
    rows = []
    for name in ("信息", "政策", "风向", "舆情"):
        for row in sections.get(name) or []:
            mc = set(row.get("matched_codes") or row.get("matched_holdings") or [])
            mt = set(row.get("matched_themes") or [])
            if (mc & hold_codes) or (mt & hold_sectors):
                rows.append((name, mc & hold_codes, mt & hold_sectors, row))
    return rows


def _holding_keywords(pmap: dict, sectors: list, sector_mapping: list) -> dict:
    """§2 兜底匹配的持仓关键词 → {裸代码: (名称, [关键词])}。

    关键词 = 股票名 + 行业/细分板块名（holding_sector_mapping 的 industry /
    raw_relation BlockName / concepts）+ 关联主题的 theme_name 分段与
    semantic_tags（如 电力/电网/船舶/核电/燃气/算力/液冷）。
    ⚠️ 只收 ≥2 字的词；关键词匹配允许误伤（「核电」命中不相关国际新闻可容忍），
    本节是复盘核对节不是交易依据 —— 误伤比漏报好（owner 2026-08-28）。"""
    mapping = {bare(x.get("code")): x for x in sector_mapping or []}
    keywords = {}
    for code, position in pmap.items():
        name = position.get("名称")
        words = {name} if name else set()
        held = mapping.get(code) or {}
        if held.get("industry"):
            words.add(held["industry"])
        words.update(held.get("concepts") or [])
        words.update(
            r.get("BlockName")
            for r in held.get("raw_relation") or []
            if r.get("BlockName")
        )
        theme = sector_for(code, sectors)
        words.update(t.strip() for t in str(theme.get("theme_name") or "").split("/"))
        words.update(theme.get("semantic_tags") or [])
        keywords[code] = (name or code, sorted(w for w in words if w and len(w) >= 2))
    return keywords


def _evidence_fallback(evidence: list, keywords: dict, skip_titles: set, limit=8):
    """全量 RSS 证据兜底（v0.136）：digest 没选上的候选里，按持仓关键词扫
    title+summary，命中即与持仓相关 → (证据行, 关联持仓) 列表，有界 ``limit`` 条。"""
    hits = []
    for item in evidence or []:
        title = item.get("title") or ""
        text = title + (item.get("summary") or "")
        if not text or title in skip_titles:
            continue
        related = sorted(
            f"{code} {name}"
            for code, (name, words) in keywords.items()
            if any(w in text for w in words)
        )
        if related:
            hits.append((item, related))
        if len(hits) >= limit:
            break
    return hits


def render_news(
    lines, news, hold_codes=None, hold_sectors=None, evidence=None, hold_keywords=None
):
    """§2（v0.57 角色定版 + v0.136 兜底）：盘后=复盘纠错+预案主产地 ⇒ 新闻节压缩为
    「与今日操作相关的事实核对」——只留与当日持仓/当日成交有交集的事实。

    v0.136 匹配层修复：digest 优先 + **全量 RSS 证据兜底**——此前只在 digest
    选出的 ~15 条里找交集（候选 20 → digest 15，持仓相关新闻常被挤掉），本节
    几乎恒空；现在对持仓关键词扫 `{date}_rss_evidence.json` 全量，digest 没选上
    不再漏报。仍无交集如实写「已检索无交集」，证据文件缺失报 unavailable
    （「没跑」与「跑了没有」必须分得开）。"""
    lines += [
        "",
        "## 2. 新闻、政策、风向与舆情（与今日操作相关的事实核对）",
        "",
        "> 复盘视角：本节只核对**与今日持仓/操作有交集**的新闻事实；"
        "digest 优先，全量 RSS 证据兜底（v0.136）。",
        "",
    ]
    sections = news.get("sections") or {}
    hold_codes = set(hold_codes or [])
    hold_sectors = set(hold_sectors or [])
    rows = _digest_rows(news, hold_codes, hold_sectors)
    if rows:
        lines += [
            "| 类别 | 时间 | 事件 | 来源/质量 | 与今日操作的交集 | 交易含义 |",
            "|---|---|---|---|---|---|",
        ]
        for name, codes_hit, themes_hit, row in rows[:8]:  # 有界：最多 8 条
            intersection = "、".join(sorted(codes_hit) + sorted(themes_hit))
            lines.append(
                f"| {name} | {row.get('published_at')} | {row.get('title')} | {row.get('source_name')}/{row.get('fact_status')} | {intersection} | {row.get('trade_meaning')} |"
            )
    skip_titles = {r.get("title") for _n, _c, _t, r in rows[:8]}
    fallback = (
        _evidence_fallback(evidence, hold_keywords or {}, skip_titles)
        if evidence is not None
        else []
    )
    if fallback:
        lines += [
            "",
            "全量证据兜底命中（digest 未选用但与持仓相关）：",
            "",
            "| 时间 | 事实 | 来源/tier | 关联持仓 |",
            "|---|---|---|---|",
        ]
        for item, related in fallback:
            lines.append(
                f"| {item.get('published_at')} | {item.get('title')} | "
                f"{item.get('source_name')}/{item.get('source_tier')} | {'、'.join(related)} |"
            )
    if not rows and not fallback:
        if sections and evidence is None:
            lines.append("- digest 内无与今日持仓/操作的交集。")
        elif sections or evidence:
            # 全量证据真的扫过了（空列表也算扫过）才能说「已检索无交集」
            lines.append("- 信息流已检索，无与持仓/操作交集的事实。")
        else:
            lines.append("- `unavailable`：当前窗口没有通过时效和来源质量门的证据。")
    if evidence is None:
        lines.append(
            "- `unavailable`：RSS 全量证据文件缺失，兜底匹配未执行（不等于无交集）。"
        )
    if news.get("missing"):
        lines.append("\n- 新闻数据缺失：" + "、".join(news["missing"]))


def revalue_positions(
    day, ff_map, mfe_map, pmap, qmap, regime, sectors, tmap, total_assets, plans=None
):
    """按当日行情重估每只持仓，返回逐票字典列表。

    2026-08-07 从 `main`（原 210 行）抽出。抽的是**数据计算**，与下面的
    `render_*` 分开 —— 重估逻辑因此可以单测，不必先铺一整份报告的上游产物。

    ``plans`` 是 position_plans 的 positions 节（{代码: 计划}），只喂给
    ``evaluate`` 的影子判定 —— 不影响 close/pnl/任何既有字段。
    """
    plans = plans or {}
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
            plan=plans.get(code),
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


def _sector_name_map() -> dict:
    """板块名称表（tdxzs.cfg）；不可用返回空 —— 兜底榜退化为代码名，不算失败。"""
    try:
        from custos.datasource.local_tdx import tq_sector  # noqa: PLC0415 惰性：与 sector_daily_rank 同例

        return tq_sector.load_sector_names() or {}
    except Exception:  # noqa: BLE001 —— 名称表缺失只影响展示名，不影响涨跌幅
        return {}


def _sector_rank_fallback(day: str, index_dir) -> dict | None:
    """采集器当日榜缺失时的兜底：用板块指数缓存（run_1800 refresh_sector_index
    每日更新）自算当日涨跌幅 TOP5。宇宙口径向 #26 采集器对齐（名称表可得时
    只收 行业(2)+概念(4)）；缓存无当日数据返回 None。"""
    name_map = _sector_name_map()
    rows = []
    for path in sorted(index_dir.glob("*.csv")):
        code = path.name.split(".")[0]
        info = name_map.get(code) or {}
        if name_map and str(info.get("tdx_type") or "") not in {"2", "4"}:
            continue
        pct = pct_on(read_close_series(path), day)
        if pct is not None:
            rows.append(
                {"code": code, "name": str(info.get("name") or code), "pct": pct * 100}
            )
    if not rows:
        return None
    by_pct = sorted(rows, key=lambda x: (-x["pct"], x["code"]))
    return {
        "gainers_top": [dict(e, rank=i + 1) for i, e in enumerate(by_pct[:5])],
        "losers_top": [
            dict(e, rank=i + 1)
            for i, e in enumerate(sorted(rows, key=lambda x: (x["pct"], x["code"]))[:5])
        ],
    }


def _sector_rank(day: str):
    """板块涨跌幅榜 → (榜 payload 或 None, 来源)。优先复用 #26 采集器
    `sector_daily_rank` 的当日产物；缺失用板块指数缓存自算（口径对齐）。"""
    path = DATA / "sectors" / "daily_rank" / f"{day}.json"
    if path.exists():
        return load(path, {}), "sector_daily_rank 采集器产物"
    fallback = _sector_rank_fallback(day, DATA / "market" / "sector_index")
    if fallback:
        return fallback, "板块指数缓存自算（采集器当日榜缺失的兜底）"
    return None, None


def _temperature_verdict(pct: float) -> str:
    """市场温度六档判定（CZ 波段战法 §三，governance/strategy/cz/02_swing_tactics.md）。
    文档 80~90% 强势、130~150% 较佳，中间档未细分 —— 这里按阈值阶梯连续覆盖。"""
    if pct < 0:
        return "冰点（不做，观察）"
    if pct < 65:
        return "不达标（观望，不做波段）"
    if pct < 80:
        return "及格"
    if pct < 130:
        return "强势（上升趋势）"
    if pct <= 150:
        return "较佳（最佳状态）"
    return "警惕冲顶（仅超短线或观望）"


def render_sector_board(lines, market: dict, rank, rank_source) -> None:
    """§4 板块题材涨跌幅榜与市场温度（v0.136 整节替换）：**客观事实展示，非主线判定**
    —— 原「主线生命周期与持续性」判定口径不准（#26 待重设计），本节只摆事实。"""
    lines += [
        "",
        "## 4. 板块题材涨跌幅榜与市场温度",
        "",
        "> 客观事实展示，非主线判定（原主线生命周期判定口径已随 #26 撤下）。",
        "",
    ]
    if not rank:
        lines.append(
            "- `unavailable`：当日板块涨跌幅榜不可得（采集器未产出、板块指数缓存亦无当日数据）。"
            "板块榜采集器（sector_daily_rank，#26）目前未接入日链——是否接入待拍板，此处仅注明，不改链路。"
        )
    else:
        lines.append(f"- 板块榜数据来源：{rank_source}。")
        for title, key in (("涨幅 TOP5", "gainers_top"), ("跌幅 TOP5", "losers_top")):
            lines += [
                "",
                f"### 板块{title}",
                "",
                "| 排名 | 板块 | 当日涨跌幅 |",
                "|---:|---|---:|",
            ]
            for e in (rank.get(key) or [])[:5]:
                pct = optional_finite(e.get("pct"))
                pct_text_ = "unavailable" if pct is None else f"{pct:+.2f}%"
                lines.append(
                    f"| {e.get('rank') or ''} | {e.get('name')}（{e.get('code')}） | {pct_text_} |"
                )
    lines += ["", "### 市场温度（CZ 波段战法口径）", ""]
    breadth = market.get("market_breadth") or {}
    up, down = breadth.get("up_count"), breadth.get("down_count")
    if (
        not isinstance(up, (int, float))
        or not isinstance(down, (int, float))
        or not down
    ):
        lines.append(
            "- `unavailable`：涨/跌家数不齐（880005 只给涨家数，跌家数无真实来源时不编造）"
            " —— 温度不算，不等于冰点。"
        )
    else:
        pct = (up - down) / down * 100
        lines.append(
            f"- 涨 {int(up)} 家 / 跌 {int(down)} 家（880005 口径，数据日 {breadth.get('as_of') or 'unavailable'}）："
            f"温度 **{pct:+.1f}%** —— {_temperature_verdict(pct)}。"
        )
        lines.append(
            "- 六档：<0 冰点 / 0~65% 不达标 / 65%+ 及格 / 80%+ 强势 / 130~150% 较佳 / >150% 警惕冲顶。"
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

    # 持仓计划影子对比（v0.83 Phase C，观察期：只展示不生效）——
    # 计划判定 vs 现行 B1 判定，不一致行内标注；无计划（早于机制落地）如实呈现。
    lines += ["", "- 持仓计划影子对比（影子期：不影响判定与权限）："]
    for row in revalued:
        b1 = row["b1_holding_state"]
        lines.append(
            shadow_compare_line(
                row["code"],
                row["name"] or row["code"],
                b1["final_priority"],
                f"{b1['final_priority']} {b1['final_action']}",
                b1.get("shadow"),
            )
        )


# 「今日纪律检查」扛单不止损的判读口径（v0.136 owner 定稿）：**亏损越过止损线**才算
# 扛单 —— 优先持仓计划 stop.price（收盘低于计划止损价），无计划用 exit_rules 的
# loss_reduction −7% 减仓线口径；亏损未越线是正常观察，不是扛单
# （旧版对所有 P0/P1 信号都报，2026-08-28 改）。


def _sold_codes(execution: dict):
    """当日有**卖出**成交的裸代码集合；`rows` 缺失返回 None —— fail-closed：
    「没核对」与「核对了没有卖出」必须分得开（同 §2 unavailable 的教训）。"""
    rows = execution.get("rows")
    if not isinstance(rows, list):
        return None
    return {
        bare(row.get("code"))
        for row in rows
        for t in row.get("actual_trades") or []
        if t.get("交易类别") == "卖出"
    }


def _stop_breach(row: dict, plan: dict):
    """扛单判读 → (是否越线, 依据)；数据不足返回 (None, '')，由调用方计入「无法判读」。"""
    stop = (plan or {}).get("stop") or {}
    price = optional_finite(stop.get("price"))
    close = row.get("close")
    if price is not None:
        if close is None:
            return None, ""
        return (
            close < price,
            f"收盘 {close:.2f} 低于计划止损价 {price:.2f}（{stop.get('basis') or '持仓计划'}）",
        )
    pnl = row.get("pnl_pct")
    if pnl is None:
        return None, ""
    return (
        pnl <= LOSS_REDUCTION_PCT,
        f"持仓盈亏 {pnl:+.1%} 越过 loss_reduction −7% 减仓线（无持仓计划，exit_rules 口径）",
    )


def _buy_point_hits(execution: dict, regime, tmap: dict):
    """买入不符合策略买点（v0.136 新增）：当日买入成交票的买点合规判读
    → (违规名单, 无法判读名单)。① 0AMV 空头期买入（空头不买）；
    ② 买入日 J ≥13（非 B1 买点，J<13 是硬门槛，阈值唯一来源 b1_thresholds）。"""
    hits, unknown = [], []
    for row in execution.get("rows") or []:
        trades = row.get("actual_trades") or []
        if not any(t.get("交易类别") == "买入" for t in trades):
            continue
        code = bare(row.get("code"))
        name = row.get("name") or code
        reasons = []
        if regime == "空头":
            reasons.append("空头期买入，违反纪律（空头不买）")
        j = optional_finite((tmap.get(code) or {}).get("daily_j"))
        if j is None:
            unknown.append((code, name))
        elif j >= J_LOW_THRESHOLD:
            reasons.append(
                f"非 B1 买点买入（买入日 J={j:.1f} ≥{J_LOW_THRESHOLD:.0f}，J<13 是硬门槛）"
            )
        if reasons:
            hits.append((code, name, "；".join(reasons)))
    return hits, unknown


def _tp_basis(row: dict) -> list:
    """不止盈依据：双中大阳分批止盈（two_bull_profit_take）或影子计划分批止盈。"""
    b1 = row["b1_holding_state"]
    reasons = []
    if any(s.get("signal") == "two_bull_profit_take" for s in b1.get("signals") or []):
        reasons.append("双中大阳分批止盈（two_bull_profit_take）")
    shadow = b1.get("shadow") or {}
    if any(s.get("signal") == "plan_tp_scale_out" for s in shadow.get("signals") or []):
        reasons.append("影子计划分批止盈（plan_tp_scale_out）")
    return reasons


def _habit_hits(
    revalued: list, execution: dict, sold: set, plans: dict, regime, tmap: dict
):
    """逐票判三类旧习惯复发 → (扛单不止损, 买入违规, 应止盈未止盈, 无法判读) 四份名单。"""
    stop_hits, tp_hits, unknown = [], [], []
    for row in revalued:
        if row["code"] in sold:
            continue  # 当日已卖 ⇒ 不算扛单/不止盈
        name = row["name"] or row["code"]
        breached, basis = _stop_breach(row, (plans or {}).get(row["code"]))
        if breached is None:
            unknown.append((row["code"], name))
        elif breached:
            stop_hits.append((row["code"], name, basis))
        tp = _tp_basis(row)
        if tp:
            tp_hits.append((row["code"], name, "；".join(tp)))
    buy_hits, buy_unknown = _buy_point_hits(execution, regime, tmap or {})
    return stop_hits, buy_hits, tp_hits, unknown + buy_unknown


def render_habit_check(lines, revalued, execution, plans=None, regime=None, tmap=None):
    """§1 延伸小节「今日纪律检查」（owner 2026-08-28 定稿，v0.136 三类判读）：
    旧错误习惯当日复发点名。

    判读口径：
    - **扛单不止损**：持仓亏损**越过止损线**（持仓计划 stop.price 优先，无计划用
      exit_rules loss_reduction −7%）且当日无该票卖出成交；未越线属正常观察不报；
    - **买入不符合策略买点**：当日买入票 ① 0AMV 空头期买入（空头不买）
      ② 买入日 J≥13（非 B1 买点）；
    - **应止盈未止盈**：双中大阳/影子计划止盈信号命中但当日无卖出。
    数据缺失 fail-closed 如实降级，不冒充「无复发」；
    计划外交易等执行对账归 §1 表（execution_review），本节不重复判定。"""
    lines += [
        "",
        "### 今日纪律检查（旧错误习惯当日复发点名）",
        "",
        "> 判读口径：扛单=亏损越过止损线（计划止损价优先，无计划按 −7% 减仓线）且当日未卖出；"
        "买入违规=空头期买入 / 买入日 J≥13；应止盈未止盈=止盈信号命中且当日未卖出。"
        "数据缺失如实降级，不冒充无复发。",
        "",
    ]
    sold = _sold_codes(execution)
    if sold is None:
        lines.append(
            "- `unavailable`：execution_review 缺 `rows`，无法核对当日卖出成交"
            " —— 本节未执行检查（不等于无复发）。"
        )
        return
    stop_hits, buy_hits, tp_hits, unknown = _habit_hits(
        revalued, execution, sold, plans or {}, regime, tmap
    )
    if not stop_hits and not buy_hits and not tp_hits and not unknown:
        lines.append("- 今日无复发：三类旧习惯均未检出（已核对）。")
        return
    for code, name, basis in stop_hits:
        lines.append(
            f"- ⚠️ **扛单不止损**：{code} {name} —— {basis}，但当日无该票卖出成交。"
        )
    for code, name, basis in buy_hits:
        lines.append(f"- ⚠️ **买入不符合策略买点**：{code} {name} —— {basis}。")
    for code, name, basis in tp_hits:
        lines.append(
            f"- ⚠️ **应止盈未止盈**：{code} {name} —— 出现{basis}信号，但当日无该票卖出成交。"
        )
    if unknown:
        lines.append(
            "- `unavailable`：以下标的数据不足，止损线/买点判读未执行（不等于无复发）："
            + "、".join(f"{c} {n}" for c, n in unknown)
        )


def render_next_day(lines, enrichment):
    """§6 下一交易日条件化交易计划；返回 `next_plan`（落盘 payload 还要用）。

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
    （matched_themes 命中持仓板块也算交集）。

    ⚠️ 2026-08-28 修：板块名要读 `theme_name`——生产上的 sector_technical_summary
    落的就是这个键（`sector`/`name` 只是旧形状/测试形状），漏了它 hold_sectors
    在生产上恒为空，digest 的主题交集这条腿等于没有。"""
    hold_codes = set(pmap) | {bare(x.get("代码")) for x in today}
    hold_sectors = {
        s
        for c in hold_codes
        for s in (
            sector_for(c, sectors).get("sector")
            or sector_for(c, sectors).get("theme_name")
            or sector_for(c, sectors).get("name"),
        )
        if s
    }
    return hold_codes, hold_sectors


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
    today: list,
    actual_position,
    quotes_current: bool,
    technical_dates: list,
    technical_current: bool,
    gate: dict,
    out,
) -> dict:
    """落盘 JSON payload（`require("final_review")` 校验的形状）。

    ⚠️ `theme_lifecycles`/`rule_review` 不再进 md（§4/§7 已于 v0.136 撤下/替换），
    但保留在 payload —— final_review_validator 的 REQUIRED_JSON_KEYS 钉着这两个键。"""
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
        "rule_review": enrichment.get("rule_review") or {},
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
    # 可选输入（v0.136 新增三个，缺失降级不硬失败）：RSS 全量证据（§2 兜底）、
    # 持仓板块映射（§2 关键词）、板块涨跌幅榜（§4，#26 采集器产物）。
    rss_path = DATA / "news" / "rss" / "normalized" / f"{day}_rss_evidence.json"
    mapping_path = DATA / "holdings" / f"{day}_holding_sector_mapping.json"
    rank_path = DATA / "sectors" / "daily_rank" / f"{day}.json"
    rss_evidence = load(rss_path, None) if rss_path.exists() else None
    sector_mapping = load(mapping_path, [])
    freshness = gate.get("position_freshness", {})
    technical_dates = sorted(
        {str(x.get("latest_date")) for x in tech if x.get("latest_date")}
    )
    technical_current = technical_dates == [day]
    total_assets = _total_assets(positions)
    # 持仓计划（v0.83 Phase C）：只喂影子判定与 §5 影子对比渲染，不进任何既有字段。
    plan_positions = load_plans().get("positions", {})
    revalued = revalue_positions(
        day,
        ff_map,
        mfe_map,
        pmap,
        qmap,
        regime,
        sectors,
        tmap,
        total_assets,
        plans=plan_positions,
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
            rss_path,
            mapping_path,
            rank_path,
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
    # §1 延伸小节「今日纪律检查」（v0.136 三类判读）：扛单不止损（亏损越过止损线）/
    # 买入不符合策略买点（空头期 / J≥13）/ 应止盈未止盈 的当日复发点名。
    render_habit_check(
        lines, revalued, execution, plans=plan_positions, regime=regime, tmap=tmap
    )
    hold_codes, hold_sectors = _news_interest(pmap, today, sectors)
    render_news(
        lines,
        news,
        hold_codes=hold_codes,
        hold_sectors=hold_sectors,
        evidence=rss_evidence,
        hold_keywords=_holding_keywords(pmap, sectors, sector_mapping),
    )

    render_market(lines, checks, chief, indices)

    # §4（v0.136 整节替换）：板块涨跌幅榜 + 市场温度——客观事实展示，非主线判定。
    rank, rank_source = _sector_rank(day)
    render_sector_board(lines, market, rank, rank_source)

    render_holdings(lines, enrichment, revalued, day)

    next_plan = render_next_day(lines, enrichment)

    unavailable = list(
        dict.fromkeys(
            (enrichment.get("unavailable") or [])
            + (news.get("missing") or [])
            + (execution.get("missing") or [])
        )
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
