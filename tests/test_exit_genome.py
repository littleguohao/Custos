# -*- coding: utf-8 -*-
"""R37 出场轴基因组钉测：合法空间/归一化/变异算子/灵敏度臂。

钉的是预注册语义：基准档 pct5_trail08 在空间内且 = C2 参照点；关闭家族
不可复活（CTL-2 死刑是终局）；基因组键 = evaluate_trades 形参（防回测
引擎改名漂移）。
"""

from __future__ import annotations

import inspect
import random

from custos.research import backtest_factors as bt
from custos.research.evolution import exit_genome as eg


class TestSpace:
    def test_baseline_in_space(self):
        base = eg.baseline_genome()
        assert eg.validate(base) == []
        assert eg.families_on(base) == frozenset({"trail"})
        assert base["stop_pct"] == 5.0 and base["trail_pct"] == 0.08

    def test_fixed_params_only_pct_stop_mode(self):
        # R10：low 是不可执行基准——stop_mode 轴已闭环，不进 Phase 1 空间
        assert eg.FIXED_PARAMS == {"stop_mode": "pct"}

    def test_genome_keys_are_evaluate_trades_params(self):
        """基因组展开的每个键都必须是 evaluate_trades 形参（引擎改名即红）。"""
        sig = set(inspect.signature(bt.evaluate_trades).parameters)
        for k in list(eg.LEVELS) + list(eg.FIXED_PARAMS):
            assert k in sig, f"{k} 不是 evaluate_trades 形参"

    def test_family_switch_matches_simulate_off_semantics(self):
        # 开关参数 =0 即关闭，与 simulate_b1_trade 既有语义一致
        for fam, sw in eg.FAMILY_SWITCH.items():
            assert sw in eg.FAMILIES[fam]
            assert sw in eg.LEVELS or sw == "cost_zone_bars"


class TestNormalizeValidate:
    def test_sparse_to_full_keys(self):
        n = eg.normalize({"stop_pct": 5, "trail_pct": 0.08})
        assert set(n) == set(eg.LEVELS)
        assert n["breakeven_trigger"] == 0.0
        assert n["cost_zone_bars"] == 0.0
        assert n["cost_zone_pct"] == 3.0  # 关闭占位 = simulate 默认

    def test_off_family_params_zeroed(self):
        n = eg.normalize({"stop_pct": 5, "trail_pct": 0.08, "qsx_exit_consec": 0})
        assert n["qsx_exit_consec"] == 0.0
        assert eg.families_on(n) == frozenset({"trail"})

    def test_validate_out_of_level(self):
        assert eg.validate({"stop_pct": 7})  # 7 不在档位
        assert eg.validate({"stop_pct": 5, "trail_pct": 0.09})  # 开启家族档位外
        assert eg.validate({})  # 缺 stop_pct

    def test_validate_off_family_placeholder_ok(self):
        # cost_zone_pct 关闭时是占位值，不在档位也合法
        assert eg.validate({"stop_pct": 5}) == []

    def test_validate_on_cost_zone_needs_level_pct(self):
        assert eg.validate({"stop_pct": 5, "cost_zone_bars": 3, "cost_zone_pct": 4.0})

    def test_genome_key_order_insensitive(self):
        a = eg.genome_key({"stop_pct": 5, "trail_pct": 0.08})
        b = eg.genome_key(eg.baseline_genome())
        assert a == b
        assert isinstance(a, str) and "trail" in a


class TestRandomGenome:
    def test_valid_and_deterministic(self):
        r1 = eg.random_genome(random.Random(1))
        r2 = eg.random_genome(random.Random(1))
        assert r1 == r2  # 同种子同基因组（可复现）
        for seed in range(200):
            g = eg.random_genome(random.Random(seed))
            assert eg.validate(g) == [], f"seed={seed} 非法基因组: {g}"

    def test_respects_open_families(self):
        for seed in range(200):
            g = eg.random_genome(random.Random(seed), open_families=["trail"])
            assert eg.families_on(g) <= {"trail"}, f"seed={seed} 越界: {g}"

    def test_covers_space(self):
        # 大样本下六家族与 stop_pct 档位都应出现（分布 sanity）
        seen_fams: set[str] = set()
        seen_sp: set[float] = set()
        for seed in range(500):
            g = eg.random_genome(random.Random(seed))
            seen_fams |= set(eg.families_on(g))
            seen_sp.add(g["stop_pct"])
        assert seen_fams == set(eg.all_families())
        assert seen_sp == set(eg.STOP_PCT_LEVELS)


class TestMutate:
    def test_mutants_stay_valid(self):
        base = eg.baseline_genome()
        for seed in range(500):
            m = eg.mutate(base, random.Random(seed))
            assert eg.validate(m) == [], f"seed={seed} 非法变异: {m}"

    def test_never_revives_closed_family(self):
        base = eg.normalize({"stop_pct": 5, "trail_pct": 0.08, "qsx_exit_consec": 2})
        for seed in range(500):
            m = eg.mutate(base, random.Random(seed), open_families=["trail", "qsx"])
            assert eg.families_on(m) <= {"trail", "qsx"}, f"seed={seed} 复活: {m}"

    def test_toggle_turns_off_and_on(self):
        # 大样本下应同时出现「关掉 trail」与「开启新家族」的变异
        base = eg.baseline_genome()
        saw_off, saw_new = False, False
        for seed in range(300):
            m = eg.mutate(base, random.Random(seed))
            fams = eg.families_on(m)
            saw_off = saw_off or "trail" not in fams
            saw_new = saw_new or bool(fams - {"trail"})
        assert saw_off and saw_new

    def test_deterministic(self):
        base = eg.baseline_genome()
        assert eg.mutate(base, random.Random(9)) == eg.mutate(base, random.Random(9))


class TestPerturb50:
    def test_stays_valid_and_off_stays_off(self):
        g = eg.normalize({"stop_pct": 8, "qsx_exit_consec": 2})
        for seed in range(200):
            p = eg.perturb_50(g, random.Random(seed))
            assert eg.validate(p) == [], f"seed={seed} 非法扰动: {p}"
            assert "trail" not in eg.families_on(p)  # 关闭家族不被扰开

    def test_moves_when_possible(self):
        # 大档位参数（stop_pct=12 → [6,18]）必然吸附到他档或端点，恒有移动能力
        g = eg.normalize({"stop_pct": 12, "trail_pct": 0.08})
        moved = any(
            eg.perturb_50(g, random.Random(s))["stop_pct"] != 12 for s in range(20)
        )
        assert moved

    def test_deterministic(self):
        g = eg.baseline_genome()
        assert eg.perturb_50(g, random.Random(4)) == eg.perturb_50(g, random.Random(4))
