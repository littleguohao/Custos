# -*- coding: utf-8 -*-
"""TODO #112 加载侧提速（v0.441 --load-workers）：并行与串行**逐位一致**钉测。

依据 #92 实测：数据加载占总耗时 26~56%。三处并行化——
``backtest_factors._load_bars_local(load_workers)``、
``exit_campaign.warm_v0_signals(load_workers)``、
``bear_regime_study.warm_gates(load_workers)``，全部默认 1=原行为。
产物语义 = 返回 dict 的键序与逐值；stderr WARN 行的先后顺序不在产物语义内。
"""

from __future__ import annotations

import time

import pandas as pd
import pytest

from custos.research import backtest_factors as bt
from custos.research import bear_regime_study as brs
from custos.research import exit_campaign as ec

CODES = [f"60000{i}" for i in range(12)]  # 含 600005（0 信号）/600009（加载失败）


def _fake_bars(code, count, start, end, warmup_bars=0, allow_holdout=False):
    time.sleep((sum(map(ord, code)) % 5) * 0.002)  # 强制完成顺序交错
    if code.endswith("9"):
        return None  # 加载失败（串行/并行都剔）
    return pd.DataFrame(
        {
            "date": ["2022-05-05", "2022-05-06"],
            "close": [10.0 + int(code[-2:]), 11.0],
        }
    )


def _fake_eval(data, **kw):
    code = next(iter(data))
    sigs = kw["signals_out"]
    if code.endswith("5"):
        return  # 0 信号
    sigs.append({"i": 0, "date": "2022-05-05", "code": code})


def _fake_score(df, index_df, i, code):
    return (float(int(code[-2:])), None, None)


def _packs_equal(a: dict, b: dict) -> None:
    """两个 per_code 字典逐位比对（键序 + df 值 + signals + scores）。"""
    assert list(a) == list(b)
    for k in a:
        pa, pb = a[k], b[k]
        if isinstance(pa, pd.DataFrame):
            assert pa.equals(pb), k
            continue
        assert set(pa) == set(pb), k
        for key in pa:
            va, vb = pa[key], pb[key]
            if isinstance(va, pd.DataFrame):
                assert va.equals(vb), (k, key)
            else:
                assert va == vb, (k, key)


# ---------------------------------------------------------------------------
# run_codes_parallel 本体
# ---------------------------------------------------------------------------


def test_run_codes_parallel_order_and_filter():
    def fn(code):
        time.sleep((12 - int(code[-2:])) * 0.002)  # 尾部代码先完成
        return None if code.endswith(("5", "9")) else {"v": code}

    got = bt.run_codes_parallel(CODES, fn, 4)
    serial = {c: fn(c) for c in CODES if fn(c) is not None}
    assert list(got) == list(serial)
    assert got == serial


def test_run_codes_parallel_reraises():
    def fn(code):
        if code.endswith("3"):
            raise RuntimeError("boom")
        return 1

    with pytest.raises(RuntimeError, match="boom"):
        bt.run_codes_parallel(CODES, fn, 4)


# ---------------------------------------------------------------------------
# _load_bars_local
# ---------------------------------------------------------------------------


def test_load_bars_local_parallel_bit_identical(monkeypatch):
    monkeypatch.setattr(bt, "_load_one_bars", _fake_bars)
    serial = bt._load_bars_local(CODES, 100, None, None)
    par = bt._load_bars_local(CODES, 100, None, None, load_workers=4)
    _packs_equal(serial, par)
    assert "600009" not in par and "600005" in par  # None 剔、0 信号票保留


# ---------------------------------------------------------------------------
# warm_v0_signals
# ---------------------------------------------------------------------------


def _patch_warm(monkeypatch):
    monkeypatch.setattr(bt, "_load_one_bars", _fake_bars)
    monkeypatch.setattr(bt, "evaluate_trades", _fake_eval)
    monkeypatch.setattr(
        "custos.research.score_return_study.asof_technical_score", _fake_score
    )


def test_warm_v0_signals_parallel_bit_identical(monkeypatch):
    _patch_warm(monkeypatch)
    kw = dict(count=100, cost_bps=25.0, start="2022-01-01", end="2022-12-31")
    serial = ec.warm_v0_signals(CODES, {}, None, **kw)
    par = ec.warm_v0_signals(CODES, {}, None, load_workers=4, **kw)
    _packs_equal(serial, par)
    assert "600005" not in par and "600009" not in par


def test_warm_v0_signals_parallel_warmup_filter(monkeypatch):
    _patch_warm(monkeypatch)
    kw = dict(count=100, cost_bps=25.0, start="2022-01-01", end="2022-12-31")
    serial = ec.warm_v0_signals(CODES, {}, None, warmup_bars=300, **kw)
    par = ec.warm_v0_signals(CODES, {}, None, warmup_bars=300, load_workers=4, **kw)
    _packs_equal(serial, par)


# ---------------------------------------------------------------------------
# warm_gates（含 0 信号票保留——随机臂母体口径）
# ---------------------------------------------------------------------------


def test_warm_gates_parallel_bit_identical(monkeypatch):
    _patch_warm(monkeypatch)
    monkeypatch.setattr(
        bt, "ENTRY_GATES", {"g1": object(), "g2": object()}, raising=False
    )
    kw = dict(
        gates=["g1", "g2"],
        count=100,
        cost_bps=25.0,
        start="2022-01-01",
        end="2022-12-31",
    )
    serial = brs.warm_gates(CODES, {}, None, **kw)
    par = brs.warm_gates(CODES, {}, None, load_workers=4, **kw)
    _packs_equal(serial, par)
    # 0 信号票（600005）保留 pack（随机臂母体=全宇宙 bar 口径），加载失败票剔除
    assert "600005" in par and par["600005"]["gate_signals"] == {}
    assert "600009" not in par
