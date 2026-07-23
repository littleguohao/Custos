# -*- coding: utf-8 -*-
"""S_shape v3.0 沙漏评分测试（分项检测器 + 聚合 S** + technical_score 两条路径）。

合成 OHLCV，不依赖网络/TdxW。阈值为待回测猜测，断言只校验方向/边界，不锁死具体值。
"""
import pandas as pd

from screening import s_shape as ss
from screening import score_candidates as sc


def make_df(closes, vols=None, opens=None, highs=None, lows=None):
    n = len(closes)
    closes = [float(x) for x in closes]
    opens = [float(x) for x in (opens if opens is not None else closes)]
    highs = [float(x) for x in (highs if highs is not None else [max(o, c) * 1.01 for o, c in zip(opens, closes)])]
    lows = [float(x) for x in (lows if lows is not None else [min(o, c) * 0.99 for o, c in zip(opens, closes)])]
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="B"),
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": [float(v) for v in (vols or [1000.0] * n)], "amount": [0.0] * n,
    })


# ---------- 压缩/收敛 VCP ----------

def test_vcp_strong_contraction_scores_high():
    # 前10日大振幅高量，近10日小振幅低量 → 强收敛
    highs = [10.5] * 10 + [10.1] * 10
    lows = [9.5] * 10 + [9.9] * 10
    closes = [10.0] * 20
    vols = [2000.0] * 10 + [800.0] * 10
    r = ss.compute_vcp(make_df(closes, vols=vols, highs=highs, lows=lows))
    assert r["available"] and r["points"] == 20.0
    assert r["range_ratio"] <= 0.5 and r["vol_ratio"] <= 0.6


def test_vcp_no_contraction_scores_zero():
    highs = [10.5] * 20
    lows = [9.5] * 20
    r = ss.compute_vcp(make_df([10.0] * 20, vols=[1000.0] * 20, highs=highs, lows=lows))
    assert r["available"] and r["points"] == 0.0


# ---------- 枢轴 ----------

def test_pivot_breakout_scores_high():
    closes = [10 + i * 0.1 for i in range(22)]  # 单边上行，末位突破前高
    r = ss.compute_pivot(make_df(closes))
    assert r["available"] and r["points"] >= 12.0


def test_pivot_far_below_scores_zero():
    closes = [12.0] * 20 + [10.0, 9.5]  # 远低于近20日高点
    r = ss.compute_pivot(make_df(closes))
    assert r["available"] and r["points"] == 0.0


# ---------- 量 ----------

def test_volume_health_surge_and_uptrend():
    vols = [800.0] * 40 + [1200.0] * 21 + [2000.0]  # ma20 抬升 + 当日放量
    r = ss.compute_volume_health(make_df([10.0] * 62, vols=vols))
    assert r["available"] and r["points"] >= 8.0


# ---------- 口袋妖怪 ----------

def test_pocket_pivot_hit():
    closes = [10, 9.9, 10.0, 9.8, 10.0, 9.7, 10.0, 9.9, 10.1, 10.0, 10.05, 10.1, 10.0, 10.05, 10.6]
    vols = [1000, 1200, 1000, 1400, 1000, 1300, 1000, 1100, 1000, 1000, 1000, 1000, 900, 1000, 2500]
    r = ss.check_pocket_pivot(make_df(closes, vols=vols))
    assert r["available"] and r["hit"] is True and r["points"] == 15.0


def test_pocket_pivot_miss_on_flat():
    r = ss.check_pocket_pivot(make_df([10.0] * 15, vols=[1000.0] * 15))
    assert r["available"] and r["hit"] is False and r["points"] == 0.0


# ---------- 上方套牢供给 ----------

def test_overhead_supply_low_when_price_at_highs():
    closes = [10 + i * 0.05 for i in range(60)]  # 单边上行 → 上方几乎无套牢
    r = ss.compute_overhead_supply(make_df(closes))
    assert r["available"] and r["points"] >= 8.0 and r["overhead_frac"] <= 0.2


def test_overhead_supply_high_when_price_near_bottom():
    closes = [20 - i * 0.2 for i in range(60)]  # 单边下行 → 上方大量套牢
    r = ss.compute_overhead_supply(make_df(closes))
    assert r["available"] and r["points"] <= 3.0


# ---------- 均线结构 ----------

def test_ma_structure_bull_stack_and_higher_low():
    closes = [10 + i * 0.1 for i in range(52)]  # 多头排列 + 低点抬高
    r = ss.compute_ma_structure(make_df(closes))
    assert r["available"] and r["bull_stack"] is True and r["higher_low"] is True and r["points"] == 10.0


def test_ma_structure_downtrend_zero():
    closes = [20 - i * 0.1 for i in range(52)]
    r = ss.compute_ma_structure(make_df(closes))
    assert r["available"] and r["points"] == 0.0


# ---------- Δ 催化 ----------

def test_delta_strong_close_and_up():
    # 末日大阳、收在最高附近
    closes = [10.0] * 10 + [10.6]
    opens = [10.0] * 10 + [10.0]
    highs = [10.05] * 10 + [10.62]
    lows = [9.95] * 10 + [9.98]
    r = ss.compute_delta_catalyst(make_df(closes, opens=opens, highs=highs, lows=lows))
    assert r["available"] and r["points"] >= 6.0 and r["closing_strength"] >= 0.9


# ---------- P 惩罚 ----------

def test_penalty_recent_unrecovered_big_bear():
    # 末日放量大阴(-6%)、未收复
    closes = [10.0] * 24 + [9.4]
    opens = [10.0] * 24 + [10.0]
    vols = [1000.0] * 24 + [3000.0]
    r = ss.compute_penalty(make_df(closes, opens=opens, vols=vols), code="600000")
    assert r["available"] and r["points"] >= 5.0


def test_penalty_none_on_healthy():
    closes = [10 + i * 0.05 for i in range(30)]
    r = ss.compute_penalty(make_df(closes), code="600000")
    assert r["available"] and r["points"] == 0.0


# ---------- 聚合 S** ----------

def test_compute_s_shape_bounds_and_suggestion():
    closes = [10 + i * 0.08 for i in range(80)]
    r = ss.compute_s_shape(make_df(closes), "600000")
    assert r["available"] is True
    assert 0.0 <= r["s_star"] <= 100.0
    assert r["suggestion"] in ("可买", "观望", "不买")
    # 分项都在各自上限内
    caps = {"compression": 20, "pivot": 15, "volume": 20, "pocket_pivot": 15,
            "overhead_supply": 10, "ma_structure": 10, "event_risk": 10}
    for k, cap in caps.items():
        assert 0.0 <= r["components"][k]["points"] <= cap


def test_compute_s_shape_unavailable_when_short():
    assert ss.compute_s_shape(make_df([10.0] * 40))["available"] is False


def test_sstar_level_thresholds():
    assert ss.sstar_level(80) == "强"
    assert ss.sstar_level(50) == "中"
    assert ss.sstar_level(20) == "弱"
    assert ss.sstar_level(None) == "弱"


# ---------- technical_score 两条路径 ----------

def test_technical_score_uses_s_shape_when_present():
    cand = {"patterns": {}, "s_shape": {
        "available": True, "s_star": 78.0, "s_shape": 73.0, "delta": 5.0, "penalty": 0.0,
        "suggestion": "可买", "components": {"compression": {"points": 18.0}}}}
    score, level, detail = sc.technical_score(cand)
    assert score == 78 and level == "强"
    assert detail["scorer"] == "s_shape_v3" and detail["s_star"] == 78.0
    assert detail["sshape_compression"] == 18.0


def test_technical_score_falls_back_without_s_shape():
    cand = {"patterns": {"bbi_above": True, "j_low": True, "volume_contraction": True}}
    score, level, detail = sc.technical_score(cand)
    assert "scorer" not in detail          # 走旧加权路径
    assert detail.get("bbi_above") == 25   # 旧因子贡献仍在
    assert score == 60 and level == "强"
