# -*- coding: utf-8 -*-
"""轨迹池：LLM 因子进化引擎的结构化谱系（研究侧专用）。

借鉴 QuantaAlpha 的轨迹级进化：一条「研究轨迹」= 假设 + 因子表达式 +
挖掘窗指标 + 判定 + 反馈 + parent_ids 血统。轨迹池持久化全部轨迹，
支持按方向索引、按父代反查子代、按主指标（RankIC）排序选父代。

Custos 现有研究单元（governance/research/R1-R31）是平铺 markdown，
谱系靠文档自觉维护、已知会漂移 —— 本模块把谱系结构化、可机读。

设计约束：
  · 纯 stdlib，不 import 同包其他模块 —— expr_dsl / ic_eval / dual_window
    与本模块并行开发，互相不依赖才能避免循环与合并冲突。
  · fail-closed：枚举越界 / 血统数量不符 / 指标缺主键 / JSON 损坏，
    一律 raise，不静默修复。
  · decision 由确定性判据给出、LLM 不得改写；feedback 才是 LLM 的解读区。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EVOLUTION_PHASES: tuple[str, ...] = ("origin", "mutation", "crossover")
DECISIONS: tuple[str, ...] = ("pass", "fail", "pending")

# 挖掘窗指标主键：mining_metrics 非空时必须带数值型 rank_ic_mean；
# best() 的主排序键 rank_icir / 次键 rank_ic_mean 也从该 dict 取。
_REQUIRED_METRIC = "rank_ic_mean"

_FILE_VERSION = 1

# from_dict 的必填字段（缺任一 → ValueError，fail-closed）
_FIELDS: tuple[str, ...] = (
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
)

_STR_FIELDS: tuple[str, ...] = (
    "id",
    "direction",
    "phase",
    "hypothesis",
    "expression",
    "decision",
    "feedback",
    "created_at",
    "run_tag",
)


def make_id(
    *, direction: str, hypothesis: str, expression: str, created_at: str
) -> str:
    """轨迹 id：四元组 sha1 的前 10 位（``\\x1f`` 分隔，防拼接歧义）。

    ``created_at`` 参与哈希 ⇒ id **每次创建都唯一**：同一假设+表达式重跑出来
    的是新 id 的新轨迹，``add`` 不会把它们幂等跳过（``add`` 的幂等跳过只对
    「同 id 且内容逐位相同」的重复入池生效，例如跨 run 从盘上 load 回同一批
    轨迹后重建的同内容实例）。表达式级去重发生在 prompt 层
    （``operators._existing_expressions`` 注入去重列表，约束 LLM 产出机制
    不同的候选）；池中刻意允许累积相似表达式 —— 它们是谱系（lineage）证据。
    ``run_tag`` 不参与哈希。
    """
    blob = "\x1f".join((direction, hypothesis, expression, created_at))
    return "t_" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]


def _is_number(v: Any) -> bool:
    """数值判定：bool 是 int 的子类，但「真/假」不是研究读数，剔除。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_param_value(v: Any) -> bool:
    """出场参数值的结构判定：数值或 str（stop_mode 等枚举键也是合法分量）。

    bool / 容器 / None 一律拒（结构级 fail-closed）；档位白名单归 genome。
    """
    if isinstance(v, bool):
        return False
    return isinstance(v, (int, float, str))


def _validate(t: Trajectory) -> None:
    """单条轨迹的自洽校验（from_dict 与 add 共用同一套判据）。"""
    if t.phase not in EVOLUTION_PHASES:
        raise ValueError(f"phase 越界: {t.phase!r}（合法: {EVOLUTION_PHASES}）")
    if t.decision not in DECISIONS:
        raise ValueError(f"decision 越界: {t.decision!r}（合法: {DECISIONS}）")
    n = len(t.parent_ids)
    if t.phase == "origin" and n != 0:
        raise ValueError(f"origin 必须无父代，实际 {n} 个: {t.parent_ids}")
    if t.phase == "mutation" and n != 1:
        raise ValueError(f"mutation 必须恰好 1 个父代，实际 {n} 个")
    if t.phase == "crossover" and n < 2:
        raise ValueError(f"crossover 必须 ≥2 个父代，实际 {n} 个")
    if t.mining_metrics:
        v = t.mining_metrics.get(_REQUIRED_METRIC)
        if not _is_number(v):
            raise ValueError(
                f"mining_metrics 非空时必须含数值型 {_REQUIRED_METRIC}，实际: {v!r}"
            )
    if not isinstance(t.gate, str):
        raise ValueError(f"gate 必须是 str，实际: {type(t.gate).__name__}")
    if not isinstance(t.exit_params, dict) or not all(
        isinstance(k, str) and _is_param_value(v) for k, v in t.exit_params.items()
    ):
        # 结构级校验（{str: 数值|str}，bool/容器/None 拒；stop_mode 等枚举键
        # 的值是 str）；语义白名单（gate 枚举/参数档位）归 genome.validate_params
        # —— 本模块保持零同包依赖。
        raise ValueError(
            f"exit_params 必须是 dict[str, 数值|str]（bool/容器拒）: {t.exit_params!r}"
        )


def _metric_rank(v: Any) -> float:
    """排序键：非数值（含 bool）与 NaN 一律 -inf（垫底）。"""
    if not _is_number(v):
        return float("-inf")
    f = float(v)
    return float("-inf") if math.isnan(f) else f


def _fingerprint(t: Trajectory) -> str:
    """内容指纹：NaN 在 JSON 层序列化为 ``NaN`` 字面量，天然规避 NaN != NaN。

    add 的幂等判定靠它：跨 run 合并时，从盘上 load 回来的轨迹与内存里
    重建的同内容实例必须被判等，而 mining_metrics 里的 NaN 读数
    （零方差 IC）会让普通 ``==`` 误判不等。
    """
    return json.dumps(t.to_dict(), ensure_ascii=False, sort_keys=True, allow_nan=True)


@dataclass(frozen=True)
class Trajectory:
    """一条研究轨迹（不可变；改判定请新建一条，别原地改历史）。"""

    id: str  # "t_" + sha1(...)[:10]，由 make_id 生成
    direction: str  # 探索方向（用户/规划层给的一句话）
    phase: str  # ∈ EVOLUTION_PHASES
    hypothesis: str  # 假设文本
    expression: str  # DSL 因子表达式
    complexity: dict[str, Any]  # Complexity 的 asdict（symbol_len/free_params/...）
    mining_metrics: dict[
        str, Any
    ]  # 挖掘窗 ICStats asdict（rank_ic_mean/rank_icir/...）
    decision: str  # ∈ DECISIONS，确定性判据给出，LLM 不得改写
    feedback: str  # 反馈文本（LLM 可写解读，但判定以 decision 为准）
    parent_ids: tuple[str, ...]  # 血统：origin 空 / mutation 恰 1 / crossover ≥2
    created_at: str  # ISO8601
    run_tag: str  # 一次进化运行的标识
    # 联合演化第一档（TODO #68）的基因组参数分量；非 joint 轨迹留默认。
    # 默认值放最后 ⇒ 旧代码按前 12 字段构造不受影响；from_dict 容忍旧产物缺省。
    gate: str = ""  # gate 配置（genome.GATE_CHOICES 之一；""=未启用 joint）
    exit_params: dict[str, Any] = field(default_factory=dict)  # 出场参数分量

    def to_dict(self) -> dict[str, Any]:
        """手工序列化（不用 dataclasses.asdict 递归，显式控制字段序）。"""
        return {
            "id": self.id,
            "direction": self.direction,
            "phase": self.phase,
            "hypothesis": self.hypothesis,
            "expression": self.expression,
            "complexity": dict(self.complexity),
            "mining_metrics": dict(self.mining_metrics),
            "decision": self.decision,
            "feedback": self.feedback,
            "parent_ids": list(self.parent_ids),
            "created_at": self.created_at,
            "run_tag": self.run_tag,
            "gate": self.gate,
            "exit_params": dict(self.exit_params),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Trajectory:
        """反序列化 + 校验；任何形状/枚举/血统违规 → ValueError。"""
        if not isinstance(d, dict):
            raise ValueError(f"轨迹记录必须是 dict，实际: {type(d).__name__}")
        missing = [k for k in _FIELDS if k not in d]
        if missing:
            raise ValueError(f"轨迹记录缺字段: {missing}")
        str_fields: dict[str, str] = {}
        for k in _STR_FIELDS:
            v = d[k]
            if not isinstance(v, str):
                raise ValueError(f"{k} 必须是 str，实际: {type(v).__name__}")
            str_fields[k] = v
        dict_fields: dict[str, dict[str, Any]] = {}
        for k in ("complexity", "mining_metrics"):
            v = d[k]
            if not isinstance(v, dict):
                raise ValueError(f"{k} 必须是 dict，实际: {type(v).__name__}")
            dict_fields[k] = dict(v)
        raw_parents = d["parent_ids"]
        if not isinstance(raw_parents, (list, tuple)) or not all(
            isinstance(p, str) for p in raw_parents
        ):
            raise ValueError("parent_ids 必须是 str 列表")
        # 联合演化字段：旧产物（v1 无 gate/exit_params）缺省 → 落默认（回兼容）；
        # 存在但形状不对 → 交给 _validate 拒（结构级，fail-closed）。
        t = cls(
            id=str_fields["id"],
            direction=str_fields["direction"],
            phase=str_fields["phase"],
            hypothesis=str_fields["hypothesis"],
            expression=str_fields["expression"],
            complexity=dict_fields["complexity"],
            mining_metrics=dict_fields["mining_metrics"],
            decision=str_fields["decision"],
            feedback=str_fields["feedback"],
            parent_ids=tuple(raw_parents),
            created_at=str_fields["created_at"],
            run_tag=str_fields["run_tag"],
            gate=d.get("gate", ""),
            exit_params=d.get("exit_params", {}),
        )
        _validate(t)
        return t


class TrajectoryPool:
    """轨迹池：全量轨迹的内存索引 + JSON 持久化。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._items: list[Trajectory] = []  # 插入序，all/best 的稳定序来源
        self._by_id: dict[str, Trajectory] = {}

    def add(self, t: Trajectory) -> None:
        """入池。id 冲突：同内容 → 幂等跳过；不同内容 → ValueError。

        parent_ids 引用的 id 允许不在池中（跨 run 合并场景），不强制。
        """
        _validate(t)
        old = self._by_id.get(t.id)
        if old is not None:
            if _fingerprint(old) == _fingerprint(t):
                return
            raise ValueError(f"id 冲突且内容不同: {t.id}")
        self._items.append(t)
        self._by_id[t.id] = t

    def __len__(self) -> int:
        return len(self._items)

    def get(self, tid: str) -> Trajectory | None:
        return self._by_id.get(tid)

    def all(self) -> list[Trajectory]:
        """插入序的副本（改返回值不影响池）。"""
        return list(self._items)

    def by_direction(self, direction: str) -> list[Trajectory]:
        return [t for t in self._items if t.direction == direction]

    def children_of(self, tid: str) -> list[Trajectory]:
        return [t for t in self._items if tid in t.parent_ids]

    def best(
        self,
        n: int = 5,
        *,
        direction: str | None = None,
        decisions: tuple[str, ...] = ("pass",),
    ) -> list[Trajectory]:
        """按 rank_icir 降序选父代（缺失/NaN 垫底），次键 rank_ic_mean。

        list.sort 稳定 ⇒ 主次键全并列时保持插入序，结果可复现。
        """
        cands = [
            t
            for t in self._items
            if (direction is None or t.direction == direction)
            and t.decision in decisions
        ]
        cands.sort(
            key=lambda t: (
                _metric_rank(t.mining_metrics.get("rank_icir")),
                _metric_rank(t.mining_metrics.get(_REQUIRED_METRIC)),
            ),
            reverse=True,
        )
        return cands[: max(n, 0)]

    def save(self) -> None:
        """原子写（tmp + replace）；path 为 None → RuntimeError。

        允许 NaN（研究侧刻意允许：零方差 IC 之类是合法读数），所以不复用
        生产侧 ``paths.write_json``（allow_nan=False）；也不 import 同包的
        ``backtest_factors.write_json_stream`` —— 本模块保持零同包依赖。
        """
        if self._path is None:
            raise RuntimeError("未绑定路径的池不能 save（构造时传 path 或用 load）")
        payload = {
            "version": _FILE_VERSION,
            "trajectories": [t.to_dict() for t in self._items],
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, allow_nan=True)
        tmp.replace(self._path)

    @classmethod
    def load(cls, path: Path) -> TrajectoryPool:
        """文件不存在 → 空池（path 仍绑定，可直接 save）；损坏 → raise。"""
        pool = cls(path)
        if not path.exists():
            return pool
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)  # 损坏 → JSONDecodeError(ValueError)
        if not isinstance(payload, dict) or payload.get("version") != _FILE_VERSION:
            raise ValueError(f"轨迹池版本不符或顶层不是对象: {path}")
        records = payload.get("trajectories")
        if not isinstance(records, list):
            raise ValueError(f"trajectories 必须是 list: {path}")
        for rec in records:
            pool.add(Trajectory.from_dict(rec))
        return pool
