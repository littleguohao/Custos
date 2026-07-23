# -*- coding: utf-8 -*-
"""J<13 硬门槛 + 完美 B1 图形贴合度（perfect_b1_fit）测试。"""
from __future__ import annotations

import pandas as pd
import pytest

from screening import enrich_candidates as ec
from test_enrich_b1cz import make_df


def _flat_df(n=120, close=10.0):
    dates = pd.date_range(end="2026-07-22", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates, "open": close, "high": close * 1.005, "low": close * 0.995,
        "close": close, "volume": 1000.0, "amount": 0.0,
    })


def _hits(*codes):
    return {"date": "2026-07-22", "status": "ok",
            "formulas": [{"id": "POOL_ZHENDANG", "category": "manual_pool",
                          "hits": [{"code": c, "name": ""} for c in codes]}]}


def _run_enrich(monkeypatch, df_by_code, universe_cfg):
    monkeypatch.setattr(ec, "build_stock_theme_map", lambda **k: ({}, True))
    return ec.enrich("2026-07-22", hits_data=_hits(*df_by_code),
                     ohlcv_loader=lambda c: df_by_code[c].copy(),
                     index_loader=lambda: None, universe_cfg=universe_cfg)


def test_j_gate_excludes_high_j_pool_member(monkeypatch):
    # 自选池成员同样过 J 门槛：J≈50 的平盘票被剔除
    monkeypatch.setattr(ec, "kdj", lambda df: {"available": True, "j": 50.0})
    r = _run_enrich(monkeypatch, {"600000": _flat_df()}, {"j_low_required": True})
    assert r["candidates"] == []
    assert r["excluded"] and r["excluded"][0]["reason"].startswith("j_not_low")


def test_j_gate_keeps_low_j_and_j_none_excluded(monkeypatch):
    monkeypatch.setattr(ec, "kdj", lambda df: {"available": True, "j": 5.0})
    r = _run_enrich(monkeypatch, {"600000": _flat_df()}, {"j_low_required": True})
    assert len(r["candidates"]) == 1
    monkeypatch.setattr(ec, "kdj", lambda df: {"available": False, "j": None})
    r = _run_enrich(monkeypatch, {"600000": _flat_df()}, {"j_low_required": True})
    assert r["candidates"] == []  # J 不可计算视同不满足
    r = _run_enrich(monkeypatch, {"600000": _flat_df()}, {"j_low_required": False})
    assert len(r["candidates"]) == 1  # 开关可关


def test_fit_grading_uptrend_perfect_pattern():
    # 长上行慢牛 + 末端贴线：DKS 上行、DIF>0 → 这两个分量满分
    closes = [10 + i * 0.05 for i in range(200)]
    df = make_df(closes)
    zx = {"available": True, "qsx": closes[-1] * 1.0, "dks": closes[-1] * 0.99}
    pull = {"available": True, "detail": {"pullback_vol_ratio": 0.4}}
    fit = ec.compute_perfect_b1_fit(df, daily_j=-2.0, zx=zx, pullback=pull)
    c = fit["components"]
    assert c["j_depth"]["points"] == 2.0        # J<0
    assert c["near_line"]["points"] == 2.0      # 贴 QSX
    assert c["shrink_degree"]["points"] == 2.0  # 深缩量
    assert c["macd_above_zero"]["points"] == 1.0
    assert c["dks_rising"]["points"] == 1.0
    assert fit["score"] == 8.0


def test_fit_grading_poor_pattern():
    closes = [30.0 - i * 0.1 for i in range(200)]  # 长跌：DIF<0、DKS 下行
    df = make_df(closes)
    zx = {"available": True, "qsx": 20.0, "dks": 21.0}  # 收盘远在均线下方
    pull = {"available": True, "detail": {"pullback_vol_ratio": 0.95}}
    fit = ec.compute_perfect_b1_fit(df, daily_j=12.5, zx=zx, pullback=pull)
    c = fit["components"]
    assert c["j_depth"]["points"] == 1.0   # 仅 J<13 及格线
    assert c["near_line"]["points"] == 0.0
    assert c["shrink_degree"]["points"] == 0.0
    assert c["macd_above_zero"]["points"] == 0.0
    assert c["dks_rising"]["points"] == 0.0
    assert fit["score"] == 1.0


def test_fit_handles_missing_inputs():
    df = make_df([10.0] * 30)  # K线不足 114+5 → DKS 分量 0，不炸
    fit = ec.compute_perfect_b1_fit(df, daily_j=None, zx={"available": False},
                                    pullback={"available": False})
    assert fit["score"] == 0.0
