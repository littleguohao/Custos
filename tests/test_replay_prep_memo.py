"""重放路径性能优化逐位等价钉测（v0.401）：prep 备忘 + 惰性切片。"""

import gc

import numpy as np
import pandas as pd

from custos.research import backtest_factors as bt
from custos.research import factor_exit_study as fes


def _world(seed=0, n_codes=6, n_bars=400):
    rng = np.random.RandomState(seed)
    dates = [
        d.strftime("%Y-%m-%d") for d in pd.bdate_range("2021-01-04", periods=n_bars)
    ]
    per_code, subset = {}, []
    for k in range(n_codes):
        c = f"6000{k:02d}"
        close = 10 * np.exp(np.cumsum(rng.normal(0.0005, 0.025, n_bars)))
        op = close * (1 + rng.normal(0, 0.006, n_bars))
        df = pd.DataFrame(
            {
                "date": dates,
                "open": op,
                "high": np.maximum(op, close) * 1.012,
                "low": np.minimum(op, close) * 0.988,
                "close": close,
                "volume": rng.uniform(1e6, 2e6, n_bars),
                "amount": rng.uniform(1e7, 2e7, n_bars),
            }
        )
        per_code[c] = {"df": df}
        for i in rng.choice(np.arange(40, n_bars - 5), 30, replace=False):
            subset.append(
                {
                    "code": c,
                    "date": dates[i],
                    "i": int(i),
                    "score": 1.0,
                    "sig": {"code": c, "i": int(i), "date": dates[i], "score": 1.0},
                }
            )
    return per_code, subset


PARAMS = [
    {"stop_mode": "pct", "stop_pct": 10, "scale_out_frac": 0.5, "bbi_exit_consec": 2},
    {"stop_mode": "low", "trail_pct": 0.08},
    {"stop_mode": "pct", "stop_pct": 5, "cost_zone_bars": 3, "stop_buffer": "atr"},
    {"stop_mode": "pct", "stop_pct": 8, "qsx_exit_consec": 2, "time_stop_bars": 10},
]


def _run_all(per_code, subset):
    return [fes.replay_signals(per_code, subset, p, {}, 25.0) for p in PARAMS]


def test_memo_bit_identical_to_cold(monkeypatch):
    per_code, subset = _world()
    warm = _run_all(per_code, subset)
    warm2 = _run_all(per_code, subset)  # 二次命中备忘
    bt._REPLAY_PREP_MEMO.clear()
    monkeypatch.setattr(bt, "_prepare_stock_memo", lambda raw, key, build: build())
    cold = _run_all(per_code, subset)
    assert warm == cold == warm2


def test_memo_invalidates_on_any_column_mutation_and_gc():
    """T1：内部 high/low/close 原地改也必须失效（原指纹只看首末点）；df 回收即释放。"""
    for col in ("low", "high", "close"):
        per_code, subset = _world(1, n_codes=1)
        df = per_code["600000"]["df"]
        fes.replay_signals(per_code, subset, PARAMS[0], {}, 25.0)
        df.loc[df.index[50:350], col] *= 0.5
        warm = fes.replay_signals(per_code, subset, PARAMS[0], {}, 25.0)
        object.__delattr__(df, "_custos_prep_memo")
        cold = fes.replay_signals(per_code, subset, PARAMS[0], {}, 25.0)
        assert warm == cold, col
    rid = id(df)
    assert rid in bt._REPLAY_PREP_MEMO
    del per_code, df
    gc.collect()
    assert rid not in bt._REPLAY_PREP_MEMO


def test_platform_mode_still_slices():
    per_code, subset = _world(2, n_codes=2)
    tr = fes.replay_signals(per_code, subset, {"stop_mode": "platform"}, {}, 25.0)
    assert len(tr) > 0  # platform 模式仍构造切片走平台检测（不因惰性切片崩）
