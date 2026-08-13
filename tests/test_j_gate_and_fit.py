# -*- coding: utf-8 -*-
"""J<13 硬门槛 + 完美 B1 图形贴合度（perfect_b1_fit）测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from custos.pipeline.screening import enrich_candidates as ec
from test_enrich_b1cz import make_df


def _flat_df(n=120, close=10.0):
    dates = pd.date_range(end="2026-07-22", periods=n, freq="B")
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": 1000.0,
            "amount": 0.0,
        }
    )


def _hits(*codes):
    return {
        "date": "2026-07-22",
        "status": "ok",
        "formulas": [
            {
                "id": "POOL_ZHENDANG",
                "category": "manual_pool",
                "hits": [{"code": c, "name": ""} for c in codes],
            }
        ],
    }


def _run_enrich(monkeypatch, df_by_code, universe_cfg):
    monkeypatch.setattr(ec, "build_stock_theme_map", lambda **k: ({}, True))
    return ec.enrich(
        "2026-07-22",
        hits_data=_hits(*df_by_code),
        ohlcv_loader=lambda c: df_by_code[c].copy(),
        index_loader=lambda: None,
        universe_cfg=universe_cfg,
    )


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
    assert c["j_depth"]["points"] == 2.0  # J<0
    assert c["near_line"]["points"] == 2.0  # 贴 QSX
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
    assert c["j_depth"]["points"] == 1.0  # 仅 J<13 及格线
    assert c["near_line"]["points"] == 0.0
    assert c["shrink_degree"]["points"] == 0.0
    assert c["macd_above_zero"]["points"] == 0.0
    assert c["dks_rising"]["points"] == 0.0
    assert fit["score"] == 1.0


def test_fit_handles_missing_inputs():
    df = make_df([10.0] * 30)  # K线不足 114+5 → DKS 分量 0，不炸
    fit = ec.compute_perfect_b1_fit(
        df, daily_j=None, zx={"available": False}, pullback={"available": False}
    )
    assert fit["score"] == 0.0


# ---------------------------------------------------------------------------
# v0.51（#37 阶段 B）：adx25 证据列 + 门槛外观察区
# ---------------------------------------------------------------------------


def _trend_df(n=120, start=10.0, step=0.05):
    """单调上行（ADX 必然 >25）。"""
    dates = pd.date_range(end="2026-07-22", periods=n, freq="B")
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": 1000.0,
            "amount": 0.0,
        }
    )


class TestAdx25EvidenceColumn:
    """R2:67-72「J<13 且 ADX≥25」——严格证据层：只落盘/展示，不进分层。"""

    def test_adx25_true_when_j_low_and_trending(self, monkeypatch):
        monkeypatch.setattr(ec, "kdj", lambda df: {"available": True, "j": 5.0})
        r = _run_enrich(monkeypatch, {"600000": _trend_df()}, {"j_low_required": True})
        cand = r["candidates"][0]
        assert cand["adx"] is not None and cand["adx"] >= 25
        assert cand["adx25"] is True

    def test_adx25_false_when_flat(self, monkeypatch):
        """平盘 ADX≈0 ⇒ 即便 J 低也不中。"""
        monkeypatch.setattr(ec, "kdj", lambda df: {"available": True, "j": 5.0})
        r = _run_enrich(monkeypatch, {"600000": _flat_df()}, {"j_low_required": True})
        cand = r["candidates"][0]
        assert cand["adx25"] is False

    def test_adx25_false_when_j_not_low(self, monkeypatch):
        """J 条件是合取的一半：趋势再强、J 不低也不中。"""
        monkeypatch.setattr(ec, "kdj", lambda df: {"available": True, "j": 50.0})
        r = _run_enrich(monkeypatch, {"600000": _trend_df()}, {"j_low_required": False})
        cand = r["candidates"][0]
        assert cand["adx"] >= 25 and cand["adx25"] is False

    def test_adx25_never_changes_bucket_or_score(self):
        """防回归：adx25 是证据列——置真/置假的分层与总分必须逐位相同。"""
        from custos.pipeline.screening import score_candidates as sc

        base = {"code": "600000", "name": "甲", "patterns": {"bbi_above": True}}
        a = sc.score_candidate({**base, "adx25": True}, None, "做多")
        b = sc.score_candidate({**base, "adx25": False}, None, "做多")
        assert a["bucket"] == b["bucket"] and a["score"] == b["score"]


class TestOutsideGateWatchlist:
    """门槛外观察区：J<13 挡掉但异动强（底部巨量/放量点火）的票，只展示。"""

    def _run(self, monkeypatch, j, bottom_hit=False, ignition_hit=False):
        monkeypatch.setattr(ec, "kdj", lambda df: {"available": True, "j": j})
        monkeypatch.setattr(
            ec,
            "check_bottom_volume",
            lambda df: {"available": True, "hit": bottom_hit},
        )
        monkeypatch.setattr(
            ec, "check_ignition", lambda df: {"available": True, "hit": ignition_hit}
        )
        return _run_enrich(
            monkeypatch, {"600000": _flat_df()}, {"j_low_required": True}
        )

    def test_blocked_with_strong_move_lands_in_watchlist(self, monkeypatch):
        r = self._run(monkeypatch, 50.0, bottom_hit=True)
        assert r["candidates"] == []
        assert r["excluded"][0]["reason"].startswith("j_not_low"), "门槛行为不变"
        w = r["watchlist_outside_gate"]
        assert len(w) == 1 and w[0]["code"] == "600000"
        assert w[0]["gate_reason"].startswith("j_not_low")

    def test_blocked_without_move_stays_out(self, monkeypatch):
        r = self._run(monkeypatch, 50.0)
        assert r["watchlist_outside_gate"] == []

    def test_ignition_hit_also_qualifies(self, monkeypatch):
        r = self._run(monkeypatch, 50.0, ignition_hit=True)
        assert len(r["watchlist_outside_gate"]) == 1

    def test_passing_candidate_not_in_watchlist(self, monkeypatch):
        r = self._run(monkeypatch, 5.0, bottom_hit=True)
        assert len(r["candidates"]) == 1 and r["watchlist_outside_gate"] == []

    def test_key_always_present(self, monkeypatch):
        """无观察对象时键也必须在（空数组）——缺键与「今天没有」分不开。"""
        r = self._run(monkeypatch, 5.0)
        assert r["watchlist_outside_gate"] == []


class TestSreversalEvidenceColumn:
    """TODO ② 闭环（v0.51）：s_reversal 接进证据列（此前存在但 live 从不调）。"""

    def test_s_reversal_landed_and_passthrough(self, monkeypatch):
        monkeypatch.setattr(ec, "kdj", lambda df: {"available": True, "j": 5.0})
        r = _run_enrich(monkeypatch, {"600000": _trend_df()}, {"j_low_required": True})
        sr = r["candidates"][0]["s_reversal"]
        assert isinstance(sr, dict) and "available" in sr
        # score_candidates 白名单透传（不加就丢——2026-08-04 signals 的教训）
        from custos.pipeline.screening import score_candidates as sc

        scored = sc.score_candidate(r["candidates"][0], None, "做多")
        assert scored["s_reversal"] == sr

    def test_s_reversal_never_changes_bucket_or_score(self):
        from custos.pipeline.screening import score_candidates as sc

        base = {"code": "600000", "name": "甲", "patterns": {"bbi_above": True}}
        a = sc.score_candidate(
            {**base, "s_reversal": {"available": True, "s_reversal": 90.0}},
            None,
            "做多",
        )
        b = sc.score_candidate({**base}, None, "做多")
        assert a["bucket"] == b["bucket"] and a["score"] == b["score"]
