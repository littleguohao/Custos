# -*- coding: utf-8 -*-
"""**可执行**的 stage 产物契约 —— 让 schema 参与执行，而不是只写在文档里。

## 为什么需要它

2026-08-07 架构审查实测：19 种 stage 产物里 **文档覆盖 6 个、代码级校验只有 1 个**，
消费端有 **2391 处 `.get()` 不带默认值**（取不到就是 `None`）。
后果不是抽象的洁癖问题 —— 它是同一批 bug 反复出现的**共同机制**：
某个字段在上游产物里缺失或畸形，消费端静默替换成别的东西继续往下算。

同一天在四个不同模块里查到的实例，全是这个机制：

    门控缺失兜底成 `{}` → status=None → 两个 `== "blocked"` 判定落空
                                      ⇒ **没有门控照样输出「允许开新仓」**
    sector_state.score 为 NaN        ⇒ `nan >= 60` 恒 False ⇒ 板块**静默降级**
    pct_text(NaN) → "+nan%"          ⇒ 报告里出现既不是数也不是「缺失」的东西
    ma_flag(None) → 「下MA240」        ⇒ 把「算不出」显示成「在均线下方」（且方向偏空）
    risk_map 的 str(None) → "None"   ⇒ 幽灵持仓键
    market_regime 只读 effective_state ⇒ 漏掉 amv_zone 那套词表

`00_governance/contracts/DATA_FLOW_CONTRACT.md` 是文档，**不参与执行**，
所以会漂移 —— 那份文档里曾登记一个叫 `SkillEvidence` 的实体，
实际上项目里从来没有任何产出同时具备它描述的 8 个字段。这就是文档型契约的下限。

## 执行策略：写严、读松

    生产者 require(name, obj)   → 不合规**当场 SystemExit**
    消费者 check(name, obj)     → 返回结构化结论，由调用方按既有降级策略裁决

为什么不对消费者也强制：README 记着 2026-07-30 的事故 ——
「悄悄收紧硬闸 + 收紧 stale 判定，两者叠加导致 17:00 盘后复盘直接失败」。
消费端的降级策略是**校准过的**，不能因为新增校验就改变它。
而生产者侧不同：产物是它自己造的，造出畸形产物没有任何正当理由，
在源头失败比让下游猜要好 —— 这也让排错从「下游哪个字段是 None」
变成「哪个生产者写坏了」。

## 两类产物，契约的约束对象不同

    **终态产物**（`runtime_gate` / `risk_decision` / `chief_decision` / `b1_holding_state`）
        一次写成、之后只被读。契约管**值**：字段必须有内容、枚举必须在域内。

    **渐进填充产物**（`market_timing_input`）
        多个 stage 依次读-改-写（collector → sync_compass_amv → merge → amv_state），
        每一步都只填自己那部分。契约只能管**结构**（哪些节存在、类型对不对），
        不能要求「值已填」—— 那会让第一个写者就失败。
        这类产物里有**刻意留 None** 的字段（用 `nullable`），
        每个都必须在 spec 里说清为什么。

## 覆盖范围（按**消费者数量**与是否在硬失败链上排的优先级）

    钱的路径（第一批）
      runtime_gate               9 消费者  ⛔  权限总闸
      chief_decision             7 消费者  ⛔  最终交易计划
      risk_decision              6 消费者  ⛔  风控否决
      b1_holding_state           3 消费者  ⛔  持仓动作
    扇出最大（第二批）
      market_timing_input       19 消费者  ⛔  渐进填充，12 个读 amv_0
      holding_technical_summary 11 消费者  ⛔  分支型
      sector_state               6 消费者      score 的 NaN 曾致静默降级
    硬失败链其余（第三批）
      holding_quotes             5 消费者  ⛔  分支型
      sector_technical_summary   3 消费者  ⛔  96 处读 available
      execution_review           2 消费者  ⛔
      review_enrichment          1 消费者  ⛔

仍未纳入的：`stock_pool` / `final_review` / `mfe_mae` / `fund_flow_rank` /
`holding_review` / `formula_hits` / `candidates_enriched` / `rss_*` /
`postclose_news_digest` —— 都不在硬失败链上，按需再加。
"""
from __future__ import annotations

import math
from typing import Any

# ── 取值域。**必须与生产者代码一致**，不是理想设计。
#    改这些值之前先确认生产者真的会写出新值，否则会把正常产物判成畸形。
REGIMES = {"做多", "中性", "空头", "未知"}          # runtime_guards.normalize_regime 的输出域
RISK_LEVELS = {"普通", "提高", "强风控"}            # generate_risk_and_sectors.build_risk_decision
GATE_STATUS = {"pass", "degraded", "blocked"}       # runtime_guards.market_quality_gate / position_gate
RISK_PRIORITY = {"高", "中", "低"}                  # build_risk_decision
B1_PRIORITY = {"P0", "P1", "P2", "P3"}              # b1_holding_state.evaluate
# generate_risk_and_sectors.build_sector_state 的三个枚举域（与 normalize_stage 一致）
SECTOR_STATES = {"退潮", "主升", "修复", "分歧", "震荡"}
SECTOR_TRENDS = {"上涨", "横盘震荡", "下跌"}
SECTOR_PERMISSIONS = {"支持", "观察", "回避"}
# `amv_0.quality` 的取值域。只有 merge_incremental_market 会置 confirmed
# （数据日可证）；collector 手工 --amv 时刻意不置，门控按 candidate 处理。
AMV_QUALITY = {"confirmed", "candidate", "auto", "missing", "unknown"}
FRESHNESS = {"confirmed", "stale", "missing", "candidate", "auto"}

# ── 已知的 new_position_permission 取值。它是**从 markdown 报告正则抽出来的**
#    （chief_decision_report:39 `extract(r'今日是否允许开新仓：\*\*(.*?)\*\*', ...)`），
#    所以不能强枚举 —— 上游报告改一个字就会出现新值。
#    这里只作 warning 白名单：出现未知值时提示「上游措辞可能变了」，不阻断。
KNOWN_PERMISSIONS = {"禁止", "仅观察，不得加仓", "原则不允许", "允许", "谨慎允许", "待确认"}

_TYPE_NAMES = {str: "字符串", dict: "对象", list: "数组", bool: "布尔", (int, float): "数字"}


def _type_name(t) -> str:
    return _TYPE_NAMES.get(t, getattr(t, "__name__", str(t)))


def _check_field(path: str, value: Any, spec: dict, errors: list, warnings: list) -> None:
    if value is None:
        if spec.get("nullable"):
            # ⚠️ `nullable` 只给**渐进填充产物**里「刻意留 None」的字段用，
            # 且必须能说出为什么。反例：`market_timing_input.amv_0.as_of` 就是
            # 刻意的 —— 08:50 手工填的 0AMV 属哪个数据日无法自证，
            # 「编一个 as_of 等于给门控一个假的新鲜度」（源码原话）。
            # 不要拿它当「懒得填」的出口：null 与缺失在下游会走不同分支。
            return
        errors.append(f"{path}: 值为 null（字段存在但没有内容 —— "
                      f"`.get(k, 默认值)` 在这种情况下返回 None 而不是默认值）")
        return
    want = spec.get("type")
    if want is not None:
        # bool 是 int 的子类，数字校验要排除它，否则 True 会被当成 1 通过
        if want == (int, float) and isinstance(value, bool):
            errors.append(f"{path}: 期望数字，得到布尔")
            return
        if not isinstance(value, want):
            errors.append(f"{path}: 期望{_type_name(want)}，得到 {type(value).__name__}")
            return
    if spec.get("non_empty") and not str(value).strip():
        errors.append(f"{path}: 不得为空串")
    if spec.get("finite") and isinstance(value, (int, float)) and not math.isfinite(value):
        errors.append(f"{path}: 非有限值（{value}）—— NaN/Infinity 不是合法 JSON，"
                      f"且会让下游数值比较静默为 False")
    choices = spec.get("choices")
    if choices and value not in choices:
        errors.append(f"{path}: 取值 {value!r} 不在允许集合 {sorted(choices)} 内")
    known = spec.get("known")
    if known and value not in known:
        warnings.append(f"{path}: 取值 {value!r} 不在已知集合内（上游措辞可能变了）")
    if spec.get("fields") and isinstance(value, dict):
        _check_obj(path, value, spec["fields"], errors, warnings)
    if spec.get("items") and isinstance(value, list):
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                errors.append(f"{path}[{i}]: 期望对象，得到 {type(item).__name__}")
                continue
            _check_obj(f"{path}[{i}]", item, spec["items"], errors, warnings)


def _check_obj(path: str, obj: dict, fields: dict, errors: list, warnings: list) -> None:
    for name, spec in fields.items():
        p = f"{path}.{name}" if path else name
        if name not in obj:
            if spec.get("required"):
                errors.append(f"{p}: 缺失")
            continue
        _check_field(p, obj[name], spec, errors, warnings)


# ══════════════════════════════════════════════════════════════════════════
# 契约定义。字段集来自**实测生产者代码**，不是设计稿。
# ══════════════════════════════════════════════════════════════════════════

_GATE_BLOCK = {
    "status": {"type": str, "required": True, "choices": GATE_STATUS},
}

SPECS: dict[str, dict] = {
    # runtime_guards.write_runtime_gate
    "runtime_gate": {
        "kind": "object",
        "fields": {
            "date": {"type": str, "required": True, "non_empty": True},
            "calendar": {"type": dict, "required": True, "fields": {
                "is_trading_day": {"type": bool, "required": True},
            }},
            "position_freshness": {"type": dict, "required": True, "fields": {
                "status": {"type": str, "required": True},
            }},
            "technical_freshness": {"type": dict, "required": True, "fields": {
                "status": {"type": str, "required": True, "choices": FRESHNESS},
            }},
            # ⚠️ 这三个布尔是**权限本身**。缺失或为 null 时下游的
            # `is False` / `== "blocked"` 判定会落空 ⇒ 未获授权被当成已获授权。
            "position_gate": {"type": dict, "required": True, "fields": {
                "status": {"type": str, "required": True, "choices": GATE_STATUS},
                "allow_position_increase": {"type": bool, "required": True},
                "allow_position_reduction": {"type": bool, "required": True},
                "allow_precise_quantity": {"type": bool, "required": True},
                "market_regime": {"type": str, "required": True, "choices": REGIMES},
                "limitations": {"type": list, "required": True},
            }},
            "market_quality": {"type": dict, "required": True, "fields": {
                "status": {"type": str, "required": True, "choices": GATE_STATUS},
                "quality_score": {"type": (int, float), "required": True, "finite": True},
                "checks": {"type": list, "required": True},
                "limitations": {"type": list, "required": True},
            }},
            "generated_at": {"type": str, "required": True, "non_empty": True},
        },
    },
    # batch_holding_technical.analyze_one —— 落盘是**数组**（每持仓一条）。
    # **11 个消费者**（扇出第二），其中 27 处读 `code`、8 处读 `latest_date`。
    # ⚠️ 这是**分支型产物**：`technical_available=False` 时后面那堆技术字段**全不存在**
    # （生产者直接 `return {**it, 'technical_available': False, 'technical_error': ...}`）。
    # 所以契约只能要求这两个字段 + code —— 要求技术字段会让「取不到技术面」这个
    # 正常状态被判成畸形产物。技术字段的存在性由消费端按 `technical_available` 分支判。
    "holding_technical_summary": {
        "kind": "array",
        "items": {
            "code": {"type": str, "required": True, "non_empty": True},
            # ⚠️ 消费端 8 处读它做**陈旧判定**（`runtime_gate.technical_freshness`
            # 就靠它比对目标日）。取不到技术面时它不存在，所以这里不 required——
            # 但 `technical_available=True` 却缺 latest_date 是矛盾态，见下面 warning。
            "technical_available": {"type": bool, "required": True},
        },
    },

    # market_timing_collector.main —— ⚠️ **渐进填充产物**（见模块 docstring）
    #
    # 它是全项目扇出最大的产物：**19 个消费者**，其中 12 个读 `amv_0`。
    # 契约只管**结构**：多个 stage 依次改写它
    # （collector → sync_compass_amv 填 amv_0day → merge 置 quality/effective_state
    #  → amv_state 切 regime），要求「值已填」会让第一个写者就失败。
    "market_timing_input": {
        "kind": "object",
        "fields": {
            "date": {"type": str, "required": True, "non_empty": True},
            "collector_version": {"type": str, "required": True, "non_empty": True},
            # ⚠️ 12 个消费者读它，也是审计 B1 与 v0.22（confirmed 门）的所在。
            "amv_0": {"type": dict, "required": True, "fields": {
                # `amv_zone` 由 `amv_zone(args.amv)` 派生，collector 一定会写。
                # **不设 non_empty**：实测 0AMV 未填时它就是空串（已核对真实产出）。
                "amv_zone": {"type": str, "required": True},
                # 0AMV 是**盘后**指标，08:50 手工 --amv 时值可能还没有 ⇒ 允许 null。
                # 门控按「缺 0AMV」降级处理（不得 pass），那是既有校准过的行为。
                "amv_change_pct": {"type": (int, float), "required": True,
                                   "nullable": True, "finite": True},
                # ⚠️ **刻意留 None**（collector 源码原话）：08:50 手工读数属哪个数据日
                # 无法自证（盘前能看到的最新值其实是 T-1），
                # 「编一个 as_of 等于给门控一个假的新鲜度」。
                # 唯一会写它的是 merge_incremental_market（数据日可证）。
                "as_of": {"type": str, "required": True, "nullable": True},
                # ⚠️ 以下两个由 **merge_incremental_market** 写，collector 刻意不置
                # （门控按 candidate 处理）⇒ `required=False`，
                # 但**出现时必须在域内** —— 这正是审计 B1 的所在：
                # `effective_state` 写成「空头触发」这种未归一的值，会让
                # 下游精确等值比较落空、`allow_add=False` 漏置。
                "quality": {"type": str, "required": False, "choices": AMV_QUALITY},
                "effective_state": {"type": str, "required": False, "choices": REGIMES},
            }},
            "overseas_market": {"type": dict, "required": True},
            "a_share_indices": {"type": dict, "required": True},
            "market_breadth": {"type": dict, "required": True},
            "sentiment": {"type": dict, "required": True},
            "turnover": {"type": dict, "required": True},
            "theme": {"type": dict, "required": True},
            "macro_policy": {"type": dict, "required": True},
            "data_quality": {"type": dict, "required": True},
        },
    },
    # generate_risk_and_sectors.build_risk_decision
    "risk_decision": {
        "kind": "object",
        "fields": {
            "date": {"type": str, "required": True, "non_empty": True},
            # ⚠️ **证据日 ≠ 运行日**：09:05 盘前也产 risk_decision，那时当日 K 线
            # 不存在 ⇒ 依据是前一交易日收盘。缺这个字段的话，下游只能按文件名
            # 判「当日」，把 T-1 的风控依据显示成今天的。允许空串（技术面全缺时）。
            "evidence_date": {"type": str, "required": True},
            "market_regime": {"type": str, "required": True, "choices": REGIMES},
            "regime_directive": {"type": dict, "required": True, "fields": {
                "reduce_top_priority": {"type": bool, "required": True},
            }},
            "risk_level": {"type": str, "required": True, "choices": RISK_LEVELS},
            "forbidden_actions": {"type": list, "required": True},
            "stock_risks": {"type": list, "required": True, "items": {
                "code": {"type": str, "required": True, "non_empty": True},
                "risk_type": {"type": str, "required": True},
                "action": {"type": str, "required": True, "non_empty": True},
                "priority": {"type": str, "required": True, "choices": RISK_PRIORITY},
                "reason": {"type": str, "required": True, "non_empty": True},
            }},
        },
    },
    # chief_decision_report.main
    "chief_decision": {
        "kind": "object",
        "fields": {
            "date": {"type": str, "required": True, "non_empty": True},
            "market_state": {"type": str, "required": True, "non_empty": True},
            "total_position_range": {"type": str, "required": True, "non_empty": True},
            # 从 markdown 正则抽取 ⇒ 只作已知值告警，不强枚举
            "new_position_permission": {"type": str, "required": True, "non_empty": True,
                                        "known": KNOWN_PERMISSIONS},
            "risk_level": {"type": str, "required": True, "choices": RISK_LEVELS},
            "position_gate": {"type": dict, "required": True},
            "market_quality": {"type": dict, "required": True},
            "allowed_actions": {"type": list, "required": True},
            "forbidden_actions": {"type": list, "required": True},
            "holding_actions": {"type": list, "required": True, "items": {
                "priority": {"type": str, "required": True, "choices": B1_PRIORITY},
                "code": {"type": str, "required": True, "non_empty": True},
                "action": {"type": str, "required": True, "non_empty": True},
                "reasons": {"type": list, "required": True},
            }},
            "buy_actions": {"type": list, "required": True},
            "risk_notice": {"type": str, "required": True, "non_empty": True},
            "sources": {"type": dict, "required": True, "fields": {
                "risk_decision": {"type": str, "required": True, "non_empty": True},
                "runtime_gate": {"type": str, "required": True, "non_empty": True},
            }},
        },
    },
    # generate_risk_and_sectors.build_sector_state —— 落盘是**数组**。
    # 6 个消费者。⚠️ `score` 的 NaN 曾让 `nan >= 60` 恒为 False ⇒ 板块**静默降级**
    # 成「观察」，2026-08-07 已在生产者侧过 `fnum`（NaN→None）；契约在此把它钉住。
    "sector_state": {
        "kind": "array",
        "items": {
            "date": {"type": str, "required": True, "non_empty": True},
            "sector": {"type": str, "required": True, "non_empty": True},
            "state": {"type": str, "required": True, "choices": SECTOR_STATES},
            "trend": {"type": str, "required": True, "choices": SECTOR_TRENDS},
            "trade_permission": {"type": str, "required": True,
                                 "choices": SECTOR_PERMISSIONS},
            # 允许 null（板块技术面没给分是正常的），但**不允许 NaN** ——
            # 那会让下游阈值判定静默为 False。
            "score": {"type": (int, float), "required": True,
                      "nullable": True, "finite": True},
            "risk_flags": {"type": list, "required": True},
        },
    },

    # collect_holding_quotes.main —— 5 个消费者，⛔ 硬失败链
    # ⚠️ **分支型**：取不到数的票只有 `{code, name, market, available: False, reason}`，
    # 所以只有 `code`/`name`/`available` 是普遍字段。
    "holding_quotes": {
        "kind": "object",
        "fields": {
            "as_of_date": {"type": str, "required": True, "non_empty": True},
            "captured_at": {"type": str, "required": True, "non_empty": True},
            "source": {"type": str, "required": True, "non_empty": True},
            "quotes": {"type": list, "required": True, "items": {
                "code": {"type": str, "required": True, "non_empty": True},
                "available": {"type": bool, "required": True},
                # `price` 由落盘时归一补上（`q["price"] = q.get("close")`）——
                # 5 个 quote 变体里有 5 个原本只有 close。**5 个消费者读 price**，
                # 所以它是契约的一部分，不是实现细节。取不到数的票没有它 ⇒ 非必填。
            }},
            "indices": {"type": dict, "required": True},
            "breadth": {"type": dict, "required": True},
        },
    },
    # theme_tracker_report.build_sector_summary —— 3 个消费者，⛔ 硬失败链
    # ⚠️ **分支型**：`available=False` 的板块只有
    # `{theme_id, theme_name, priority, available, reason, representative_stocks,
    #   semantic_tags}`，技术字段全不存在。
    # 消费端有 **96 处 `.get("available")`** —— 这个布尔是全项目最常被读的分支键。
    "sector_technical_summary": {
        "kind": "array",
        "items": {
            "theme_id": {"type": str, "required": True, "non_empty": True},
            "theme_name": {"type": str, "required": True, "non_empty": True},
            "available": {"type": bool, "required": True},
        },
    },
    # execution_review.main —— 2 个消费者，⛔ 硬失败链
    "execution_review": {
        "kind": "object",
        "fields": {
            "date": {"type": str, "required": True, "non_empty": True},
            "status": {"type": str, "required": True, "non_empty": True},
            "recorded_trade_count": {"type": (int, float), "required": True, "finite": True},
            "no_trades_confirmed": {"type": bool, "required": True},
            "premarket_snapshot_available": {"type": bool, "required": True},
            "rows": {"type": list, "required": True},
            # ⚠️ `behavior_checks` 是纪律核查结论，`missing` 是数据缺口清单 ——
            # 两者混淆会让「缺文件」看起来像「违纪」（见 weekly_review 同类教训）。
            "behavior_checks": {"type": dict, "required": True},
            "missing": {"type": list, "required": True},
            "sources": {"type": list, "required": True},
        },
    },
    # review_enrichment.main —— ⛔ 硬失败链
    "review_enrichment": {
        "kind": "object",
        "fields": {
            "date": {"type": str, "required": True, "non_empty": True},
            "theme_lifecycles": {"type": list, "required": True},
            "holding_diagnoses": {"type": list, "required": True},
            "next_day_plan": {"type": dict, "required": True, "fields": {
                "holding_plans": {"type": list, "required": True, "items": {
                    "code": {"type": str, "required": True, "non_empty": True},
                    "priority": {"type": str, "required": True, "choices": B1_PRIORITY},
                    "direction": {"type": str, "required": True, "non_empty": True},
                    # ⚠️ **必须恒为 None**：精确减仓量另需当日行情授权
                    # （`runtime_gate.position_gate.allow_precise_quantity`），
                    # 复盘层无权给出。契约把这条钉死。
                    "exact_quantity": {"type": (int, float), "required": True,
                                       "nullable": True, "finite": True},
                }},
            }},
            "rule_review": {"type": dict, "required": True},
            "unavailable": {"type": list, "required": True},
            # ⚠️ 复盘层是**解释**不是裁决 —— 这句必须在产物里。
            "permission_rule": {"type": str, "required": True, "non_empty": True},
        },
    },

    # b1_holding_state.evaluate —— 落盘是**数组**（每持仓一条）
    "b1_holding_state": {
        "kind": "array",
        "items": {
            "version": {"type": str, "required": True, "non_empty": True},
            "code": {"type": str, "required": True, "non_empty": True},
            "market_regime": {"type": str, "required": True},
            "final_priority": {"type": str, "required": True, "choices": B1_PRIORITY},
            "final_action": {"type": str, "required": True, "non_empty": True},
            "final_reason": {"type": str, "required": True},
            "signals": {"type": list, "required": True, "items": {
                "signal": {"type": str, "required": True, "non_empty": True},
                "priority": {"type": str, "required": True, "choices": B1_PRIORITY},
                "action": {"type": str, "required": True, "non_empty": True},
                "reason": {"type": str, "required": True, "non_empty": True},
            }},
            "unavailable": {"type": list, "required": True},
            "facts": {"type": dict, "required": True},
        },
    },
}


def _narrow(fields: dict, only: tuple[str, ...]) -> tuple[dict, list[str]]:
    """按 `only` 裁剪 spec，支持**点号路径**（`"amv_0.quality"`）。

    ⚠️ 责任是**字段级**的，不是节级。实例：`merge_incremental_market` 只写
    `amv_0` 里的 `quality`/`effective_state`/`as_of`，**不拥有** `amv_zone`
    （那是 collector 从 `--amv` 派生的）。给它 `only=("amv_0",)` 会让它为
    collector 的字段背责 —— 那正是第一版踩到的（既有测试的最小 fixture
    没有 amv_zone，merge 直接硬失败）。
    """
    keep: dict = {}
    unknown: list[str] = []
    for path in only:
        head, _, rest = path.partition(".")
        if head not in fields:
            unknown.append(path)
            continue
        if not rest:
            # ⚠️ **`only` 一律去掉 `required`**。部分写者的责任是
            # 「**我写的字段格式正确**」，字段是否**存在**是文档创建者的责任。
            #
            # 这不是放松，是把责任划清。两次实测都撞在这上面：
            #   · merge_incremental 用 `setdefault`，只为**有增量数据的节**建节
            #     ⇒ 只给了 breadth 增量时，sentiment/turnover 本就不该存在
            #   · merge 的第一处落盘根本不碰 amv_0 ⇒ 要求 amv_0 存在是替 collector 背责
            # 存在性由 collector 侧的完整 `require("market_timing_input", data)` 保证。
            keep[head] = {**fields[head], "required": False}
            continue
        sub = fields[head].get("fields") or {}
        narrowed, sub_unknown = _narrow(sub, (rest,))
        unknown += [f"{head}.{x}" for x in sub_unknown]
        if narrowed:
            # ⚠️ 起点必须**去掉原始的 fields**，否则把裁剪结果合并回完整集
            # 等于没裁（第一版就是这个 bug：`amv_0.amv_zone: 缺失` 仍然报出来，
            # 而 amv_zone 根本不在 only 里）。
            prev = keep.get(head)
            base = dict(prev) if prev else {k: v for k, v in fields[head].items()
                                           if k != "fields"}
            keep[head] = {**base, "required": False,
                          "fields": {**(base.get("fields") or {}), **narrowed}}
    return keep, unknown


def check(name: str, obj: Any, only: tuple[str, ...] | None = None) -> dict[str, Any]:
    """校验产物，返回 `{"artifact","valid","errors","warnings"}`。**不抛异常。**

    供消费端使用：拿到结构化结论后由调用方按**自己既有的**降级策略裁决。
    刻意不在这里决定阻断与否 —— 见模块 docstring「写严、读松」。

    `only`：只校验这几个顶层字段。用于**部分写者** ——

        每个写者只保证**自己负责的那部分**，不为别人写的部分背责。

    ⚠️ 这个参数不是「放松校验」的出口，它是**责任边界**。实例：
    `market_timing_input` 由 4 个 stage 依次改写，`merge_incremental_market`
    只修补 `amv_0`（置 quality/effective_state/as_of）。要它保证整份 11 节文档
    等于要它为 collector 的产出背责 —— 而它既没写那些节、也无法补出来。
    真正该由它保证的是「改完之后 `amv_0` 仍然合规」。
    """
    spec = SPECS.get(name)
    if spec is None:
        return {"artifact": name, "valid": True, "errors": [],
                "warnings": [f"{name}: 尚未定义契约（已覆盖 {len(SPECS)} 个，"
                             f"见 SPECS；其余产物暂按无契约处理）"]}
    errors: list[str] = []
    warnings: list[str] = []
    if spec["kind"] == "array":
        if not isinstance(obj, list):
            errors.append(f"{name}: 期望数组，得到 {type(obj).__name__}")
        else:
            for i, item in enumerate(obj):
                if not isinstance(item, dict):
                    errors.append(f"{name}[{i}]: 期望对象，得到 {type(item).__name__}")
                    continue
                _check_obj(f"{name}[{i}]", item, spec["items"], errors, warnings)
    else:
        if not isinstance(obj, dict):
            errors.append(f"{name}: 期望对象，得到 {type(obj).__name__}")
        else:
            fields = spec["fields"]
            if only is not None:
                fields, unknown = _narrow(fields, only)
                if unknown:
                    warnings.append(f"{name}: only 里有契约未定义的路径 {unknown}"
                                    f" —— 拼错了会让校验变成空操作")
            _check_obj("", obj, fields, errors, warnings)
    return {"artifact": name, "valid": not errors, "errors": errors, "warnings": warnings}


def require(name: str, obj: Any, only: tuple[str, ...] | None = None) -> Any:
    """校验产物，不合规**当场 SystemExit**。供**生产者**在落盘前调用。

    为什么生产者要硬失败：产物是它自己造的，造出畸形产物没有正当理由。
    在源头失败让排错从「下游哪个字段是 None」变成「哪个生产者写坏了」。

    `only` 见 `check()` —— 部分写者只为自己改的那部分背责。
    """
    result = check(name, obj, only=only)
    if not result["valid"]:
        raise SystemExit(
            f"产物契约校验失败 [{name}]（见 07_tools/contracts.py）：\n  "
            + "\n  ".join(result["errors"]))
    return obj
