# -*- coding: utf-8 -*-
"""genome（联合演化第一档的参数格点层）钉测：lattice 归并 / 确定性与边界 / 校验拒收。

原则钉在这里：参数空间归确定性格点 —— 同种子必同结果（可复现），
产物必过 validate_params（白名单不收的一律 ValueError）。
"""

from __future__ import annotations

import random

import pytest

from custos.research import strategy_grid as sg
from custos.research.evolution import genome
from custos.research.evolution.genome import (
    EXIT_LATTICE,
    GATE_CHOICES,
    crossover_params,
    default_params,
    mutate_params,
    validate_params,
)


class TestLattice:
    def test_merge_correctness(self):
        # 归并不漏不重：grid 里出现的每个值都在档里；档内有序去重
        for entry in sg.DEFAULT_EXIT_GRID:
            for k, v in (entry.get("params") or {}).items():
                if isinstance(v, str):
                    assert v in genome._STR_LATTICE[k]
                else:
                    assert float(v) in EXIT_LATTICE[k]
        for k, vals in EXIT_LATTICE.items():
            assert list(vals) == sorted(set(vals))
            assert k in sg.EXIT_PARAM_FLAGS

    def test_gate_choices_mirror_default_gates(self):
        assert GATE_CHOICES == tuple(sg.DEFAULT_GATES)


class TestDefaultParams:
    def test_default_is_first_gate_plus_mid_exit(self):
        gate, params = default_params()
        assert gate == GATE_CHOICES[0]
        mid = sg.DEFAULT_EXIT_GRID[len(sg.DEFAULT_EXIT_GRID) // 2]
        assert params == dict(mid.get("params") or {})

    def test_default_returns_copies(self):
        _, params = default_params()
        params["stop_pct"] = 999  # 改副本不污染 DEFAULT_EXIT_GRID
        _, params2 = default_params()
        assert params2.get("stop_pct") != 999
        validate_params(*default_params())  # 默认产物自身合法


class TestValidateParams:
    def test_rejects_bad_gate(self):
        with pytest.raises(ValueError, match="GATE_CHOICES"):
            validate_params("nope", {})

    def test_rejects_unknown_param_key(self):
        with pytest.raises(ValueError, match="越界"):
            validate_params(GATE_CHOICES[0], {"bogus": 1})

    def test_rejects_value_off_lattice(self):
        _, params = default_params()
        params["stop_pct"] = 7  # 不在档（档内只有 grid 登记过的值）
        with pytest.raises(ValueError, match="不在档位"):
            validate_params(GATE_CHOICES[0], params)

    def test_rejects_bool_value(self):
        with pytest.raises(ValueError, match="bool"):
            validate_params(GATE_CHOICES[0], {"stop_pct": True})

    def test_rejects_off_lattice_stop_mode(self):
        with pytest.raises(ValueError):
            validate_params(GATE_CHOICES[0], {"stop_mode": "low"})

    def test_accepts_default(self):
        validate_params(*default_params())  # 不抛即过


class TestMutateParams:
    def test_deterministic_same_seed(self):
        gate, params = default_params()
        a = mutate_params(gate, params, random.Random(42))
        b = mutate_params(gate, params, random.Random(42))
        assert a == b  # 确定性：同种子同结果（参数空间必须可复现）

    def test_product_always_valid(self):
        gate, params = default_params()
        for seed in range(50):
            g2, p2 = mutate_params(gate, params, random.Random(seed))
            validate_params(g2, p2)  # 产物必过 validate（任何种子）

    def test_gate_shift_reflects_at_edges(self):
        # 端点内折：首 gate 只能平移到相邻档或自身，绝不越界
        assert genome._reflect(-1, 3) == 1
        assert genome._reflect(3, 3) == 1
        assert genome._reflect(0, 1) == 0
        assert genome._reflect(5, 4) == 1

    def test_gate_mutation_moves_within_choices(self):
        # 多次种子跑总有一次 gate 变异（50% 概率支路），且必在 CHOICES 内
        seen = set()
        for seed in range(20):
            g2, _ = mutate_params(
                GATE_CHOICES[0], dict(default_params()[1]), random.Random(seed)
            )
            seen.add(g2)
        assert seen <= set(GATE_CHOICES) and len(seen) > 1


class TestCrossoverParams:
    PA = (GATE_CHOICES[0], {"stop_pct": 5, "stop_mode": "pct"})
    PB = (GATE_CHOICES[-1], {"trail_pct": 0.08, "stop_mode": "pct"})

    def test_components_come_from_parents(self):
        gate, params = crossover_params(self.PA, self.PB, random.Random(0))
        # gate 取自一方、exit_params 取自另一方（不许自造第三套）
        assert (gate, params) in [
            (self.PA[0], self.PB[1]),
            (self.PB[0], self.PA[1]),
        ]
        validate_params(gate, params)

    def test_deterministic_same_seed(self):
        a = crossover_params(self.PA, self.PB, random.Random(7))
        b = crossover_params(self.PA, self.PB, random.Random(7))
        assert a == b

    def test_both_orientations_reachable(self):
        seen = set()
        for seed in range(20):
            g, p = crossover_params(self.PA, self.PB, random.Random(seed))
            seen.add((g, tuple(sorted(p.items()))))  # dict 不可哈希，摊平进集合
        flat = {
            (self.PA[0], tuple(sorted(self.PB[1].items()))),
            (self.PB[0], tuple(sorted(self.PA[1].items()))),
        }
        assert seen == flat  # 两个选向都可达

    def test_rejects_illegal_inputs(self):
        # 两侧 gate 都非法 ⇒ 无论 rng 选向哪边都 fail-closed
        with pytest.raises(ValueError):
            crossover_params(("nope", {}), ("alsono", {}), random.Random(0))
