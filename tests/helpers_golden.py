# -*- coding: utf-8 -*-
"""金样比对浮点归一化（v0.359，CI 红根因）：numpy 求和按 CPU SIMD 路径
（AVX2/AVX512）分派，同一计算末位 ULP 可不同——本机捕获的全精度金样在
GitHub runner（不同 CPU 代际）字符串比对必挂。归一到 1e-12 后比对，
行为级断言（读数量级 ~1e-2）完全不受影响。"""

from __future__ import annotations

import json


def norm_floats(obj, digits: int = 12):
    """递归把 float 归一到 digits 位小数（dict/list 结构原样）。"""
    if isinstance(obj, float):
        return round(obj, digits)
    if isinstance(obj, dict):
        return {k: norm_floats(v, digits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [norm_floats(v, digits) for v in obj]
    return obj


def canon_json(obj, digits: int = 12) -> str:
    """norm_floats + canonical JSON（sort_keys；金样两侧必须同口径比对）。"""
    return json.dumps(
        norm_floats(obj, digits), ensure_ascii=False, sort_keys=True, allow_nan=True
    )
