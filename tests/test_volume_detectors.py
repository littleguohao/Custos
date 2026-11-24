"""底部放量合并候选。"""


def test_base_volume_levels_from_stage_low_v0570():
    """owner 2026-11-24：点火 / 龙头量能 / 底部巨量合并为底部放量，按程度 1~3 星，当根形态不管。"""
    import numpy as np
    import pandas as pd

    from custos.core.factors import volume_detectors as vd

    n = 200
    close = np.r_[
        np.linspace(20, 10, n - 12),
        [10.2, 10.8, 11.5, 12.0, 11.8, 11.6, 11.5, 11.4, 11.3, 11.2, 11.1, 11.0],
    ]
    vol = np.full(n, 1000.0)
    df = lambda v: pd.DataFrame(
        {
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": v,
        }
    )
    assert vd.check_base_volume(df(vol))["level"] == 0
    v1 = vol.copy()
    v1[n - 10] = 1600  # 单根放量阳线（+5.9%）
    assert vd.check_base_volume(df(v1))["level"] == 1
    v2 = vol.copy()
    v2[n - 11 : n - 8] = 1800  # 连续 3 根 ≥ 地量 1.7 倍
    assert vd.check_base_volume(df(v2))["level"] == 2
    v3 = v2.copy()
    v3[n - 10] = 5000  # 巨量
    r = vd.check_base_volume(df(v3))
    assert r["level"] == 3 and r["stars"] == "⭐⭐⭐" and r["stage_low_i"] == n - 13
