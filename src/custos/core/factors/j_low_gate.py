# -*- coding: utf-8 -*-
"""J<13 进池硬门槛（j_low_gate）—— gate 的因子化登记入口。

2026-08-20（v0.86，因子化批 C）补登记：J 值唯一实现早已在 L0
（`indicators.j_series` / `kdj`）、阈值唯一来源在 L0
（`b1_thresholds.J_LOW_THRESHOLD`）、判定本体在 `weekly_j.j_below_threshold`
（批 B 迁入），缺的只是 FACTOR 登记。单列本模块而不是并入 weekly_j：
注册表每模块只收一个 FACTOR（`meta["id"]` 为键），weekly_j 已登记
`weekly_j`（scorer 向）；门槛是与「周线 J 状态」不同维度的消费方式
（gate vs scorer），各自登记才能让 live_use 维度如实。

**执行点不在本模块**：18:00 进池硬门槛的编排在
`pipeline/screening/enrich_candidates._apply_j_gate`（被挡写 excluded），
本函数只是判定入口（2026-07-22 用户
决策：全通道候选——公式与自选池一视同仁——必须先满足日 J<13，J 不可计算
视同不满足）。

## status 定档理由（candidate）

- `live_use=gate` / `stage=release` 是**事实**：它是 18:00 选股链的进池
  硬门槛（不是打分腿）。
- `NOT_FOR_LIVE={needs_work, untested}` 由
  `tests/test_factor_registry.py::test_needs_work_cannot_be_gate_or_scorer`
  机械禁止与 gate 共存 ⇒ 合规候选只剩 active / candidate。
- `active` 语义是「已验证可用」——J<13 阈值源自策略材料与 R1 框架、长期
  在 live 跑，但**没有新增回测终审证据**（本轮只是补登记，零行为变化）
  ⇒ 不标 active。
- ⇒ 取 **candidate**（有依据未终审），阈值校准挂回测 TODO（#62）。
"""

from __future__ import annotations

from typing import Any

from custos.core.b1_thresholds import J_LOW_THRESHOLD  # noqa: E402
from custos.core.factors.weekly_j import j_below_threshold  # noqa: E402

FACTOR: dict[str, Any] = {
    "id": "j_low_gate",
    "name": "J<13 进池硬门槛（j_low_gate）",
    "kind": "state",
    # 见模块 docstring「status 定档理由」：gate ⇒ 不能是 needs_work/untested；
    # 无新增回测终审 ⇒ 不能标 active；取 candidate。
    "status": "candidate",
    "evidence": "",
    "note": "v0.86 补 FACTOR 登记（零行为变化）：J 值/阈值唯一来源在 L0"
    "（indicators.j_series / b1_thresholds.J_LOW_THRESHOLD），判定本体复用 "
    "weekly_j.j_below_threshold；18:00 进池硬门槛，执行点在 "
    "enrich_candidates._apply_j_gate（公式与自选池一视同仁）",
    "min_bars": 25,
    "live_use": "gate",
    "stage": "release",
}


def j_low_gate_hit(daily_j: Any, threshold: float = J_LOW_THRESHOLD) -> bool:
    """日 J 是否满足 `J < threshold` 的进池硬门槛。**NaN/None/非数值一律不满足**。

    判定本体即 `weekly_j.j_below_threshold`（NaN/±inf/None 判否的审计说明见
    该函数）；这里只做 gate 语义的因子化入口，门槛外的落盘编排
    （excluded）留在 enrich `_apply_j_gate`。

    撞名避让：研究侧 `research/backtest_factors.j_low_gate(df_slice, precomputed)`
    是同一门槛的 as-of 回测适配器（ENTRY_GATES 双形态签名），故本函数命名
    `j_low_gate_hit`（FACTOR id 仍 `j_low_gate`，与 ENTRY_GATES 键对齐；
    守卫 `TestNoRefork` 拦同名两份实现）。
    """
    return j_below_threshold(daily_j, threshold)
