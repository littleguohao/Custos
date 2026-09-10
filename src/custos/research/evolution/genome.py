# -*- coding: utf-8 -*-
"""联合演化第一档（TODO #68，owner 拍板）：基因组建模 = (DSL 表达式, gate, 出场参数)。

原则：表达式空间归 LLM，**参数空间归确定性格点** —— LLM 不碰数值调参
（数值寻优是多重检验重灾区，必须可复现）。本模块只管参数分量：

- 格点（lattice）来自 strategy_grid 的 DEFAULT_GATES / DEFAULT_EXIT_GRID /
  EXIT_PARAM_FLAGS —— 出厂粗网格就是目前唯一被治理认可的参数允许值集合；
- 变异 = 档位 ±1 平移（端点内折），杂交 = 跨父代交换分量，全部 seeded
  （random.Random 注入，可复现）；decision 仍纯确定性，本模块不做任何 IC 判定。
"""

from __future__ import annotations

import random
from typing import Any

from custos.research.strategy_grid import (
    DEFAULT_EXIT_GRID,
    DEFAULT_GATES,
    EXIT_PARAM_FLAGS,
)

GATE_CHOICES: tuple[str, ...] = tuple(DEFAULT_GATES)


def _build_lattices() -> tuple[
    dict[str, tuple[float, ...]], dict[str, tuple[str, ...]]
]:
    """由 DEFAULT_EXIT_GRID 各档 params 按键归并：每键一档有序允许值（去重排序）。

    数值键进 EXIT_LATTICE（档位平移的操作面）；字符串键（stop_mode）进
    STR_LATTICE（只允许档内取值，不参与 ±1 平移）。
    """
    num: dict[str, set[float]] = {}
    strings: dict[str, set[str]] = {}
    for entry in DEFAULT_EXIT_GRID:
        for k, v in (entry.get("params") or {}).items():
            if isinstance(v, str):
                strings.setdefault(k, set()).add(v)
            else:
                num.setdefault(k, set()).add(float(v))
    return (
        {k: tuple(sorted(vs)) for k, vs in num.items()},
        {k: tuple(sorted(vs)) for k, vs in strings.items()},
    )


EXIT_LATTICE: dict[str, tuple[float, ...]]
_STR_LATTICE: dict[str, tuple[str, ...]]
EXIT_LATTICE, _STR_LATTICE = _build_lattices()


def default_params() -> tuple[str, dict]:
    """默认基因组参数分量：首个 gate + DEFAULT_EXIT_GRID 中间档的 params 副本。"""
    mid = DEFAULT_EXIT_GRID[len(DEFAULT_EXIT_GRID) // 2]
    return GATE_CHOICES[0], dict(mid.get("params") or {})


def validate_params(gate: str, exit_params: dict) -> None:
    """语义白名单校验（fail-closed ValueError）：gate ∈ GATE_CHOICES、
    exit_params 键 ⊆ EXIT_PARAM_FLAGS、值 ∈ 对应 LATTICE 档（bool 拒）。"""
    if gate not in GATE_CHOICES:
        raise ValueError(
            f"gate 不在 GATE_CHOICES: {gate!r}（合法: {list(GATE_CHOICES)}）"
        )
    for k, v in exit_params.items():
        if k not in EXIT_PARAM_FLAGS:
            raise ValueError(
                f"出场参数键越界: {k!r}（允许: {sorted(EXIT_PARAM_FLAGS)}）"
            )
        if isinstance(v, bool):
            raise ValueError(f"{k} 的值不许是 bool: {v!r}")
        if k in EXIT_LATTICE:
            if not isinstance(v, (int, float)) or float(v) not in EXIT_LATTICE[k]:
                raise ValueError(f"{k}={v!r} 不在档位 {EXIT_LATTICE[k]}")
        elif k in _STR_LATTICE:
            if v not in _STR_LATTICE[k]:
                raise ValueError(f"{k}={v!r} 不在档位 {_STR_LATTICE[k]}")
        else:
            raise ValueError(f"{k!r} 无已登记档位（DEFAULT_EXIT_GRID 未覆盖该键）")


def _reflect(i: int, n: int) -> int:
    """端点内折：越界下标反射回合法区间（n<=1 恒 0）。"""
    if n <= 1:
        return 0
    if i < 0:
        return -i
    if i >= n:
        return 2 * n - 2 - i
    return i


def _shift_one_param(exit_params: dict, rng: random.Random) -> dict:
    """随机选一个数值键沿其 LATTICE 平移 ±1（端点内折）；无数值键 → 原样副本。"""
    keys = [k for k in exit_params if k in EXIT_LATTICE]
    out = dict(exit_params)
    if not keys:
        return out
    k = rng.choice(keys)
    lattice = EXIT_LATTICE[k]
    try:
        i = lattice.index(float(exit_params[k]))
    except ValueError:
        return out  # 当前值不在档（外来参数）→ 不动，由调用方保证入参合法
    out[k] = lattice[_reflect(i + rng.choice((-1, 1)), len(lattice))]
    return out


def mutate_params(gate: str, exit_params: dict, rng: random.Random) -> tuple[str, dict]:
    """确定性格点变异（seeded）：50% gate 沿 GATE_CHOICES 平移 ±1（端点内折），
    否则随机选一参数沿其 LATTICE 平移 ±1；产物必过 validate_params。"""
    if rng.random() < 0.5:
        i = GATE_CHOICES.index(gate) if gate in GATE_CHOICES else 0
        new_gate = GATE_CHOICES[_reflect(i + rng.choice((-1, 1)), len(GATE_CHOICES))]
        new_params = dict(exit_params)
    else:
        new_gate, new_params = gate, _shift_one_param(exit_params, rng)
    validate_params(new_gate, new_params)
    return new_gate, new_params


def crossover_params(
    pa: tuple[str, dict], pb: tuple[str, dict], rng: random.Random
) -> tuple[str, dict]:
    """分量交换杂交（seeded）：gate 取自一方、exit_params 取自另一方（rng 选向）。

    入参须各自合法（loop 对缺字段的老父代先落 default 再进这里）；
    校验是按分量独立的 ⇒ 交换产物必过 validate_params（仍显式校验兜底）。
    """
    (gate_a, params_a), (gate_b, params_b) = pa, pb
    if rng.random() < 0.5:
        gate, params = gate_a, dict(params_b)
    else:
        gate, params = gate_b, dict(params_a)
    validate_params(gate, params)
    return gate, params
