# -*- coding: utf-8 -*-
"""挖掘窗/判定窗硬隔离驱动 —— LLM 因子进化引擎的过拟合防线。

制度核心：**挖掘过程物理上读不到判定窗数据**。mining 评估前先把 bars 截尾到
``mining.end``（同时截头到 ``mining.start``）再交给 ic_eval —— 即使评估层有
bug，判定窗的行也不在被传入的数据里；judgment 用独立切片同理，两窗无共享数据。

``passed`` 判定是纯确定性规则（不依赖任何 LLM 输出）：两窗有效截面日数达标、
judgment 窗 rank_ic_mean / rank_icir 过阈值、（可选）两窗 rank_ic_mean 同号；
``reasons`` 逐条记录不达标项，空列表 = 通过。
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

from custos.research.evolution import ic_eval
from custos.research.evolution.ic_eval import ICStats


@dataclass(frozen=True)
class Window:
    start: str  # YYYY-MM-DD（含端点）
    end: str


@dataclass(frozen=True)
class DualWindowResult:
    expression: str
    horizon: int
    mining: ICStats
    judgment: ICStats
    passed: bool  # 全部确定性门槛通过
    reasons: list[str]  # 未通过的原因（空 = 通过）


def validate_windows(mining: Window, judgment: Window) -> None:
    """fail-closed：窗口格式/自身倒挂/重叠或逆序一律 ValueError。

    窗口必须严格不重叠且 mining 在 judgment 之前：``mining.end >= judgment.start``
    即拒绝（端点相接也算重叠 —— 同一天不能既挖又判）。
    """
    for w in (mining, judgment):
        try:
            s, e = date.fromisoformat(w.start), date.fromisoformat(w.end)
        except ValueError as exc:
            raise ValueError(f"窗口日期须为 YYYY-MM-DD: {w}") from exc
        if s > e:
            raise ValueError(f"窗口起止倒挂: start={w.start} > end={w.end}")
    if mining.end >= judgment.start:
        raise ValueError(
            "窗口必须严格不重叠且 mining 在 judgment 之前: "
            f"mining.end={mining.end} >= judgment.start={judgment.start}"
        )


def _clip(
    bars_by_code: dict[str, pd.DataFrame], start: str, end: str
) -> dict[str, pd.DataFrame]:
    """物理切片：返回只含 [start, end] 行的新 dict（copy），空帧剔除。"""
    out = {}
    for code, df in bars_by_code.items():
        d = ic_eval._slice_bars(df, start, end)
        if len(d):
            out[code] = d
    return out


def _sign(v: float) -> int:
    if math.isnan(v):
        return 0
    return (v > 0) - (v < 0)


def run_dual_window(
    expr: str | ast.AST,
    bars_by_code: dict[str, pd.DataFrame],
    mining: Window,
    judgment: Window,
    *,
    horizon: int = 5,
    min_days: int = 20,
    min_rank_ic: float = 0.02,
    min_rank_icir: float = 0.1,
    require_same_sign: bool = True,
) -> DualWindowResult:
    """双窗评估 + 确定性通过判定（reasons 空 = 通过）。"""
    validate_windows(mining, judgment)
    # 挖掘窗：先把数据截尾到 mining.end 再评估 —— 判定窗数据物理上不在场。
    mining_bars = _clip(bars_by_code, mining.start, mining.end)
    mining_stats = ic_eval.evaluate_expression(
        expr, mining_bars, start=mining.start, end=mining.end, horizon=horizon
    )
    # 判定窗：独立切片（warmup 从 judgment.start 重新开始），与 mining 无共享数据。
    judgment_bars = _clip(bars_by_code, judgment.start, judgment.end)
    judgment_stats = ic_eval.evaluate_expression(
        expr, judgment_bars, start=judgment.start, end=judgment.end, horizon=horizon
    )

    reasons = []
    if mining_stats.n_days < min_days:
        reasons.append(f"mining 有效截面日数 {mining_stats.n_days} < {min_days}")
    if judgment_stats.n_days < min_days:
        reasons.append(f"judgment 有效截面日数 {judgment_stats.n_days} < {min_days}")
    if not judgment_stats.rank_ic_mean >= min_rank_ic:  # nan 比较为 False → 不达标
        reasons.append(
            f"judgment.rank_ic_mean={judgment_stats.rank_ic_mean:.4f} < {min_rank_ic}"
        )
    if not judgment_stats.rank_icir >= min_rank_icir:
        reasons.append(
            f"judgment.rank_icir={judgment_stats.rank_icir:.4f} < {min_rank_icir}"
        )
    if require_same_sign and _sign(mining_stats.rank_ic_mean) != _sign(
        judgment_stats.rank_ic_mean
    ):
        reasons.append(
            "两窗 rank_ic_mean 异号: "
            f"mining={mining_stats.rank_ic_mean:.4f} vs "
            f"judgment={judgment_stats.rank_ic_mean:.4f}"
        )

    return DualWindowResult(
        expression=expr if isinstance(expr, str) else ast.unparse(expr),
        horizon=horizon,
        mining=mining_stats,
        judgment=judgment_stats,
        passed=not reasons,
        reasons=reasons,
    )
