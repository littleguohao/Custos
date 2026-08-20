# -*- coding: utf-8 -*-
"""资金意图因子（capital_intent）—— 分层第二轴「资金在进的量价/资金流证据」。

2026-08-20（v0.84，因子×止盈×止损架构 Phase D）从
`pipeline/screening/score_candidates.py` 迁入（原 502-610 行段）。
**行为零变化**：判定逻辑、证据分值、detail 键序逐条未动；score_candidates
改为 import 调用（`sc.capital_intent_strength` 仍是同一函数对象）。

## status 定档理由（candidate）

- `live_use=scorer` / `stage=release` 是**事实**：本因子驱动的
  capital_intent_level 直接进 RESONANCE_MATRIX 决定分层（A/B/C/D）。
- `NOT_FOR_LIVE={needs_work, untested}` 由
  `tests/test_factor_registry.py::test_needs_work_cannot_be_gate_or_scorer`
  机械禁止与 scorer 共存 ⇒ 合规候选只剩 active / candidate。
- `active` 语义是「已验证可用」——本轮只是**搬迁**（零行为变化），没有新增
  任何回测证据，各证据分值仍是「待回测」启发式 ⇒ 不能标 active。
- ⇒ 取 **candidate**（有依据未终审），分值校准挂回测 TODO：是否进分、
  各证据权重由后续回测证据决定（同 Phase E 因子组合寻优的入口）。

## 权重外置（v0.84）

证据分值与分级阈值默认表 `DEFAULT_EVIDENCE_WEIGHTS` == 现值；
`governance/contracts/SCREEN_FORMULA_REGISTRY.json` 的 `scoring.weights`
可覆盖（仿 `score_candidates.resolve_cap_rules`：未知键忽略、默认兜底）。
键名带 `ci_` 前缀以区别于技术分同名证据（b1_ignition/leader_volume 等
在技术分里是另一组分值）。
"""

from __future__ import annotations

from typing import Any, Optional

FACTOR: dict[str, Any] = {
    "id": "capital_intent",
    "name": "资金意图强度（量价证明资金在进）",
    "kind": "state",
    # 见模块 docstring「status 定档理由」：scorer ⇒ 不能是 needs_work/untested；
    # 无回测证据 ⇒ 不能标 active；取 candidate 并挂回测 TODO。
    "status": "candidate",
    "evidence": "",
    "note": "分层第二轴（技术结构 × 资金意图 → base bucket）；证据分值待回测校准",
    "min_bars": 1,
    "live_use": "scorer",
    "stage": "release",
}

# 资金意图强度阈值（待回测）：>=CAP_STRONG 强 / >=CAP_MID 中 / 否则 弱
CAP_STRONG = 5
CAP_MID = 2

# 证据分值默认表 == 现值（v0.84 外置前 score_candidates 内的硬编码值）。
DEFAULT_EVIDENCE_WEIGHTS: dict[str, int] = {
    "ci_b1_ignition": 3,  # B1 点火
    "ci_zhixing_ride": 2,  # 知行多头且沿短线上行
    "ci_relative_strength": 2,  # 20 日相对强度强
    "ci_leader_volume": 2,  # 龙头量能
    "ci_bottom_volume": 2,  # 底部巨量
    "ci_volume_sustain_mainline": 2,  # 量能持续=主线确认
    "ci_ignition": 1,  # 放量点火
    "ci_reversal_k": 1,  # 反转K
    "ci_pullback_shrink": 2,  # 回调缩量（v0.61）
    "ci_fund_flow_inflow": 2,  # 个股主力净流入在榜（v0.80 起板块净流入不计分）
    "cap_strong": CAP_STRONG,
    "cap_mid": CAP_MID,
}


def resolve_capital_weights(overrides: Optional[dict]) -> dict:
    """把外部（registry scoring.weights）传入的权重并入默认表；未知键忽略。

    覆盖模式仿 `score_candidates.resolve_cap_rules`：默认表兜底，只认已知键。
    """
    w = dict(DEFAULT_EVIDENCE_WEIGHTS)
    if isinstance(overrides, dict):
        for key, val in overrides.items():
            if key in w:
                w[key] = val
    return w


def capital_intent_strength(
    cand: dict, weights: Optional[dict] = None
) -> tuple[str, int, dict]:
    """资金意图强度（量价证明资金在进）→ 分层第二轴。确定性加分，落盘明细。

    证据（待回测权重）：b1_ignition +3、知行多头且沿短线上行 +2、20日相对强度强 +2、
    龙头量能 +2、底部巨量 +2、量能持续=主线确认 +2、放量点火 +1、反转K +1。
    仅正向计"资金在进"证据；资金流出/派发风险（出货五方式、MACD 顶背离/三打白骨精）
    由 score_candidate 的风控 cap 层单独否决，不在此重复扣减，避免双计。
    返回 (level, score, detail)。

    2026-08-19（#58 收尾）：证据项按主题拆成 `_capital_intent_*_evidence` 段落函数，
    本函数只做「逐段汇总 → 分级」；分值、detail 键与键序均未变。
    2026-08-20（v0.84）：迁入因子注册表（零行为变化）；分值/阈值改走
    `resolve_capital_weights(weights)`，`weights=None` 时全默认 == 现值。
    """
    w = resolve_capital_weights(weights)
    detail: dict[str, Any] = {}
    score = 0
    # 段顺序＝历史上 detail 的键序，勿动（落盘明细供复盘对照）。
    for pts, part in (
        _capital_intent_zhixing_evidence(cand, w),
        _capital_intent_volume_evidence(cand, w),
        _capital_intent_fund_flow_evidence(cand, w),
    ):
        score += pts
        detail.update(part)
    level = (
        "强" if score >= w["cap_strong"] else ("中" if score >= w["cap_mid"] else "弱")
    )
    return level, score, detail


def _capital_intent_add(detail: dict, key: str, cond: Any, pts: int) -> int:
    """记录一条资金证据（hit 布尔化 + 命中才计分），返回本条得分。"""
    hit = bool(cond)
    detail[key] = {"hit": hit, "points": pts if hit else 0}
    return pts if hit else 0


def _capital_intent_zhixing_evidence(cand: dict, w: dict) -> tuple[int, dict]:
    """知行/形态证据：B1 点火 +3、知行多头骑线 +2、20日相对强度强 +2。"""
    detail: dict[str, Any] = {}
    zx = cand.get("zhixing") or {}
    score = _capital_intent_add(
        detail,
        "b1_ignition",
        (cand.get("b1_ignition") or {}).get("hit"),
        w["ci_b1_ignition"],
    )
    score += _capital_intent_add(
        detail,
        "zhixing_ride",
        zx.get("available") and zx.get("qsx_gt_dks") and zx.get("close_above_qsx"),
        w["ci_zhixing_ride"],
    )
    score += _capital_intent_add(
        detail,
        "relative_strength_strong",
        (cand.get("patterns") or {}).get("relative_strength_strong"),
        w["ci_relative_strength"],
    )
    return score, detail


def _capital_intent_volume_evidence(cand: dict, w: dict) -> tuple[int, dict]:
    """量能证据：龙头量/底部巨量/量能持续主线 +2，放量点火/反转K +1，回调缩量 +2。"""
    detail: dict[str, Any] = {}
    leader = cand.get("leader_volume") or {}
    bottom = cand.get("bottom_volume") or {}
    score = _capital_intent_add(
        detail,
        "leader_volume",
        leader.get("available") and leader.get("hit"),
        w["ci_leader_volume"],
    )
    score += _capital_intent_add(
        detail,
        "bottom_volume",
        bottom.get("available") and bottom.get("hit"),
        w["ci_bottom_volume"],
    )
    score += _capital_intent_add(
        detail,
        "volume_sustain_mainline",
        (cand.get("volume_sustain") or {}).get("status") == "mainline_confirmed",
        w["ci_volume_sustain_mainline"],
    )
    score += _capital_intent_add(
        detail,
        "ignition",
        (cand.get("ignition") or {}).get("hit"),
        w["ci_ignition"],
    )
    score += _capital_intent_add(
        detail,
        "reversal_k",
        (cand.get("patterns") or {}).get("reversal_k_candidate"),
        w["ci_reversal_k"],
    )
    # v0.61（owner 定向）：回调缩量计资金证据 +2--缩量回调=抛压衰竭、主力未撤
    # （与「量能撤退封顶去除」v0.60 同一语义的正向面）；正例 002074 资金意图
    # 原为 0 分（其余证据均未命中）被分层压到 D，此证据让它回到「中」。
    score += _capital_intent_add(
        detail,
        "pullback_shrink",
        (cand.get("pullback_shrink") or {}).get("hit"),
        w["ci_pullback_shrink"],
    )
    return score, detail


def _capital_intent_fund_flow_evidence(cand: dict, w: dict) -> tuple[int, dict]:
    """资金流向（正交于量价）：个股在主力净流入榜且净流入 +2。

    v0.80（owner 拍板）：板块净流入 OR 分支移除——板块净流入是成员加总、蹭标签
    拿分，且「个股与板块资金流同源」假设 v0.79 已被板块证据证伪。enrich 落盘的
    sector_inflow_positive 等展示字段保留，不再参与打分。
    """
    detail: dict[str, Any] = {}
    ff = cand.get("fund_flow") or {}
    score = _capital_intent_add(
        detail,
        "fund_flow_inflow",
        ff.get("available") and ff.get("in_rank_positive"),
        w["ci_fund_flow_inflow"],
    )
    return score, detail
