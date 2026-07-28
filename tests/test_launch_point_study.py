# -*- coding: utf-8 -*-
"""起涨点 vs 0AMV 研究(launch_point_study)测试。"""
import pandas as pd

from screening import launch_point_study as lp


def test_window_return():
    dates = [f"2025-01-{d:02d}" for d in range(1, 7)]
    closes = [10, 9, 8, 12, 15, 11]
    assert abs(lp.window_return(dates, closes, "2025-01-01", "2025-01-06") - 0.1) < 1e-9
    assert lp.window_return(dates, closes, "2025-02-01", "2025-02-06") is None   # 区间外


def test_find_launch_picks_max_forward_gain():
    dates = [f"2025-01-{d:02d}" for d in range(1, 7)]
    closes = [10, 9, 8, 12, 15, 11]
    r = lp.find_launch(dates, closes, [1, 2], "2025-01-06")   # idx2(收8)前向到峰15 收益更大
    assert r["date"] == "2025-01-03" and abs(r["fwd_gain"] - (15 / 8 - 1)) < 1e-6


def test_regime_at_and_lead():
    regime = {"2025-01-01": "空头", "2025-01-02": "空头", "2025-01-03": "做多", "2025-01-06": "做多"}
    r = lp.regime_at_and_lead(regime, "2025-01-01")
    assert r["regime"] == "空头" and r["lead_days_to_long"] == 2   # 距 01-03 做多 2 个 regime 日
    r2 = lp.regime_at_and_lead(regime, "2025-01-03")
    assert r2["regime"] == "做多" and r2["lead_days_to_long"] is None


def test_analyze_smoke():
    dates = pd.date_range("2025-01-01", periods=60, freq="B")
    ds = [str(d)[:10] for d in dates]
    # 赢家:先跌后大涨;起涨点应落在早期(空头段)
    close = [20 - 0.3 * i for i in range(20)] + [14 + 0.6 * i for i in range(40)]
    win = pd.DataFrame({"date": dates, "open": close, "high": [c * 1.02 for c in close],
                        "low": [c * 0.98 for c in close], "close": close, "volume": [1e6] * 60})
    flat = pd.DataFrame({"date": dates, "open": [10] * 60, "high": [10.1] * 60,
                         "low": [9.9] * 60, "close": [10] * 60, "volume": [1e6] * 60})
    regime = {ds[i]: ("空头" if i < 25 else "做多") for i in range(60)}
    res = lp.analyze({"WIN": win, "FLAT": flat}, regime, ds[0], ds[-1],
                     entry_gate=lambda s: True, top_pct=50, buffer_days=0, min_bars=40)
    assert res["n_winners"] >= 1 and res["n_launches"] >= 1
    assert set(res["by_regime"]) <= {"做多", "空头", "中性", "未知"}
