# -*- coding: utf-8 -*-
"""止损冷却名单 + 胜率降仓提示（TODO #51，2026-08-12 owner 定性）—— 复盘环节用。

## 这是什么、不是什么

owner 2026-08-12 定：冷却机制（原 #31「触发止损的票进冷却不重复买入」+
#51②「当月短线胜率 <35% 降仓」）是**同一机制家族**，落**复盘报告节**
（参照 ① 连亏检查的落点 `loss_streak.py`），**只提示不拦截** ——
自动链里没有仓位/买入决策可拦（`chief_decision.buy_actions` 字面量空表；
仓位建议由 `total_position_range` 给，是文本不是执行）。

⇒ 本模块**只产出事实与判定**：哪只票在冷却期、当月胜率是否低于降仓阈值。
不改任何 gate、不接选股链。

## 口径

- **止损平仓判据**：`match_status == "full"` 且 `pnl_pct <= -7`（与
  `weekly_review._slow_stops` 的止损线同一阈值；两边一致由测试钉住）。
  `partial`/`none` 不计入（盈亏系统性少算/无成本基准），如实上报 excluded。
- **冷却期 10 个交易日**：`system_principles` 用户画像第 4 条的既有约定
  （「同股连续亏损 2 次 → 冷却 10 个交易日」），不是新发明的参数。
  截止日按交易日历数；日历不确定的日子跳过不计 ⇒ 冷却只会拉长不会缩短；
  完全数不出截止日（日历缺失）时该票仍列出并标注「截止日无法确定」——
  保守方向是让警告**多留几天**，不是提前消失。
- **台账缺失报 `unavailable`**，不返回「无冷却中的票」（「没查」≠「查了没有」，同
  `loss_streak` 的既有惯例）。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Optional

from custos.core.runtime_guards import trading_day_status  # noqa: E402  L0，L3 可依赖

# 既有约定值（见模块 docstring「口径」）：阈值同 weekly_review.STOP_LOSS_PCT，
# 冷却天数同 system_principles 用户画像第 4 条。
STOP_COOLDOWN_THRESHOLD_PCT = -7.0
COOLDOWN_TRADING_DAYS = 10
WIN_RATE_REDUCE_THRESHOLD_PCT = 35.0  # #51②：当月短线胜率 < 35% → 提示降仓

_CALENDAR_SCAN_CAP = 120  # 数 10 个交易日的扫描上限（自然日）；到顶 = 日历缺失


def _cooldown_until(
    last_stop_date: str,
    n: int,
    day_status: Callable[[str], dict[str, Any]],
) -> Optional[str]:
    """最后一次止损平仓之后第 ``n`` 个交易日的日期；数不出来返回 None。

    `is_trading_day is None`（日历不确定）的日子**跳过不计** ⇒ 截止日只会
    推后不会提前（保守：警告多留几天，而不是提前消失）。
    """
    d = date.fromisoformat(last_stop_date)
    counted = 0
    for _ in range(_CALENDAR_SCAN_CAP):
        d += timedelta(days=1)
        if day_status(d.isoformat()).get("is_trading_day") is True:
            counted += 1
            if counted >= n:
                return d.isoformat()
    return None


def stop_cooldowns(
    closings: list[dict],
    *,
    as_of: str,
    threshold_pct: float = STOP_COOLDOWN_THRESHOLD_PCT,
    cooldown_trading_days: int = COOLDOWN_TRADING_DAYS,
    day_status: Callable[[str], dict[str, Any]] = trading_day_status,
) -> dict[str, Any]:
    """从平仓单列表（`weekly_review.fifo_pair` 输出，全台账）算**当前冷却名单**。

    返回::

        {"available": True,
         "stops": {code: {"name","last_stop_date","pnl_pct","cooldown_until",
                          "cooldown_until_unknown","active"}},
         "active": [code, ...],        # as_of 仍在冷却期内的票
         "excluded": {"partial": n, "none": n, "no_pnl_pct": n,
                      "nan_pnl": n, "bad_date": n},   # 后两个：2026-08-13 目标机
                                                    # review 实测踩到（坏行不炸报告）
         "threshold_pct": ..., "cooldown_trading_days": ..., "as_of": as_of}
    """
    excluded = {"partial": 0, "none": 0, "no_pnl_pct": 0, "nan_pnl": 0, "bad_date": 0}
    stops: dict[str, Any] = {}
    for c in closings or []:
        st = str(c.get("match_status") or "")
        if st == "partial":
            excluded["partial"] += 1
            continue
        if st == "none":
            excluded["none"] += 1
            continue
        raw_pnl = c.get("pnl_pct")
        if raw_pnl is None:
            excluded["no_pnl_pct"] += 1
            continue
        try:
            pnl = float(raw_pnl)
        except (TypeError, ValueError):
            pnl = float("nan")
        if pnl != pnl:  # NaN/非法值：NaN 比较恒 False ⇒ 不写防御会**误入**冷却名单
            excluded["nan_pnl"] += 1  # 目标机 review 实测踩到（2026-08-13）
            continue
        if pnl > threshold_pct:
            continue  # 非止损平仓（含盈利与浅亏），不进冷却
        code = str(c.get("code") or "")
        sell_date = str(c.get("sell_date") or "")
        if not code:
            continue
        try:
            date.fromisoformat(sell_date)  # 台账一行坏日期不该炸掉三份复盘报告
        except ValueError:
            excluded["bad_date"] += 1  # 目标机 review 实测踩到（2026-08-13）
            continue
        prev = stops.get(code)
        if prev is None or sell_date > prev["last_stop_date"]:
            stops[code] = {
                "name": c.get("name") or "",
                "last_stop_date": sell_date,
                "pnl_pct": c["pnl_pct"],
            }
    active: list[str] = []
    for code, v in stops.items():
        until = _cooldown_until(v["last_stop_date"], cooldown_trading_days, day_status)
        v["cooldown_until"] = until
        v["cooldown_until_unknown"] = until is None
        # 截止日数不出来（日历缺失）⇒ 保守视为仍在冷却（警告多留，不提前消失）
        v["active"] = until is None or as_of <= until
        if v["active"]:
            active.append(code)
    active.sort(key=lambda c: stops[c]["last_stop_date"], reverse=True)
    return {
        "available": True,
        "stops": stops,
        "active": active,
        "excluded": excluded,
        "threshold_pct": threshold_pct,
        "cooldown_trading_days": cooldown_trading_days,
        "as_of": as_of,
    }


def win_rate_check(
    win_rate: Optional[float], *, threshold_pct: float = WIN_RATE_REDUCE_THRESHOLD_PCT
) -> dict[str, Any]:
    """当月胜率降仓提示的判定（#51②）。``win_rate=None`` ⇒ unavailable（不编结论）。"""
    if win_rate is None:
        return {"available": False, "reason": "当月无可估值平仓单，胜率不可得"}
    return {
        "available": True,
        "win_rate_pct": win_rate,
        "threshold_pct": threshold_pct,
        "below": win_rate < threshold_pct,
    }


def format_cooldown_lines(
    result: dict,
    *,
    title: str = "止损冷却名单",
    watch: Optional[dict[str, str]] = None,
) -> list[str]:
    """复盘报告用的 markdown 片段（无命中也出一行，**不静默**——同 loss_streak 惯例）。

    ``watch``：{code: 位置标签}（如当日在持）。冷却期内的票出现在 watch 里时
    追加一行提示——**只提示**，不构成任何拦截。
    """
    lines = [f"### {title}", ""]
    if result.get("available") is False:
        lines += [
            f"- unavailable：{result.get('reason') or '未说明'}"
            f"（**不等于「无冷却中的票」**）",
            "",
        ]
        return lines
    n_days = result.get("cooldown_trading_days", COOLDOWN_TRADING_DAYS)
    thr = result.get("threshold_pct", STOP_COOLDOWN_THRESHOLD_PCT)
    active = result.get("active") or []
    stops = result.get("stops") or {}
    if not active:
        lines.append(f"- 无止损冷却中的票（止损线 {thr}%，冷却 {n_days} 个交易日）。")
    else:
        lines.append(
            f"| 代码 | 名称 | 最近止损日 | 该单收益 | 冷却至（{n_days} 个交易日） |"
        )
        lines.append("|---|---|---|---|---|")
        for code in active:
            v = stops[code]
            until = (
                "无法确定（日历缺失）"
                if v.get("cooldown_until_unknown")
                else str(v["cooldown_until"])
            )
            lines.append(
                f"| {code} | {v['name']} | {v['last_stop_date']} | "
                f"{v['pnl_pct']}% | {until} |"
            )
        lines.append(
            "- ⚠️ 以上各票仍在冷却期：**只提示、不拦截**——再考虑它们之前先复盘止损原因。"
        )
    if watch:
        hits = [c for c in active if c in watch]
        if hits:
            lines.append(
                "- ⚠️ 冷却期内的票出现在 "
                + "、".join(f"{c}（{watch[c]}）" for c in hits)
                + " —— 复核是否该回避。"
            )
    ex = result.get("excluded") or {}
    n_ex = sum(int(v or 0) for v in ex.values())
    if n_ex:
        lines.append(
            f"- ⚠️ 有 {n_ex} 笔平仓单未计入"
            f"（部分配平 {ex.get('partial', 0)} / 无成本基准 {ex.get('none', 0)} / "
            f"缺收益率 {ex.get('no_pnl_pct', 0)} / pnl 非法(NaN) {ex.get('nan_pnl', 0)} / "
            f"日期无法解析 {ex.get('bad_date', 0)}）⇒ 以上基于**残缺台账**。"
        )
    lines.append("")
    return lines


def format_win_rate_lines(result: dict, *, title: str = "胜率降仓提示") -> list[str]:
    """#51② 的正式一节：输出当月胜率与 35% 阈值的判定结果。只提示。"""
    lines = [f"### {title}", ""]
    if result.get("available") is False:
        lines += [f"- unavailable：{result.get('reason') or '未说明'}。", ""]
        return lines
    thr = result.get("threshold_pct", WIN_RATE_REDUCE_THRESHOLD_PCT)
    wr = result.get("win_rate_pct")
    if result.get("below"):
        lines.append(
            f"- ⚠️ 当月短线胜率 **{wr}%** 低于 {thr}% 降仓阈值 ⇒ **提示降低短线仓位**"
            f"（只提示：自动链没有仓位决策可拦，降仓由人裁决执行）。"
        )
    else:
        lines.append(f"- 当月短线胜率 {wr}%，未低于 {thr}% 降仓阈值。")
    lines.append("")
    return lines
