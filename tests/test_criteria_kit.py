# -*- coding: utf-8 -*-
"""criteria_kit（判据件单一来源，v0.322，owner 方法论 review #7）钉测。

锁的契约：q95（campaign 语义——空池 None/len1 该点/inclusive [94]）；
verdict 四态（C1 untested 优先；C2/C3 False 或 confirmed_fail=falsified；
全过+confirmed_pass=candidate；c2/c3 None 不可评=provisional）；
c4_state_of（空池 indeterminate/池未满 provisional/Δ>q95 confirmed_pass）；
assemble_c4_pool（重抽至过门臂满 N 或上限 10×N，确定性）。
"""

from __future__ import annotations

import pytest

from custos.research import criteria_kit as kit


class TestQ95:
    def test_empty_and_single(self):
        assert kit.q95([]) is None
        assert kit.q95([0.5]) == 0.5

    def test_inclusive_interpolation(self):
        # inclusive 法：位置 = 0.95×(n−1)；pool=1..100 → 94.05 位 → 95.05
        assert kit.q95(list(range(1, 101))) == pytest.approx(95.05)

    def test_quantile_converges_where_max_diverges(self):
        pool = [0.5] * 79 + [0.99]
        assert kit.q95(pool) == pytest.approx(0.5)  # 分位不被单点拉走（棘轮已废）


class TestVerdictFourState:
    def test_c1_untested_priority(self):
        # C1 不过 ⇒ untested 优先——即使 C2 也不过（样本不足≠否定证据）
        assert (
            kit.verdict_four_state(
                c1_ok=False, c2_ok=False, c3_ok=True, c4_state="confirmed_pass"
            )
            == "untested"
        )

    def test_falsified_branches(self):
        for c2, c3, c4s in (
            (False, True, "confirmed_pass"),
            (True, False, "confirmed_pass"),
            (True, True, "confirmed_fail"),
        ):
            assert (
                kit.verdict_four_state(c1_ok=True, c2_ok=c2, c3_ok=c3, c4_state=c4s)
                == "falsified"
            ), (c2, c3, c4s)

    def test_candidate_requires_all_pass(self):
        assert (
            kit.verdict_four_state(
                c1_ok=True, c2_ok=True, c3_ok=True, c4_state="confirmed_pass"
            )
            == "candidate"
        )

    def test_provisional_on_pool_shortfall_and_na(self):
        # C4 池未满/indeterminate ⇒ provisional 不放行
        for c4s in ("provisional", "indeterminate"):
            assert (
                kit.verdict_four_state(c1_ok=True, c2_ok=True, c3_ok=True, c4_state=c4s)
                == "provisional"
            )
        # C3 not_applicable（c3_ok=None，R40 v0.307 族）：不降 falsified 也不放行
        assert (
            kit.verdict_four_state(
                c1_ok=True, c2_ok=True, c3_ok=None, c4_state="confirmed_pass"
            )
            == "provisional"
        )
        # C2 池未建（c2_ok=None）同样落 provisional
        assert (
            kit.verdict_four_state(
                c1_ok=True, c2_ok=None, c3_ok=True, c4_state="confirmed_pass"
            )
            == "provisional"
        )

    def test_illegal_c4_state_rejected(self):
        with pytest.raises(ValueError, match="非法 C4 状态"):
            kit.verdict_four_state(c1_ok=True, c2_ok=True, c3_ok=True, c4_state="pass")


class TestC4StateOf:
    def test_empty_indeterminate(self):
        assert kit.c4_state_of([], min_pool=3, plan_delta=0.5) == "indeterminate"

    def test_small_pool_provisional(self):
        assert kit.c4_state_of([0.1], min_pool=3, plan_delta=0.5) == "provisional"

    def test_confirmed_pass_and_fail(self):
        pool = [0.0] * 10
        assert kit.c4_state_of(pool, min_pool=3, plan_delta=0.1) == "confirmed_pass"
        assert kit.c4_state_of(pool, min_pool=3, plan_delta=-0.1) == "confirmed_fail"
        assert kit.c4_state_of(pool, min_pool=3, plan_delta=None) == "confirmed_fail"


class TestAssembleC4Pool:
    def test_fill_at_low_pass_rate(self):
        """20% 过门：重抽至过门臂满 N=5 ⇒ 评估 25 次（v0.317 族）。"""
        calls = {"n": 0}

        def _arm(_i):
            calls["n"] += 1
            return 0.1 if calls["n"] % 5 == 0 else None

        got = kit.assemble_c4_pool(5, _arm)
        assert got["pool"] == [0.1] * 5
        assert got["evaluated"] == 25
        assert got["max_arms"] == 50
        assert got["gate_pass_rate"] == pytest.approx(0.2)

    def test_cap_degrades(self):
        """全不过门 ⇒ 打满上限 10×N，池空（由 c4_state_of 判 indeterminate）。"""
        got = kit.assemble_c4_pool(3, lambda _i: None)
        assert got["pool"] == []
        assert got["evaluated"] == got["max_arms"] == 30
        assert (
            kit.c4_state_of(got["pool"], min_pool=3, plan_delta=0.5) == "indeterminate"
        )

    def test_deterministic(self):
        a = kit.assemble_c4_pool(5, lambda i: float(i) if i % 2 else None)
        b = kit.assemble_c4_pool(5, lambda i: float(i) if i % 2 else None)
        assert a == b
