# -*- coding: utf-8 -*-
"""patterns 五单项判定（entry_patterns）—— 技术分 patterns 段的五个复合布尔。

2026-08-20（v0.86，因子化批 C）从
`pipeline/screening/enrich_candidates.py` 迁入（原 `_reversal_flags` +
`_base_scalars`/`_assemble_metrics` 内联的 bbi_above / relative_strength_strong
判定式 + RS_STRONG_PP 常量）。**行为零变化**：判定式逐字未动；
enrich_candidates 改为 import 调用并保名 re-export。

五单项：bbi_above（收盘站上 BBI）/ j_low（日 J < J_LOW_THRESHOLD）/
volume_contraction（极致缩量）/ reversal_k_candidate（反转K候选）/
relative_strength_strong（20 日相对强度 >= +3pp）。

⚠️ **只搬判定，不搬计算**：bbi / kdj / 量比 / 20 日相对强度等中间量在 enrich
`_base_scalars` 里各只算一次、被多处消费（落盘字段 + 证据块），故本模块的
入口全部**接收已算好的中间量**做纯判定，不在此重算——单次计算语义与内联时
一致（不多算不少算）。

## status 定档理由（candidate）

- `live_use=scorer` / `stage=release` 是**事实**：五单项是技术分 patterns 段
  五条打分腿（score_candidates._pattern_score；j_low +24 是技术分最大单项），
  reversal_k_candidate / relative_strength_strong 另喂资金意图证据
  （capital_intent）；j_low 的阈值语义与 J<13 进池硬门槛（j_low_gate）同源。
- `NOT_FOR_LIVE={needs_work, untested}` 由
  `tests/test_factor_registry.py::test_needs_work_cannot_be_gate_or_scorer`
  机械禁止与 scorer 共存 ⇒ 合规候选只剩 active / candidate。
- `active` 语义是「已验证可用」——本轮只是**搬迁**（零行为变化），没有新增
  任何回测证据，阈值仍是「待回测」启发式 ⇒ 不能标 active。
- ⇒ 取 **candidate**（有依据未终审），阈值校准挂回测 TODO（#62）。
"""

from __future__ import annotations

from typing import Any, Optional

from custos.core.b1_thresholds import (  # noqa: E402
    J_LOW_THRESHOLD,
    VOL_PCTILE_MAX,
    VOL_RATIO_MAX,
)
from custos.core.b1_thresholds import (
    REVERSAL_AMPLITUDE_PCT,  # noqa: E402
    change_in_range,
)

FACTOR: dict[str, Any] = {
    "id": "entry_patterns",
    "name": "patterns 五单项判定（bbi_above/j_low/volume_contraction/"
    "reversal_k_candidate/relative_strength_strong）",
    "kind": "state",
    # 见模块 docstring「status 定档理由」：scorer ⇒ 不能是 needs_work/untested；
    # 无回测证据 ⇒ 不能标 active；取 candidate 并挂回测 TODO。
    "status": "candidate",
    "evidence": "",
    "note": "v0.86 自 enrich_candidates 迁入（零行为变化）；喂技术分 patterns 段"
    "五条打分腿（j_low +24 为最大单项）+ 资金意图证据（reversal_k_candidate/"
    "relative_strength_strong），阈值待回测校准",
    "min_bars": 25,
    "live_use": "scorer",
    "stage": "release",
}

RS_STRONG_PP = 3.0  # 20日相对强度 >= +3pp


def reversal_flags(
    daily_j: Any,
    vol_ratio: Optional[float],
    vol_pctile: Optional[float],
    change_pct: Optional[float],
    amplitude_pct: Optional[float],
) -> tuple[bool, bool, bool]:
    """j_low / volume_contraction / reversal_k_candidate 三个派生布尔。"""
    j_low = daily_j is not None and daily_j < J_LOW_THRESHOLD
    vol_contraction = (
        vol_ratio is not None
        and vol_ratio <= VOL_RATIO_MAX
        and vol_pctile is not None
        and vol_pctile <= VOL_PCTILE_MAX
    )
    reversal_k = bool(
        j_low
        and vol_contraction
        and change_in_range(change_pct)
        and amplitude_pct is not None
        and amplitude_pct <= REVERSAL_AMPLITUDE_PCT
    )
    return j_low, vol_contraction, reversal_k


def bbi_above(bbi: dict) -> bool:
    """bbi_above 单项：BBI 可算且收盘站上 BBI。"""
    return bool(bbi.get("available") and bbi.get("close_above"))


def relative_strength_strong(rs_20d: Optional[float]) -> bool:
    """relative_strength_strong 单项：20 日相对强度 >= RS_STRONG_PP。"""
    return rs_20d is not None and rs_20d >= RS_STRONG_PP
