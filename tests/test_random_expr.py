# -*- coding: utf-8 -*-
"""random_expr 采样器钉测：构造即合法 / 同种子逐位一致 / 深度与窗参档位。"""

from __future__ import annotations

import random
import re

from custos.research.evolution import expr_dsl
from custos.research.evolution.random_expr import WINDOW_CHOICES, sample_expression


def _sample_n(n: int, seed: int, **kw) -> list[str]:
    rng = random.Random(seed)
    return [sample_expression(rng, **kw) for _ in range(n)]


class TestSampleExpression:
    def test_200_samples_all_legal(self):
        # 构造即合法：200 个采样全部 parse + violations 通过（测 IC 门不是 DSL 门）
        for expr in _sample_n(200, 20260911):
            expr_dsl.parse(expr)  # 不抛即过白名单
            assert not expr_dsl.violations(expr_dsl.complexity(expr))

    def test_deterministic_bitwise(self):
        assert _sample_n(30, 42) == _sample_n(30, 42)  # 同种子逐位一致
        assert _sample_n(30, 42) != _sample_n(30, 43)  # 不同种子不同序列

    def test_max_depth_respected(self):
        for expr in _sample_n(100, 7, max_depth=2):
            assert expr_dsl.complexity(expr).depth <= 2
        for expr in _sample_n(100, 8, max_depth=3):
            assert expr_dsl.complexity(expr).depth <= 3

    def test_window_params_on_lattice(self):
        # 采样器只在窗参位置产字面量 ⇒ 所有整数都必须在常见档位内
        for expr in _sample_n(200, 99):
            for m in re.finditer(r"\d+", expr):
                assert int(m.group()) in WINDOW_CHOICES
                assert int(m.group()) <= expr_dsl.MAX_WINDOW

    def test_leaf_weight_bias(self):
        # 分布口径钉测：close/volume 权重高 ⇒ 出现频次显著多于 open
        exprs = _sample_n(400, 20260911)
        n_close = sum(e.count("close") for e in exprs)
        n_volume = sum(e.count("volume") for e in exprs)
        n_open = sum(e.count("open") for e in exprs)
        assert n_close > n_open * 1.5 and n_volume > n_open

    def test_forms_mimic_llm_shape(self):
        # 形态口径：多数采样含 1-3 个算子嵌套或四则组合（非纯叶子为主）
        exprs = _sample_n(200, 5)
        with_op = sum(1 for e in exprs if "(" in e)
        with_binop = sum(
            1 for e in exprs if any(f" {op} " in e for op in ("+", "-", "*", "/"))
        )
        assert with_op + with_binop > len(exprs) * 0.6  # 叶子只是少数派
