# -*- coding: utf-8 -*-
"""月度复盘 —— MASTER_WORKFLOW §七「正式报告五」的实现。

规范要点（时间/目标/结构/指标/产物）以 `governance/contracts/MASTER_WORKFLOW.md` §七
为唯一来源。本模块是它允许范围内的**确定性**实现：只汇总既有数据，不做策略判断。

## 与 weekly_review 的关系

月度是周度的自然推广：**计算件全部复用** `weekly_review`（台账解析 `parse_ledger`、
FIFO 配平 `fifo_pair`、交易日历、组合轨迹、计划遵守、无交易确认、空头归因），
连亏检查复用 `loss_streak`——口径只有一份是本仓库的既有不变量。本模块只做
「月窗口 + §七 的九节结构 + 月度特有指标（波动率/收益回撤比/集中度/期望值）」。

## 刻意 unavailable 的部分（不编数据）

- **换手率/收益率的资金基数口径**：换手率需要平均权益基数，而轨迹市值只是
  持仓市值（无现金账户）⇒ 报成交总额与笔数，比率 unavailable。
- **板块归因 / 行业集中度 / 相关性暴露**：板块映射是**当前快照**
  （holding_sector_mapper 无历史版本）⇒ 归因只到个股；行业集中度与相关性
  没有数据源，如实 unavailable。
- **规则/策略版本贡献**：版本记录（CHANGELOG）与成交台账之间没有关联键
  （哪笔单是在哪个版本下做的无从判定）⇒ unavailable，不猜。

⚠️ 失败方向（fail-closed）：台账缺失/平仓单全未配平时，盈亏与胜率类指标给
unavailable 而不是「零」——「本月没亏」与「没算出来」必须可区分
（同 weekly_review 对 partial/none 配平单的处理）。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from custos.core.paths import BASE, cn_now, cn_today
from custos.pipeline.close_review.loss_streak import format_lines as loss_streak_lines
from custos.pipeline.close_review.loss_streak import loss_streaks
from custos.pipeline.close_review.cooldowns import (  # noqa: E402
    WIN_RATE_REDUCE_THRESHOLD_PCT,
    format_cooldown_lines,
    format_win_rate_lines,
    stop_cooldowns,
    win_rate_check,
)
from custos.pipeline.close_review.weekly_review import (
    BUY,
    SELL,
    _bear_regime_stats,
    _loss_structure,
    _no_trade_confirmations,
    _plan_adherence,
    _risk_levels_of_week,
    _slow_stops,
    _trading_days_and_reviews,
    fifo_pair,
    load_amv_regimes,
    parse_ledger,
    portfolio_trajectory,
    sse_daily_map,
)


def month_range(month: str | None) -> dict[str, Any]:
    """`YYYY-MM` → 完整自然月区间；缺省 = cn_today 口径的**上个月**（含跨年）。

    ⚠️ 月份计算必须走 `cn_today`（Asia/Shanghai）而不是宿主机时区 ——
    UTC 主机上月初早晨本地还在上月，用错时区会复盘错月份
    （同 paths.py 顶部「市场时钟」注释的事故史）。
    """
    if month:
        y, m = int(month[:4]), int(month[5:7])
        if not (1 <= m <= 12):
            raise SystemExit(f"--month 格式应为 YYYY-MM，收到 {month!r}")
    else:
        today = cn_today()
        y, m = (
            (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        )
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return {
        "year": y,
        "month": m,
        "label": f"{y:04d}-{m:02d}",
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def month_dates(rng: dict) -> list[str]:
    start = date.fromisoformat(rng["start"])
    n = (date.fromisoformat(rng["end"]) - start).days
    return [(start + timedelta(days=i)).isoformat() for i in range(n + 1)]


def _month_trade_stats(base: Path, rng: dict, unavailable: list[str]) -> dict:
    """台账加载与本月成交统计（复用 weekly_review 的解析，口径只有一份）。"""
    ledger_path = base / "data" / "trades" / "master_trade_ledger.csv"
    all_trades = parse_ledger(ledger_path)
    if all_trades is None:
        unavailable.append(f"成交台账缺失：{ledger_path}")
        all_trades = []
    month_trades = [t for t in all_trades if rng["start"] <= t["date"] <= rng["end"]]
    buys = [t for t in month_trades if t["side"] == BUY]
    sells = [t for t in month_trades if t["side"] == SELL]
    return {
        "all_trades": all_trades,
        "month_trades": month_trades,
        "buys": buys,
        "sells": sells,
        "fee_total": round(sum(t["fee"] for t in month_trades), 2),
        "amount_total": round(sum(t["amount"] for t in month_trades), 2),
    }


def _month_pnl_stats(all_trades: list[dict], rng: dict, unavailable: list[str]) -> dict:
    """FIFO 盈亏（只信 match_status == "full"，其余如实报 unavailable）。"""
    closings_all = fifo_pair(all_trades)
    closings = [c for c in closings_all if rng["start"] <= c["sell_date"] <= rng["end"]]
    _report_month_unmatched(closings, unavailable)
    valued = [
        c
        for c in closings
        if c["gross_pnl"] is not None and c["match_status"] == "full"
    ]
    return {
        "closings_all": closings_all,
        "closings": closings,
        "valued": valued,
        **_month_pnl_totals(valued),
    }


def _report_month_unmatched(closings: list[dict], unavailable: list[str]) -> None:
    """部分/零配平单如实 unavailable 报告，排除出盈亏与胜率统计。"""
    for c in closings:
        if c["match_status"] == "partial":
            unavailable.append(
                f"{c['sell_date']} {c['code']} 平仓单部分配平（缺 "
                f"{c['unmatched_qty']:g} 股买入来源），已排除出盈亏与胜率统计"
            )
        elif c["match_status"] == "none":
            unavailable.append(
                f"{c['sell_date']} {c['code']} 平仓单无买入来源，已排除出盈亏与胜率统计"
            )


def _win_loss_partition(valued: list[dict]) -> tuple[list[dict], list[dict]]:
    """按毛盈亏拆盈利单 / 亏损单（打平单两边都不算）。"""
    wins = [c for c in valued if c["gross_pnl"] > 0]
    losses = [c for c in valued if c["gross_pnl"] < 0]
    return wins, losses


def _avg_win_loss(
    wins: list[dict], losses: list[dict]
) -> tuple[float | None, float | None]:
    """平均盈利 / 平均亏损（绝对值）；无样本时 None。"""
    avg_win = sum(c["gross_pnl"] for c in wins) / len(wins) if wins else None
    avg_loss = (
        abs(sum(c["gross_pnl"] for c in losses)) / len(losses) if losses else None
    )
    return avg_win, avg_loss


def _pl_ratio(avg_win: float | None, avg_loss: float | None) -> float | None:
    """盈亏比；任一侧缺失（含 avg_loss 为 0）时 None。"""
    return round(avg_win / avg_loss, 2) if (avg_win and avg_loss) else None


def _avg_hold_days(valued: list[dict]) -> float | None:
    """平均持有天数（只统计 hold_days 非 None 的单）。"""
    hold_vals = [c["hold_days"] for c in valued if c["hold_days"] is not None]
    return round(sum(hold_vals) / len(hold_vals), 1) if hold_vals else None


def _month_pnl_totals(valued: list[dict]) -> dict:
    """毛/净盈亏、胜率/盈亏比、期望值、平均持有天数（只信 full 配平单）。"""
    gross_total = round(sum(c["gross_pnl"] for c in valued), 2)
    matched_buy_fee = round(sum(c["matched_buy_fee"] for c in valued), 2)
    closed_fee = round(matched_buy_fee + sum(c["sell_fee"] for c in valued), 2)
    net_total = round(gross_total - closed_fee, 2)
    wins, losses = _win_loss_partition(valued)
    win_rate = round(len(wins) / len(valued) * 100, 2) if valued else None
    avg_win, avg_loss = _avg_win_loss(wins, losses)
    return {
        "losses": losses,
        "gross_total": gross_total,
        "closed_fee": closed_fee,
        "net_total": net_total,
        "win_rate": win_rate,
        "pl_ratio": _pl_ratio(avg_win, avg_loss),
        # 期望值（金额口径）= 平均每笔已实现净盈亏。⚠️ 不是 R 倍数口径：
        # 台账没有逐笔止损位，算不出 R —— 缺什么说什么，不拿金额冒充 R。
        "expectancy": round(net_total / len(valued), 2) if valued else None,
        "avg_hold": _avg_hold_days(valued),
    }


def _stock_attribution(valued: list[dict]) -> list[dict]:
    """个股归因（按净盈亏汇总，升序）。板块归因 unavailable：板块映射只有当前快照，
    拿今天的板块去归上月的因是后视偏差。"""
    by_stock: dict[str, dict] = {}
    for c in valued:
        e = by_stock.setdefault(
            c["code"],
            {"name": c.get("name") or "", "n": 0, "net_pnl": 0.0},
        )
        e["n"] += 1
        e["net_pnl"] = round(e["net_pnl"] + (c["gross_pnl"] or 0), 2)
    return sorted(
        ({"code": k, **v} for k, v in by_stock.items()),
        key=lambda x: x["net_pnl"],
    )


def _month_environment(
    base: Path, trading_days: list[str], unavailable: list[str]
) -> dict:
    """板块 1：市场环境（0AMV regime 分布）。"""
    amv_path = base / "data" / "market" / "0amv_observations.jsonl"
    regimes = load_amv_regimes(amv_path)
    if regimes is None:
        unavailable.append(f"0AMV 历史缺失：{amv_path}")
        regimes = {}
    regime_counts = {"多头": 0, "震荡": 0, "空头": 0}
    for d in trading_days:
        r = regimes.get(d)
        if r:
            regime_counts[r["regime"]] = regime_counts.get(r["regime"], 0) + 1
    return regime_counts


def _month_performance_stats(traj: dict) -> dict:
    """区间收益/回撤/收益回撤比/波动率/平均仓位（由组合轨迹推导）。"""
    month_return = traj["week_return_pct"]
    max_dd = traj["max_drawdown_pct"]
    rd_ratio = (
        round(month_return / abs(max_dd), 2) if (month_return and max_dd) else None
    )
    # 波动率：完整日持仓市值的日收益标准差（未年化——年化假设 252 交易日
    # 对单账户持仓口径意义有限，如要年化请显式再开一项）。
    vol = None
    complete_mv = [pt["market_value"] for pt in traj["daily"] if not pt["partial"]]
    if len(complete_mv) >= 3 and complete_mv[0]:
        rets = [
            complete_mv[i] / complete_mv[i - 1] - 1
            for i in range(1, len(complete_mv))
            if complete_mv[i - 1]
        ]
        if len(rets) >= 2:
            vol = round(statistics.pstdev(rets) * 100, 2)
    pos_vals = [pt["total_position_pct"] for pt in traj["daily"] if not pt["partial"]]
    return {
        "month_return_pct": month_return,
        "max_drawdown_pct": max_dd,
        "return_drawdown_ratio": rd_ratio,
        "volatility_daily_pct": vol,
        "avg_position_pct": (
            round(sum(pos_vals) / len(pos_vals), 2) if pos_vals else None
        ),
    }


def _month_discipline(
    base: Path,
    days: list[str],
    trading_days: list[str],
    daily_reviews: dict,
    month_trades: list[dict],
    losses: list[dict],
    unavailable: list[str],
    execution_issues: list[dict],
    strategy_issues: list[dict],
) -> dict:
    """执行与纪律维度（复用周度实现，月窗口）。"""
    plan_checks, unplanned_ratio = _plan_adherence(
        base, daily_reviews, execution_issues, unavailable, month_trades
    )
    slow_stops = _slow_stops(execution_issues, losses)
    no_trade_days, unconfirmed = _no_trade_confirmations(
        base, execution_issues, trading_days, unavailable, month_trades
    )
    short_loss_share, total_loss = _loss_structure(losses, strategy_issues)
    bear_days, bear_loss_share, bear_day_ratio = _bear_regime_stats(
        base, losses, total_loss, trading_days, unavailable, label="本月"
    )
    risk_levels = _risk_levels_of_week(base, days)  # 函数名带 week，实为任意日期段
    risk_counts: dict[str, int] = {}
    for lv in risk_levels.values():
        risk_counts[lv] = risk_counts.get(lv, 0) + 1
    return {
        "plan_checks": plan_checks,
        "unplanned_ratio_pct": unplanned_ratio,
        "slow_stops": slow_stops,
        "short_loss_share_pct": short_loss_share,
        "no_trade_days": no_trade_days,
        "unconfirmed_no_trade_days": unconfirmed,
        "risk_level_counts": risk_counts,
        "bear_context": {
            "bear_days": bear_days,
            "bear_day_ratio_pct": bear_day_ratio,
            "bear_loss_share_pct": bear_loss_share,
        },
    }


def _month_concentration(base: Path, rng: dict, unavailable: list[str]) -> dict | None:
    """期末组合集中度（成本口径；current_positions 是当前快照，
    只有复盘「刚结束的当月」时它才约等于期末持仓——更早月份标 unavailable）。"""
    pos_path = base / "data" / "trades" / "current_positions.json"
    if rng["label"] != month_range(None)["label"]:
        unavailable.append(
            "组合集中度：current_positions 是当前快照，仅复盘上个月时可用"
        )
        return None
    if not pos_path.exists():
        unavailable.append(f"组合集中度：{pos_path} 缺失")
        return None
    from custos.core.paths import read_json as _rj

    positions = _rj(pos_path, []) or []
    costs = sorted(
        (
            float(p.get("持有数量") or 0) * float(p.get("单位成本") or 0)
            for p in positions
        ),
        reverse=True,
    )
    costs = [c for c in costs if c > 0]
    total_cost = sum(costs)
    if total_cost <= 0:
        return None
    return {
        "basis": "成本口径",
        "n_positions": len(costs),
        "top1_pct": round(costs[0] / total_cost * 100, 2),
        "top3_pct": round(sum(costs[:3]) / total_cost * 100, 2),
    }


def _next_month_notes(
    streak_result: dict,
    win_rate,
    max_dd,
    unplanned_ratio,
) -> list[str]:
    """下月观察方向：只从已算出的**事实**生成机械提示，不做策略判断。"""
    notes: list[str] = []
    if streak_result.get("flagged"):
        notes.append(
            "连亏名单仍在："
            + "、".join(streak_result["flagged"])
            + "（下月考虑这些票之前先看连亏节）"
        )
    if win_rate is not None and win_rate < WIN_RATE_REDUCE_THRESHOLD_PCT:
        notes.append(
            f"月胜率 {win_rate}% 低于 {WIN_RATE_REDUCE_THRESHOLD_PCT:g}% 降仓阈值——"
            "见「胜率降仓提示」节（只提示，降仓由人裁决）。"
        )
    if max_dd is not None and max_dd <= -10:
        notes.append(f"月内最大回撤 {max_dd}% —— 复核止损执行节与仓位上限。")
    if unplanned_ratio is not None and unplanned_ratio > 0:
        notes.append(f"存在计划外交易（占比 {unplanned_ratio}%）——见「行为偏差」节。")
    if not notes:
        notes.append("无机械触发的观察项；按 §七 应由人复核本报告后填写下月方向。")
    return notes


def _append_known_gaps(unavailable: list[str]) -> None:
    """刻意 unavailable 的项（不编数据，见模块 docstring「刻意 unavailable 的部分」）。"""
    for key in ("换手率", "板块归因", "行业集中度与相关性暴露", "规则/策略版本贡献"):
        unavailable.append(
            {
                "换手率": "换手率：缺平均权益基数（轨迹只有持仓市值、无现金账户）",
                "板块归因": "板块归因：板块映射只有当前快照，归入历史月份是后视偏差",
                "行业集中度与相关性暴露": "行业集中度/相关性暴露：无行业分类历史与相关性矩阵数据源",
                "规则/策略版本贡献": "规则版本贡献：CHANGELOG 版本记录与成交台账无关联键，无法按版本归因",
            }[key]
        )


def build_monthly_review(base: Path, month: str | None) -> dict[str, Any]:
    rng = month_range(month)
    days = month_dates(rng)
    unavailable: list[str] = []
    execution_issues: list[dict] = []
    strategy_issues: list[dict] = []

    # --- 台账与月度交易统计 + FIFO 盈亏（复用 weekly_review 的解析与 FIFO，口径只有一份）---
    trade = _month_trade_stats(base, rng, unavailable)
    pnl = _month_pnl_stats(trade["all_trades"], rng, unavailable)
    stock_attrib = _stock_attribution(pnl["valued"])

    # --- 交易日历与日报 ---
    trading_days, daily_reviews = _trading_days_and_reviews(base, days, unavailable)

    # --- 板块 1：市场环境（0AMV regime 分布 + 基准）---
    regime_counts = _month_environment(base, trading_days, unavailable)

    sse_map = sse_daily_map(base, rng["start"], rng["end"])
    traj = portfolio_trajectory(
        days, trading_days, daily_reviews, sse_map, unavailable, label="本月"
    )
    perf = _month_performance_stats(traj)

    # --- 执行与纪律维度（复用周度实现，月窗口）---
    disc = _month_discipline(
        base,
        days,
        trading_days,
        daily_reviews,
        trade["month_trades"],
        pnl["losses"],
        unavailable,
        execution_issues,
        strategy_issues,
    )

    # --- 连亏（全台账口径，跨月不打断）---
    streak_result = loss_streaks(pnl["closings_all"])
    # --- 止损冷却名单（#51，2026-08-12：同连亏落点，全台账口径；as_of=月末）---
    cooldown_result = stop_cooldowns(pnl["closings_all"], as_of=rng["end"])
    # --- 胜率降仓提示（#51② owner 2026-08-12 定：正式一节，只提示不拦截）---
    win_rate_hint = win_rate_check(pnl["win_rate"])

    # --- 期末组合集中度 ---
    concentration = _month_concentration(base, rng, unavailable)

    # --- 下月观察方向 + 刻意 unavailable 的项 ---
    notes = _next_month_notes(
        streak_result,
        pnl["win_rate"],
        perf["max_drawdown_pct"],
        disc["unplanned_ratio_pct"],
    )
    _append_known_gaps(unavailable)

    return {
        "month": rng["label"],
        "year": rng["year"],
        "month_num": rng["month"],
        "generated_at": cn_now().isoformat(timespec="seconds"),
        "start": rng["start"],
        "end": rng["end"],
        "environment": {
            "trading_days": trading_days,
            "n_trading_days": len(trading_days),
            "regime_counts": regime_counts,
            "benchmark_month_pct": traj["benchmark_week_pct"],
        },
        "performance": {
            "month_return_pct": perf["month_return_pct"],
            "max_drawdown_pct": perf["max_drawdown_pct"],
            "return_drawdown_ratio": perf["return_drawdown_ratio"],
            "volatility_daily_pct": perf["volatility_daily_pct"],
            "avg_position_pct": perf["avg_position_pct"],
            "benchmark_month_pct": traj["benchmark_week_pct"],
            "trajectory": traj["daily"],
            "partial_notes": traj["partial_notes"],
        },
        "realized": {
            "n_trades": len(trade["month_trades"]),
            "n_buys": len(trade["buys"]),
            "n_sells": len(trade["sells"]),
            "amount_total": trade["amount_total"],
            "fee_total": trade["fee_total"],
            "turnover_rate_pct": None,  # 缺平均权益基数，见 unavailable
            "n_closings": len(pnl["closings"]),
            "n_valued": len(pnl["valued"]),
            "gross_total": pnl["gross_total"],
            "closed_fee_total": pnl["closed_fee"],
            "net_total": pnl["net_total"],
            "win_rate_pct": pnl["win_rate"],
            "pl_ratio": pnl["pl_ratio"],
            "expectancy_per_trade": pnl["expectancy"],
            "avg_hold_days": pnl["avg_hold"],
            "stock_attribution": stock_attrib,
        },
        "discipline": {
            "plan_checks": disc["plan_checks"],
            "unplanned_ratio_pct": disc["unplanned_ratio_pct"],
            "slow_stops": disc["slow_stops"],
            "short_loss_share_pct": disc["short_loss_share_pct"],
            "no_trade_days": disc["no_trade_days"],
            "unconfirmed_no_trade_days": disc["unconfirmed_no_trade_days"],
            "risk_level_counts": disc["risk_level_counts"],
        },
        "bear_context": disc["bear_context"],
        "loss_streaks": streak_result,
        "cooldown": cooldown_result,
        "win_rate_hint": win_rate_hint,
        "concentration": concentration,
        "execution_issues": execution_issues,
        "strategy_issues": strategy_issues,
        "version_attribution": None,  # 无版本-成交关联键，见 unavailable
        "next_month_notes": notes,
        "unavailable": unavailable,
    }


def _fmt(v, suffix="") -> str:
    return f"{v}{suffix}" if v is not None else "unavailable"


def render_markdown(review: dict) -> str:
    """按 §七 固定结构渲染（九节，一节不缺；缺数据的节如实写 unavailable）。"""
    env = review["environment"]
    perf = review["performance"]
    real = review["realized"]
    disc = review["discipline"]
    L: list[str] = [
        f"# {review['month']} 月度复盘（{review['start']} ~ {review['end']}）",
        "",
        f"> 生成时间：{review['generated_at']}（确定性汇总，不含策略判断）",
        "",
        "## 1. 月度市场环境和主线阶段",
        "",
        f"- 交易日 {env['n_trading_days']} 天；0AMV regime 分布：多头 "
        f"{env['regime_counts'].get('多头', 0)} / 震荡 {env['regime_counts'].get('震荡', 0)} / "
        f"空头 {env['regime_counts'].get('空头', 0)} 天",
        f"- 基准（上证指数）月涨跌：{_fmt(env['benchmark_month_pct'], '%')}",
        "",
        "## 2. 月度收益、回撤、波动和资金使用效率",
        "",
        f"- 区间收益（持仓市值口径）：{_fmt(perf['month_return_pct'], '%')}；"
        f"最大回撤：{_fmt(perf['max_drawdown_pct'], '%')}；"
        f"收益回撤比：{_fmt(perf['return_drawdown_ratio'])}",
        f"- 日收益波动率（未年化）：{_fmt(perf['volatility_daily_pct'], '%')}；"
        f"平均仓位：{_fmt(perf['avg_position_pct'], '%')}",
        "",
        "## 3. 已实现/未实现盈亏及归因",
        "",
        f"- 平仓 {real['n_closings']} 单（有效配平 {real['n_valued']} 单）；"
        f"毛盈亏 {real['gross_total']}，费用 {real['closed_fee_total']}，"
        f"**净盈亏 {real['net_total']}**",
        f"- 胜率 {_fmt(real['win_rate_pct'], '%')}；盈亏比 {_fmt(real['pl_ratio'])}；"
        f"期望值（每笔净额）{_fmt(real['expectancy_per_trade'])}；"
        f"平均持有 {_fmt(real['avg_hold_days'], ' 天')}",
        f"- 本月成交 {real['n_trades']} 笔（买 {real['n_buys']} / 卖 {real['n_sells']}），"
        f"成交额 {real['amount_total']}，总费用 {real['fee_total']}；"
        "换手率 unavailable（缺平均权益基数）",
        "",
    ]
    if real["stock_attribution"]:
        L += [
            "个股归因（按净盈亏升序）：",
            "",
            "| 代码 | 名称 | 笔数 | 净盈亏 |",
            "|---|---|---|---|",
        ]
        for s in real["stock_attribution"]:
            L.append(f"| {s['code']} | {s['name']} | {s['n']} | {s['net_pnl']} |")
        L.append("")
    L += [
        "- 板块归因 unavailable（板块映射只有当前快照，归入历史月份是后视偏差）。",
        "",
    ]

    L += [
        "## 4. 买入、补仓、卖出、止损和仓位规则表现",
        "",
        f"- 止损偏慢（已实现亏损超阈值）{len(disc['slow_stops'])} 单；"
        f"亏损单短持有占比：{_fmt(disc['short_loss_share_pct'], '%')}",
        f"- 仓位纪律：平均仓位 {_fmt(perf['avg_position_pct'], '%')}；"
        f"风控等级分布 {disc['risk_level_counts'] or 'unavailable'}",
        "",
        "## 5. 计划内外交易与行为偏差",
        "",
        f"- 计划外交易占比：{_fmt(disc['unplanned_ratio_pct'], '%')}；"
        f"无交易确认缺失天数：{len(disc['unconfirmed_no_trade_days'])}",
        "",
    ]
    if disc["plan_checks"]:
        L.append("| 日期 | 计划来源 | 结果 |")
        L.append("|---|---|---|")
        for c in disc["plan_checks"]:
            L.append(
                f"| {c.get('date', '')} | {c.get('plan_date', '')} | {c.get('status', '')} |"
            )
        L.append("")

    L += [
        "## 6. 选股池、主线识别和风险否决效果",
        "",
        f"- 风控等级分布：{disc['risk_level_counts'] or 'unavailable'}",
        "- 选股池/主线识别的前向效果 unavailable（月度口径无池级前向收益归因数据）。",
        "",
        "## 7. 规则版本表现及样本量",
        "",
        "- unavailable：版本记录与成交台账无关联键，无法按版本归因（不猜）。",
        "",
        "## 8. 重复错误和重大风险事件",
        "",
    ]
    L += loss_streak_lines(review["loss_streaks"], title="连亏检查（全台账）")
    L += format_cooldown_lines(review["cooldown"], title="止损冷却名单（全台账）")
    L += format_win_rate_lines(review["win_rate_hint"])
    bear = review["bear_context"]
    L += [
        f"- 空头日 {bear['bear_days']} 天（占 {_fmt(bear['bear_day_ratio_pct'], '%')}）；"
        f"亏损单发生于空头日占比：{_fmt(bear['bear_loss_share_pct'], '%')}",
        "",
    ]
    if review["execution_issues"]:
        L.append(
            f"- 执行问题 {len(review['execution_issues'])} 条（见 JSON execution_issues）。"
        )
    if review["strategy_issues"]:
        L.append(
            f"- 策略问题 {len(review['strategy_issues'])} 条（见 JSON strategy_issues）。"
        )
    L.append("")

    L += ["## 9. 下月策略参数、风险预算和观察方向", ""]
    for note in review["next_month_notes"]:
        L.append(f"- {note}")
    L += ["", "（本节为机械提示，非策略判断；正式方向由人复核后填写。）", ""]

    if review["unavailable"]:
        L += ["## 数据缺口（unavailable 汇总）", ""]
        for u in review["unavailable"]:
            L.append(f"- {u}")
        L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="确定性月度复盘（MASTER_WORKFLOW §七）")
    ap.add_argument(
        "--month",
        default=None,
        help="复盘月份 YYYY-MM，默认上个月（Asia/Shanghai 口径，含跨年）",
    )
    ap.add_argument("--base", default=str(BASE), help="项目根目录（测试用）")
    args = ap.parse_args(argv)
    base = Path(args.base)

    review = build_monthly_review(base, args.month)
    out_dir = base / "artifacts/reports" / "monthly"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{review['month']}_monthly_review"
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(
        json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(render_markdown(review), encoding="utf-8")
    print(f"monthly review written: {json_path}")
    print(f"monthly review written: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
