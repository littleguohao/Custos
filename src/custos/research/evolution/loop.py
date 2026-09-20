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
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from custos.research.evolution import genome, operators
from custos.research.evolution.dual_window import (
    DualWindowResult,
    Window,
    run_dual_window,
    validate_windows,
)
from custos.research.evolution.expr_dsl import ExprError, complexity, parse, violations
from custos.research.evolution.ic_eval import evaluate_expression_rich
from custos.research.evolution.llm_client import ChatLLM, LLMError
from custos.research.evolution.trajectory import (
    EVOLUTION_PHASES,
    IC_SKIPPED_MARK,
    Trajectory,
    TrajectoryPool,
    make_id,
)

# 三轴单元格执行器协议（joint 模式的适应度函数；CLI 层实现注入，测试注入假实现）：
#   cell_runner(expr, gate, exit_params, *, start, end) -> dict | None
# 返回 {"objective": float|None, "margin": ..., "expectancy_R": ...,
#       "cell_signature": str}；None = 单元格失败/无交易。
CellRunner = Callable[..., dict | None]


@dataclass(frozen=True)
class LoopConfig:
    """进化循环配置（不可变；judge_mining 的阈值也挂这里）。

    字段各有正交语义（方向/轮次/窗口/horizon/phase 轮换/种子/判定阈值/joint 开关
    与适应度阈值），刻意不拆子配置 —— 摊开让 CLI 参数面与配置一一对齐
    （R0902 有意保留）。
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
    # 联合演化第一档（TODO #68）：基因组 = (表达式 × gate × 出场参数)；
    # IC 门（廉价初筛）过门后才跑三轴单元格适应度（控成本）。
    joint: bool = False
    min_objective: float = 0.0  # 三轴适应度阈值：objective 低于此值 → fail
    # marks 监督模式（R36 Phase 2）：IC 门后再过正例分离门（自指口径，
    # 见 evolution/marks_fitness.py）；空 = 关闭（关闭时行为逐位不变）。
    marks_path: str = ""  # B1_DATA 目录（CSV 片段）或点对清单 .json（权威清单
    # R36_perfect_b1_marks.json；bars 全靠 provider，无 provider 时 fail-closed）
    min_marks_rank: float = 0.7  # 买点 mean_rank 下限（(0,1] 分位口径）
    min_marks_contrast: float = 0.0  # 买点均值 − 案例内全日均值 的下限
    marks_rank_window: int = 20  # TS_RANK 窗口（必须远小于案例窗长 61~78 根；
    # K=250 在全案例上恒 NaN——warmup 覆盖全窗，反例钉见 marks_fitness docstring）
    # IC 门指标口径（R36 Phase 2 三轮，owner 拍板 2026-09-18；默认 rank 逐位不变）：
    # rank=全谱 RankIC；top_tail=头部价差（对齐 top20 消费口径——二轮实证全谱
    # RankIC 两头错配：误杀 marks 0.83 尖峰候选 / 独苗 IC 正但头部撑不住）；
    # off=IC 面纯观测（须配 marks 门，见 _validate_cfg）。
    ic_gate: str = "rank"
    min_top_tail: float = 0.002  # 头部价差均值下限（5 日前向；量级锚见 R36 预注册）
    min_top_tail_ir: float = 0.1  # 头部价差 IR 下限（mean/std，同 rank_icir 哲学）
    top_tail_frac: float = 0.2  # 头部组分位（对齐 top20 消费口径）
    # 门次序：ic_first（默认，逐位不变）；marks_first=marks 门先判、fail 即跳过
    # 全宇宙 IC 评估——纯成本控制（两门合取，晋级集合与次序无关；代价是
    # marks-fail 候选没有 IC/头部价差观测读数，轨迹留痕「未跑」而非留空）。
    gate_order: str = "ic_first"


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
    cell_runner: CellRunner | None  # joint 模式的三轴适应度执行器（非 joint 为 None）
    marks_cases: list | None = None  # marks 监督模式的正例案例（None=关闭）
    marks_bars_provider: Any = None  # 全历史 loader（Callable[[str], df|None]；
    # None → excerpt 回退。marks 用 bars 与挖掘窗解耦：截至 buy_date 的尽量长
    # 历史，不受 mining_start/end 截断——正例点是发现材料不是挖掘数据）


@dataclass(frozen=True)
class _CandCtx:
    """单候选上下文：方向 / 实际 phase / 父代血统 / 轮次序号 / 基因组参数分量。"""

    direction: str
    phase: str  # 实际 phase（父代不足降级后可能与计划 phase 不同）
    parent_ids: tuple[str, ...]
    round_i: int
    cand_i: int
    gate: str = ""  # joint 的 gate 分量（非 joint 空串）
    exit_params: dict[str, Any] = field(default_factory=dict)  # joint 出场参数分量


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


def _parent_params(t: Trajectory) -> tuple[str, dict]:
    """父代的基因组参数分量；缺 gate/exit_params 的老轨迹 → 先落 default。"""
    if t.gate and t.exit_params:
        return t.gate, dict(t.exit_params)
    return genome.default_params()


def _assemble_params(
    phase: str, parents: list[Trajectory], ctx: _RunCtx
) -> tuple[str, dict]:
    """joint 参数装配（确定性格点，LLM 不碰数值调参；seeded rng 可复现）。

    origin → ``default_params()``；mutation → 父代参数格点变异；
    crossover → 两父代分量交换（缺参数字段的父代先落 default 再变异/交换）。
    """
    if not ctx.cfg.joint:
        return "", {}
    if phase == "origin":
        return genome.default_params()
    if phase == "mutation":
        return genome.mutate_params(*_parent_params(parents[0]), ctx.rng)
    return genome.crossover_params(
        _parent_params(parents[0]), _parent_params(parents[1]), ctx.rng
    )


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
        gate=cand.gate,
        exit_params=dict(cand.exit_params),
    )


def _emit(on_event: Callable[[dict], None] | None, **kw: Any) -> None:
    if on_event is not None:
        on_event(kw)


def _event(
    cand: _CandCtx,
    decision: str,
    rank_ic_mean: Any,
    expression: str,
    objective: Any = None,
) -> dict:
    """on_event 事件 dict 的唯一构造点（键集合钉在这里，三处上报共用）。

    joint 增补键「有则给」：gate 非空 / objective 非 None 才进事件。
    """
    ev = {
        "round_i": cand.round_i,
        "cand_i": cand.cand_i,
        "direction": cand.direction,
        "phase": cand.phase,
        "expression": expression,
        "decision": decision,
        "rank_ic_mean": rank_ic_mean,
    }
    if cand.gate:
        ev["gate"] = cand.gate
    if objective is not None:
        ev["objective"] = objective
    return ev


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
    if ctx.pool.path is not None:  # 增量落盘（v0.258）：进程被杀不丢已产出
        ctx.pool.save()
    _emit(ctx.on_event, **_event(cand, "fail", None, t.expression))


def _judge_cell(
    cell: dict | None, metrics: dict[str, Any], min_objective: float
) -> list[str]:
    """三轴适应度层判定（joint 第二层，decision 仍纯确定性）：返回追加的 fail 原因。

    cell None（格子失败/无交易）或 objective 缺失/低于阈值 → fail（NaN 比较
    为 False，fail-closed）；过则把 objective/margin/expectancy_R/
    cell_signature 写进 mining_metrics（rank_ic_mean 仍在，轨迹校验不破）。
    """
    if cell is None:
        return ["三轴单元格失败或无交易（cell_runner 返回 None）"]
    metrics.update(
        {
            "objective": cell.get("objective"),
            "margin": cell.get("margin"),
            "expectancy_R": cell.get("expectancy_R"),
            "cell_signature": cell.get("cell_signature"),
        }
    )
    obj = cell.get("objective")
    if obj is None or not obj >= min_objective:
        return [f"objective={obj} 未达阈值 {min_objective}"]
    return []


def _cell_layer(
    ctx: _RunCtx, cand: _CandCtx, payload: dict[str, str], metrics: dict[str, Any]
) -> list[str]:
    """joint 第二层：IC 过门后跑三轴单元格（控成本），返回追加的 fail 原因。"""
    assert ctx.cell_runner is not None  # run_loop 入口已校验 joint 必有执行器
    cell = ctx.cell_runner(
        payload["expression"],
        cand.gate,
        cand.exit_params,
        start=ctx.cfg.mining_start,
        end=ctx.cfg.mining_end,
    )
    return _judge_cell(cell, metrics, ctx.cfg.min_objective)


def _marks_layer(
    ctx: _RunCtx, payload: dict[str, str], metrics: dict[str, Any]
) -> list[str]:
    """marks 监督门（R36 Phase 2 第二层，IC 门后）：自指分离读数 + 阈值判定。

    读数写进 mining_metrics["marks"]（mean_rank/min_rank/contrast/n_hit/per_case
    全量留痕）；n_hit=0（全部无值）/ mean_rank < min_marks_rank /
    contrast < min_marks_contrast → fail（None 比较为 False，fail-closed）。
    """
    assert ctx.marks_cases is not None  # 只在 marks_path 非空时被调
    from custos.research.evolution.marks_fitness import marks_score  # noqa: PLC0415

    m = marks_score(
        payload["expression"],
        ctx.marks_cases,
        rank_window=ctx.cfg.marks_rank_window,
        bars_provider=ctx.marks_bars_provider,
    )
    metrics["marks"] = m
    reasons: list[str] = []
    if m["n_hit"] == 0:
        reasons.append("marks 全部买点无值（warmup/数据缺，fail-closed 不当高分）")
        return reasons
    if m["mean_rank"] is None or m["mean_rank"] < ctx.cfg.min_marks_rank:
        reasons.append(
            f"marks mean_rank={m['mean_rank']} 未达阈值 {ctx.cfg.min_marks_rank}"
        )
    if m["contrast"] is None or m["contrast"] < ctx.cfg.min_marks_contrast:
        reasons.append(
            f"marks contrast={m['contrast']} 未达阈值 {ctx.cfg.min_marks_contrast}"
        )
    return reasons


def _judge_and_record(ctx: _RunCtx, cand: _CandCtx, payload: dict[str, str]) -> None:
    """复杂度门 → mining 评估 → 确定性判定 → interpret 解读 → 落池 → 事件。

    门链（合取，全部过才 pass）：复杂度门 → 指标门（``cfg.ic_gate`` 三口径，
    默认 rank 全谱 RankIC；top_tail 头部价差；off 纯观测）→ marks 门（开启时）
    → joint cell 层（开启时）。``cfg.gate_order="marks_first"`` 时 marks 门
    先判：fail 即跳过全宇宙 IC 评估（纯成本控制——合取门的晋级集合与判定
    次序无关；代价是该候选没有 IC/头部价差观测读数，fail 原因里写明「未跑」）。
    """
    comp = complexity(payload["expression"])
    comp_viol = violations(comp)
    if comp_viol:
        # 复杂度门 fail：mining_metrics 留空，不为必然淘汰的候选浪费回测。
        _fail_step(ctx, cand, payload, asdict(comp), "；".join(comp_viol))
        return
    metrics: dict[str, Any] = {}
    reasons: list[str] = []
    decision = ""
    rank_ic_mean: Any = None
    if ctx.marks_cases is not None and ctx.cfg.gate_order == "marks_first":
        if marks_reasons := _marks_layer(ctx, payload, metrics):
            decision = "fail"
            metrics[IC_SKIPPED_MARK] = "marks_first"  # 缺席标记见 trajectory 模块
            reasons = marks_reasons + [
                "marks_first：marks 门 fail，IC/头部价差评估未跑"
                "（成本控制；读数缺席≠零值，复核请用 ic_first 重跑）"
            ]
    if decision != "fail":
        # ⚠️ ctx.mining_bars 已物理截尾到 mining_end（run_loop 入口），判定窗
        # 数据不在内存对象里 —— 双窗制度核心，绝不能用未截尾数据调本行。
        # 逐日三列帧（rank_ic/ic/top_tail）随 stats 一并算出：judge_mining 按
        # ic_gate 口径吃对应列做 R3 半窗同正门。
        stats, ic_df = evaluate_expression_rich(
            payload["expression"],
            ctx.mining_bars,
            start=ctx.cfg.mining_start,
            end=ctx.cfg.mining_end,
            horizon=ctx.cfg.horizon,
            top_frac=ctx.cfg.top_tail_frac,
        )
        rank_ic_mean = stats.rank_ic_mean
        decision, reasons = operators.judge_mining(
            stats,
            comp,
            rank_ic_series=ic_df["rank_ic"],
            min_days=ctx.cfg.min_days,
            min_rank_ic=ctx.cfg.min_rank_ic,
            min_rank_icir=ctx.cfg.min_rank_icir,
            ic_gate=ctx.cfg.ic_gate,
            top_tail_series=ic_df["top_tail"],
            min_top_tail=ctx.cfg.min_top_tail,
            min_top_tail_ir=ctx.cfg.min_top_tail_ir,
        )
        metrics.update(asdict(stats))
        if ctx.marks_cases is not None and ctx.cfg.gate_order == "ic_first":
            # marks 读数对**所有**候选计算留痕（R36 Phase 2 二轮起）：复核「IC 门
            # 是否误杀买点高分候选」需要 IC-fail 侧的 marks 读数（marks_score 无
            # 宇宙加载，成本远低于 IC 评估，parse 失败逐点 None 不炸）。
            # **门判定次序不变**：阈值 reasons 只在 IC pass 后并入（decision 语义
            # 与此前逐位一致）。
            marks_reasons = _marks_layer(ctx, payload, metrics)
            if decision == "pass":
                reasons = reasons + marks_reasons
                if marks_reasons:
                    decision = "fail"
        if ctx.cfg.joint and decision == "pass":
            # 指标门 fail 时零 cell 调用；第二层结果并进 reasons/decision（纯确定性）。
            reasons = reasons + _cell_layer(ctx, cand, payload, metrics)
            if reasons:
                decision = "fail"
    t0 = _new_trajectory(
        ctx.cfg, cand, payload, _Judged(asdict(comp), metrics, decision, "")
    )
    best = ctx.pool.best(1, direction=cand.direction)
    note = operators.interpret(t0, best[0] if best else None, ctx.llm)
    feedback = ("；".join(reasons) + "\n" if reasons else "") + note
    t = replace(t0, feedback=feedback)  # feedback 不参与 make_id，id 不变
    ctx.pool.add(t)
    if ctx.pool.path is not None:  # 增量落盘（v0.258）：进程被杀不丢已产出
        ctx.pool.save()
    _emit(
        ctx.on_event,
        **_event(cand, decision, rank_ic_mean, t.expression, metrics.get("objective")),
    )


def _step(ctx: _RunCtx, direction: str, phase: str, round_i: int, cand_i: int) -> None:
    """单候选：选父代 → 参数装配（joint）→ LLM 产出 → DSL 门 → 判定落池。"""
    actual_phase, parents = _resolve_phase(phase, direction, ctx.pool, ctx.rng)
    gate, exit_params = _assemble_params(actual_phase, parents, ctx)
    cand = _CandCtx(
        direction,
        actual_phase,
        tuple(p.id for p in parents),
        round_i,
        cand_i,
        gate,
        exit_params,
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
    """fail-closed 配置校验：方向非空 / 轮数下限 / phase 合法 / 窗口不倒挂 /
    门口径合法且组合有意义。"""
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
    if cfg.ic_gate not in operators.IC_GATES:
        raise ValueError(f"ic_gate 非法: {cfg.ic_gate!r}（须为 {operators.IC_GATES}）")
    if cfg.gate_order not in ("ic_first", "marks_first"):
        raise ValueError(
            f"gate_order 非法: {cfg.gate_order!r}（须为 ic_first/marks_first）"
        )
    if not 0 < cfg.top_tail_frac <= 1:
        raise ValueError(f"top_tail_frac 必须 ∈ (0,1]，得到 {cfg.top_tail_frac}")
    if cfg.gate_order == "marks_first" and not cfg.marks_path:
        raise ValueError("gate_order=marks_first 需要 marks_path（无 marks 门可前置）")
    if cfg.ic_gate == "off" and not cfg.marks_path:
        raise ValueError("ic_gate=off 需要 marks 门（否则挖掘侧没有任何统计门）")


def run_loop(
    cfg: LoopConfig,
    bars_by_code: dict[str, pd.DataFrame],
    llm: ChatLLM,
    pool: TrajectoryPool,
    *,
    on_event: Callable[[dict], None] | None = None,
    cell_runner: CellRunner | None = None,
    marks_bars_provider: Any = None,
) -> TrajectoryPool:
    """跑完整进化循环，返回 pool（就地累积，返回值仅为方便链式）。

    ⚠️ 入口第一件事是把 bars **物理截尾**到 ``cfg.mining_end``：循环全程（含
    所有候选的 mining 评估）只用这份副本，mining_end 之后的数据绝不在场。
    调用方（CLI）通常已先截尾过一次 —— 这里再截一次是库层的自保，幂等无害。

    ``cfg.joint=True`` 时必须给 ``cell_runner``（三轴适应度执行器，协议见
    模块顶部 ``CellRunner`` 注释）——它是循环内唯一的三轴单元格入口，
    只在 IC 门过门后被调用（控成本）。
    """
    _validate_cfg(cfg)
    if cfg.joint and cell_runner is None:
        raise ValueError(
            "LoopConfig.joint=True 必须提供 cell_runner（三轴适应度执行器）"
        )
    marks_cases = None
    if cfg.marks_path:
        from custos.research.b1_perfect_dataset import (  # noqa: PLC0415
            load_cases,
            load_marks_json,
        )

        mp = str(cfg.marks_path)
        # 两种形态：目录 = B1_DATA CSV 集（含材料片段）；.json = 点对清单
        # （权威清单 governance/research/R36_perfect_b1_marks.json，生产机 pull 即用，
        # bars 全靠 marks_bars_provider 全历史）
        marks_cases = (
            load_marks_json(mp) if mp.lower().endswith(".json") else load_cases(mp)
        )  # fail-closed（坏路径/空清单/坏 JSON raise）
    ctx = _RunCtx(
        cfg=cfg,
        mining_bars=clip_tail(bars_by_code, cfg.mining_end),
        llm=llm,
        pool=pool,
        rng=random.Random(cfg.seed),
        on_event=on_event,
        cell_runner=cell_runner,
        marks_cases=marks_cases,
        marks_bars_provider=marks_bars_provider,
    )
    for direction in cfg.directions:
        for round_i in range(cfg.rounds):
            phase = cfg.phase_schedule[round_i % len(cfg.phase_schedule)]
            for cand_i in range(cfg.candidates_per_round):
                _step(ctx, direction, phase, round_i, cand_i)
    return pool


@dataclass(frozen=True)
class JointVerdict:
    """joint 模式的终审行：双窗 IC 判定 + 判定窗三轴单元格重跑结果。

    ``judgment_cell`` 只对 gate 非空的 joint 轨迹重跑（非 joint 轨迹 / 格子
    失败时为 None）；判定窗数据同样只在闭环结束后被本函数触及。
    """

    dual: DualWindowResult
    judgment_cell: dict[str, Any] | None


def final_judgment(
    pool: TrajectoryPool,
    bars_by_code: dict[str, pd.DataFrame],
    *,
    mining: Window,
    judgment: Window,
    top_n: int = 5,
    horizon: int = 5,
    cell_runner: CellRunner | None = None,
) -> list[DualWindowResult] | list[JointVerdict]:
    """对池内 best(top_n) 逐个跑双窗判定。

    ⚠️ 这是全包**唯一允许读判定窗数据**的函数：仅在进化闭环结束后调用
    （run_loop 全程不接触 mining.end 之后的数据）。窗口重叠/倒挂 →
    ``validate_windows`` 先 raise ValueError（fail-closed）。

    给了 ``cell_runner``（joint 模式）时返回 ``list[JointVerdict]``：对 gate
    非空的轨迹用判定窗 start/end 重跑三轴单元格；否则返回
    ``list[DualWindowResult]``（既有调用方行为逐位不变）。
    """
    validate_windows(mining, judgment)
    out: list[Any] = []
    for t in pool.best(top_n):
        dual = run_dual_window(
            t.expression, bars_by_code, mining, judgment, horizon=horizon
        )
        if cell_runner is None:
            out.append(dual)
            continue
        cell = None
        if t.gate:  # joint 轨迹才重跑判定窗单元格（老轨迹无参数分量可跑）
            cell = cell_runner(
                t.expression,
                t.gate,
                dict(t.exit_params),
                start=judgment.start,
                end=judgment.end,
            )
        out.append(JointVerdict(dual=dual, judgment_cell=cell))
    return out
