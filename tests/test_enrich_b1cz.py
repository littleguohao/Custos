# -*- coding: utf-8 -*-
"""Tests for B1/CZ pattern detectors in screening.enrich_candidates.

表驱动：注入合成 OHLCV DataFrame，每个检测器至少正反两例；不依赖 TdxW/网络。
"""
import pandas as pd
import pytest

from screening import enrich_candidates as ec


def make_df(closes, vols=None, highs=None, lows=None):
    n = len(closes)
    closes = [float(x) for x in closes]
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n, freq="B"),
        "open": closes,
        "high": [float(x) for x in (highs or [c * 1.005 for c in closes])],
        "low": [float(x) for x in (lows or [c * 0.995 for c in closes])],
        "close": closes,
        "volume": [float(v) for v in (vols or [1000.0] * n)],
        "amount": [0.0] * n,
    })


# ---------- wave_type（B1 §四.0 拉升波三分类） ----------

def test_wave_buildup():
    # 50 平盘 + 启动放量长阳(+6%, 量2x) + 10日温和上行至13（段涨幅约31%）
    closes = [10.0] * 50 + [10.6] + [10.6 + i * 0.27 for i in range(1, 10)]
    lows = [c * 0.995 for c in closes]
    lows[49] = 9.8  # 启动低点
    vols = [1000.0] * 50 + [2000.0] + [1000.0] * 9
    r = ec.detect_wave_type(make_df(closes, vols=vols, lows=lows))
    assert r["available"] and r["wave_type"] == "buildup"
    assert 25 <= r["detail"]["seg_gain_pct"] <= 50
    assert r["detail"]["start_bull_candle"] is True


def test_wave_rally():
    # 前一段 10.5→12.2（摆动>15%）→ 回踩 10.6（窗口最低）→ 二段至 14.5（段涨幅约37%）
    closes = [11.0] * 20
    closes += [10.5 + i * 0.19 for i in range(10)]          # 10.5→12.21
    closes += [10.6]                                          # 回踩低点
    closes += [10.6 + (i + 1) * 0.39 for i in range(10)]      # →14.5
    lows = [c * 0.995 for c in closes]
    lows[30] = 10.4  # 窗口最低价在回踩处
    r = ec.detect_wave_type(make_df(closes, lows=lows))
    assert r["wave_type"] == "rally"
    assert r["detail"]["second_start"] is True
    assert 35 <= r["detail"]["seg_gain_pct"] <= 50


def test_wave_sprint():
    # 近20日2次涨停(+10%)、近10日涨幅>25%、顶部放量3x → 冲刺波（优先级最高）
    closes = [10.0] * 40 + [11.0, 11.0, 12.1, 12.5, 13.5, 14.5, 15.5]
    vols = [1000.0] * 46 + [3000.0]
    r = ec.detect_wave_type(make_df(closes, vols=vols))
    assert r["wave_type"] == "sprint"
    assert r["detail"]["limit_up_count_20d"] >= 2
    assert r["detail"]["top_vol_ratio"] >= 1.5


def test_wave_unknown_on_flat():
    r = ec.detect_wave_type(make_df([10.0] * 50))
    assert r["wave_type"] == "unknown"


def test_wave_insufficient_bars():
    r = ec.detect_wave_type(make_df([10.0] * 20))
    assert r["available"] is False and r["wave_type"] == "unknown"


# ---------- weekly_j（B1 §四.1 主线口径） ----------

def test_weekly_j_low_in_downtrend():
    closes = [20.0 - i * 0.1 for i in range(120)]  # 单边阴跌
    r = ec.weekly_j_state(make_df(closes))
    assert r["available"] and r["weekly_j"] < 13 and r["weekly_j_low"] is True


def test_weekly_j_not_low_in_uptrend():
    closes = [10.0 + i * 0.1 for i in range(120)]
    r = ec.weekly_j_state(make_df(closes))
    assert r["available"] and r["weekly_j_low"] is False


# ---------- non_one_wave（B1 §四 非一波流确认） ----------

def _now_base_df(pull_vols, top_drop=None):
    # 30 平盘 → 10日上行（均量1000）→ 高点 → 5日回调（可控量与跌幅）
    closes = [10.0] * 30 + [10.0 + i * 0.2 for i in range(1, 11)]
    closes += [closes[-1] - 0.05 * i for i in range(1, 6)]
    vols = [1000.0] * 40 + list(pull_vols)
    lows = [c * 0.995 for c in closes]
    lows[29] = 9.8
    df = make_df(closes, vols=vols, lows=lows)
    if top_drop is not None:
        df.loc[40, "close"] = df.loc[39, "close"] * (1 + top_drop / 100)
        df.loc[40, "volume"] = 2000.0
    return df


def test_non_one_wave_confirmed():
    r = ec.check_non_one_wave(_now_base_df([500.0] * 5))
    assert r["available"] and r["status"] == "confirmed"
    assert r["conditions"]["mild_volume"]["hit"] is True
    assert r["conditions"]["no_top_big_bear"]["hit"] is True
    assert r["conditions"]["pullback_shrink"]["hit"] is True


def test_non_one_wave_revoked_by_top_big_bear():
    # 高点次日 -5% 且量 2x（放量大阴）→ 撤销
    closes = [10.0] * 30 + [10.0 + i * 0.2 for i in range(1, 11)]
    down = closes[-1] * 0.95
    closes += [down, down * 0.99, down * 0.98, down * 0.97, down * 0.96]
    vols = [1000.0] * 40 + [2000.0, 500.0, 500.0, 500.0, 500.0]
    lows = [c * 0.995 for c in closes]
    lows[29] = 9.8
    r = ec.check_non_one_wave(make_df(closes, vols=vols, lows=lows))
    assert r["status"] == "revoked"
    assert r["conditions"]["no_top_big_bear"]["hit"] is False


def test_non_one_wave_insufficient_when_pullback_not_shrinking():
    r = ec.check_non_one_wave(_now_base_df([900.0] * 5))
    assert r["status"] == "insufficient"
    assert r["conditions"]["pullback_shrink"]["hit"] is False


def test_non_one_wave_unavailable_without_segment():
    r = ec.check_non_one_wave(make_df([10.0] * 50))
    assert r["available"] is False and r["status"] == "insufficient"


# ---------- five_day_entry（CZ §十六） ----------

def _five_day_df(last_close_drop=False):
    closes = [10.0 + i * 0.05 for i in range(25)]
    if last_close_drop:
        closes[-1] = closes[-1] - 0.5
    vols = [100.0] * 25
    vols[20] = 150.0          # 7日内单日量 ≥ 前一日×1.45
    vols[-3:] = [100.0, 110.0, 120.0]  # 连续3日放量（递增）
    return make_df(closes, vols=vols)


def test_five_day_entry_hit():
    r = ec.check_five_day_entry(_five_day_df())
    assert r["hit"] is True
    assert all(c["hit"] for c in r["conditions"].values())


def test_five_day_entry_miss_when_below_ma5():
    r = ec.check_five_day_entry(_five_day_df(last_close_drop=True))
    assert r["hit"] is False
    assert r["conditions"]["close_above_ma5"]["hit"] is False


# ---------- volume_sustain（CZ §14.6） ----------

def test_volume_sustain_mainline_confirmed():
    vols = [100.0] * 7 + [1000.0] + [600.0] * 12   # 峰值12日前，后续均值60%≥55%
    r = ec.check_volume_sustain(make_df([10.0] * 20, vols=vols))
    assert r["status"] == "mainline_confirmed"
    assert r["days_since_peak"] == 12
    assert len(r["vol_ratios_last13"]) == 13


def test_volume_sustain_retreat():
    vols = [100.0] * 7 + [1000.0] + [600.0] * 9 + [400.0, 400.0, 400.0]
    r = ec.check_volume_sustain(make_df([10.0] * 20, vols=vols))
    assert r["status"] == "retreat"


def test_volume_sustain_neutral_when_peak_too_recent():
    vols = [100.0] * 16 + [1000.0] + [600.0, 600.0, 600.0]  # 峰值仅3日前
    r = ec.check_volume_sustain(make_df([10.0] * 20, vols=vols))
    assert r["status"] == "neutral"


# ---------- leader_volume（CZ §九） ----------

def test_leader_volume_hit_and_miss():
    vols = [100.0] * 22 + [200.0, 200.0, 200.0]
    assert ec.check_leader_volume(make_df([10.0] * 25, vols=vols))["hit"] is True
    vols[-3:] = [200.0, 150.0, 200.0]
    r = ec.check_leader_volume(make_df([10.0] * 25, vols=vols))
    assert r["hit"] is False and r["available"] is True


# ---------- three_lows / bottom_volume（CZ §九/§14.6，250日口径） ----------

def _cz250_df(today_vol, close_now=11.0):
    closes = [20.0] * 125 + [close_now] * 125
    vols = [1000.0] * 249 + [today_vol]
    return make_df(closes, vols=vols)


def test_three_lows_hit():
    r = ec.check_three_lows(_cz250_df(200.0))
    assert r["available"] and r["hit"] is True
    assert r["conditions"]["low_price"]["hit"] is True
    assert r["conditions"]["low_volume"]["hit"] is True


def test_three_lows_miss_when_drawdown_shallow():
    r = ec.check_three_lows(_cz250_df(200.0, close_now=15.0))  # 回撤约26%<40%
    assert r["hit"] is False
    assert r["conditions"]["low_price"]["hit"] is False


def test_bottom_volume_hit_and_miss():
    assert ec.check_bottom_volume(_cz250_df(2500.0))["hit"] is True
    r = ec.check_bottom_volume(_cz250_df(1500.0))
    assert r["hit"] is False and r["conditions"]["huge_volume"]["hit"] is False


def test_cz_tags_unavailable_below_250_bars():
    df = make_df([10.0] * 100)
    assert ec.check_three_lows(df)["available"] is False
    assert ec.check_bottom_volume(df)["available"] is False


# ---------- repair_signals（B1 §四.2） ----------

def test_repair_signals_volume_shrink_stop_fall():
    closes = [10.0 - i * 0.05 for i in range(30)]
    closes[-1] = closes[-2] * 1.01  # 涨跌幅∈[-2%,+2%]
    vols = [1000.0] * 29 + [500.0]  # 量比 0.5 ≤ 0.7
    r = ec.check_repair_signals(make_df(closes, vols=vols), None)
    assert "volume_shrink_stop_fall" in r["signals"]
    assert r["detail"]["volume_shrink_stop_fall"]["hit"] is True


def test_repair_signals_empty_when_no_repair():
    closes = [10.0 - i * 0.1 for i in range(30)]  # 持续大跌、均量
    r = ec.check_repair_signals(make_df(closes), None)
    assert r["signals"] == []


# ---------- compute_metrics 整合 ----------

def test_compute_metrics_contains_b1cz_fields():
    df = _cz250_df(2500.0)
    m = ec.compute_metrics(df, None)
    for key in ["wave", "weekly_j", "weekly_j_low", "non_one_wave", "repair_signals",
                "five_day_entry", "volume_sustain", "leader_volume",
                "three_lows", "bottom_volume"]:
        assert key in m, f"compute_metrics 缺字段 {key}"
    assert m["bottom_volume"]["hit"] is True


# ---------- P2: 数据源当日一致性（formula_hits 日期交叉校验 + signal_date） ----------

def test_enrich_flags_formula_hits_date_mismatch(monkeypatch):
    # 命中清单是昨日产出、本段目标是今日 → partial + formula_hits_date_mismatch
    hits = {"date": "2026-07-20", "status": "ok",
            "formulas": [{"id": "F1", "hits": [{"code": "600000", "name": "浦发"}]}]}
    dates = pd.date_range(end="2026-07-21", periods=80, freq="B")
    df = pd.DataFrame({
        "date": dates, "open": 10.0, "high": 10.05, "low": 9.95,
        "close": 10.0, "volume": 1000.0, "amount": 0.0,
    })
    # 隔离板块映射，聚焦一致性断言
    monkeypatch.setattr(ec, "build_stock_theme_map", lambda **k: ({}, True))
    result = ec.enrich("2026-07-21", hits_data=hits,
                       ohlcv_loader=lambda c: df.copy(), index_loader=lambda: None,
                       universe_cfg={"j_low_required": False})
    assert result["status"] == "partial"
    assert "formula_hits_date_mismatch:2026-07-20" in result["degraded_reason"]
    assert "signal_date_contract" in result
    assert result["candidates"]
    assert result["candidates"][0]["signal_date"] == "2026-07-21"


def test_enrich_same_day_hits_no_mismatch(monkeypatch):
    hits = {"date": "2026-07-21", "status": "ok",
            "formulas": [{"id": "F1", "hits": [{"code": "600000", "name": "浦发"}]}]}
    dates = pd.date_range(end="2026-07-21", periods=80, freq="B")
    df = pd.DataFrame({
        "date": dates, "open": 10.0, "high": 10.05, "low": 9.95,
        "close": 10.0, "volume": 1000.0, "amount": 0.0,
    })
    monkeypatch.setattr(ec, "build_stock_theme_map", lambda **k: ({}, True))
    result = ec.enrich("2026-07-21", hits_data=hits,
                       ohlcv_loader=lambda c: df.copy(), index_loader=lambda: None,
                       universe_cfg={"j_low_required": False})
    assert "formula_hits_date_mismatch" not in result["degraded_reason"]
    assert result["candidates"][0]["signal_date"] == "2026-07-21"


# ---------- code review 修复回归 ----------

def test_bottom_volume_miss_when_today_makes_new_20d_low():
    # #1：当日刚创 20 日新低（剔除当日的前20日最低被跌破）→ no_new_low=False 不命中
    df = _cz250_df(2500.0)
    df.loc[249, "low"] = df["low"].iloc[-21:-1].min() - 0.5
    r = ec.check_bottom_volume(df)
    assert r["available"] and r["hit"] is False
    assert r["conditions"]["no_new_low"]["hit"] is False


def test_wave_sprint_survives_pullback_after_top():
    # #2：冲刺到顶后回调约 8%（B1 回调时点），段内加速口径仍判 sprint
    closes = [10.0] * 40 + [11.0, 11.0, 12.1, 12.5, 13.5, 14.5, 15.5]
    closes += [15.0, 14.5, 14.26]  # 顶部后回调 ~8%
    vols = [1000.0] * 46 + [3000.0, 800.0, 700.0, 600.0]
    r = ec.detect_wave_type(make_df(closes, vols=vols))
    assert r["wave_type"] == "sprint"
    assert r["detail"]["accel_10d_gain_pct"] >= 25


def test_volume_sustain_daily_breach_not_confirmed():
    # #6：均值达标（约67%>55%）但有单日 40%<55% → 逐日口径不 confirmed
    vols = [100.0] * 7 + [1000.0] + [700.0] * 5 + [400.0] + [700.0] * 6
    r = ec.check_volume_sustain(make_df([10.0] * 20, vols=vols))
    assert r["status"] != "mainline_confirmed"
    assert r["post_mean_ratio"] >= 0.55  # 均值口径本会误判
    assert r["post_min_ratio"] < 0.55


def test_limit_up_mask_ignores_zero_close_bars():
    # #11a：close=0 脏数据 bar 不产生假性涨停（11/0=inf 不得计入）
    closes = [10.0] * 40 + [0.0, 11.0, 11.0, 12.1, 12.5, 13.5, 14.5, 15.5]
    r = ec.detect_wave_type(make_df(closes))
    assert r["detail"]["limit_up_count_20d"] == 1  # 仅 12.1/11.0=+10% 一次


def test_distribution_unavailable_when_vol_ma20_near_zero():
    # #11b：全零成交量 → vol_ma20 近零 → 派发检测器 available=False
    r = ec.detect_distribution(make_df([10.0] * 40, vols=[0.0] * 40), code="600000")
    assert r["available"] is False
    assert r["hits"] == []


def test_enrich_metrics_error_excluded_not_abort():
    # #5：单股 compute_metrics 抛错计入 excluded，不中断批次
    date = "2026-07-21"
    dates = pd.date_range(end="2026-07-21", periods=60, freq="B")
    good = pd.DataFrame({
        "date": dates, "open": [10.0] * 60, "high": [10.1] * 60, "low": [9.9] * 60,
        "close": [10.0] * 60, "volume": [1000.0] * 60, "amount": [0.0] * 60,
    })
    bad = pd.DataFrame({"date": dates, "open": [10.0] * 60})  # 缺 close/high/low/volume
    hits = {"date": date, "status": "ok", "formulas": [{"id": "F", "hits": [
        {"code": "900001", "name": "好股票"}, {"code": "900002", "name": "坏数据"},
    ]}]}
    loader = lambda c: good if c == "900001" else bad
    r = ec.enrich(date, hits_data=hits, ohlcv_loader=loader, index_loader=lambda: None,
                  universe_cfg={"exclude_bj": True, "exclude_st": True, "min_list_days": 60,
                            "j_low_required": False})
    assert [c["code"] for c in r["candidates"]] == ["900001"]
    assert len(r["excluded"]) == 1
    assert r["excluded"][0]["code"] == "900002"
    assert r["excluded"][0]["reason"].startswith("metrics_error:")
