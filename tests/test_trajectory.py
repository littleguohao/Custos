# -*- coding: utf-8 -*-
"""轨迹池（research/evolution/trajectory.py）契约测试。

锁定：谱系约束（phase ↔ parent 数量）、add 幂等/冲突语义、
best 的 RankIC 排序（NaN/缺失垫底、次键 rank_ic_mean、稳定序）、
save/load 往返逐字段相等与 fail-closed 行为。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from custos.research.evolution.trajectory import (
    DECISIONS,
    EVOLUTION_PHASES,
    Trajectory,
    TrajectoryPool,
    make_id,
)

_BASE_CREATED = "2026-09-09T10:00:00"


def _mk(*, created_at: str = _BASE_CREATED, **over) -> Trajectory:
    """造一条合法轨迹；id 默认按 make_id 规则由四元组推出。"""
    kw = dict(
        direction="量价背离",
        phase="origin",
        hypothesis="放量滞涨后 5 日内回撤",
        expression="div(volume, close)",
        complexity={"symbol_len": 3, "free_params": 1},
        mining_metrics={"rank_ic_mean": 0.05, "rank_icir": 0.4, "n_days": 60},
        decision="pass",
        feedback="IC 为正但 IR 偏低",
        parent_ids=(),
        created_at=created_at,
        run_tag="evo-2026-09-09-a",
    )
    kw.update(over)
    kw.setdefault(
        "id",
        make_id(
            direction=kw["direction"],
            hypothesis=kw["hypothesis"],
            expression=kw["expression"],
            created_at=kw["created_at"],
        ),
    )
    return Trajectory(**kw)


def _mk_scored(icir, mean, **over) -> Trajectory:
    return _mk(mining_metrics={"rank_ic_mean": mean, "rank_icir": icir}, **over)


def _dict_eq_nan(a: dict, b: dict) -> bool:
    """NaN 安全的 dict 逐键比较（roundtrip 断言用）。"""
    if a.keys() != b.keys():
        return False
    for k in a:
        x, y = a[k], b[k]
        if (
            isinstance(x, float)
            and isinstance(y, float)
            and math.isnan(x)
            and math.isnan(y)
        ):
            continue
        if x != y:
            return False
    return True


class TestMakeId:
    def test_deterministic(self):
        a = make_id(direction="d", hypothesis="h", expression="e", created_at="c")
        b = make_id(direction="d", hypothesis="h", expression="e", created_at="c")
        assert a == b

    def test_sensitive_to_any_field(self):
        base = dict(direction="d", hypothesis="h", expression="e", created_at="c")
        ref = make_id(**base)
        for k in base:
            changed = {**base, k: base[k] + "x"}
            assert make_id(**changed) != ref, k

    def test_format(self):
        tid = make_id(direction="d", hypothesis="h", expression="e", created_at="c")
        assert tid.startswith("t_")
        assert len(tid) == 12  # "t_" + sha1 前 10 位
        assert all(c in "0123456789abcdef" for c in tid[2:])

    def test_no_concat_ambiguity(self):
        """字段拼接必须无歧义：("ab","c") 与 ("a","bc") 不得同 id。"""
        a = make_id(direction="ab", hypothesis="c", expression="e", created_at="t")
        b = make_id(direction="a", hypothesis="bc", expression="e", created_at="t")
        assert a != b

    def test_rerun_same_content_gets_new_id(self):
        """钉住 docstring 描述的现实：created_at 在哈希里 ⇒ 同假设+表达式重跑
        得到的是新 id 的新轨迹（add 不幂等跳过，池中刻意累积作谱系证据）。"""
        a = _mk()
        b = _mk(created_at="2026-09-09T11:00:00")  # 同假设+表达式，仅创建时间不同
        assert a.id != b.id
        pool = TrajectoryPool()
        pool.add(a)
        pool.add(b)
        assert len(pool) == 2  # 表达式级去重在 prompt 层，不在 id 层


class TestAddGet:
    def test_add_get_len(self):
        pool = TrajectoryPool()
        t = _mk()
        pool.add(t)
        assert len(pool) == 1
        assert pool.get(t.id) == t
        assert pool.get("t_missing") is None

    def test_add_idempotent_same_content(self):
        pool = TrajectoryPool()
        t = _mk()
        pool.add(t)
        pool.add(_mk())  # 重建的同内容实例（dict 字段是新对象）
        pool.add(t)
        assert len(pool) == 1

    def test_add_conflict_raises(self):
        """同 id（四元组相同）但内容不同 → raise，不静默覆盖。"""
        pool = TrajectoryPool()
        pool.add(_mk())
        with pytest.raises(ValueError, match="冲突"):
            pool.add(_mk(feedback="被改写的反馈"))

    def test_add_allows_missing_parent(self):
        """跨 run 合并：parent 不在池中不强制。"""
        pool = TrajectoryPool()
        t = _mk(phase="mutation", parent_ids=("t_not_in_pool",))
        pool.add(t)
        assert len(pool) == 1
        assert pool.children_of("t_not_in_pool") == [t]

    def test_all_returns_insertion_order_copy(self):
        pool = TrajectoryPool()
        ts = [_mk(created_at=f"2026-09-09T10:00:0{i}") for i in range(3)]
        for t in ts:
            pool.add(t)
        assert pool.all() == ts
        pool.all().clear()  # 返回副本，改它不影响池
        assert len(pool) == 3


class TestValidation:
    @pytest.mark.parametrize(
        "over",
        [
            {"phase": "mutate"},  # phase 越界
            {"decision": "maybe"},  # decision 越界
            {"phase": "origin", "parent_ids": ("t_x",)},  # origin 带父
            {"phase": "mutation", "parent_ids": ()},  # mutation 无父
            {"phase": "mutation", "parent_ids": ("t_a", "t_b")},  # mutation 两父
            {"phase": "crossover", "parent_ids": ("t_a",)},  # crossover 单父
            {"phase": "crossover", "parent_ids": ()},  # crossover 无父
            {"mining_metrics": {"ic_mean": 0.1}},  # 缺 rank_ic_mean
            {"mining_metrics": {"rank_ic_mean": "高"}},  # 非数值
            {"mining_metrics": {"rank_ic_mean": True}},  # bool 不算数值
        ],
    )
    def test_add_invalid_raises(self, over):
        with pytest.raises(ValueError):
            TrajectoryPool().add(_mk(**over))

    def test_empty_mining_metrics_allowed(self):
        pool = TrajectoryPool()
        pool.add(_mk(mining_metrics={}))
        assert len(pool) == 1

    def test_enums_exported(self):
        assert EVOLUTION_PHASES == ("origin", "mutation", "crossover")
        assert DECISIONS == ("pass", "fail", "pending")


class TestFromDict:
    def test_roundtrip(self):
        t = _mk(phase="mutation", parent_ids=("t_parent",))
        d = t.to_dict()
        assert isinstance(d["parent_ids"], list)  # 序列化层是 list
        t2 = Trajectory.from_dict(d)
        assert t2 == t
        assert isinstance(t2.parent_ids, tuple)  # 读回转 tuple

    def test_field_order(self):
        keys = list(_mk().to_dict())
        assert keys == [
            "id",
            "direction",
            "phase",
            "hypothesis",
            "expression",
            "complexity",
            "mining_metrics",
            "decision",
            "feedback",
            "parent_ids",
            "created_at",
            "run_tag",
            "gate",  # 联合演化第一档（TODO #68）新增字段，殿后
            "exit_params",
        ]

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda d: d.update(phase="x"),  # 枚举越界
            lambda d: d.pop("hypothesis"),  # 缺字段
            lambda d: d.update(parent_ids="t_a"),  # parent_ids 非列表
            lambda d: d.update(parent_ids=[1, 2]),  # 元素非 str
            lambda d: d.update(complexity=[1]),  # complexity 非 dict
            lambda d: d.update(decision=3),  # str 字段非 str
        ],
    )
    def test_invalid_raises(self, mutate):
        d = _mk().to_dict()
        mutate(d)
        with pytest.raises(ValueError):
            Trajectory.from_dict(d)

    def test_non_dict_raises(self):
        with pytest.raises(ValueError):
            Trajectory.from_dict(["not", "a", "dict"])


class TestIndex:
    def test_by_direction(self):
        pool = TrajectoryPool()
        a = _mk(created_at="2026-09-09T10:00:01")
        b = _mk(direction="动量反转", created_at="2026-09-09T10:00:02")
        c = _mk(created_at="2026-09-09T10:00:03")
        for t in (a, b, c):
            pool.add(t)
        assert pool.by_direction("量价背离") == [a, c]
        assert pool.by_direction("动量反转") == [b]
        assert pool.by_direction("不存在") == []

    def test_lineage_three_generations(self):
        """origin → mutation(1 父) → crossover(2 父) 三代链。"""
        o1 = _mk(created_at="2026-09-09T10:00:01")
        o2 = _mk(expression="mul(volume, open)", created_at="2026-09-09T10:00:02")
        m = _mk(
            phase="mutation",
            parent_ids=(o1.id,),
            expression="div(volume, open)",
            created_at="2026-09-09T10:00:03",
        )
        x = _mk(
            phase="crossover",
            parent_ids=(m.id, o2.id),
            expression="add(div(volume, open), mul(volume, open))",
            created_at="2026-09-09T10:00:04",
        )
        pool = TrajectoryPool()
        for t in (o1, o2, m, x):
            pool.add(t)
        assert pool.children_of(o1.id) == [m]
        assert pool.children_of(m.id) == [x]
        assert pool.children_of(o2.id) == [x]
        assert pool.children_of(x.id) == []


class TestBest:
    def test_ordering_desc(self):
        pool = TrajectoryPool()
        low = _mk_scored(0.1, 0.01, created_at="2026-09-09T10:00:01")
        high = _mk_scored(0.9, 0.02, created_at="2026-09-09T10:00:02")
        mid = _mk_scored(0.5, 0.05, created_at="2026-09-09T10:00:03")
        for t in (low, high, mid):  # 刻意乱序插入
            pool.add(t)
        assert pool.best(3) == [high, mid, low]
        assert pool.best(1) == [high]
        assert pool.best(0) == []

    def test_tie_break_by_mean_then_stable(self):
        pool = TrajectoryPool()
        a = _mk_scored(0.5, 0.01, created_at="2026-09-09T10:00:01")
        b = _mk_scored(0.5, 0.08, created_at="2026-09-09T10:00:02")
        c = _mk_scored(0.5, 0.08, created_at="2026-09-09T10:00:03")
        for t in (a, b, c):
            pool.add(t)
        # icir 并列 → rank_ic_mean 高者先；mean 也并列 → 插入序稳定
        assert pool.best(3) == [b, c, a]

    def test_nan_and_missing_sink(self):
        pool = TrajectoryPool()
        real = _mk_scored(-0.2, 0.01, created_at="2026-09-09T10:00:01")
        nan = _mk_scored(float("nan"), 9.9, created_at="2026-09-09T10:00:02")
        missing = _mk(
            mining_metrics={"rank_ic_mean": 0.03},
            created_at="2026-09-09T10:00:03",
        )
        for t in (nan, missing, real):
            pool.add(t)
        # 负 icir 也是真实读数，排在 NaN/缺失前；后两者之间按 mean 排
        assert pool.best(3) == [real, nan, missing]

    def test_direction_filter(self):
        pool = TrajectoryPool()
        a = _mk_scored(0.9, 0.05, created_at="2026-09-09T10:00:01")
        b = _mk_scored(
            0.1,
            0.05,
            direction="动量反转",
            created_at="2026-09-09T10:00:02",
        )
        for t in (a, b):
            pool.add(t)
        assert pool.best(5, direction="动量反转") == [b]
        assert pool.best(5, direction="空方向") == []

    def test_decisions_filter(self):
        pool = TrajectoryPool()
        ok = _mk_scored(0.3, 0.05, created_at="2026-09-09T10:00:01")
        bad = _mk_scored(0.9, 0.05, decision="fail", created_at="2026-09-09T10:00:02")
        wait = _mk_scored(
            0.8, 0.05, decision="pending", created_at="2026-09-09T10:00:03"
        )
        for t in (ok, bad, wait):
            pool.add(t)
        assert pool.best(5) == [ok]  # 默认只选 pass
        assert pool.best(5, decisions=("pass", "fail")) == [bad, ok]
        assert pool.best(5, decisions=("pending",)) == [wait]


class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path):
        p = tmp_path / "pool.json"
        pool = TrajectoryPool(p)
        ts = [
            _mk(created_at="2026-09-09T10:00:01"),
            _mk_scored(
                float("nan"), 0.02, created_at="2026-09-09T10:00:02"
            ),  # NaN 读数须往返
            _mk(
                phase="mutation",
                parent_ids=("t_absent_parent",),
                mining_metrics={},
                created_at="2026-09-09T10:00:03",
            ),
        ]
        for t in ts:
            pool.add(t)
        pool.save()

        raw = json.loads(p.read_text(encoding="utf-8"))
        assert raw["version"] == 1
        assert len(raw["trajectories"]) == 3
        assert isinstance(raw["trajectories"][0]["parent_ids"], list)

        got = TrajectoryPool.load(p)
        assert len(got) == len(ts)
        for a, b in zip(pool.all(), got.all()):
            for name in (
                "id",
                "direction",
                "phase",
                "hypothesis",
                "expression",
                "decision",
                "feedback",
                "parent_ids",
                "created_at",
                "run_tag",
            ):
                assert getattr(a, name) == getattr(b, name), name
            assert _dict_eq_nan(a.complexity, b.complexity)
            assert _dict_eq_nan(a.mining_metrics, b.mining_metrics)

    def test_load_missing_file_gives_empty_pool(self, tmp_path):
        p = tmp_path / "nope" / "pool.json"
        pool = TrajectoryPool.load(p)
        assert len(pool) == 0
        # path 已绑定：可直接 save（父目录自动建）
        pool.add(_mk())
        pool.save()
        assert len(TrajectoryPool.load(p)) == 1

    def test_load_corrupted_json_raises(self, tmp_path):
        p = tmp_path / "pool.json"
        p.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ValueError):  # JSONDecodeError 是 ValueError 子类
            TrajectoryPool.load(p)

    def test_load_version_mismatch_raises(self, tmp_path):
        p = tmp_path / "pool.json"
        p.write_text(json.dumps({"version": 2, "trajectories": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="版本"):
            TrajectoryPool.load(p)

    def test_load_bad_record_raises(self, tmp_path):
        """池文件里混入非法轨迹 → fail-closed，不跳过。"""
        p = tmp_path / "pool.json"
        good = _mk().to_dict()
        bad = _mk().to_dict() | {"phase": "mutate"}
        p.write_text(
            json.dumps({"version": 1, "trajectories": [good, bad]}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            TrajectoryPool.load(p)

    def test_save_without_path_raises(self):
        with pytest.raises(RuntimeError):
            TrajectoryPool().save()

    def test_save_is_atomic_no_tmp_left(self, tmp_path):
        p = tmp_path / "pool.json"
        pool = TrajectoryPool(p)
        pool.add(_mk())
        pool.save()
        assert p.exists()
        assert not Path(str(p) + ".tmp").exists()


# ---------- 联合演化第一档（TODO #68）：gate / exit_params 基因组字段 ----------


class TestGenomeFields:
    def test_roundtrip_with_genome_fields(self):
        t = _mk(gate="j_low_adx25", exit_params={"stop_pct": 5, "trail_pct": 0.08})
        t2 = Trajectory.from_dict(t.to_dict())
        assert t2 == t
        assert t2.gate == "j_low_adx25"
        assert t2.exit_params == {"stop_pct": 5, "trail_pct": 0.08}

    def test_old_artifact_missing_fields_loads(self):
        # 旧产物（v1 无 gate/exit_params）load 回兼容：缺字段落默认
        d = _mk().to_dict()
        del d["gate"]
        del d["exit_params"]
        t = Trajectory.from_dict(d)
        assert t.gate == "" and t.exit_params == {}

    def test_old_pool_file_loads(self, tmp_path):
        d = _mk().to_dict()
        del d["gate"]
        del d["exit_params"]
        p = tmp_path / "pool.json"
        p.write_text(json.dumps({"version": 1, "trajectories": [d]}), encoding="utf-8")
        pool = TrajectoryPool.load(p)
        assert len(pool) == 1 and pool.all()[0].gate == ""

    def test_default_fields_when_unset(self):
        t = _mk()  # 老代码按前 12 字段构造 → 默认空 gate/空参数
        assert t.gate == "" and t.exit_params == {}
        assert t.to_dict()["gate"] == ""

    def test_bad_gate_rejected(self):
        with pytest.raises(ValueError, match="gate"):
            TrajectoryPool().add(_mk(gate=123))

    def test_bad_exit_params_rejected(self):
        for bad in (
            "not-a-dict",  # 非 dict
            {"stop_pct": True},  # bool 值拒（「真/假」不是参数读数）
            {1: 5},  # 非 str 键
            {"stop_pct": [5]},  # 容器值拒（结构级；str 值合法——stop_mode 枚举键）
            {"stop_pct": None},  # None 拒
        ):
            with pytest.raises(ValueError):
                TrajectoryPool().add(_mk(exit_params=bad))

    def test_genome_fields_in_fingerprint(self):
        # gate/exit_params 进 to_dict ⇒ 进内容指纹：同 id 不同参数 ≠ 幂等跳过
        pool = TrajectoryPool()
        pool.add(_mk(gate="j_low", exit_params={"stop_pct": 5}))
        with pytest.raises(ValueError, match="冲突"):
            pool.add(_mk(gate="j_low_adx25", exit_params={"stop_pct": 5}))
