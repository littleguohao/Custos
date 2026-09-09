# -*- coding: utf-8 -*-
"""双窗（挖掘/判定）硬隔离驱动测试：窗口校验 / 物理隔离 / passed-reasons 逻辑。

合成宇宙：每股日漂移率 r_i 按代码序递增，叠加确定性噪声（模运算，无 RNG）让逐日
截面秩相关在 1.0 附近抖动（std>0，ICIR 有定义）。``flip=True`` 时判定窗内漂移
按 5 日块交替反转 → judgment 期 rank_ic_mean 转负（反转型失败用例）。
"""

import math

import pandas as pd
import pytest

from custos.research.evolution import dual_window as dw
from custos.research.evolution.dual_window import Window

N_DAYS = 140
DATES = pd.date_range("2024-01-01", periods=N_DAYS, freq="B")
SPLIT = 70  # mining / judgment 之间的分界（判定窗起点）

MINING = Window(str(DATES[0].date()), str(DATES[59].date()))
JUDGMENT = Window(str(DATES[70].date()), str(DATES[129].date()))
EXPR = "ROC(CLOSE,5)"


def regime_universe(flip=False, n_stocks=10, amp=0.005, block=5):
    out = {}
    for i in range(n_stocks):
        r = 0.002 + 0.001 * i
        price, closes = 100.0, []
        for t in range(N_DAYS):
            drift = r
            if flip and t >= SPLIT:
                drift = r if (t // block) % 2 == 0 else -r
            eps = amp * (((i * 3 + t * 7) % 4) - 1.5)
            price *= (1 + drift) * (1 + eps)
            closes.append(price)
        out[f"S{i:03d}"] = pd.DataFrame(
            {
                "date": DATES,
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [1000.0] * N_DAYS,
                "amount": [0.0] * N_DAYS,
            }
        )
    return out


def assert_stats_equal(a, b):
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


# ---------- 窗口校验（fail-closed） ----------


def test_overlapping_windows_rejected():
    # 端点相接也算重叠：同一天不能既挖又判
    with pytest.raises(ValueError, match="不重叠"):
        dw.validate_windows(
            Window("2024-01-01", "2024-03-01"), Window("2024-03-01", "2024-05-01")
        )


def test_reversed_windows_rejected():
    with pytest.raises(ValueError, match="不重叠"):
        dw.validate_windows(
            Window("2024-05-01", "2024-06-01"), Window("2024-01-01", "2024-03-01")
        )


def test_inverted_window_rejected():
    with pytest.raises(ValueError, match="倒挂"):
        dw.validate_windows(
            Window("2024-03-01", "2024-01-01"), Window("2024-04-01", "2024-05-01")
        )


def test_malformed_date_rejected():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        dw.validate_windows(
            Window("2024/01/01", "2024-03-01"), Window("2024-04-01", "2024-05-01")
        )


def test_valid_windows_pass():
    dw.validate_windows(MINING, JUDGMENT)  # 不抛即通过


# ---------- passed / reasons 逻辑 ----------


def test_pass_case():
    res = dw.run_dual_window(EXPR, regime_universe(flip=False), MINING, JUDGMENT)
    assert res.passed and res.reasons == []
    assert res.expression == EXPR and res.horizon == 5
    # 60 日窗 - 5 日 ROC warmup - 5 日前向尾部
    assert res.mining.n_days == res.judgment.n_days == 50
    assert res.mining.rank_ic_mean > 0.3  # 合成趋势因子的量级钉测
    assert res.judgment.rank_ic_mean >= 0.02 and res.judgment.rank_icir >= 0.1


def test_fail_case_judgment_reversal():
    res = dw.run_dual_window(EXPR, regime_universe(flip=True), MINING, JUDGMENT)
    assert not res.passed
    assert any("rank_ic_mean" in r for r in res.reasons)
    assert any("异号" in r for r in res.reasons)
    assert res.judgment.rank_ic_mean < 0


def test_min_days_gate():
    # 12 日窗：n_days = 12 - 5 - 5 = 2 < min_days
    mining = Window(str(DATES[0].date()), str(DATES[11].date()))
    judgment = Window(str(DATES[20].date()), str(DATES[31].date()))
    res = dw.run_dual_window(EXPR, regime_universe(), mining, judgment)
    assert not res.passed
    assert any("mining" in r and "日数" in r for r in res.reasons)
    assert any("judgment" in r and "日数" in r for r in res.reasons)


# ---------- 物理隔离：挖掘过程读不到判定窗数据 ----------


def test_mining_cannot_see_judgment_data():
    uni = regime_universe(flip=False)
    before = dw.run_dual_window(EXPR, uni, MINING, JUDGMENT)
    tampered = {}
    for code, df in uni.items():
        d = df.copy()
        seg = d.loc[d.index[SPLIT:], "close"]
        d.loc[d.index[SPLIT:], "close"] = seg.to_numpy()[::-1]  # 判定窗整段反转
        tampered[code] = d
    after = dw.run_dual_window(EXPR, tampered, MINING, JUDGMENT)
    # mining 结果逐位不变（判定窗数据物理上不在场）
    assert_stats_equal(before.mining, after.mining)
    # sanity：篡改确实生效（judgment 变了）
    assert before.judgment.rank_ic_mean != after.judgment.rank_ic_mean


def test_judgment_warmup_independent_of_mining():
    # judgment 独立切片：warmup 从 judgment.start 重新开始，
    # 若共享 mining 数据，judgment 头部 5 天会多出 score（n_days 变 55）
    res = dw.run_dual_window(EXPR, regime_universe(), MINING, JUDGMENT)
    assert res.judgment.n_days == 50
