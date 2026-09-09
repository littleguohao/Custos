# -*- coding: utf-8 -*-
"""截面 RankIC/ICIR 评估器 —— LLM 因子进化引擎的统一适应度函数。

- ``score_frame``：表达式在每股 bars 上独立求值（expr_dsl 的 rolling/shift 保证
  只用 ≤t 历史），按日期对齐成截面矩阵（index=各股交易日并集排序，columns=code，
  缺失 = NaN）。
- ``rank_ic_by_day``：逐截面日计算当日 score 与 t→t+horizon 前向收益的 Spearman
  秩相关。前向收益口径唯一：``close[t+horizon]/close[t]-1``；t+horizon 越出该股
  数据末尾 → 该股当日剔除（尾部 horizon 个交易日因此天然不进序列）。当日有效
  股票数 < ``MIN_STOCKS``(5) → 该日跳过；score 零方差（常数）导致相关无定义 →
  该日同样跳过。跳过日**不进入**返回序列。
- Spearman/Pearson **复用** ``score_return_study.correlations`` 的手工实现
  （平均秩上的 Pearson；环境无 scipy），本模块只做签名适配（组装它吃的
  trades 字典列表），不重写算法。⚠️ 该实现把单日相关系数 round 到 4 位小数
  —— 对本模块的阈值判定（0.02/0.1 量级）无影响，但单日 IC 读数不是全精度值。

无未来函数纪律：score 由 DSL 保证只看 ≤t；前向收益只看 t 收盘到 t+horizon 收盘；
``evaluate_expression`` 的 start/end 切片发生在**任何计算之前**（滚动窗口的
warmup 从切片起点重新开始）。
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass

import pandas as pd

from custos.research.evolution import expr_dsl
from custos.research.score_return_study import correlations

# 截面有效股票数下限：低于此数 Spearman 没有统计意义，该日跳过。
MIN_STOCKS = 5


@dataclass(frozen=True)
class ICStats:
    n_days: int  # 有效截面日数（跳过日不计）
    ic_mean: float  # Pearson IC 均值（score vs 前向收益）
    icir: float  # ic_mean / ic_std（std 为 0 或样本不足时 nan）
    rank_ic_mean: float  # Spearman 秩 IC 均值（主指标）
    rank_icir: float  # rank_ic_mean / rank_ic_std（主稳定性指标）
    horizon: int
    start: str | None
    end: str | None


def _slice_bars(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    """按 date 列切片（含端点，YYYY-MM-DD），返回 copy；任何计算之前调用。"""
    ds = pd.to_datetime(df["date"])
    mask = pd.Series(True, index=df.index)
    if start is not None:
        mask &= ds >= pd.Timestamp(start)
    if end is not None:
        mask &= ds <= pd.Timestamp(end)
    return df[mask].copy().reset_index(drop=True)


def _date_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(df["date"]))


def score_frame(
    expr: str | ast.AST, bars_by_code: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    """每股独立求值后按日期对齐：index=交易日并集（升序），columns=code。"""
    cols: dict[str, pd.Series] = {}
    for code, df in bars_by_code.items():
        if not len(df):
            continue
        s = expr_dsl.evaluate(expr, df)
        cols[code] = pd.Series(
            s.to_numpy(dtype=float), index=_date_index(df), name=code
        )
    if not cols:
        return pd.DataFrame()
    frame = pd.DataFrame(cols).sort_index()
    frame.index.name = "date"
    return frame


def _ic_pair(scores: list[float], fwd: list[float]) -> tuple[float, float]:
    """(Spearman, Pearson)；零方差等无定义情形返回 (nan, nan)。

    签名适配：把截面 (score, ret) 对组装成 score_return_study.correlations
    吃的 trades 字典列表，算法（平均秩上的 Pearson）完全复用不重写。
    """
    trades = [{"tech_score": s, "ret": r} for s, r in zip(scores, fwd)]
    res = correlations(trades)
    sp, pe = res["spearman"], res["pearson"]
    return (
        float("nan") if sp is None else float(sp),
        float("nan") if pe is None else float(pe),
    )


def _forward_return_maps(
    bars_by_code: dict[str, pd.DataFrame], horizon: int
) -> dict[str, pd.Series]:
    """每股前向收益序列（close[t+horizon]/close[t]-1），按日期索引。"""
    fwd_maps: dict[str, pd.Series] = {}
    for code, df in bars_by_code.items():
        if not len(df):
            continue
        close = pd.Series(
            pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float),
            index=_date_index(df),
        )
        fwd_maps[code] = close.shift(-horizon) / close - 1.0
    return fwd_maps


def _day_pairs(
    row: pd.Series, fwd_maps: dict[str, pd.Series], d: pd.Timestamp
) -> tuple[list[float], list[float]]:
    """单截面日的 (score, 前向收益) 有效对：score NaN / 前向收益越界 → 剔除。"""
    xs: list[float] = []
    ys: list[float] = []
    for code, v in row.items():
        fwd = fwd_maps.get(str(code))
        if fwd is None or pd.isna(v):
            continue
        fv = fwd.get(d)
        if fv is None or pd.isna(fv):
            continue
        xs.append(float(v))
        ys.append(float(fv))
    return xs, ys


def _ic_by_day(
    scores: pd.DataFrame, bars_by_code: dict[str, pd.DataFrame], horizon: int
) -> pd.DataFrame:
    """逐截面日的 (rank_ic, ic) 两列；跳过日不进结果。"""
    if horizon < 1:
        raise ValueError(f"horizon 必须 >= 1，得到 {horizon}")
    fwd_maps = _forward_return_maps(bars_by_code, horizon)
    days: list = []
    rows: list[tuple[float, float]] = []
    for d in scores.index:
        xs, ys = _day_pairs(scores.loc[d], fwd_maps, d)
        if len(xs) < MIN_STOCKS:
            continue
        sp, pe = _ic_pair(xs, ys)
        if math.isnan(sp):  # 零方差（常数 score 等）→ 该日跳过
            continue
        days.append(d)
        rows.append((sp, pe))
    idx = pd.DatetimeIndex(days)
    idx.name = "date"
    return pd.DataFrame(
        {
            "rank_ic": pd.Series([r[0] for r in rows], index=idx, dtype=float),
            "ic": pd.Series([r[1] for r in rows], index=idx, dtype=float),
        }
    )


def rank_ic_by_day(
    scores: pd.DataFrame, bars_by_code: dict[str, pd.DataFrame], horizon: int = 5
) -> pd.Series:
    """逐截面日 Spearman 秩 IC 序列（只含有效日；主指标的日频形态）。"""
    return _ic_by_day(scores, bars_by_code, horizon)["rank_ic"]


def _series_stats(s: pd.Series) -> tuple[float, float]:
    """(mean, mean/std)；std 为 0 / 样本不足 / 空序列 → icir 为 nan。"""
    s = s.dropna()
    if not len(s):
        return float("nan"), float("nan")
    mean = float(s.mean())
    std = float(s.std(ddof=1))
    # 单日 IC 被 correlations round 到 4 位小数：std < 1e-12 只可能是
    # 零方差序列的浮点残差（真实离散至少 1e-4 量级），按「std 为 0」处理。
    if math.isnan(std) or std < 1e-12:
        return mean, float("nan")
    return mean, mean / std


def ic_stats_from_series(
    ic_series: pd.Series,
    horizon: int,
    start: str | None = None,
    end: str | None = None,
    *,
    pearson_series: pd.Series | None = None,
) -> ICStats:
    """把日频 IC 序列聚合成 ICStats。

    ``ic_series`` 按主指标（Spearman 秩 IC）口径聚合；``pearson_series`` 提供时
    同时填 Pearson 字段，缺省时 ic_mean/icir 为 nan（单序列调用方拿不到第二条序列，
    留白比静默复制主指标更诚实）。
    """
    rank = ic_series.dropna()
    rank_mean, rank_icir = _series_stats(rank)
    if pearson_series is not None:
        ic_mean, icir = _series_stats(pearson_series)
    else:
        ic_mean, icir = float("nan"), float("nan")
    return ICStats(
        n_days=int(len(rank)),
        ic_mean=ic_mean,
        icir=icir,
        rank_ic_mean=rank_mean,
        rank_icir=rank_icir,
        horizon=horizon,
        start=start,
        end=end,
    )


def evaluate_expression(
    expr: str | ast.AST,
    bars_by_code: dict[str, pd.DataFrame],
    *,
    start: str | None = None,
    end: str | None = None,
    horizon: int = 5,
) -> ICStats:
    """表达式 → ICStats 一站式评估；start/end（含端点）切片发生在任何计算之前。"""
    sliced = {}
    for code, df in bars_by_code.items():
        d = _slice_bars(df, start, end)
        if len(d):
            sliced[code] = d
    scores = score_frame(expr, sliced) if sliced else pd.DataFrame()
    ic_df = _ic_by_day(scores, sliced, horizon)
    return ic_stats_from_series(
        ic_df["rank_ic"], horizon, start, end, pearson_series=ic_df["ic"]
    )
