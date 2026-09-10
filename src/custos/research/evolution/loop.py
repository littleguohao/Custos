# -*- coding: utf-8 -*-
"""进化编排循环（纯库，无 argparse）：方向 × 轮次 × 候选 的算子编排与轨迹落池。

双窗纪律（本模块存在的核心理由）：``run_loop`` 进入循环前先把 bars **物理截尾**
到 ``mining_end``，全程只用这份副本评估 —— ``mining_end`` 之后的行不在被传入的
内存对象里，LLM 产出 / 评估层 bug 都读不到判定窗数据。判定窗数据**只有**
``final_judgment()`` 能碰，且只能在进化闭环结束后调用。

算子与判定的分工见 operators.py 模块 docstring：LLM 只产出候选与解读，
``decision`` 一律由 ``operators.judge_mining``（确定性规则）给出。
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from custos.research.evolution import operators
from custos.research.evolution.dual_window import (
    DualWindowResult,
    Window,
    run_dual_window,
    validate_windows,
)
from custos.research.evolution.expr_dsl import ExprError, complexity, parse, violations
from custos.research.evolution.ic_eval import evaluate_expression_with_series
from custos.research.evolution.llm_client import ChatLLM, LLMError
from custos.research.evolution.trajectory import (
    EVOLUTION_PHASES,
    Trajectory,
    TrajectoryPool,
    make_id,
)


@dataclass(frozen=True)
class LoopConfig:
    """进化循环配置（不可变；judge_mining 的阈值也挂这里）。

    12 个字段各有正交语义（方向/轮次/窗口/horizon/phase 轮换/种子/判定阈值），
    刻意不拆子配置 —— 摊开让 CLI 参数面与配置一一对齐（R0902 有意保留）。
    """

    directions: tuple[str, ...]  # 探索方向（每方向独立跑 rounds × candidates）
    rounds: int  # 进化轮数
    candidates_per_round: int  # 每方向每轮候选数
    mining_start: str  # 挖掘窗起点 YYYY-MM-DD（含端点）
    mining_end: str  # 挖掘窗终点（硬隔离线：之后的数据循环全程不接触）
    horizon: int = 5  # 前向收益 horizon（交易日）
    phase_schedule: tuple[str, ...] = ("origin", "mutation", "crossover", "mutation")
    seed: int = 20260909  # 父代选择等随机性的种子（可复现）
    run_tag: str = "evolution"  # Trajectory.run_tag；CLI 传 --tag
    min_days: int = 20
    min_rank_ic: float = 0.02
    min_rank_icir: float = 0.1


def _now_iso() -> str:
    """轨迹 created_at 的唯一来源（测试可 monkeypatch 钉死时间做逐位对比）。"""
    return datetime.now(timezone.utc).isoformat()


def clip_tail(
    bars_by_code: dict[str, pd.DataFrame], end: str
) -> dict[str, pd.DataFrame]:
    """物理截尾到 end（含端点，按 date 列比较），返回新 dict 的 copy，空帧剔除。

    只截尾不截头：挖掘窗起点切片由 ``evaluate_expression(start=...)`` 负责，
    本函数的唯一目的是让 end 之后的数据**不在场**（双窗硬隔离的第一道）。
    """
    end_ts = pd.Timestamp(end)
    out: dict[str, pd.DataFrame] = {}
    for code, df in bars_by_code.items():
        if not len(df):
            continue
        d = df[pd.to_datetime(df["date"]) <= end_ts].copy().reset_index(drop=True)
        if len(d):
            out[code] = d
    return out


# ── 父代选择（seeded rng，可复现）───────────────────────────────────────────


def _pick_crossover_parents(
    pool: TrajectoryPool, rng: random.Random
) -> list[Trajectory]:
    """crossover 父代：池内 best(5) 里 seeded 选 2 个，**方向不同优先**。"""
    cands = pool.best(5)
    if len(cands) < 2:
        return []
    first = rng.choice(cands)
    others = [t for t in cands if t.id != first.id]
    diff = [t for t in others if t.direction != first.direction]
    return [first, rng.choice(diff if diff else others)]


def _resolve_phase(
    phase: str, direction: str, pool: TrajectoryPool, rng: random.Random
) -> tuple[str, list[Trajectory]]:
    """按计划 phase 落实父代；父代不足沿 crossover → mutation → origin 降级。"""
    if phase == "crossover":
        parents = _pick_crossover_parents(pool, rng)
        if len(parents) >= 2:
            return "crossover", parents
        phase = "mutation"
    if phase == "mutation":
        best = pool.best(1, direction=direction)
        if best:
            return "mutation", best
    return "origin", []


# ── 单候选流水线 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _RunCtx:
    """一次 run_loop 的运行上下文（聚合原先在 helper 间穿行的 6 个散参）。

    rng 是可变对象（random.Random）：frozen 只冻结属性绑定，不冻结其内部状态。
    """

    cfg: LoopConfig
    mining_bars: dict[str, pd.DataFrame]  # 已物理截尾到 mining_end 的副本
    llm: ChatLLM
    pool: TrajectoryPool
    rng: random.Random
    on_event: Callable[[dict], None] | None


@dataclass(frozen=True)
class _CandCtx:
    """单候选上下文：方向 / 落实后的实际 phase / 父代血统 / 轮次序号。"""

    direction: str
    phase: str  # 实际 phase（父代不足降级后可能与计划 phase 不同）
    parent_ids: tuple[str, ...]
    round_i: int
    cand_i: int


@dataclass(frozen=True)
class _Judged:
    """单候选的判定产出（_new_trajectory 的四个散参聚合）。"""

    comp_dict: dict[str, Any]
    metrics: dict[str, Any]
    decision: str
    feedback: str


def _call_operator(
    cand: _CandCtx, parents: list[Trajectory], ctx: _RunCtx, hint: str | None
) -> dict[str, str]:
    if cand.phase == "origin":
        return operators.propose(
            cand.direction, ctx.pool, ctx.llm, rng=ctx.rng, hint=hint
        )
    if cand.phase == "mutation":
        return operators.mutate(parents[0], ctx.pool, ctx.llm, hint=hint)
    return operators.crossover(parents, ctx.pool, ctx.llm, hint=hint)


def _produce_candidate(
    cand: _CandCtx, parents: list[Trajectory], ctx: _RunCtx
) -> tuple[dict[str, str], ExprError | None]:
    """LLM 产出 + DSL 白名单门：ExprError 把错误回注 LLM 重试一次。

    返回 (payload, dsl_error)；dsl_error 非空表示重试后仍未过白名单，
    payload 里是**最后一次** LLM 产出（expression 原文用于记失败轨迹）。
    LLM 级失败（契约重试耗尽/网络）抛 LLMError，由调用方记事件跳过。
    parse 的规模上限（``MAX_EXPR_LEN``/``MAX_EXPR_NODES``）应已挡住超长表达式，
    但防御到底：万一递归仍被打爆（RecursionError），同样按无效候选处理，
    绝不让一条胡言表达式炸掉整个 run_loop。
    """
    hint: str | None = None
    dsl_err: ExprError | None = None
    payload: dict[str, str] = {}
    for attempt in range(2):
        payload = _call_operator(cand, parents, ctx, hint)
        try:
            parse(payload["expression"])
            return payload, None
        except ExprError as exc:
            dsl_err = exc
        except RecursionError as exc:
            dsl_err = ExprError(f"表达式递归超限（按白名单违规处理）: {exc}")
        if attempt == 0:
            hint = f"上次表达式未通过 DSL 白名单：{dsl_err}；请修正后重新产出。"
    return payload, dsl_err


def _new_trajectory(
    cfg: LoopConfig, cand: _CandCtx, payload: dict[str, str], outcome: _Judged
) -> Trajectory:
    created_at = _now_iso()
    return Trajectory(
        id=make_id(
            direction=cand.direction,
            hypothesis=payload["hypothesis"],
            expression=payload["expression"],
            created_at=created_at,
        ),
        direction=cand.direction,
        phase=cand.phase,
        hypothesis=payload["hypothesis"],
        expression=payload["expression"],
        complexity=outcome.comp_dict,
        mining_metrics=outcome.metrics,
        decision=outcome.decision,
        feedback=outcome.feedback,
        parent_ids=cand.parent_ids,
        created_at=created_at,
        run_tag=cfg.run_tag,
    )


def _emit(on_event: Callable[[dict], None] | None, **kw: Any) -> None:
    if on_event is not None:
        on_event(kw)


def _event(cand: _CandCtx, decision: str, rank_ic_mean: Any, expression: str) -> dict:
    """on_event 事件 dict 的唯一构造点（键集合钉在这里，三处上报共用）。"""
    return {
        "round_i": cand.round_i,
        "cand_i": cand.cand_i,
        "direction": cand.direction,
        "phase": cand.phase,
        "expression": expression,
        "decision": decision,
        "rank_ic_mean": rank_ic_mean,
    }


def _emit_llm_error(ctx: _RunCtx, cand: _CandCtx, exc: LLMError) -> None:
    """LLM 级失败（网络/契约重试耗尽）：没有可记的轨迹，事件上报后跳过本候选。"""
    ev = _event(cand, "llm_error", None, "")
    ev["error"] = str(exc)
    _emit(ctx.on_event, **ev)


def _fail_step(
    ctx: _RunCtx,
    cand: _CandCtx,
    payload: dict[str, str],
    comp_dict: dict[str, Any],
    feedback: str,
) -> None:
    """失败轨迹落池（教训数据）+ 事件上报：DSL 违规与复杂度违规共用。"""
    outcome = _Judged(comp_dict, {}, "fail", feedback)
    t = _new_trajectory(ctx.cfg, cand, payload, outcome)
    ctx.pool.add(t)
    _emit(ctx.on_event, **_event(cand, "fail", None, t.expression))


def _judge_and_record(ctx: _RunCtx, cand: _CandCtx, payload: dict[str, str]) -> None:
    """复杂度门 → mining 评估 → 确定性判定 → interpret 解读 → 落池 → 事件。"""
    comp = complexity(payload["expression"])
    comp_viol = violations(comp)
    if comp_viol:
        # 复杂度门 fail：mining_metrics 留空，不为必然淘汰的候选浪费回测。
        _fail_step(ctx, cand, payload, asdict(comp), "；".join(comp_viol))
        return
    # 通过复杂度门才回测。⚠️ ctx.mining_bars 已物理截尾到 mining_end（run_loop
    # 入口），判定窗数据不在内存对象里 —— 双窗制度核心，绝不能用未截尾数据调本行。
    # ic_series（逐日 RankIC）随 stats 一并算出：judge_mining 的 R3 半窗同正门要吃它。
    stats, ic_series = evaluate_expression_with_series(
        payload["expression"],
        ctx.mining_bars,
        start=ctx.cfg.mining_start,
        end=ctx.cfg.mining_end,
        horizon=ctx.cfg.horizon,
    )
    decision, reasons = operators.judge_mining(
        stats,
        comp,
        rank_ic_series=ic_series,
        min_days=ctx.cfg.min_days,
        min_rank_ic=ctx.cfg.min_rank_ic,
        min_rank_icir=ctx.cfg.min_rank_icir,
    )
    t0 = _new_trajectory(
        ctx.cfg, cand, payload, _Judged(asdict(comp), asdict(stats), decision, "")
    )
    best = ctx.pool.best(1, direction=cand.direction)
    note = operators.interpret(t0, best[0] if best else None, ctx.llm)
    feedback = ("；".join(reasons) + "\n" if reasons else "") + note
    t = replace(t0, feedback=feedback)  # feedback 不参与 make_id，id 不变
    ctx.pool.add(t)
    _emit(ctx.on_event, **_event(cand, decision, stats.rank_ic_mean, t.expression))


def _step(ctx: _RunCtx, direction: str, phase: str, round_i: int, cand_i: int) -> None:
    """单候选：选父代 → LLM 产出 → DSL 门 → 判定落池（细节全在 helper）。"""
    actual_phase, parents = _resolve_phase(phase, direction, ctx.pool, ctx.rng)
    cand = _CandCtx(
        direction, actual_phase, tuple(p.id for p in parents), round_i, cand_i
    )
    try:
        payload, dsl_err = _produce_candidate(cand, parents, ctx)
    except LLMError as exc:
        _emit_llm_error(ctx, cand, exc)
        return
    if dsl_err is not None:
        # 失败也要进池（教训数据）：expression 原文 + decision=fail + 指标留空。
        _fail_step(ctx, cand, payload, {}, f"表达式未通过 DSL 白名单： {dsl_err}")
        return
    _judge_and_record(ctx, cand, payload)


# ── 编排入口 ─────────────────────────────────────────────────────────────────


def _validate_cfg(cfg: LoopConfig) -> None:
    """fail-closed 配置校验：方向非空 / 轮数下限 / phase 合法 / 窗口不倒挂。"""
    if not cfg.directions:
        raise ValueError("LoopConfig.directions 不能为空")
    if cfg.rounds < 1 or cfg.candidates_per_round < 1:
        raise ValueError("rounds / candidates_per_round 必须 >= 1")
    if not cfg.phase_schedule:
        raise ValueError("phase_schedule 不能为空")
    bad = set(cfg.phase_schedule) - set(EVOLUTION_PHASES)
    if bad:
        raise ValueError(f"phase_schedule 含非法 phase: {sorted(bad)}")
    if cfg.mining_start > cfg.mining_end:  # ISO 日期字符串序即时间序
        raise ValueError(f"挖掘窗倒挂: {cfg.mining_start} > {cfg.mining_end}")


def run_loop(
    cfg: LoopConfig,
    bars_by_code: dict[str, pd.DataFrame],
    llm: ChatLLM,
    pool: TrajectoryPool,
    *,
    on_event: Callable[[dict], None] | None = None,
) -> TrajectoryPool:
    """跑完整进化循环，返回 pool（就地累积，返回值仅为方便链式）。

    ⚠️ 入口第一件事是把 bars **物理截尾**到 ``cfg.mining_end``：循环全程（含
    所有候选的 mining 评估）只用这份副本，mining_end 之后的数据绝不在场。
    调用方（CLI）通常已先截尾过一次 —— 这里再截一次是库层的自保，幂等无害。
    """
    _validate_cfg(cfg)
    ctx = _RunCtx(
        cfg=cfg,
        mining_bars=clip_tail(bars_by_code, cfg.mining_end),
        llm=llm,
        pool=pool,
        rng=random.Random(cfg.seed),
        on_event=on_event,
    )
    for direction in cfg.directions:
        for round_i in range(cfg.rounds):
            phase = cfg.phase_schedule[round_i % len(cfg.phase_schedule)]
            for cand_i in range(cfg.candidates_per_round):
                _step(ctx, direction, phase, round_i, cand_i)
    return pool


def final_judgment(
    pool: TrajectoryPool,
    bars_by_code: dict[str, pd.DataFrame],
    *,
    mining: Window,
    judgment: Window,
    top_n: int = 5,
    horizon: int = 5,
) -> list[DualWindowResult]:
    """对池内 best(top_n) 逐个跑双窗判定，返回 DualWindowResult 列表。

    ⚠️ 这是全包**唯一允许读判定窗数据**的函数：仅在进化闭环结束后调用
    （run_loop 全程不接触 mining.end 之后的数据）。窗口重叠/倒挂 →
    ``validate_windows`` 先 raise ValueError（fail-closed）。
    """
    validate_windows(mining, judgment)
    out = []
    for t in pool.best(top_n):
        out.append(
            run_dual_window(
                t.expression, bars_by_code, mining, judgment, horizon=horizon
            )
        )
    return out
