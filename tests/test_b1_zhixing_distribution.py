# -*- coding: utf-8 -*-
"""知行量价（good_b1）正向因子 + 主力出货五方式负向因子 + score 接线测试。

表驱动：注入合成 OHLCV，不依赖 TdxW/网络。
"""
import pandas as pd

from screening import enrich_candidates as ec
from screening import score_candidates as sc


def make_df(closes, vols=None, opens=None, highs=None, lows=None):
    n = len(closes)
    closes = [float(x) for x in closes]
    opens = [float(x) for x in (opens if opens is not None else closes)]
    highs = [float(x) for x in (highs if highs is not None else [max(o, c) * 1.005 for o, c in zip(opens, closes)])]
    lows = [float(x) for x in (lows if lows is not None else [min(o, c) * 0.995 for o, c in zip(opens, closes)])]
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="B"),
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": [float(v) for v in (vols or [1000.0] * n)], "amount": [0.0] * n,
    })


# ---------- 知行趋势线 zhixing_state（QSX/DKS） ----------

def test_zhixing_bull_and_golden_cross():
    closes = [10.0] * 120 + [10 + i * 0.3 for i in range(1, 30)]
    r = ec.zhixing_state(make_df(closes))
    assert r["available"] and r["qsx_gt_dks"] and r["close_above_qsx"]
    assert r["days_since_golden_cross"] is not None


def test_zhixing_bear_and_unavailable():
    closes = [30.0 - i * 0.12 for i in range(150)]
    r = ec.zhixing_state(make_df(closes))
    assert r["available"] and r["qsx_gt_dks"] is False
    assert ec.zhixing_state(make_df([10.0] * 100))["available"] is False


# ---------- 放量点火 check_ignition ----------

def test_ignition_hit():
    closes = [10.0] * 30
    closes[-1] = closes[-2] * 1.04
    opens = list(closes)
    opens[-1] = closes[-2]  # 收阳
    vols = [1000.0] * 20 + [500.0] * 9 + [1300.0]  # 前段缩量后放量
    r = ec.check_ignition(make_df(closes, vols=vols, opens=opens))
    assert r["hit"] is True
    assert r["detail"]["vol_ratio5"] >= 1.5


def test_ignition_miss_on_flat():
    r = ec.check_ignition(make_df([10.0] * 30))
    assert r["available"] and r["hit"] is False


# ---------- 回调缩量企稳 check_pullback_shrink ----------

def test_pullback_shrink_hit():
    closes = [10.0] * 10 + [10 + i * 0.33 for i in range(15)] + [14.2, 14.0, 13.9, 13.85, 13.8]
    vols = [1000.0] * 10 + [2000.0] * 15 + [600.0, 550.0, 500.0, 480.0, 470.0]
    r = ec.check_pullback_shrink(make_df(closes, vols=vols), dks_last=None)
    assert r["hit"] is True
    assert r["detail"]["drop_from_high_pct"] >= 3.0


def test_pullback_shrink_miss_when_volume_not_shrinking():
    closes = [10.0] * 10 + [10 + i * 0.33 for i in range(15)] + [14.2, 14.0, 13.9, 13.85, 13.8]
    vols = [1000.0] * 30  # 回调不缩量
    r = ec.check_pullback_shrink(make_df(closes, vols=vols), dks_last=None)
    assert r["hit"] is False


# ---------- 出货五方式 detect_distribution ----------

def test_distribution_top_huge_vol_bear():
    # 20 平 + 10 快涨(~+35%) + 顶部天量大阴(-6%, 4x量)
    base = [10.0] * 20
    up = [10.0 * (1.031 ** i) for i in range(1, 11)]
    red_open = up[-1]
    red_close = up[-1] * 0.94
    closes = base + up + [red_close]
    opens = base + up + [red_open]
    vols = [1000.0] * 20 + [1500.0] * 10 + [4200.0]
    r = ec.detect_distribution(make_df(closes, vols=vols, opens=opens), code="600000")
    assert r["available"]
    assert r["signals"]["top_huge_vol_bear"]["hit"] is True
    assert r["severe"] is True and r["risk_level"] == "high"


def test_distribution_green_heavy_red_light():
    base = [13.0] * 20
    closes, opens, vols = list(base), list(base), [1000.0] * 20
    price = 13.0
    for _ in range(5):
        opens.append(price); closes.append(price * 0.97); vols.append(2200.0)
        price = price * 0.97
        opens.append(price); closes.append(price * 1.008); vols.append(700.0)
        price = price * 1.008
    r = ec.detect_distribution(make_df(closes, vols=vols, opens=opens), code="600000")
    assert r["signals"]["top_green_heavy_red_light"]["hit"] is True
    assert r["risk_level"] in ("watch", "high")


def test_distribution_none_on_healthy_uptrend():
    closes = [10.0 + i * 0.15 for i in range(60)]
    vols = [1000.0 + i * 5 for i in range(60)]
    r = ec.detect_distribution(make_df(closes, vols=vols), code="600000")
    assert r["available"] and r["hits"] == [] and r["risk_level"] == "none"


# ---------- compute_metrics 落盘新字段 ----------

def test_compute_metrics_has_new_fields():
    closes = [10.0] * 120 + [10 + i * 0.2 for i in range(1, 20)]
    m = ec.compute_metrics(make_df(closes), None, code="600000")
    for key in ["zhixing", "ignition", "pullback_shrink", "ride_above_fast", "b1_ignition", "distribution"]:
        assert key in m, f"缺字段 {key}"
    assert m["zhixing"]["available"] is True


# ---------- score_candidates 接线 ----------

def _scand(**extra):
    base = dict(
        code="600000", name="示例", sector="半导体", theme_id="t", formula_hits=[],
        patterns={"bbi_above": True, "j_low": True, "volume_contraction": True,
                  "reversal_k_candidate": True, "relative_strength_strong": False},
        daily_j=10.0, stop_loss_ref={"price": 10.0, "basis": "x"}, is_holding=False,
    )
    base.update(extra)
    return base


SECTOR = {"state": "主升", "score": 80}


def test_score_positive_zhixing_and_ignition_add():
    s0 = sc.score_candidate(_scand(), SECTOR, "做多")
    s1 = sc.score_candidate(_scand(
        zhixing={"available": True, "qsx_gt_dks": True},
        ignition={"hit": True}, pullback_shrink={"hit": True}, b1_ignition={"hit": True},
    ), SECTOR, "做多")
    c = s1["score_detail"]["factor_contrib"]
    assert c.get("zhixing_bull") == 6 and c.get("b1_ignition") == 8
    assert c.get("ignition") == 4 and c.get("pullback_shrink") == 3
    assert s1["score_detail"]["technical_score"] >= s0["score_detail"]["technical_score"]


def test_distribution_cap_high_forces_d():
    s = sc.score_candidate(_scand(distribution={
        "available": True, "hits": ["top_huge_vol_bear"], "risk_level": "high"}), SECTOR, "做多")
    assert s["bucket"] == "D" and "distribution_high" in s["risk_flags"]


def test_distribution_cap_watch_caps_c():
    s = sc.score_candidate(_scand(distribution={
        "available": True, "hits": ["top_green_heavy_red_light"], "risk_level": "watch"}), SECTOR, "做多")
    assert s["bucket"] == "C" and "distribution_watch" in s["risk_flags"]


def test_distribution_cap_disabled_keeps_a():
    s = sc.score_candidate(_scand(distribution={
        "available": True, "hits": ["top_huge_vol_bear"], "risk_level": "high"}),
        SECTOR, "做多", cap_rules={"distribution_cap": False})
    assert s["bucket"] == "A" and "distribution_detected_cap_disabled" in s["risk_flags"]
