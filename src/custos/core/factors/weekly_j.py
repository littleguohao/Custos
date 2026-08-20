# -*- coding: utf-8 -*-
"""周线 J 状态（weekly_j）—— 周线 KDJ 的 J 值与 J<13 低位判定。

2026-08-20（v0.86，因子化批 B）从
`pipeline/screening/enrich_candidates.py` 迁入（weekly_j_state +
j_below_threshold）。**行为零变化**：函数体逐字未动；enrich_candidates 改为
import 调用（`enrich_candidates.weekly_j_state` / `enrich_candidates.j_below_threshold`
仍是同一函数对象，tests 的 `ec.weekly_j_state` / `signal_labels` 惰性导入通道不变）。

`j_below_threshold` 随本模块走：weekly_j_state 的低位判定走它，而 L2 不得
import L3 ⇒ 这个 J 门槛助手只能下移；enrich 的 J 硬门槛（_apply_j_gate）
与 tests 的 `ec.j_below_threshold` 通道改由 re-export 满足。

## status 定档理由（candidate）

- `live_use=scorer` / `stage=release` 是**事实**：weekly_j_low 是技术分
  「weekly_j_low +5」腿（score_candidates）与 signal_labels「周线B1(周J<13)」
  标签的唯一生产者。
- `NOT_FOR_LIVE={needs_work, untested}` 由
  `tests/test_factor_registry.py::test_needs_work_cannot_be_gate_or_scorer`
  机械禁止与 scorer 共存 ⇒ 合规候选只剩 active / candidate。
- `active` 语义是「已验证可用」——本轮只是**搬迁**（零行为变化），没有新增
  任何回测证据，J<13 阈值仍是「待回测」启发式 ⇒ 不能标 active。
- ⇒ 取 **candidate**（有依据未终审），阈值校准挂回测 TODO。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from custos.core.b1_thresholds import J_LOW_THRESHOLD  # noqa: E402
from custos.core.indicators import kdj, resample  # noqa: E402

FACTOR: dict[str, Any] = {
    "id": "weekly_j",
    "name": "周线 J 状态（weekly_j_state：周 KDJ J 值 + J<13 低位）",
    "kind": "state",
    # 见模块 docstring「status 定档理由」：scorer ⇒ 不能是 needs_work/untested；
    # 无回测证据 ⇒ 不能标 active；取 candidate 并挂回测 TODO。
    "status": "candidate",
    "evidence": "",
    "note": "v0.86 自 enrich_candidates 迁入（零行为变化）；weekly_j_low 喂技术分 "
    "weekly_j_low +5 腿 + signal_labels「周线B1(周J<13)」标签，J 阈值待回测校准",
    "min_bars": 40,
    "live_use": "scorer",
    "stage": "release",
}


def j_below_threshold(j: Any, threshold: float = J_LOW_THRESHOLD) -> bool:
    """J 是否满足 `J < threshold` 的硬门槛。**NaN/None/非数值一律不满足**。

    审计：原判否写作 `dj is None or dj >= J_LOW_THRESHOLD`。IEEE 754 下
    `float("nan") >= 13` 为 False，`nan is None` 也为 False —— 于是"J 算不出来"
    被当成"J<13 满足买点"直接放行，坏数据成了最好的数据。KDJ 目前走
    `rsv.fillna(50)` 不易产出 NaN，但 daily_j 也可能来自落盘 JSON / 别的口径，
    这道门槛是全通道硬门槛，不能依赖上游恰好不脏。
    """
    if j is None or isinstance(j, bool):
        return False
    try:
        v = float(j)
    except (TypeError, ValueError):
        return False
    if not np.isfinite(v):  # NaN / ±inf 均视为不可用
        return False
    return v < threshold


def weekly_j_state(df) -> dict[str, Any]:
    """周线 J（B1 §四.1 主线口径：周线 J<13 为周线 B1 候选）。

    ``weekly_j_available`` 与 ``available`` 同值：本 dict 会被 `**weekly_j_state(df)`
    摊进 compute_metrics 的返回值，一个裸 ``available`` 键直接落到**候选顶层**、
    读起来像"这个候选可用"（审计）。compute_metrics 只摊 weekly_ 前缀的键；
    ``available`` 保留给直接调用方（既有测试/脚本）。
    weekly_j / weekly_j_low 本来就在候选顶层，下游 score_candidates 读得到。
    """
    weekly = resample(df, "W-FRI")
    w = kdj(weekly)
    if not w.get("available"):
        return {
            "available": False,
            "weekly_j_available": False,
            "weekly_j": None,
            "weekly_j_low": False,
        }
    return {
        "available": True,
        "weekly_j_available": True,
        "weekly_j": w["j"],
        "weekly_j_low": j_below_threshold(w["j"]),
    }
