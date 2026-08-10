# -*- coding: utf-8 -*-
"""同股连续亏损统计（连亏识别）—— 复盘环节用。

## 这是什么、不是什么

owner 2026-08-10 定：**连亏冷却放在复盘环节，每日/每周都统计并判断是否有连亏行为。**

⇒ 本模块**只产出事实与判定**，不拦任何交易。原因是自动链里**没有买入决策可拦**：
`chief_decision_report` 的 `buy_actions` 是字面量空表（源码注释：
`Candidate discovery disabled in pure-script mode; buy_actions always empty`），
「买入计划审核」表永远显示「暂无」。把冷却做成 gate 会是个挂在空处的闸。
所以它的作用是**让复盘看见**：某只票已连亏 N 次，下次考虑它之前先想清楚。

（用户画像第 4 条「九丰能源等案例显示需要连续亏损冷却机制」是本条的动因；
待办 #51 ① / #31 是同一件事。）

## 口径

- **只用 `match_status == "full"` 的平仓单**：`partial` 的 `gross_pnl` 只覆盖已配平
  部分、系统性少算（台账缺早期买入），拿它判盈亏会把赚的算成亏的。`none` 无成本基准。
  这条口径与 `weekly_review` 判胜率时一致 —— 不另立一套。
- **配平复用 `weekly_review.fifo_pair`**，不自己再写一遍 FIFO ——
  「持仓/盈亏推导逻辑只有一份」是本仓库的既有不变量（见 `reconcile_positions` 的同类约束）。
- **亏损判据用 `net_pnl`**（扣费后）而不是 `gross_pnl`：连亏关心的是账户实际损失，
  而 A 股来回费用足以把小幅浮盈变成实亏。`net_pnl` 缺失时该单不计入（不猜）。
- **连亏 = 最近一段连续的亏损平仓单**，被任何一次盈利打断即归零。
  这是「冷却」的原意（连续踩同一只票），不是「历史累计亏损次数」。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

# 阈值：同股连续亏损达到几次算「连亏」。材料原文是 2 次 → 冷却 10 个交易日；
# 冷却天数在本模块**不使用**（不拦交易，见模块 docstring），只保留次数阈值。
LOSS_STREAK_MIN = 2


def loss_streaks(closings: list[dict], *, min_streak: int = LOSS_STREAK_MIN) -> dict[str, Any]:
    """从平仓单列表算每只票的**当前**连亏段。

    入参是 `weekly_review.fifo_pair()` 的输出（**全台账**，不是某一周的切片
    —— 连亏是跨周的事实，只喂一周的单子会把上周的亏损段截断）。

    返回::

        {"streaks": {code: {"name","count","last_sell_date","total_net_pnl","sell_dates"}},
         "flagged": [code, ...],          # count >= min_streak，按 count 降序
         "excluded": {"partial": n, "none": n, "no_net_pnl": n},
         "min_streak": min_streak}

    ⚠️ `excluded` 必须如实给出：被排除的单子数不为 0 时，`streaks` 是**在残缺台账上**
    算出来的，读复盘的人有权知道（同 `weekly_review` 对 partial 的处理）。
    """
    excluded = {"partial": 0, "none": 0, "no_net_pnl": 0}
    usable: list[dict] = []
    for c in closings or []:
        st = str(c.get("match_status") or "")
        if st == "partial":
            excluded["partial"] += 1
            continue
        if st == "none":
            excluded["none"] += 1
            continue
        if c.get("net_pnl") is None:
            excluded["no_net_pnl"] += 1
            continue
        usable.append(c)

    by_code: dict[str, list[dict]] = {}
    for c in usable:
        by_code.setdefault(str(c.get("code") or ""), []).append(c)

    streaks: dict[str, Any] = {}
    for code, rows in by_code.items():
        if not code:
            continue
        # 按卖出日升序；同日多单按出现顺序（fifo_pair 已按 (代码, 卖出日) 聚合）
        rows = sorted(rows, key=lambda x: str(x.get("sell_date") or ""))
        streak: list[dict] = []
        for r in rows:
            if float(r["net_pnl"]) < 0:
                streak.append(r)
            else:
                streak = []            # 被一次盈利打断即归零 —— 「连续」的原意
        if not streak:
            continue
        streaks[code] = {
            "name": streak[-1].get("name") or "",
            "count": len(streak),
            "last_sell_date": str(streak[-1].get("sell_date") or ""),
            "total_net_pnl": round(sum(float(r["net_pnl"]) for r in streak), 2),
            "sell_dates": [str(r.get("sell_date") or "") for r in streak],
        }

    flagged = sorted([c for c, v in streaks.items() if v["count"] >= min_streak],
                     key=lambda c: (-streaks[c]["count"], streaks[c]["last_sell_date"]))
    return {"streaks": streaks, "flagged": flagged,
            "excluded": excluded, "min_streak": min_streak}



def format_lines(result: dict, *, title: str = "连亏检查") -> list[str]:
    """复盘报告用的 markdown 片段（无命中时也出一行，**不静默**）。

    ⚠️ 无命中时必须出「无连亏」而不是整节消失 —— 节消失读者分不清
    「查了没有」与「没查」。这是本仓库反复出现的失真类型。
    """
    lines = [f"### {title}", ""]
    if result.get("available") is False:
        lines += [f"- unavailable：{result.get('reason') or '未说明'}"
                  f"（**不等于「无连亏」**）", ""]
        return lines
    ex = result.get("excluded") or {}
    n_ex = sum(int(v or 0) for v in ex.values())
    flagged = result.get("flagged") or []
    if not flagged:
        lines.append(f"- 无同股连亏 ≥{result.get('min_streak', LOSS_STREAK_MIN)} 次。")
    else:
        lines.append(f"| 代码 | 名称 | 连亏次数 | 最近卖出日 | 累计净亏 |")
        lines.append("|---|---|---|---|---|")
        for code in flagged:
            v = result["streaks"][code]
            lines.append(f"| {code} | {v['name']} | {v['count']} | "
                         f"{v['last_sell_date']} | {v['total_net_pnl']} |")
    if n_ex:
        lines.append(f"- ⚠️ 有 {n_ex} 笔平仓单未计入"
                     f"（部分配平 {ex.get('partial', 0)} / 无成本基准 {ex.get('none', 0)} / "
                     f"缺净盈亏 {ex.get('no_net_pnl', 0)}）⇒ 以上基于**残缺台账**。")
    lines.append("")
    return lines
