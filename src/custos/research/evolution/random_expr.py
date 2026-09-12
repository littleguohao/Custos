# -*- coding: utf-8 -*-
"""随机 DSL 表达式采样器（TODO #71 随机 baseline 裁决实验的生成器）。

纯确定性（调用方注入 seeded ``random.Random``）、不烧 token。

**分布口径**（写死在此供复现）：叶子 = ``BASE_VARIABLES``，权重倾向
close/volume（close×3 / volume×2 / 其余×1 —— LLM 产出里最常用的两个变量）；
节点 45% 白名单窗参算子（REF/MA/SUM/STD/MAX/MIN/DELTA/ROC/TS_RANK，窗参从
2/3/5/10/20/40/60 常见档抽，均 ≤ MAX_WINDOW）、10% ABS/LOG 单参算子、
45% 四则 BinOp 组合；深度受 ``max_depth``（默认 3）约束 ⇒ 产物呈
「1-3 个算子嵌套 + 四则组合」形态，模仿 LLM 产出规模。

**构造即合法**：产物必须过 ``expr_dsl.parse`` 且 ``violations(complexity())``
为空 —— 越界重采样，有界次数后缩小 max_depth 重试（本实验测的是 IC 门，
不是 DSL 门，DSL 违规样本不进对照组）。
"""

from __future__ import annotations

import random

from custos.research.evolution import expr_dsl
from custos.research.evolution.expr_dsl import BASE_VARIABLES, MAX_WINDOW

# 叶子权重：close/volume 是 LLM 产出最常用的两个变量（倾向采样）。
_LEAF_WEIGHTS = {"open": 1.0, "high": 1.0, "low": 1.0, "close": 3.0, "volume": 2.0}

# 窗参常见档位（均 ≤ MAX_WINDOW；档位制而非连续采样，对齐 LLM 产出习惯）。
WINDOW_CHOICES: tuple[int, ...] = (2, 3, 5, 10, 20, 40, 60)

_BIN_OPS = ("+", "-", "*", "/")
_UNARY_OPS = ("ABS", "LOG")

_LEAF_PROB = 0.30  # 每层落叶子的概率（控制树的茂密程度）


def _sample_node(rng: random.Random, depth: int) -> str:
    """递归采一棵 AST 深度 ≤ depth 的表达式子树（叶子概率提前落子）。"""
    if depth <= 1 or rng.random() < _LEAF_PROB:
        return rng.choices(
            BASE_VARIABLES, weights=[_LEAF_WEIGHTS[v] for v in BASE_VARIABLES]
        )[0]
    kind = rng.random()
    if kind < 0.45:  # 白名单窗参算子（窗参从常见档抽）
        op = rng.choice(sorted(expr_dsl._WINDOW_OPS))
        return f"{op}({_sample_node(rng, depth - 1)}, {rng.choice(WINDOW_CHOICES)})"
    if kind < 0.55:  # 单参算子
        return f"{rng.choice(_UNARY_OPS)}({_sample_node(rng, depth - 1)})"
    # 四则组合（必须带括号：否则子树里的 BinOp 文本内联后会被 Python 优先级
    # 重新分组，一个采样层叠出多个 AST 层，深度口径就失真了）
    return (
        f"({_sample_node(rng, depth - 1)} "
        f"{rng.choice(_BIN_OPS)} {_sample_node(rng, depth - 1)})"
    )


def _legal(expr: str) -> bool:
    """parse + violations 双过才算合法（采样器内部的拒收判据）。"""
    try:
        comp = expr_dsl.complexity(expr)
    except expr_dsl.ExprError:
        return False
    return not expr_dsl.violations(comp)


def sample_expression(rng: random.Random, *, max_depth: int = 3) -> str:
    """采一条**构造即合法**的 DSL 表达式（seeded，逐位可复现）。

    越界（复杂度超限/非法形态）重采样；每 20 次不中缩小一档 max_depth 重试。
    深度 1 时退化为纯叶子（必然合法），故循环有界且必有合法产出。
    """
    depth = max(1, max_depth)
    for attempt in range(60):
        expr = _sample_node(rng, depth)
        if _legal(expr):
            return expr
        if attempt % 20 == 19:
            depth = max(1, depth - 1)
    # 防御兜底（理论不可达：深度 1 的纯叶子必然合法）：绝不返回非法表达式。
    return "close"
