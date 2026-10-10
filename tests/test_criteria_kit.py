# -*- coding: utf-8 -*-
"""criteria_kit（判据件单一来源，v0.322，owner 方法论 review #7）钉测。

锁的契约：q95（campaign 语义——空池 None/len1 该点/inclusive [94]）；
verdict 四态（C1 untested 优先；C2/C3 False 或 confirmed_fail=falsified；
全过+confirmed_pass=candidate；c2/c3 None 不可评=provisional）；
c4_state_of（空池 indeterminate/池未满 provisional/Δ>q95 confirmed_pass）；
assemble_c4_pool（重抽至过门臂满 N 或上限 10×N，确定性）。
"""

from __future__ import annotations

import random

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


# ---------------------------------------------------------------------------
# 簇构造（v0.335，owner review——C4 打乱粒度按簇不按笔）
# ---------------------------------------------------------------------------


class TestClusterIds:
    def test_adjacent_i_merges_same_code(self):
        """同 code、i 间隔 ≤5 并一簇；间隔 >5 分簇。"""
        recs = [
            {"code": "000001", "i": 10, "date": "2023-01-02"},
            {"code": "000001", "i": 12, "date": "2023-01-04"},  # 间隔 2 ⇒ 同簇
            {"code": "000001", "i": 15, "date": "2023-01-06"},  # 间隔 3 ⇒ 同簇
            {"code": "000001", "i": 21, "date": "2023-01-12"},  # 间隔 6 >5 ⇒ 新簇
            {"code": "000001", "i": 22, "date": "2023-01-13"},  # 接新簇
        ]
        ids = kit.cluster_ids(recs)
        assert len(ids) == 5
        assert ids[0] == ids[1] == ids[2]
        assert ids[3] == ids[4] != ids[0]

    def test_cross_code_never_same_cluster(self):
        recs = [
            {"code": "000001", "i": 10, "date": "2023-01-02"},
            {"code": "000002", "i": 11, "date": "2023-01-03"},  # 间隔 1 但跨 code
        ]
        ids = kit.cluster_ids(recs)
        assert ids[0] != ids[1]

    def test_unsorted_input_sorted_within_code(self):
        """输入乱序 ⇒ 同 code 内按 i 排序划簇；返回与输入等长同序。"""
        recs = [
            {"code": "000001", "i": 30, "date": "2023-02-01"},
            {"code": "000001", "i": 10, "date": "2023-01-02"},
            {"code": "000001", "i": 12, "date": "2023-01-04"},
        ]
        ids = kit.cluster_ids(recs)
        assert ids[1] == ids[2]  # i=10 与 i=12 同簇
        assert ids[0] != ids[1]  # i=30 间隔 18 ⇒ 分簇

    def test_missing_i_falls_back_to_date_gap(self):
        """缺 i ⇒ 退日期差（日历日 ≤5）；日期不可解析 ⇒ 不判邻各自成簇。"""
        recs = [
            {"code": "000001", "date": "2023-01-02"},
            {"code": "000001", "date": "2023-01-05"},  # 3 天 ⇒ 同簇
            {"code": "000001", "date": "2023-01-20"},  # 15 天 ⇒ 分簇
            {"code": "000001", "date": "not-a-date"},  # 不可解析 ⇒ 新簇
        ]
        ids = kit.cluster_ids(recs)
        assert ids[0] == ids[1]
        assert ids[2] != ids[1]
        assert ids[3] != ids[2]

    def test_mixed_i_and_date_pair_uses_date_fallback(self):
        """相邻对任一缺 i ⇒ 退日期差（1 天 ⇒ 同簇）。"""
        recs = [
            {"code": "000001", "i": 10, "date": "2023-01-02"},
            {"code": "000001", "date": "2023-01-03"},
        ]
        ids = kit.cluster_ids(recs)
        assert ids[0] == ids[1]

    def test_max_gap_param_respected(self):
        recs = [
            {"code": "000001", "i": 10, "date": "2023-01-02"},
            {"code": "000001", "i": 14, "date": "2023-01-06"},  # 间隔 4
        ]
        assert (
            kit.cluster_ids(recs, max_gap=5)[0] == kit.cluster_ids(recs, max_gap=5)[1]
        )
        a, b = kit.cluster_ids(recs, max_gap=3)
        assert a != b


class TestClusterDraw:
    def test_same_cluster_same_choice(self):
        """整簇移动钉：同簇所有元素抽中同一归属。"""
        rng = random.Random(7)
        clusters = [0, 0, 1, 2, 2, 2, 1, 0]
        out = kit.cluster_draw(clusters, ("a", "b", "c"), rng)
        assert len(out) == len(clusters)
        for cid in (0, 1, 2):
            assert len({lab for c, lab in zip(clusters, out) if c == cid}) == 1

    def test_deterministic(self):
        a = kit.cluster_draw([0, 1, 0, 2, 3, 3], ("x", "y"), random.Random(3))
        b = kit.cluster_draw([0, 1, 0, 2, 3, 3], ("x", "y"), random.Random(3))
        assert a == b

    def test_counts_fluctuate_not_aligned(self):
        """簇独立均匀抽签 ⇒ 各归属数量自然波动（v0.335：不再对齐笔数）。"""
        clusters = [i // 3 for i in range(90)]  # 30 簇 × 3 元素
        out = kit.cluster_draw(clusters, ("a", "b", "c"), random.Random(11))
        counts = {c: out.count(c) for c in ("a", "b", "c")}
        assert all(v > 0 for v in counts.values())
        # 计数按簇（30 簇二项波动）而非逐笔恒等 30/30/30
        assert not all(v == 30 for v in counts.values())
