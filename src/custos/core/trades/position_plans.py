# -*- coding: utf-8 -*-
"""持仓止盈/止损计划持久化（v0.82，「因子×止盈×止损」架构 Phase B）。

**补的是哪条断裂链**：1800 选股时每只候选都算了 `stop_loss_ref`（止损参考价，
见 score_candidates），但买入成交导入后这个点位就丢了——持仓侧三处判定点
（b1_holding_state / review_core / portfolio_review_report）只能按 −7%/−10%
的通用阈值现算。本模块在**买入成交导入时**把该持仓的止损/止盈计划落盘到
`data/trades/position_plans.json`，Phase C 的 14:45/17:00 影子判定从这里读。

## 文件结构

    {"positions": {代码: 计划}, "archive": {代码: [历史计划, ...]}}

计划条目::

    {
      "entry_date":   首笔买入成交日期,
      "entry_price":  摊薄后单位成本（口径见 sync_plans 注释）,
      "stop":         {"rule_id", "price", "basis"},
      "take_profit":  {rule_id: 参数快照}（exit_rules 当前 enabled 止盈方案）,
      "source":       "candidate:{date}" | "default",
      "created_at":   ISO 时间戳,
      "rules_version": exit_rules 生效配置的短哈希（改配置后新旧计划可区分）,
      # 可选标记：averaged（真补仓摊薄过）、rebuilt（计划丢失后补建，非摊薄）、
      # closed_at（archive 内：实际卖出日，无卖出记录退导入日）
    }

## 止损价来源（source）

- ``candidate:{date}``：≤ 买入日的最近一份 ``data/stock_pool/{date}_stock_pool.json``
  里该代码的 ``stop_loss_ref.price``（选股口径随买入流转，basis 一并带上）。
- ``default``：候选记录找不到时兜底 —— ``entry_price × (1 + loss_reduction.pnl_pct)``
  （Phase A 收敛的 −7% 减仓口径），basis 注明是兜底。

## 同步规则（sync_plans，由 incremental_ledger 两处写入点调用）

- **新建仓**（新出现的代码且本批有「买入」行）→ 生成计划。
- **补仓**（已有代码加买）→ 不重建计划；``entry_price`` 随摊薄成本更新并标
  ``averaged: true``，止损价不动（最初的计划点位不因补仓改写）。
- **丢失后补建**（持仓在、计划不在——文件丢失或历史持仓早于机制落地）→ 按新建
  补一份并标 ``rebuilt: true``；``averaged`` 只标真补仓摊薄，两种情形不混。
- **清仓**（代码从快照消失）→ 条目移入 ``archive``（``closed_at`` 记本批最后一笔
  卖出成交日期，本批无卖出行才退导入日，可追溯）。
- **转债转入/拆股**（SHARE_CREDIT_CATEGORIES）→ 不触发计划生成（没有买入决策，
  自然没有选股侧止损参考）。

⚠️ 全量灾备导入（standardize_trades）**不经过** apply_positions（直接覆写
current_positions.json），因此不重建计划——那里没有逐笔成交上下文，且灾备重建
后的持仓与既有计划可能脱节，需人工复核；本文件是派生数据，随时可从台账+候选池
重建，不作为台账一致性的一部分。

## 落后/不一致的检测与修复（如实口径）

- **检测**：唯一自动手段是 Phase C 影子报告对无计划持仓标「无计划」行——只能发现
  **缺计划**。孤儿条目（清仓残留/计划与持仓脱节）没有自动检测，也不自愈：同代码
  再次买入时旧条目被新计划覆盖（archive 只增不改，历史仍可追）。
- **修复**：没有自动 rebuild 机制——靠重跑导入或人工编辑 JSON。台账才是事实源，
  本文件永不为对账依据。

分层：core/trades 属 L2，只依赖 L0（paths / exit_rules / code_utils）与 stdlib。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from custos.core.paths import (
    POSITION_PLANS_FILE,
    STOCK_POOL_DIR,
    cn_now,
    write_json_atomic,
)
from custos.core import exit_rules
from custos.core.code_utils import clean_code, finite, is_a_share_position

# 模块级常量：测试 monkeypatch 改道 tmp（同 incremental_ledger.POS 的模式）。
PLANS_FILE = POSITION_PLANS_FILE
POOL_DIR = STOCK_POOL_DIR


def _effective_rules() -> dict[str, Any]:
    """exit_rules 当前生效配置（默认值 + EXIT_RULES.json 覆盖）。

    与 exit_rules 模块导入时求值的 _EFFECTIVE 在一次运行内相等；这里走公开 API
    重算，position_plans 不摸 L0 模块的私有名。
    """
    return exit_rules.resolve_exit_rules(exit_rules.load_exit_rule_overrides())


def _rules_version(rules: dict[str, Any]) -> str:
    """生效配置的短哈希——改 EXIT_RULES.json 后新旧计划可区分（不强制迁移）。"""
    blob = json.dumps(rules, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _take_profit_snapshot(rules: dict[str, Any]) -> dict[str, Any]:
    """当前 enabled 止盈方案的参数快照（计划落盘时的方案口径，供 Phase C 影子消费）。"""
    return {
        rule_id: {"params": dict(rule["params"])}
        for rule_id, rule in rules["take_profit_rules"].items()
        if rule.get("enabled")
    }


def _find_candidate_stop(
    code: str, entry_date: str, pool_dir: Path
) -> tuple[float, str, str] | None:
    """≤ entry_date 的最近一份 stock_pool 里该代码的 stop_loss_ref。

    买入通常发生在候选表出炉次日（1800 选股、次日下单），故只回看 ≤ 买入日的
    池文件；找不到（当日未入选/字段缺失/价格非有限值）返回 None 走兜底。
    """
    if not pool_dir.exists():
        return None
    dated = []
    for p in pool_dir.glob("*_stock_pool.json"):
        day = p.name[:10]
        if len(day) == 10 and day <= entry_date:
            dated.append((day, p))
    for day, p in sorted(dated, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # 单份池文件损坏不阻塞：继续找更早的
        if not isinstance(data, dict):
            continue  # 顶层非 dict（坏形状，如 list）：按损坏处理，继续找更早的
        for cand in data.get("candidates") or []:
            if clean_code(cand.get("code")) != code:
                continue
            ref = cand.get("stop_loss_ref") or {}
            price = finite(ref.get("price"))
            if price > 0:
                return price, str(ref.get("basis") or ""), day
    return None


def _default_stop(entry_price: float, rules: dict[str, Any]) -> tuple[float, str]:
    """兜底止损：entry × (1 + loss_reduction.pnl_pct)（Phase A 收敛的 −7% 口径）。"""
    pct = float(rules["stop_rules"]["loss_reduction"]["params"]["pnl_pct"])
    return entry_price * (1.0 + pct), f"兜底：entry×(1+loss_reduction {pct:+.2%})"


def load_plans(path: Path | None = None) -> dict[str, Any]:
    """读计划文件；缺失/损坏/形状不对回落空结构（派生数据，可重建，不炸导入链）。"""
    p = Path(path) if path else PLANS_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"positions": {}, "archive": {}}
    if not isinstance(data, dict):
        return {"positions": {}, "archive": {}}
    positions = data.get("positions")
    archive = data.get("archive")
    return {
        "positions": positions if isinstance(positions, dict) else {},
        "archive": archive if isinstance(archive, dict) else {},
    }


def save_plans(plans: dict[str, Any], path: Path | None = None) -> None:
    """原子写（temp + os.replace，同 incremental_ledger._write_atomic 模式）。"""
    write_json_atomic(Path(path) if path else PLANS_FILE, plans)


def _new_plan(
    code: str,
    entry_date: str,
    entry_price: float,
    rules: dict[str, Any],
    pool_dir: Path,
) -> dict[str, Any]:
    found = _find_candidate_stop(code, entry_date, pool_dir)
    if found is not None:
        price, basis, pool_day = found
        stop = {"rule_id": "stock_pool_stop_ref", "price": price, "basis": basis}
        source = f"candidate:{pool_day}"
    else:
        price, basis = _default_stop(entry_price, rules)
        stop = {"rule_id": "loss_reduction", "price": price, "basis": basis}
        source = "default"
    return {
        "entry_date": entry_date,
        "entry_price": entry_price,
        "stop": stop,
        "take_profit": _take_profit_snapshot(rules),
        "source": source,
        "created_at": cn_now().isoformat(timespec="seconds"),
        "rules_version": _rules_version(rules),
    }


def sync_plans(
    trades: pd.DataFrame,
    before_rows: list[dict],
    after_rows: list[dict],
    *,
    path: Path | None = None,
    pool_dir: Path | None = None,
) -> dict[str, Any]:
    """对比导入前后持仓快照，把本批成交引起的计划变化落盘。返回最新计划 dict。

    ``trades`` 是本批新增成交（incremental_ledger.norm 过的形态，至少含
    代码/交易类别/成交日期三列）；股份入账类（转债转入/拆股）在这里只影响
    「是否触发」判定——没有「买入」行的新代码不生成计划。
    """
    plans = load_plans(path)
    positions: dict[str, Any] = plans["positions"]
    archive: dict[str, Any] = plans["archive"]
    pool = Path(pool_dir) if pool_dir else POOL_DIR
    rules = _effective_rules()
    today = cn_now().date().isoformat()

    before = {clean_code(r.get("代码")) for r in before_rows}
    after_by = {clean_code(r.get("代码")): r for r in after_rows}

    # 本批各代码的买入行（首笔日期 + 是否含真实买入）与卖出行（最后一笔日期，
    # 清仓归档的 closed_at 用实际卖出日，拿不到才退导入日）
    buys: dict[str, str] = {}
    sells: dict[str, str] = {}
    for _, t in trades.iterrows():
        cat = t.get("交易类别")
        code = clean_code(t.get("代码"))
        day = str(t.get("成交日期") or today)
        if cat == "买入":
            buys[code] = min(day, buys[code]) if code in buys else day
        elif cat == "卖出":
            sells[code] = max(day, sells[code]) if code in sells else day

    for code, row in after_by.items():
        # 2026-08-28（v0.133）：非 A 股持仓（HK/US 等）不生成计划——否则裸码
        # 002158（港股医渡科技）会去 A 股候选池查 stop_loss_ref，拿到**深市
        # 汉钟精机**的近10日低点 24.96 当 6 港元的止损价（张冠李戴）。
        if not is_a_share_position(row):
            continue
        unit_cost = finite(row.get("单位成本"))
        if code not in before:
            if code not in buys:
                continue  # 转债转入/拆股建的头寸：无买入决策，不生成计划
            positions[code] = _new_plan(code, buys[code], unit_cost, rules, pool)
        elif code in buys:
            plan = positions.get(code)
            if plan is None:
                # 计划文件丢了/历史持仓从未有计划：按新建补一份（source 正常判定）。
                # 标 rebuilt 而非 averaged——这不是真补仓摊薄，标 averaged 会谎报。
                positions[code] = _new_plan(code, buys[code], unit_cost, rules, pool)
                positions[code]["rebuilt"] = True
                continue
            # 补仓：不重建计划、不动止损价；entry_price 随摊薄成本更新。
            # 用摊薄单位成本而不是该笔成交价：live 三处判定点的 pnl 口径都是
            # （现价 − 单位成本）/单位成本（含费），计划基准与判定基准必须同源，
            # 否则补仓后 −7% 判的是另一个价。
            plan["entry_price"] = unit_cost
            plan["averaged"] = True
            plan["updated_at"] = cn_now().isoformat(timespec="seconds")

    for code in before - set(after_by):
        plan = positions.pop(code, None)
        if plan is not None:
            # closed_at 记实际卖出日（本批最后一笔卖出成交日期），拿不到才退导入日
            plan["closed_at"] = sells.get(code) or today
            archive.setdefault(code, []).append(plan)

    save_plans(plans, path)
    return plans
