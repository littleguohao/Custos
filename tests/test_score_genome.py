# -*- coding: utf-8 -*-
"""score_genome 钉测：复合编译合法性 / 权重格去重与保底 / 灵敏度扰动确定性。

口径来源：R30 的 gcd 比例等价去重（尺度不变性）+ expr_dsl 的白名单/复杂度门。
"""

import random

import pytest

from custos.research.evolution import expr_dsl
from custos.research.evolution.score_genome import (
    _COMPOSITE_VIOLATION_LIMITS,
    baseline_arms,
    compile_composite,
    perturb_weights,
    weight_lattice,
)

LEGS2 = ["MA(close,20)/close", "volume/MA(volume,20)"]


class TestCompileComposite:
    def test_product_shape_and_reparse(self):
        expr = compile_composite(LEGS2, [1, 2])
        # v0.234 瘦身：weight=1 的腿省略 1* 前缀
        assert (
            expr == "TS_RANK((MA(close,20)/close),250)"
            "+2*TS_RANK((volume/MA(volume,20)),250)"
        )
        # 产物必须再过 parse + violations（结构性豁免口径下）为空
        comp = expr_dsl.complexity(expr_dsl.parse(expr))
        assert not expr_dsl.violations(comp, **_COMPOSITE_VIOLATION_LIMITS)

    def test_weight1_prefix_omitted_and_single_leg_bare(self):
        """瘦身形态钉死：weight=1 无前缀；单腿权重 1 时是裸 TS_RANK。"""
        assert compile_composite(["close"], [1]) == "TS_RANK((close),250)"
        assert (
            compile_composite(["close"], [1], rank_window=60) == "TS_RANK((close),60)"
        )
        # 混合：weight=1 省略、weight≠1 保留前缀
        assert (
            compile_composite(["close", "volume", "open"], [1, 2, 1])
            == "TS_RANK((close),250)+2*TS_RANK((volume),250)+TS_RANK((open),250)"
        )

    def test_slimmed_product_bitwise_equal_to_prefixed(self):
        """编译瘦身对拍：旧形态（带 1* 前缀）与新产物数值逐位一致（1*x≡x 精确恒等）。"""
        import numpy as np
        import pandas as pd

        rng = np.random.default_rng(5)
        n = 300
        df = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=n),
                "open": 10 + np.cumsum(rng.normal(0, 0.2, n)),
                "high": 11 + np.cumsum(rng.normal(0, 0.2, n)),
                "low": 9 + np.cumsum(rng.normal(0, 0.2, n)),
                "close": 10 + np.cumsum(rng.normal(0, 0.2, n)),
                "volume": abs(rng.normal(1e6, 2e5, n)),
            }
        )
        new = compile_composite(LEGS2, [1, 2])
        old = (
            "1*TS_RANK((MA(close,20)/close),250)+2*TS_RANK((volume/MA(volume,20)),250)"
        )
        a = expr_dsl.evaluate(new, df).to_numpy(dtype=float)
        b = expr_dsl.evaluate(old, df).to_numpy(dtype=float)
        np.testing.assert_array_equal(a, b)  # 逐位一致（含 NaN 位置）

    def test_rank_window_custom_and_int_weight_format(self):
        expr = compile_composite(["close"], [3], rank_window=60)
        assert expr == "3*TS_RANK((close),60)"
        # 非整数权重的确定性文本形（repr 最短往返）
        expr2 = compile_composite(["close"], [0.5], rank_window=60)
        assert expr2 == "0.5*TS_RANK((close),60)"

    def test_zero_weight_leg_not_compiled(self):
        expr = compile_composite(["close", "MA(close,20)/close", "volume"], [0, 2, 0])
        assert expr == "2*TS_RANK((MA(close,20)/close),250)"

    def test_invalid_leg_rejected_with_index(self):
        with pytest.raises(ValueError, match=r"legs\[1\]"):
            compile_composite(["close", "SMA(close,20)"], [1, 1])  # SMA 不在白名单
        with pytest.raises(ValueError, match=r"legs\[0\]"):
            compile_composite(["close; drop table"], [1])

    @pytest.mark.parametrize(
        "legs,weights,frag",
        [
            ([], [1], "非空"),
            (["close"], [], "数量必须与 legs 一致"),
            (["close"], [-1], "非负"),
            (["close"], [float("nan")], "非负有限"),
            (["close"], [True], "非负数值"),
            (["close"], [0], "全零"),
            (["close"], ["1"], "非负数值"),
        ],
    )
    def test_bad_weights_rejected(self, legs, weights, frag):
        with pytest.raises(ValueError, match=frag):
            compile_composite(legs, weights)

    @pytest.mark.parametrize("k", [0, -1, 251, 999, True, 2.5])
    def test_bad_rank_window_rejected(self, k):
        with pytest.raises(ValueError, match="rank_window"):
            compile_composite(["close"], [1], rank_window=k)

    def test_r34_scale_6_leg_equal_compiles(self):
        """v0.234 口径修复：R34 实据规模（6 条长腿、symbol_len>300/depth>12）的
        等权复合**编译通过**——v1 缺口（等权基线越界 448>300、13>12）不再复现。"""
        legs = [  # VWAP 偏离×量能族风格的长腿（R34 实据腿型长度构造）
            "((close-MA(close,20))/(MA(close,20)+0.001))*TS_RANK(volume,20)",
            "(volume/(MA(volume,20)+1))*(1-TS_RANK(close,60))",
            "((high-close)/(high-low+0.001))*(volume/(MA(volume,20)+1))",
            "(close-low)/(high-low+0.001)",
            "MA(volume,5)/(MA(volume,60)+1)",
            "((close-REF(close,5))/(REF(close,5)+0.001))*(volume/(MA(volume,60)+1))",
        ]
        expr = compile_composite(legs, [1] * 6)
        comp = expr_dsl.complexity(expr)
        # 构造自检：规模确在 v1 缺口区间（旧单因子门 300/12 会拒，证明测试不是空转）
        assert comp.symbol_len > 300 or comp.depth > 12
        assert expr_dsl.violations(comp), "旧默认门应拒（v1 缺口复现）"
        # 复合专用口径下通过
        assert not expr_dsl.violations(comp, **_COMPOSITE_VIOLATION_LIMITS)

    def test_overlength_composite_rejected_by_symbol_len(self):
        """COMPOSITE 上限仍是硬门：symbol_len > 1200 的失控拼接拒收。

        ⚠️ 构造说明：MAX_EXPR_NODES=400 会先拦下多数超长拼接（病态输入其实
        死在节点数门）；要触发 symbol_len 分支需要 >3 字符/节点的形态
        （长字面量常量）——本用例钉的是「节点数门漏过去的超长产物仍被
        symbol_len 主门拦下」。
        """
        long_leg = "(" + "+".join(["volume*123456789.123456789"] * 5) + ")"
        with pytest.raises(ValueError, match="symbol_len"):
            compile_composite([long_leg] * 9, [1] * 9)

    def test_overdepth_composite_rejected_by_depth(self):
        """COMPOSITE 上限仍是硬门：depth > 16 的病态嵌套拒收。"""
        deep_leg = "MA(MA(MA(MA(MA(MA(close,5),5),5),5),5),5)"  # leg depth 7
        with pytest.raises(ValueError, match="depth"):
            compile_composite([deep_leg] * 10, [1] * 10)  # 链深 9+8=17 > 16

    def test_single_factor_default_thresholds_untouched(self):
        """单因子 DSL 的 violations() 默认阈值**一律不动**（300/6/6/8/12）——
        复合放宽只走参数化覆盖，默认面不许被顺手改松。"""
        import inspect

        sig = inspect.signature(expr_dsl.violations)
        defaults = {k: v.default for k, v in sig.parameters.items() if k != "comp"}
        assert defaults == {
            "max_symbol_len": 300,
            "max_free_params": 6,
            "max_base_features": 6,
            "max_repeat_subtrees": 8,
            "max_depth": 12,
        }
        # 行为面：symbol_len>300 的单因子表达式在默认门下仍被拒
        long_single = "+".join(["MA(close,20)"] * 24)  # ~310 字符
        bad = expr_dsl.violations(expr_dsl.complexity(long_single))
        assert any("symbol_len" in b for b in bad)

    def test_max_window_boundary_ok(self):
        assert compile_composite(["close"], [1], rank_window=250).endswith(",250)")


class TestWeightLattice:
    def test_sizes_after_gcd_dedupe(self):
        """{0,1,2,3}^n 枚举剔全零后按比例等价去重的规模钉死（防去重逻辑漂移）。"""
        assert len(weight_lattice(1)) == 1
        assert len(weight_lattice(2)) == 9
        assert len(weight_lattice(3)) == 49

    def test_ratio_equivalence_deduped(self):
        lat = set(weight_lattice(2))
        assert (2.0, 2.0) not in lat and (1.0, 1.0) in lat  # (2,2)≡(1,1)
        assert (0.0, 2.0) not in lat and (0.0, 1.0) in lat  # (0,2)≡(0,1)
        assert (0.0, 0.0) not in lat  # 全零排除

    def test_sorted_and_float_tuples(self):
        lat = weight_lattice(3)
        assert lat == sorted(lat)
        assert all(isinstance(x, float) for c in lat for x in c)

    def test_truncation_keeps_baselines(self):
        """截断保底：单腿/等权对照向量永远保留，砍的是多腿混合高档组合。"""
        lat = weight_lattice(4, max_combos=10)
        assert len(lat) == 10
        for single in [(1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)]:
            assert tuple(float(x) for x in single) in lat
        assert (1.0, 1.0, 1.0, 1.0) in lat
        # 保底 5 个之外的席位按字典序补满
        assert lat[5:] == sorted(lat[5:])

    def test_no_truncation_when_under_cap(self):
        assert len(weight_lattice(3, max_combos=64)) == 49  # 49 ≤ 64 不截

    def test_bad_args_rejected(self):
        with pytest.raises(ValueError):
            weight_lattice(0)
        with pytest.raises(ValueError):
            weight_lattice(2, levels=(0, 0))  # 全零刻度
        with pytest.raises(ValueError):
            weight_lattice(2, levels=(0, 1.5))  # 非整数刻度（gcd 去重不成立）
        with pytest.raises(ValueError):
            weight_lattice(2, max_combos=0)


class TestPerturbWeights:
    def test_deterministic_with_seeded_rng(self):
        a = perturb_weights((1.0, 2.0, 0.5), rng=random.Random(7))
        b = perturb_weights((1.0, 2.0, 0.5), rng=random.Random(7))
        assert a == b
        assert all(x >= 0 for x in a)

    def test_seed_changes_output(self):
        a = perturb_weights((1.0, 1.0), rng=random.Random(1))
        b = perturb_weights((1.0, 1.0), rng=random.Random(2))
        assert a != b

    def test_zero_weight_stays_zero_when_pct_le_1(self):
        out = perturb_weights((1.0, 0.0), pct=0.5, rng=random.Random(3))
        assert out[1] == 0.0

    def test_perturbed_weights_compile(self):
        """灵敏度臂产物必须能直接进 compile（驱动层就是这么用的）。"""
        out = perturb_weights((1.0, 2.0), pct=0.5, rng=random.Random(11))
        expr = compile_composite(LEGS2, list(out))
        assert expr_dsl.parse(expr)

    def test_bad_args_rejected(self):
        with pytest.raises(ValueError):
            perturb_weights((0.0, 0.0), rng=random.Random(1))  # 全零输入
        with pytest.raises(ValueError):
            perturb_weights((1.0,), pct=-0.1, rng=random.Random(1))


class TestBaselineArms:
    def test_equal_and_singles(self):
        arms = baseline_arms(3)
        assert arms["equal"] == (1.0, 1.0, 1.0)
        assert arms["single_0"] == (1.0, 0.0, 0.0)
        assert arms["single_2"] == (0.0, 0.0, 1.0)

    def test_arms_survive_lattice_truncation(self):
        """对照臂向量恒在格子保底集里（驱动层按名字从格子结果取臂，预算免费）。"""
        n = 5
        lat = set(weight_lattice(n, max_combos=8))  # 强制截断
        for w in baseline_arms(n).values():
            assert w in lat

    def test_bad_n_legs(self):
        with pytest.raises(ValueError):
            baseline_arms(0)
