# -*- coding: utf-8 -*-
"""研究侧 LLM 因子进化引擎：确定性核心 + LLM 循环层。

确定性核心（expr_dsl / ic_eval / dual_window / trajectory）：白名单 DSL 步行
解释（禁 eval/exec）、截面 RankIC/ICIR、挖掘窗/判定窗硬隔离评估、结构化谱系
轨迹池 —— 这四个模块**不 import 任何 LLM/网络依赖**。LLM 循环层（llm_client /
operators / loop / mock_llm）构建在核心之上，是唯一接触网络的一层。
**live 链路不得依赖本包**（research/ 只允许被 L4 依赖，由
tests/test_architecture_layers.py 强制）。
"""

from custos.research.evolution.dual_window import (
    DualWindowResult,
    Window,
    run_dual_window,
    validate_windows,
)
from custos.research.evolution.expr_dsl import (
    BASE_VARIABLES,
    MAX_WINDOW,
    OPERATORS,
    Complexity,
    ExprError,
    complexity,
    evaluate,
    parse,
    violations,
)
from custos.research.evolution.ic_eval import (
    ICStats,
    evaluate_expression,
    ic_stats_from_series,
    rank_ic_by_day,
    score_frame,
)
from custos.research.evolution.trajectory import (
    DECISIONS,
    EVOLUTION_PHASES,
    Trajectory,
    TrajectoryPool,
    make_id,
)

__all__ = [
    "BASE_VARIABLES",
    "DECISIONS",
    "EVOLUTION_PHASES",
    "MAX_WINDOW",
    "OPERATORS",
    "Complexity",
    "DualWindowResult",
    "ExprError",
    "ICStats",
    "Trajectory",
    "TrajectoryPool",
    "Window",
    "complexity",
    "evaluate",
    "evaluate_expression",
    "ic_stats_from_series",
    "make_id",
    "parse",
    "rank_ic_by_day",
    "run_dual_window",
    "score_frame",
    "validate_windows",
    "violations",
]
