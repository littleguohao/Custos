"""v0.461 逐笔重放备忘：fes.replay_signals 开 / 关备忘逐位一致；封顶后照常正确。"""

import random

import numpy as np
import pandas as pd
import pytest

from custos.research import factor_exit_study as fes
from custos.research import plan_rules_replay as prr


def _df(seed, n=900):
    rng = np.random.RandomState(seed)
    d = pd.bdate_range("2010-01-01", periods=n).strftime("%Y-%m-%d")
    c = 10 * np.exp(np.cumsum(rng.normal(0, 0.025, n)))
    return pd.DataFrame(
        {
            "date": d,
            "open": c * (1 + rng.normal(0, 0.004, n)),
            "high": c * 1.025,
            "low": c * 0.975,
            "close": c,
            "volume": 1e6,
            "amount": 1e7,
        }
    )


@pytest.fixture(autouse=True)
def _off():
    fes.enable_replay_memo(0)
    yield
    fes.enable_replay_memo(0)


def _world():
    return {f"{k:06d}": {"df": _df(k)} for k in range(5)}


def _subset(per_code, rng, n=120, stop=False):
    out = []
    for _ in range(n):
        c = rng.choice(sorted(per_code))
        df = per_code[c]["df"]
        i = rng.randrange(250, len(df) + 3)  # 含越界 bar（不出交易）
        i = min(i, len(df) - 1)
        sig = {"i": i, "date": df["date"][i]}
        if stop and rng.random() < 0.5:
            sig["stop_override"] = float(df["low"][i]) * 0.95
        out.append(
            {
                "code": c,
                "date": df["date"][i],
                "i": i,
                "sig": sig,
                "score": rng.random() * 100,
            }
        )
    return out


RG: dict = {}  # regime 按对象身份进键——生产路径整轮同一对象
PARAMS = [prr.LIVE_PARAMS, {**prr.LIVE_PARAMS, "stop_pct": 5.0}]


@pytest.mark.parametrize("stop", [False, True])
def test_memo_bit_identical_across_calls(stop):
    pc = _world()
    rng = random.Random(7)
    calls = [(_subset(pc, rng, stop=stop), p) for _ in range(6) for p in PARAMS]
    ref = [fes.replay_signals(pc, s, p, RG, 25.0) for s, p in calls]
    memo = fes.enable_replay_memo(1_000_000)
    got = [fes.replay_signals(pc, s, p, RG, 25.0) for s, p in calls]
    assert got == ref
    assert memo.hits > 0 and memo.misses > 0
    again = [fes.replay_signals(pc, s, p, RG, 25.0) for s, p in calls]
    assert again == ref  # 全命中路径同样一致


def test_cap_reached_still_correct():
    pc = _world()
    rng = random.Random(3)
    calls = [(_subset(pc, rng), PARAMS[0]) for _ in range(5)]
    ref = [fes.replay_signals(pc, s, p, RG, 25.0) for s, p in calls]
    memo = fes.enable_replay_memo(50)
    got = [fes.replay_signals(pc, s, p, RG, 25.0) for s, p in calls]
    assert got == ref and memo.n_entries == 50


def test_distinct_df_objects_not_shared():
    """同 code 不同 df 对象（v2 分段 per_code）不得串用。"""
    a = {"000001": {"df": _df(1)}}
    b = {"000001": {"df": _df(2)}}
    s = [
        {
            "code": "000001",
            "date": a["000001"]["df"]["date"][400],
            "i": 400,
            "sig": {"i": 400, "date": a["000001"]["df"]["date"][400]},
            "score": 1.0,
        }
    ]
    ra = fes.replay_signals(a, s, PARAMS[0], RG, 25.0)
    rb = fes.replay_signals(b, s, PARAMS[0], RG, 25.0)
    fes.enable_replay_memo(1000)
    assert fes.replay_signals(a, s, PARAMS[0], RG, 25.0) == ra
    assert fes.replay_signals(b, s, PARAMS[0], RG, 25.0) == rb
