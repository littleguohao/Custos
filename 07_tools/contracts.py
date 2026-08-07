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

## 只覆盖钱的路径

四个产物：`runtime_gate`（权限总闸）、`risk_decision`（风控否决）、
`chief_decision`（最终交易计划）、`b1_holding_state`（持仓动作）。
其余 15 种产物暂不纳入 —— 先在最贵的路径上验证这套机制的成本与收益。
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
    # generate_risk_and_sectors.build_risk_decision
    "risk_decision": {
        "kind": "object",
        "fields": {
            "date": {"type": str, "required": True, "non_empty": True},
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


def check(name: str, obj: Any) -> dict[str, Any]:
    """校验产物，返回 `{"artifact","valid","errors","warnings"}`。**不抛异常。**

    供消费端使用：拿到结构化结论后由调用方按**自己既有的**降级策略裁决。
    刻意不在这里决定阻断与否 —— 见模块 docstring「写严、读松」。
    """
    spec = SPECS.get(name)
    if spec is None:
        return {"artifact": name, "valid": True, "errors": [],
                "warnings": [f"{name}: 尚未定义契约（当前只覆盖钱的路径 4 个产物）"]}
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
            _check_obj("", obj, spec["fields"], errors, warnings)
    return {"artifact": name, "valid": not errors, "errors": errors, "warnings": warnings}


def require(name: str, obj: Any) -> Any:
    """校验产物，不合规**当场 SystemExit**。供**生产者**在落盘前调用。

    为什么生产者要硬失败：产物是它自己造的，造出畸形产物没有正当理由。
    在源头失败让排错从「下游哪个字段是 None」变成「哪个生产者写坏了」。
    """
    result = check(name, obj)
    if not result["valid"]:
        raise SystemExit(
            f"产物契约校验失败 [{name}]（见 07_tools/contracts.py）：\n  "
            + "\n  ".join(result["errors"]))
    return obj
