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

`governance/contracts/DATA_FLOW_CONTRACT.md` 是文档，**不参与执行**，
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

## 能力边界：只查**字段**，查不出**跨字段矛盾**

契约能查「字段存在 / 类型对 / 枚举在域内 / 非有限值拒收」，
**查不出多个字段之间的逻辑矛盾**。实例：

    {"transport_verified": False, "confirmed": True, "quality": "confirmed"}

每个字段单看都合法，但组合起来自相矛盾（传输未校验却声称已确认）。
这类不变量靠**单元测试**保证 —— 上例由
`tests/test_rss_collector.py::TestTierQuality::test_unverified_transport_never_confirmed`
覆盖（参数化四个 tier，全部要求 candidate）。

不把跨字段检查做进契约是有意的：那需要在 spec 里写谓词，
而谓词的可读性和可测性都不如一条带说明的单元测试。

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
REGIMES = {"做多", "中性", "空头", "未知"}  # runtime_guards.normalize_regime 的输出域
RISK_LEVELS = {
    "普通",
    "提高",
    "强风控",
}  # generate_risk_and_sectors.build_risk_decision
GATE_STATUS = {
    "pass",
    "degraded",
    "blocked",
}  # runtime_guards.market_quality_gate / position_gate
RISK_PRIORITY = {"高", "中", "低"}  # build_risk_decision
B1_PRIORITY = {"P0", "P1", "P2", "P3"}  # b1_holding_state.evaluate
# generate_risk_and_sectors.build_sector_state 的三个枚举域（与 normalize_stage 一致）
SECTOR_STATES = {"退潮", "主升", "修复", "分歧", "震荡"}
SECTOR_TRENDS = {"上涨", "横盘震荡", "下跌"}
SECTOR_PERMISSIONS = {"支持", "观察", "回避"}
# `amv_0.quality` 的取值域。只有 merge_incremental_market 会置 confirmed
# （数据日可证）；collector 手工 --amv 时刻意不置，门控按 candidate 处理。
AMV_QUALITY = {"confirmed", "candidate", "auto", "missing", "unknown"}
# score_candidates 的分层域（A/B/C/D 四档，RESONANCE_MATRIX 的值域）
BUCKETS = {"A", "B", "C", "D"}
# final_close_review 的报告可信度
REPORT_QUALITY = {"complete", "degraded"}
# rss_collector._tier_quality 的输出域
EVIDENCE_QUALITY = {"confirmed", "candidate"}
FRESHNESS = {"confirmed", "stale", "missing", "candidate", "auto"}

# ── `market_timing_input` **各 section 的 `quality`** 取值域（与上面的 FRESHNESS 无关，
#    后者只管 `runtime_gate.technical_freshness.status`）。
# ⚠️ 两个生产者用的是**不同词表**，2026-08-10 清点才发现：
#       merge_incremental_market.section_quality  → auto / stale / raw_only
#       market_timing_collector._freshness        → auto / degraded（+ missing 分支）
#    而消费者 `market_timing_scorer.is_stale` **只认 `"stale"` 这一个词**
#    ⇒ `raw_only` 与 `degraded` 都被当成新鲜、照满分计入评分，
#      实测 `score_breadth` 给 11 分（满分 15）且归因文案完全正常
#      —— 读报告的人无从知道这个分数建立在「不知道是哪天的数据」上。
SECTION_QUALITY = {"auto", "stale", "raw_only", "degraded", "missing"}
# 「不可按当日满分计入」的那些：**生产者已经声明过不新鲜/不可证**，消费者必须听。
# ⚠️ 判据是**生产者声明**，不是「有没有 as_of」—— 后者是更宽的 fail-closed。
#    2026-08-12（#45②，v0.47）三段（market_breadth/sentiment/turnover）的 as_of
#    已补进契约必填（nullable），scorer 的 is_stale 判据②按 as_of≠当日 判陈旧；
#    其余段仍只按本词表判。
SECTION_NOT_FRESH = {"stale", "raw_only", "degraded", "missing"}

# ── 已知的 new_position_permission 取值。它是**从 markdown 报告正则抽出来的**
#    （chief_decision_report:39 `extract(r'今日是否允许开新仓：\*\*(.*?)\*\*', ...)`），
#    所以不能强枚举 —— 上游报告改一个字就会出现新值。
#    这里只作 warning 白名单：出现未知值时提示「上游措辞可能变了」，不阻断。
KNOWN_PERMISSIONS = {
    "禁止",
    "仅观察，不得加仓",
    "原则不允许",
    "允许",
    "谨慎允许",
    "待确认",
}

_TYPE_NAMES = {
    str: "字符串",
    dict: "对象",
    list: "数组",
    bool: "布尔",
    (int, float): "数字",
}


def _type_name(t) -> str:
    return _TYPE_NAMES.get(t, getattr(t, "__name__", str(t)))


def _check_field(
    path: str, value: Any, spec: dict, errors: list, warnings: list
) -> None:
    if value is None:
        if spec.get("nullable"):
            # ⚠️ `nullable` 只给**渐进填充产物**里「刻意留 None」的字段用，
            # 且必须能说出为什么。反例：`market_timing_input.amv_0.as_of` 就是
            # 刻意的 —— 08:50 手工填的 0AMV 属哪个数据日无法自证，
            # 「编一个 as_of 等于给门控一个假的新鲜度」（源码原话）。
            # 不要拿它当「懒得填」的出口：null 与缺失在下游会走不同分支。
            return
        errors.append(
            f"{path}: 值为 null（字段存在但没有内容 —— "
            f"`.get(k, 默认值)` 在这种情况下返回 None 而不是默认值）"
        )
        return
    want = spec.get("type")
    if want is not None:
        # bool 是 int 的子类，数字校验要排除它，否则 True 会被当成 1 通过
        if want == (int, float) and isinstance(value, bool):
            errors.append(f"{path}: 期望数字，得到布尔")
            return
        if not isinstance(value, want):
            errors.append(
                f"{path}: 期望{_type_name(want)}，得到 {type(value).__name__}"
            )
            return
    if spec.get("non_empty") and not str(value).strip():
        errors.append(f"{path}: 不得为空串")
    if (
        spec.get("finite")
        and isinstance(value, (int, float))
        and not math.isfinite(value)
    ):
        errors.append(
            f"{path}: 非有限值（{value}）—— NaN/Infinity 不是合法 JSON，"
            f"且会让下游数值比较静默为 False"
        )
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


def _check_obj(
    path: str, obj: dict, fields: dict, errors: list, warnings: list
) -> None:
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

# 报告可审计块（待办 #29，report_audit.build 的输出）。**可选字段**：
# 出现时四件必须齐（report_id / 策略版本 / 数据截止 / 输入清单），
# 不出现不判畸形 —— 旧产物没有它，而契约管的是「写了的东西对不对」。
# `data_as_of` 允许 null：全部输入缺失时没的可报（与「编一个假新鲜度」同理）。
_AUDIT_FIELD = {
    "type": dict,
    "required": False,
    "fields": {
        "report_id": {"type": str, "required": True, "non_empty": True},
        "strategy_version": {"type": str, "required": True, "non_empty": True},
        "data_as_of": {"type": str, "required": True, "nullable": True},
        "inputs": {"type": list, "required": True},
    },
}

SPECS: dict[str, dict] = {
    # runtime_guards.write_runtime_gate
    "runtime_gate": {
        "kind": "object",
        "fields": {
            "date": {"type": str, "required": True, "non_empty": True},
            "calendar": {
                "type": dict,
                "required": True,
                "fields": {
                    "is_trading_day": {"type": bool, "required": True},
                },
            },
            "position_freshness": {
                "type": dict,
                "required": True,
                "fields": {
                    "status": {"type": str, "required": True},
                },
            },
            "technical_freshness": {
                "type": dict,
                "required": True,
                "fields": {
                    "status": {"type": str, "required": True, "choices": FRESHNESS},
                },
            },
            # ⚠️ 这三个布尔是**权限本身**。缺失或为 null 时下游的
            # `is False` / `== "blocked"` 判定会落空 ⇒ 未获授权被当成已获授权。
            "position_gate": {
                "type": dict,
                "required": True,
                "fields": {
                    "status": {"type": str, "required": True, "choices": GATE_STATUS},
                    "allow_position_increase": {"type": bool, "required": True},
                    "allow_position_reduction": {"type": bool, "required": True},
                    "allow_precise_quantity": {"type": bool, "required": True},
                    "market_regime": {
                        "type": str,
                        "required": True,
                        "choices": REGIMES,
                    },
                    "limitations": {"type": list, "required": True},
                },
            },
            "market_quality": {
                "type": dict,
                "required": True,
                "fields": {
                    "status": {"type": str, "required": True, "choices": GATE_STATUS},
                    "quality_score": {
                        "type": (int, float),
                        "required": True,
                        "finite": True,
                    },
                    "checks": {"type": list, "required": True},
                    "limitations": {"type": list, "required": True},
                },
            },
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
            # ⚠️ `latest_date` 刻意**不在** spec 里：消费端 8 处读它做**陈旧判定**
            # （`runtime_gate.technical_freshness` 就靠它比对目标日），
            # 但取不到技术面时它根本不存在（分支型产物，见上），要求它会让
            # 「取不到技术面」这个正常状态被判成畸形。
            # 「`technical_available=True` 却缺 latest_date」是矛盾态 ——
            # 但那是**跨字段**不变量，按本模块「只查字段、查不出跨字段矛盾」的
            # 能力边界（见模块 docstring），契约不查，靠单元测试保证。
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
            "amv_0": {
                "type": dict,
                "required": True,
                "fields": {
                    # `amv_zone` 由 `amv_zone(args.amv)` 派生，collector 一定会写。
                    # **不设 non_empty**：实测 0AMV 未填时它就是空串（已核对真实产出）。
                    "amv_zone": {"type": str, "required": True},
                    # 0AMV 是**盘后**指标，08:50 手工 --amv 时值可能还没有 ⇒ 允许 null。
                    # 门控按「缺 0AMV」降级处理（不得 pass），那是既有校准过的行为。
                    "amv_change_pct": {
                        "type": (int, float),
                        "required": True,
                        "nullable": True,
                        "finite": True,
                    },
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
                    "effective_state": {
                        "type": str,
                        "required": False,
                        "choices": REGIMES,
                    },
                },
            },
            "overseas_market": {
                "type": dict,
                "required": True,
                "fields": {
                    # ⚠️ `required + nullable`，与 `amv_0.as_of` 同形（2026-08-10，TODO #52）：
                    #    键必须在（缺失与 null 在门控里走不同分支），值允许 None
                    #    —— 一个 symbol 都没给时间戳时**不许编一个**，原话
                    #    「编一个 as_of 等于给门控一个假的新鲜度」。
                    #    ⚠️ 契约只能保证键存在，**拦不住重新伪造** ——
                    #    那件事由 `tests/test_tdx_ext_fallback.py::TestOverseasAsOfDerivation` 守。
                    "as_of": {"type": str, "required": True, "nullable": True},
                },
            },
            "a_share_indices": {"type": dict, "required": True},
            # 2026-08-12（TODO #45②，owner 拍板）：三段补 `as_of` 必填（nullable），
            # 与 `overseas_market.as_of`/`amv_0.as_of` 同形——键必须在、值允许 None
            # （「编一个 as_of 等于给门控假新鲜度」）。前置普查：三个生产者
            # （collector 的 derive_market_fields 初值 None、merge_incremental、
            # refresh_market_indices）本就键恒在 ⇒ 补契约不会硬失败（#52 教训：
            # 先补生产者再补契约）。补契约后 is_stale 的 as_of 分支（scorer :200）
            # 才真正可靠——此前段里没这键时它静默落空。
            "market_breadth": {
                "type": dict,
                "required": True,
                "fields": {
                    "as_of": {"type": str, "required": True, "nullable": True},
                },
            },
            "sentiment": {
                "type": dict,
                "required": True,
                "fields": {
                    "as_of": {"type": str, "required": True, "nullable": True},
                },
            },
            "turnover": {
                "type": dict,
                "required": True,
                "fields": {
                    "as_of": {"type": str, "required": True, "nullable": True},
                },
            },
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
            "regime_directive": {
                "type": dict,
                "required": True,
                "fields": {
                    "reduce_top_priority": {"type": bool, "required": True},
                },
            },
            "risk_level": {"type": str, "required": True, "choices": RISK_LEVELS},
            "forbidden_actions": {"type": list, "required": True},
            "stock_risks": {
                "type": list,
                "required": True,
                "items": {
                    "code": {"type": str, "required": True, "non_empty": True},
                    "risk_type": {"type": str, "required": True},
                    "action": {"type": str, "required": True, "non_empty": True},
                    "priority": {
                        "type": str,
                        "required": True,
                        "choices": RISK_PRIORITY,
                    },
                    "reason": {"type": str, "required": True, "non_empty": True},
                },
            },
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
            "new_position_permission": {
                "type": str,
                "required": True,
                "non_empty": True,
                "known": KNOWN_PERMISSIONS,
            },
            "risk_level": {"type": str, "required": True, "choices": RISK_LEVELS},
            "position_gate": {"type": dict, "required": True},
            "market_quality": {"type": dict, "required": True},
            "allowed_actions": {"type": list, "required": True},
            "forbidden_actions": {"type": list, "required": True},
            "holding_actions": {
                "type": list,
                "required": True,
                "items": {
                    "priority": {"type": str, "required": True, "choices": B1_PRIORITY},
                    "code": {"type": str, "required": True, "non_empty": True},
                    "action": {"type": str, "required": True, "non_empty": True},
                    "reasons": {"type": list, "required": True},
                },
            },
            "buy_actions": {"type": list, "required": True},
            "risk_notice": {"type": str, "required": True, "non_empty": True},
            "sources": {
                "type": dict,
                "required": True,
                "fields": {
                    "risk_decision": {"type": str, "required": True, "non_empty": True},
                    "runtime_gate": {"type": str, "required": True, "non_empty": True},
                },
            },
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
            "trade_permission": {
                "type": str,
                "required": True,
                "choices": SECTOR_PERMISSIONS,
            },
            # 允许 null（板块技术面没给分是正常的），但**不允许 NaN** ——
            # 那会让下游阈值判定静默为 False。
            "score": {
                "type": (int, float),
                "required": True,
                "nullable": True,
                "finite": True,
            },
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
            "quotes": {
                "type": list,
                "required": True,
                "items": {
                    "code": {"type": str, "required": True, "non_empty": True},
                    "available": {"type": bool, "required": True},
                    # `price` 由落盘时归一补上（`q["price"] = q.get("close")`）——
                    # 5 个 quote 变体里有 5 个原本只有 close。**5 个消费者读 price**，
                    # 所以它是契约的一部分，不是实现细节。取不到数的票没有它 ⇒ 非必填。
                },
            },
            # ⚠️ indices 是 **list**（每项 {code, name, close, ...}），不是 dict ——
            # 两个消费端（review_core x2）都按 list 迭代。spec 曾误写成 dict，
            # 而契约 08-07 傍晚才挂上、当时 run_1445 正处在 TOOLS bug 窗口，
            # 生产从未跑过 ⇒ 首个交易日 14:45 必败（08-08 重跑 08-07 时抓到）。
            # items 只钉 code：成功分支没有 available 键（仅失败分支写
            # available=False），别把成功路径判成畸形。
            "indices": {
                "type": list,
                "required": True,
                "items": {
                    "code": {"type": str, "required": True, "non_empty": True},
                },
            },
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
            "recorded_trade_count": {
                "type": (int, float),
                "required": True,
                "finite": True,
            },
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
            "next_day_plan": {
                "type": dict,
                "required": True,
                "fields": {
                    "holding_plans": {
                        "type": list,
                        "required": True,
                        "items": {
                            "code": {"type": str, "required": True, "non_empty": True},
                            "priority": {
                                "type": str,
                                "required": True,
                                "choices": B1_PRIORITY,
                            },
                            "direction": {
                                "type": str,
                                "required": True,
                                "non_empty": True,
                            },
                            # ⚠️ **必须恒为 None**：精确减仓量另需当日行情授权
                            # （`runtime_gate.position_gate.allow_precise_quantity`），
                            # 复盘层无权给出。契约把这条钉死。
                            "exact_quantity": {
                                "type": (int, float),
                                "required": True,
                                "nullable": True,
                                "finite": True,
                            },
                        },
                    },
                },
            },
            "rule_review": {"type": dict, "required": True},
            "unavailable": {"type": list, "required": True},
            # ⚠️ 复盘层是**解释**不是裁决 —— 这句必须在产物里。
            "permission_rule": {"type": str, "required": True, "non_empty": True},
        },
    },
    # ══ 第五批（2026-08-07）：扫描发现的剩余产物
    # ⚠️ **刻意不纳入**的几类（不是漏了）：
    #   · run log / `daily_pipeline_log` / `1445_review.md`
    #       —— **执行痕迹**不是决策产物。stage 成败已由退出码与
    #          `pipeline_kit.write_run_log` 的固定结构保证，再加契约是重复。
    #   · `manual_position_updates` —— **人工输入**，不是本项目的产出，
    #       形状由外部决定；校验点应在读取侧（`apply_manual_position_updates`）。
    #   · `premarket_chief_decision` —— 它是 `chief_decision` 的 `shutil.copy2`
    #       **副本**（daily_pipeline:246），schema 完全相同、源头已 require
    #       ⇒ 副本无需再校验。
    #   · `holding_sector_mapping_enriched` —— 可选产物，取不到时
    #       `batch_holding_technical` 会回落 `current_positions.json`
    #       （daily_pipeline 注释写明），即它缺失是**已设计的正常路径**。
    # collect_intraday_snapshot.main —— 14:45 盘中快照
    "intraday_snapshot": {
        "kind": "object",
        "fields": {
            "date": {"type": str, "required": True, "non_empty": True},
            # ⚠️ 盘中快照的 quality 直接决定 14:45 报告敢不敢用它算动作。
            "quality": {"type": str, "required": True, "non_empty": True},
            # ⚠️ `indices_ok` 是**计数**（成功取到的指数条数）而不是布尔 ——
            # 名字带 `_ok` 容易读成布尔，实测是 int。名字有误导性但不改它：
            # 改名要动消费端，而契约把真实类型写在这里同样能防误用。
            "indices_ok": {"type": (int, float), "required": True, "finite": True},
        },
    },
    # tq_sector.main —— 板块成分表（sector_phase / sector_mainstream 的下层）
    "tq_sector_map": {
        "kind": "object",
        "fields": {
            "as_of": {"type": str, "required": True, "non_empty": True},
            "source": {"type": str, "required": True, "non_empty": True},
            "sector_count": {"type": (int, float), "required": True, "finite": True},
            "stock_total": {"type": (int, float), "required": True, "finite": True},
            # ⚠️ `sectors` 是**数组**不是对象（`tq_sector.py:334` 返回 `sectors` 列表）。
            # 第一版猜成 dict，接生产者时被既有测试当场打回 —— 又一次「不能凭想象写 spec」。
            "sectors": {"type": list, "required": True},
            # ⚠️ `quality.name_coverage` / `sector_success_rate` 决定这份成分表
            # 能不能当全量用 —— 残缺的成分表会让板块因子算在**子集**上而不自知。
            "quality": {"type": dict, "required": True},
            "errors": {"type": list, "required": True},
        },
    },
    # holding_sector_mapper.main —— 落盘是**数组**（持仓 → 板块映射）
    "holding_sector_mapping": {
        "kind": "array",
        "items": {
            "code": {"type": str, "required": True, "non_empty": True},
            "name": {"type": str, "required": True},
        },
    },
    # ══ 第四批（2026-08-07）：硬失败链之外的产物，按需铺完
    # 这批的共同点是**都不阻断日常链**，所以契约的价值主要在
    # 「消费端读到的东西是不是它以为的东西」，而不是防止链路挂掉。
    # score_candidates.score_all —— 选股链终点，18:00 独立链（不阻断三份报告）
    "stock_pool": {
        "kind": "object",
        "fields": {
            "date": {"type": str, "required": True, "non_empty": True},
            "status": {"type": str, "required": True, "non_empty": True},
            "candidates": {
                "type": list,
                "required": True,
                "items": {
                    "code": {"type": str, "required": True, "non_empty": True},
                    # v0.50（#37 阶段 A）分层口径：技术（patterns 累加，60/30）×
                    # 资金意图共振矩阵；s_shape 与板块分**不再驱动分层/总分**
                    # （s_star/s_shape/sector_score 仍随条目落盘，纯展示列）。
                    "bucket": {"type": str, "required": True, "choices": BUCKETS},
                    # ⚠️ `next_step` 是「这只票下一步能做什么」，A/B/C/D 分层的落点。
                    #    v0.50（#37 阶段 A）：A 档值由 "generate_buy_plan" 改名
                    #    "buy_review"（BuyPlan 契约已删，无组件生成买入计划，
                    #    旧名是虚假承诺）。展示读者：daily_report / candidate_table。
                    "next_step": {"type": str, "required": True, "non_empty": True},
                    "risk_flags": {"type": list, "required": True},
                    "entry_reason": {"type": list, "required": True},
                },
            },
            "bucket_counts": {"type": dict, "required": True},
            "audit": _AUDIT_FIELD,
        },
    },
    # final_close_review.main —— 17:00 复盘产物（md 之外的机器可读版）
    "final_review": {
        "kind": "object",
        "fields": {
            "date": {"type": str, "required": True, "non_empty": True},
            # ⚠️ `report_quality` 是「这份复盘有多可信」，degraded 时下游不得当完整证据。
            "report_quality": {
                "type": str,
                "required": True,
                "choices": REPORT_QUALITY,
            },
            "unavailable": {"type": list, "required": True},
            "revalued_positions": {"type": list, "required": True},
            "next_day_plan": {"type": dict, "required": True},
            # ⚠️ 这个布尔来自门控，决定次日计划能不能给精确数量。
            "precise_quantity_allowed": {"type": bool, "required": True},
            "quotes_current": {"type": bool, "required": True},
            "technical_current": {"type": bool, "required": True},
            "audit": _AUDIT_FIELD,
        },
    },
    # portfolio_review_report.main —— 落盘是**数组**。RiskDecision 的直接上游。
    "holding_review": {
        "kind": "array",
        "items": {
            "code": {"type": str, "required": True, "non_empty": True},
            # ⚠️ `action` / `priority` 会被 build_risk_decision 转成风险条目
            # （止损/清仓 ⇒ 高优先），标错就直接错在 RiskDecision 里。
            "action": {"type": str, "required": True, "non_empty": True},
            "priority": {"type": str, "required": True, "choices": B1_PRIORITY},
            "reason": {"type": list, "required": True},
        },
    },
    # calc_mfe_mae.main
    "mfe_mae": {
        "kind": "object",
        "fields": {
            "date": {"type": str, "required": True, "non_empty": True},
            # ⚠️ 必须报 coverage —— 「卖飞 N 笔」不说分母会被读成「没卖飞」
            # （weekly_review._sell_fly_review 同类教训）。
            "coverage": {"type": dict, "required": True},
            "holdings": {"type": list, "required": True},
        },
    },
    # collect_fund_flow.main
    "fund_flow_rank": {
        "kind": "object",
        "fields": {
            "date": {"type": str, "required": True, "non_empty": True},
            "status": {
                "type": str,
                "required": True,
                "choices": {"ok", "partial", "failed"},
            },
            "stock_rank": {"type": list, "required": True},
            "sector_rank": {"type": dict, "required": True},
            # ⚠️ 分板块类型的失败状态要单独留痕：industry 成功而 concept 失败时，
            # 顶层 status=partial 说不出是哪个坏了。
            "sector_rank_status": {"type": dict, "required": True},
            "source": {"type": str, "required": True, "non_empty": True},
        },
    },
    # formula_screen.main
    # ⚠️ 字段取自**落盘的 `result`**，不是只被 print 的 `summary` ——
    # 第一版按 summary 提字段（它有 date/hit_total，result 没有），接生产者时才发现。
    # 教训：契约要对着**真正写进文件的那个对象**建。
    "formula_hits": {
        "kind": "object",
        "fields": {
            "status": {"type": str, "required": True, "non_empty": True},
            "universe_size": {"type": (int, float), "required": True, "finite": True},
            "universe_source": {"type": str, "required": True, "non_empty": True},
            # ⚠️ ST 硬排除的可信度标记 —— `stock_names.resolve_name_map` 的 diag 传下来。
            # 它不是 ok 就意味着 ST 排除不完全可信（见 stock_names 模块 docstring）。
            "st_filter": {"type": str, "required": True, "non_empty": True},
            "formulas": {"type": list, "required": True},
        },
    },
    # enrich_candidates.enrich —— 同上，字段取自落盘的 `result`
    "candidates_enriched": {
        "kind": "object",
        "fields": {
            "status": {"type": str, "required": True, "non_empty": True},
            "candidates": {"type": list, "required": True},
            # ⚠️ 被剔除的票要留清单 —— 否则「候选少」无法归因
            # （是没命中还是被筛掉了）。
            "excluded": {"type": list, "required": True},
            "st_filter": {"type": str, "required": True, "non_empty": True},
            # ⚠️ 公式命中来自 TQ 在线（按最新交易日报出），而本段用本地日线
            # `last_date==date` 校验 —— 两者口径不同，这句话把它写进产物。
            "signal_date_contract": {"type": str, "required": True, "non_empty": True},
        },
    },
    # rss_collector.main —— 落盘是**数组**
    "rss_evidence": {
        "kind": "array",
        "items": {
            "item_id": {"type": str, "required": True, "non_empty": True},
            "source_id": {"type": str, "required": True, "non_empty": True},
            "source_tier": {"type": str, "required": True, "non_empty": True},
            "title": {"type": str, "required": True},
            # ⚠️ 这三个一起决定「这条能不能当既成事实」。
            # `transport_verified=False` 时 quality 必须是 candidate
            # （tier 说的是机构权威性，transport 说的是字节真来自那个机构）。
            "quality": {"type": str, "required": True, "choices": EVIDENCE_QUALITY},
            "confirmed": {"type": bool, "required": True},
            "transport_verified": {"type": bool, "required": True},
        },
    },
    # rss_filter.main —— 落盘是**数组**（选中的候选）
    "rss_candidates": {
        "kind": "array",
        "items": {
            "item_id": {"type": str, "required": True, "non_empty": True},
            "source_id": {"type": str, "required": True, "non_empty": True},
            "relevance_score": {"type": (int, float), "required": True, "finite": True},
            "matched_themes": {"type": list, "required": True},
            "matched_holdings_or_pool": {"type": dict, "required": True},
            "filter_session": {"type": str, "required": True, "non_empty": True},
        },
    },
    # postclose_news_digest.main
    "postclose_news_digest": {
        "kind": "object",
        "fields": {
            "date": {"type": str, "required": True, "non_empty": True},
            "status": {"type": str, "required": True, "non_empty": True},
            "sections": {"type": dict, "required": True},
            "missing": {"type": list, "required": True},
            # ⚠️ 新闻只能加验证条件或收紧风险，**不能直接放宽交易权限**。
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
            "signals": {
                "type": list,
                "required": True,
                "items": {
                    "signal": {"type": str, "required": True, "non_empty": True},
                    "priority": {"type": str, "required": True, "choices": B1_PRIORITY},
                    "action": {"type": str, "required": True, "non_empty": True},
                    "reason": {"type": str, "required": True, "non_empty": True},
                },
            },
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
            base = (
                dict(prev)
                if prev
                else {k: v for k, v in fields[head].items() if k != "fields"}
            )
            keep[head] = {
                **base,
                "required": False,
                "fields": {**(base.get("fields") or {}), **narrowed},
            }
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
        return {
            "artifact": name,
            "valid": True,
            "errors": [],
            "warnings": [
                f"{name}: 尚未定义契约（已覆盖 {len(SPECS)} 个，"
                f"见 SPECS；其余产物暂按无契约处理）"
            ],
        }
    errors: list[str] = []
    warnings: list[str] = []
    if spec["kind"] == "array":
        if only is not None:
            # ⚠️ array 类契约按**条目**校验，`only`（顶层字段裁剪）对它无意义。
            # 不能静默忽略：调用方会误以为自己只校验了部分字段，
            # 实际校验的是整个数组 —— 与 unknown path 一样发 warning。
            warnings.append(
                f"{name}: array 类契约不支持 only（已按整个数组校验）"
                f" —— 拼错了会让校验范围与预期不符"
            )
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
                    warnings.append(
                        f"{name}: only 里有契约未定义的路径 {unknown}"
                        f" —— 拼错了会让校验变成空操作"
                    )
            _check_obj("", obj, fields, errors, warnings)
    return {
        "artifact": name,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def require(name: str, obj: Any, only: tuple[str, ...] | None = None) -> Any:
    """校验产物，不合规**当场 SystemExit**。供**生产者**在落盘前调用。

    为什么生产者要硬失败：产物是它自己造的，造出畸形产物没有正当理由。
    在源头失败让排错从「下游哪个字段是 None」变成「哪个生产者写坏了」。

    `only` 见 `check()` —— 部分写者只为自己改的那部分背责。
    """
    result = check(name, obj, only=only)
    if not result["valid"]:
        raise SystemExit(
            f"产物契约校验失败 [{name}]（见 src/contracts.py）：\n  "
            + "\n  ".join(result["errors"])
        )
    return obj
