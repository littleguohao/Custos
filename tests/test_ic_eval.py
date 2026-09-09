# -*- coding: utf-8 -*-
"""截面 RankIC/ICIR 评估器测试（合成 universe，无网络/无通达信依赖）。

合成宇宙口径：每股 close = 几何趋势（每股一个日漂移率），则 ``ROC(CLOSE,5)``
的截面排序与 5 日前向收益的排序严格一致 → 逐日 Spearman 恒为 ±1.0。
"""

import math

import pandas as pd
import pytest

from custos.research.evolution import ic_eval

N_DAYS = 40
DATES = pd.date_range("2024-01-01", periods=N_DAYS, freq="B")
WARMUP = 5  # ROC(CLOSE,5) 的前 5 日无 score


def make_df(closes):
    n = len(closes)
    return pd.DataFrame(
        {
            "date": DATES[:n],
            "open": closes,
            "high": closes,
            "low": closes,
            "close": [float(c) for c in closes],
            "volume": [1000.0] * n,
            "amount": [0.0] * n,
        }
    )


def trend_universe(n_stocks=6, n_days=N_DAYS):
    """每股一个几何漂移率：截面排序在整个时间轴上严格稳定。"""
    out = {}
    for i in range(n_stocks):
        r = 0.002 + 0.0015 * i
        closes = [100.0 * (1 + r) ** t for t in range(n_days)]
        out[f"S{i:03d}"] = make_df(closes)
    return out


def assert_stats_equal(a, b):
    """ICStats 逐字段相等（nan 与 nan 视为相等；确定性计算应逐位一致）。"""
    assert (a.n_days, a.horizon, a.start, a.end) == (
        b.n_days,
        b.horizon,
        b.start,
        b.end,
    )
    for f in ("ic_mean", "icir", "rank_ic_mean", "rank_icir"):
        va, vb = getattr(a, f), getattr(b, f)
        if math.isnan(va) and math.isnan(vb):
            continue
        assert va == vb, f"字段 {f}: {va} != {vb}"


# ---------- 方向性：完全正/负相关 ----------


def test_rank_ic_perfect_positive():
    stats = ic_eval.evaluate_expression("ROC(CLOSE,5)", trend_universe())
    assert stats.n_days == N_DAYS - WARMUP - 5  # 头部 warmup + 尾部 horizon 剔除
    assert stats.rank_ic_mean == pytest.approx(1.0, abs=1e-6)
    assert stats.ic_mean == pytest.approx(1.0, abs=1e-6)  # score 与 fwd 线性相等
    assert math.isnan(stats.rank_icir)  # 每日 IC 恒为 1 → std=0 → icir 为 nan
    assert stats.horizon == 5 and stats.start is None and stats.end is None


def test_rank_ic_perfect_negative():
    stats = ic_eval.evaluate_expression("-ROC(CLOSE,5)", trend_universe())
    assert stats.rank_ic_mean == pytest.approx(-1.0, abs=1e-6)
    assert stats.ic_mean == pytest.approx(-1.0, abs=1e-6)


# ---------- 跳过日语义 ----------


def test_constant_score_skips_every_day():
    stats = ic_eval.evaluate_expression("5", trend_universe())
    assert stats.n_days == 0
    assert math.isnan(stats.rank_ic_mean)


def test_too_few_stocks_skips_every_day():
    stats = ic_eval.evaluate_expression("ROC(CLOSE,5)", trend_universe(n_stocks=4))
    assert stats.n_days == 0  # < MIN_STOCKS(5) → 全部跳过


# ---------- 前向收益口径：尾部 horizon 日剔除 + 逐日有效区间 ----------


def test_tail_trim_and_valid_range():
    uni = trend_universe()
    scores = ic_eval.score_frame("ROC(CLOSE,5)", uni)
    ic = ic_eval.rank_ic_by_day(scores, uni, horizon=5)
    assert len(ic) == N_DAYS - WARMUP - 5
    assert ic.index[0] == DATES[WARMUP]
    # 最后一个有效截面日：t+horizon 不得超过数据末尾
    assert ic.index[-1] == DATES[N_DAYS - 1 - 5]
    assert (ic == 1.0).all()


def test_horizon_changes_tail_trim():
    uni = trend_universe()
    scores = ic_eval.score_frame("ROC(CLOSE,5)", uni)
    ic = ic_eval.rank_ic_by_day(scores, uni, horizon=10)
    assert ic.index[-1] == DATES[N_DAYS - 1 - 10]
    assert len(ic) == N_DAYS - WARMUP - 10


# ---------- score_frame 对齐 ----------


def test_score_frame_aligns_union_of_dates():
    uni = trend_universe()
    # S000 晚 10 天才上市：并集索引不变，缺失列为 NaN
    uni["S000"] = uni["S000"].iloc[10:].reset_index(drop=True)
    frame = ic_eval.score_frame("CLOSE", uni)
    assert len(frame) == N_DAYS
    assert list(frame.index[:2]) == list(DATES[:2])
    assert frame["S000"].iloc[:10].isna().all()
    assert frame["S000"].iloc[10] == pytest.approx(100.0 * 1.002**10)


# ---------- start/end 切片：发生在任何计算之前 ----------


def test_window_slicing_restarts_warmup():
    uni = trend_universe()
    start, end = str(DATES[10].date()), str(DATES[29].date())
    stats = ic_eval.evaluate_expression("ROC(CLOSE,5)", uni, start=start, end=end)
    # 切片后 ROC 从 start 重新 warmup：有效日 = [start+5, end-5] 共 10 天
    assert stats.n_days == 10
    assert stats.start == start and stats.end == end
    assert stats.rank_ic_mean == pytest.approx(1.0, abs=1e-6)


def test_slice_before_compute_means_no_lead_in_warmup():
    # 若切片发生在计算之后，start 起头 5 天会有 score（读到 start 之前的数据），
    # n_days 会变成 15；钉死 10 即证明「先切片后计算」。
    uni = trend_universe()
    stats = ic_eval.evaluate_expression(
        "ROC(CLOSE,5)", uni, start=str(DATES[10].date())
    )
    assert stats.n_days == N_DAYS - 10 - WARMUP - 5


# ---------- 无未来函数钉测 ----------


def test_tampering_beyond_end_changes_nothing():
    uni = trend_universe()
    kw = {"start": str(DATES[0].date()), "end": str(DATES[29].date())}
    before = ic_eval.evaluate_expression("MA(CLOSE,5)*ROC(CLOSE,3)", uni, **kw)
    tampered = {}
    for code, df in uni.items():
        d = df.copy()
        d.loc[d.index[30:], "close"] = d.loc[d.index[30:], "close"] * 100.0
        tampered[code] = d
    after = ic_eval.evaluate_expression("MA(CLOSE,5)*ROC(CLOSE,3)", tampered, **kw)
    assert_stats_equal(before, after)


def test_score_frame_no_forward_peek():
    # 截尾后重算，≤ 截尾日的 score 必须逐位不变（每个算子的 rolling/shift 只看历史）
    uni = trend_universe()
    expr = "MA(CLOSE,5)+TS_RANK(VOLUME,4)-REF(DELTA(CLOSE,2),1)"
    full = ic_eval.score_frame(expr, uni)
    cut = 25
    truncated = {c: df.iloc[:cut].copy() for c, df in uni.items()}
    part = ic_eval.score_frame(expr, truncated)
    pd.testing.assert_frame_equal(full.loc[: DATES[cut - 1]], part, check_exact=True)


# ---------- Spearman 复用 score_return_study（不重写算法） ----------


def test_spearman_reuses_score_return_study(monkeypatch):
    calls = []
    orig = ic_eval.correlations

    def spy(trades):
        calls.append(len(trades))
        return orig(trades)

    monkeypatch.setattr(ic_eval, "correlations", spy)
    ic_eval.evaluate_expression("ROC(CLOSE,5)", trend_universe())
    assert calls  # 每个有效截面日一次调用
    assert all(n >= ic_eval.MIN_STOCKS for n in calls)


# ---------- ic_stats_from_series 聚合语义 ----------


def test_ic_stats_from_series_plain():
    s = pd.Series([0.1, 0.2, 0.3, 0.4])
    stats = ic_eval.ic_stats_from_series(s, horizon=5, start="2024-01-01")
    assert stats.n_days == 4
    assert stats.rank_ic_mean == pytest.approx(0.25)
    assert stats.rank_icir == pytest.approx(0.25 / s.std(ddof=1))
    # 单序列调用拿不到 Pearson 序列 → 留白为 nan（不静默复制主指标）
    assert math.isnan(stats.ic_mean) and math.isnan(stats.icir)


def test_ic_stats_from_series_degenerate():
    assert ic_eval.ic_stats_from_series(pd.Series(dtype=float), 5).n_days == 0
    one = ic_eval.ic_stats_from_series(pd.Series([0.3]), 5)
    assert one.n_days == 1 and math.isnan(one.rank_icir)  # ddof=1 样本不足
    flat = ic_eval.ic_stats_from_series(pd.Series([0.2, 0.2, 0.2]), 5)
    assert flat.rank_ic_mean == pytest.approx(0.2) and math.isnan(flat.rank_icir)
