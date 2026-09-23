# -*- coding: utf-8 -*-
"""随机 DSL 表达式采样器（TODO #71 随机 baseline 裁决实验的生成器）。

纯确定性（调用方注入 seeded ``random.Random``）、不烧 token。

**分布口径**（写死在此供复现）：叶子 = ``BASE_VARIABLES``，权重倾向
close/volume（close×3 / volume×2 / 其余×1 —— LLM 产出里最常用的两个变量）；
节点 45% 白名单窗参算子（REF/MA/SUM/STD/MAX/MIN/DELTA/ROC/TS_RANK，窗参从
2/3/5/10/20/40/60 常见档抽，均 ≤ MAX_WINDOW）、10% ABS/LOG 单参算子、
45% 四则 BinOp 组合；深度受 ``max_depth``（默认 3）约束 ⇒ 产物呈
「1-3 个算子嵌套 + 四则组合」形态，模仿 LLM 产出规模。

**零假设口径**（v0.267，TODO #80①，owner 方法论 review——对照臂必须是
**形态噪声**，不得是已知的真因子代理）：

- **R1 根节点必须是算子**——裸终结符（close/open/volume 整表达式）=
  低价/规模等结构因子，该进基准臂、不是零假设（R34 默认种子臂2=open /
  臂3=close 实测教训）；
- **R2 必须含破尺度构造**（``/`` 或 DELTA/ROC/TS_RANK 之一）——否则
  ``MA(close,20)``/``SUM(volume,5)`` 这类「水平算子+四则（无除法）」产物
  仍是价格/流动性**水平**代理。REF 不算破尺度（``REF(close,5)`` 仍是
  价格水平）；ABS/LOG 单调保水平，同样不算。

构造即合法：产物必须过 ``expr_dsl.parse`` 且 ``violations(complexity())``
为空 —— 越界重采样，有界次数后缩小 max_depth 重试（本实验测的是 IC 门，
不是 DSL 门，DSL 违规样本不进对照组）。零假设口径下最小深度 = 2
（深度 1 = 纯叶子，必违 R1）。
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

#: 破尺度算子（R2 的合法凭证；'/' 单独判——BinOp 文本里的除法）。
#: REF 不在列：REF(close,5) 仍是价格水平；ABS/LOG 单调保水平，同不算。
_SCALE_BREAK_OPS: tuple[str, ...] = ("DELTA", "ROC", "TS_RANK")

#: 零假设口径下的兜底表达式（合法 + 破尺度；理论不可达，防御用）。
_FALLBACK_EXPR = "ROC(close, 5)"


def _null_ok(expr: str) -> bool:
    """零假设标尺（R1/R2 见模块 docstring）：非裸终结符 ∧ 含破尺度构造。"""
    if expr in BASE_VARIABLES:  # R1：裸终结符 = 结构因子，不是零假设
        return False
    if "/" in expr:
        return True
    return any(f"{op}(" in expr for op in _SCALE_BREAK_OPS)


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

    越界（复杂度超限/非法形态）重采样；**违零假设标尺（R1/R2）同样重采样**
    ——对照臂只收形态噪声。每 20 次不中缩小一档 max_depth 重试；零假设口径
    下深度下限 = 2（深度 1 必违 R1），故循环有界且必有合法产出。
    """
    depth = max(2, max_depth)
    for attempt in range(60):
        expr = _sample_node(rng, depth)
        if _legal(expr) and _null_ok(expr):
            return expr
        if attempt % 20 == 19:
            depth = max(2, depth - 1)
    # 防御兜底（理论不可达：深度 2 的算子形态必能产出合法破尺度表达式）。
    return _FALLBACK_EXPR
