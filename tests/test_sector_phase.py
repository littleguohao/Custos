# -*- coding: utf-8 -*-
"""板块相位(sector_phase)测试。"""
import numpy as np

from screening import sector_phase as sp


def test_phase_unavailable_short():
    assert sp.compute_sector_phase([10.0] * 10)["available"] is False


def test_phase_favorable_uptrend():
    # 稳步上行 → DIF>0、无顶背离 → 有利
    close = list(10 + 0.15 * np.arange(120))
    r = sp.compute_sector_phase(close)
    assert r["available"] and r["above_zero"] and r["favorable"] and not r["exhausted"]


def test_phase_downtrend_not_favorable():
    close = list(30 - 0.15 * np.arange(120))     # 单边下行 → DIF<0
    r = sp.compute_sector_phase(close)
    assert r["available"] and not r["above_zero"] and not r["favorable"]
    assert r["phase"] == "水下/调整"


def test_phase_top_divergence_filtered():
    # 顶背离检测(用较长干净序列+显式 lookback,避免 MACD 预热/回看窗口干扰):
    # 峰1陡(DIF高)→深回调→峰2缓升创新高(DIF低);末尾回调右确认峰2。
    up1 = 10 + 0.30 * np.arange(60)                # 陡升60根,MACD充分预热,峰≈27.7
    pull1 = up1[-1] - 0.25 * np.arange(1, 21)      # 深回调→≈22.7(DIF大降)
    up2 = pull1[-1] + 0.10 * np.arange(1, 61)      # 缓升创新高≈28.7(DIF更低)
    pull2 = up2[-1] - 0.30 * np.arange(1, 9)       # 末尾回调 → 确认峰2
    close = list(up1) + list(pull1) + list(up2) + list(pull2)
    r = sp.compute_sector_phase(close, lookback=200)
    assert r["available"] and r["above_zero"]
    assert r["exhausted"] and not r["favorable"]   # 顶背离/三打 → 过滤


def test_favorable_series_causal_and_gate(tmp_path):
    import numpy as np
    import pandas as pd
    # 有利板块:稳步上行(DIF>0,无顶背离);不利板块:单边下行(DIF<0)
    n = 130
    dates = [str(d)[:10] for d in pd.date_range("2022-01-03", periods=n, freq="B")]
    up = list(10 + 0.15 * np.arange(n))
    down = list(30 - 0.15 * np.arange(n))
    (tmp_path / "880201.SH.csv").write_text(
        "date,close\n" + "\n".join(f"{d},{c}" for d, c in zip(dates, up)), encoding="utf-8")
    (tmp_path / "880900.SH.csv").write_text(
        "date,close\n" + "\n".join(f"{d},{c}" for d, c in zip(dates, down)), encoding="utf-8")
    fav = sp.favorable_series(dates, up)
    assert fav[dates[-1]] is True and all(v in (True, False) for v in fav.values())   # 因果布尔
    members = {"880201.SH": ["600000"], "880900.SH": ["000002"]}
    gate = sp.load_sector_gate(tmp_path, members)
    last = dates[-1]
    assert gate("600000", last) is True     # 有利板块成员 → 放行
    assert gate("000002", last) is False    # 不利板块(DIF<0)成员 → 拦截
    assert gate("999999", last) is True     # 未分类 → 不过滤
