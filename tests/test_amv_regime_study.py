# -*- coding: utf-8 -*-
"""R51 amv_regime_study（v0.511）：状态机重放 / S1 两日型 / 翻转事件读数 / 窗口与护栏。"""

from __future__ import annotations

import argparse
import json

import pytest

from custos.pipeline.market_timing import amv_state
from custos.research import amv_regime_study as ar
from custos.research import backtest_factors as bt


def _days(n: int, start: str = "2022-01-03") -> list[str]:
    import pandas as pd

    return [str(d)[:10] for d in pd.bdate_range(start, periods=n)]


def _args(tmp_path, tag="t"):
    return argparse.Namespace(
        tag=tag, out_dir=str(tmp_path), cmdline="amv_regime_study test"
    )


def test_thresholds_match_live_single_source():
    assert ar.LONG_PCT == amv_state.AMV_LONG_PCT
    assert ar.SHORT_PCT == amv_state.AMV_SHORT_PCT


def test_s0_matches_backtest_replay():
    """S0 与研究重放 bt._amv_regime_from_records 逐日一致（含中性起始、粘滞）。"""
    import random

    rng = random.Random(3)
    ds = _days(400)
    recs = [{"date": d, "change_pct": round(rng.gauss(0, 2.5), 2)} for d in ds]
    ref = bt._amv_regime_from_records(recs)
    got = {r["date"]: r["state"] for r in ar.replay(recs, "S0", ds)}
    assert got == ref


def test_s1_two_day_rule():
    ds = _days(8)
    chg = [-3.0, 1.0, 2.0, 2.5, -2.5, 3.0, 0.5, 4.5]
    recs = [{"date": d, "change_pct": c} for d, c in zip(ds, chg)]
    s0 = ar.replay(recs, "S0", ds)
    s1 = ar.replay(recs, "S1", ds)
    assert [r["state"] for r in s0][:4] == ["空头"] * 4  # 2.0+2.5 合计 4.5 但 S0 不认
    assert s1[3]["state"] == "做多" and s1[3]["trigger"] == "two_day"
    assert s1[2]["state"] == "空头"  # 1.0+2.0=3.0 不够
    # 3.0+0.5=3.5 不够；4.5 单日型
    assert s1[6]["state"] == "空头" and s1[7]["trigger"] == "single"


def test_s1_requires_adjacent_trading_days():
    cal = _days(5)
    recs = [
        {"date": cal[0], "change_pct": -3.0},
        {"date": cal[1], "change_pct": 2.5},
        {"date": cal[3], "change_pct": 2.5},
    ]
    s1 = ar.replay(recs, "S1", cal)
    assert s1[-1]["state"] == "空头"  # 中间缺一个交易日 ⇒ 不相邻
    s1b = ar.replay(recs, "S1", None)
    assert s1b[-1]["trigger"] == "two_day"  # 无日历时按记录序相邻


def _world(n=1200, seed=0):
    import random

    rng = random.Random(seed)
    ds = _days(n, "2021-06-01")
    recs = [{"date": d, "change_pct": round(rng.gauss(0.1, 2.4), 2)} for d in ds]
    px, close = 3000.0, {}
    for d in ds:
        px *= 1 + rng.gauss(0.0003, 0.012)
        close[d] = round(px, 2)
    return recs, close


def test_flip_events_basic_and_window_bounds():
    recs, close = _world()
    s0 = ar.replay(recs, "S0", sorted(close))
    evs = ar.flip_events(s0, close, "2022-01-01", "2024-12-31")
    assert evs and all("2022-01-01" <= e["date"] <= "2024-12-31" for e in evs)
    for e in evs:
        assert e["dir"] in ("up", "down")
        if not e["censored"]:
            assert e["duration"] >= 1
            assert e["rev5"] in (None, e["duration"] <= 5)
        if e["dir"] == "up" and e["idx_fav"] is not None:
            assert e["idx_fav"] >= 0 >= e["idx_adv"]
    # 前视窗不出窗：窗末附近事件的 fwd20 为 None
    last = [e for e in evs if e["date"] > "2024-12-02"]
    assert all(e["fwd20"] is None for e in last)


def test_reversal_rate_hand_case():
    ds = _days(30)
    chg = [0.0] * 30
    chg[1], chg[3], chg[10], chg[25] = (
        -3.0,
        5.0,
        -3.0,
        5.0,
    )  # 空(1) 多(3) 空(10，7 日翻回) 多(25)
    recs = [{"date": d, "change_pct": c} for d, c in zip(ds, chg)]
    close = {d: 100.0 + i for i, d in enumerate(ds)}
    s0 = ar.replay(recs, "S0", ds)
    evs = ar.flip_events(s0, close, ds[0], ds[-1])
    up = [e for e in evs if e["date"] == ds[3]][0]
    assert up["duration"] == 7 and up["rev5"] is False and up["rev10"] is True
    assert up["trigger"] == "single" and up["trigger_chg"] == 5.0


def _run(tmp_path, **kw):
    recs, close = _world(2400, 1)
    return ar.run_study(_args(tmp_path), records=recs, index_close=close, **kw)


def test_run_study_has_both_windows_and_two_day(tmp_path, monkeypatch):
    import pandas as pd

    # 合成世界须覆盖两挖掘段：把 A 段改成合成世界内的子窗（mining_violation 仍须放行）
    monkeypatch.setattr(
        ar.lw, "SEG_MINING_A", ("2021-07-01", "2021-12-31"), raising=True
    )
    monkeypatch.setattr(ar.lw, "mining_violation", lambda s, e: None)
    rep = _run(tmp_path)
    assert set(rep["windows"]) == {"mining_a", "mining_b"}
    assert rep["amv_source"] == "ledger"
    assert rep["two_day_verdict"] in ("equivalent", "not_equivalent", "insufficient")
    assert rep["window_usage"]["window"] == "not_read"
    json.dumps(rep, default=str, allow_nan=True)
    _ = pd


def test_regime_series_path_marks_amv_unavailable(tmp_path):
    recs, close = _world(2400, 2)
    s0 = ar.replay(recs, "S0", sorted(close))
    series = {r["date"]: r["state"] for r in s0}
    rep = ar.run_study(
        _args(tmp_path), records=[], regime_series=series, index_close=close
    )
    assert rep["amv_source"] == "regime_series"
    assert rep["two_day"] == "unavailable"
    assert rep["windows"]["mining_b"]["up"]["amv"] == "unavailable"


def test_empty_guard_no_output(tmp_path):
    close = {d: 1.0 for d in _days(50, "2022-01-03")}
    rc = ar.main(
        ["--tag", "e", "--out-dir", str(tmp_path)],
        records=[{"date": d, "change_pct": 0.0} for d in close],
        index_close=close,
    )
    assert rc == 2 and not list(tmp_path.iterdir())


def test_window_violation_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(ar.lw, "SEG_MINING_A", ("2000-01-01", "2017-06-30"))
    with pytest.raises(RuntimeError, match="窗口校验失败"):
        _run(tmp_path)


def test_verdict_rules():
    g = lambda a, b, c: {"gate": {"a_rev": a, "b_fwd": b, "c_n": c}}  # noqa: E731
    assert ar.verdict_two_day({"a": g(1, 1, 1), "b": g(1, 1, 1)}) == "equivalent"
    assert ar.verdict_two_day({"a": g(1, 1, 1), "b": g(0, 1, 1)}) == "not_equivalent"
    assert ar.verdict_two_day({"a": g(1, 1, 0), "b": g(1, 1, 1)}) == "insufficient"


def world_report(tmp_path, monkeypatch):
    """result_digest 夹具。"""
    monkeypatch.setattr(
        ar.lw, "SEG_MINING_A", ("2021-07-01", "2021-12-31"), raising=True
    )
    monkeypatch.setattr(ar.lw, "mining_violation", lambda s, e: None)
    return _run(tmp_path)
