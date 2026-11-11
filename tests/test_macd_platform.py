# -*- coding: utf-8 -*-
"""v0.507 MACD 平台结构（owner 2026-11-10 通达信公式转译，候选，不进 live analyze）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from custos.core.indicators import macd_series
from custos.pipeline.market_timing import technical_monitor as tm


def _df(close, open_=None):
    c = np.asarray(close, dtype=float)
    o = np.asarray(open_, dtype=float) if open_ is not None else c * 1.002
    return pd.DataFrame(
        {
            "date": pd.date_range("2023-01-02", periods=len(c), freq="B"),
            "open": o,
            "high": np.maximum(o, c) * 1.01,
            "low": np.minimum(o, c) * 0.99,
            "close": c,
            "volume": np.full(len(c), 1e6),
            "amount": c * 1e6,
        }
    )


def _wave(n=400, seed=0, drift=0.0):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    c = 10 * np.exp(
        drift * t + 0.08 * np.sin(t / 9.0) + np.cumsum(rng.normal(0, 0.004, n))
    )
    o = c * (1 + rng.normal(0, 0.01, n))
    return _df(c, o)


def test_segments_match_formula_definition():
    df = _wave()
    segs = tm.macd_platform_segments(df)
    _d, _e, h = macd_series(df["close"])
    h = h.to_numpy()
    o, c = df["open"].to_numpy(), df["close"].to_numpy()
    assert segs and all(s["kind"] in ("H", "L") for s in segs)
    for s in segs:
        a = int(df.index[df["date"].dt.strftime("%Y-%m-%d") == s["start"]][0])
        b = int(df.index[df["date"].dt.strftime("%Y-%m-%d") == s["end"]][0])
        sign = 1 if s["kind"] == "H" else -1
        assert all(np.sign(h[k]) == sign for k in range(a, b + 1))
        bears = [k for k in range(a, b + 1) if c[k] < o[k]]
        exp = max(o[k] for k in bears) if sign > 0 else min(c[k] for k in bears)
        assert abs(s["price"] - round(exp, 4)) < 1e-9
    assert segs[-1]["ongoing"] in (True, False)


def test_segment_without_bearish_candle_skipped():
    df = _wave()
    df["open"] = df["close"] * 0.99  # 全阳线 ⇒ 每段都无阴线 ⇒ 全部跳过
    assert tm.macd_platform_segments(df) == []
    assert tm.macd_platform_structure(df)["available"] is False


def test_states_up_down_and_breach():
    up = tm.macd_platform_structure(_wave(seed=1, drift=0.004))
    down = tm.macd_platform_structure(_wave(seed=1, drift=-0.004))
    assert up["available"] and up["state"] == "上升"
    assert down["available"] and down["state"] == "下降"
    assert (
        up["L3"]["price"] > up["L2"]["price"] and up["H3"]["price"] > up["H2"]["price"]
    )
    assert up["transition"] in ("延续", f"由{up['prior_state']}转")
    df = _wave(seed=1, drift=0.004)
    st = tm.macd_platform_structure(df)
    df.loc[len(df) - 1, "close"] = st["L3"]["price"] * 0.9  # 末根收盘砸穿最新平台底 L3
    after = tm.macd_platform_structure(df)
    assert after["state"] != "上升" or after["breached"]


def test_asof_prefix_consistent():
    """as-of：前缀上算出的已完结段与全序列同段完全一致（只有末段 ongoing 可能不同）。"""
    df = _wave(seed=2)
    full = tm.macd_platform_segments(df)
    for k in (180, 250, 330):
        pre = tm.macd_platform_segments(df.iloc[:k])
        done = [s for s in pre if not s["ongoing"]]
        assert done == [s for s in full if s["end"] <= done[-1]["end"]][: len(done)]


def test_not_in_live_analyze():
    an = tm.analyze(_wave(), "600000")
    assert "n_structure_macd" not in an  # 候选不进 live 输出


def _seg(kind, price, idx):
    return {"kind": kind, "price": price, "idx": idx}


def test_backward_points_merge_same_kind_v0512():
    """owner 2026-11-11：往回交替匹配，同类相邻合并（L 取更低、H 取更高），凑满三组即停。"""
    segs = [
        _seg("L", 9.0, 0),
        _seg("H", 12.0, 1),
        _seg("L", 10.0, 2),
        _seg("L", 9.5, 3),
        _seg("H", 13.0, 4),
        _seg("H", 13.5, 5),
        _seg("L", 11.0, 6),
        _seg("H", 14.0, 7),
    ]
    pts = tm.macd_platform_points(segs, 3)
    assert [(p["kind"], p["price"]) for p in pts] == [
        ("H", 14.0),
        ("L", 11.0),
        ("H", 13.5),
        ("L", 9.5),
        ("H", 12.0),
        ("L", 9.0),
    ]
    # 并列取较新者（往回先见到的较新点保留）
    tie = tm.macd_platform_points([_seg("L", 9.0, 0), _seg("L", 9.0, 1)], 3)
    assert tie == [_seg("L", 9.0, 1)]


def test_state_and_transition_from_three_groups_v0512(monkeypatch):
    """最近两组定状态，第三组判延续 / 转折。"""
    import pandas as pd

    segs = [
        _seg("L", 10.0, 0),
        _seg("H", 12.0, 1),
        _seg("L", 9.0, 2),
        _seg("H", 11.0, 3),
        _seg("L", 9.5, 4),
        _seg("H", 11.5, 5),
    ]
    monkeypatch.setattr(tm, "macd_platform_segments", lambda df: segs)
    df = pd.DataFrame({"close": [10.0] * 8})
    st = tm.macd_platform_structure(df)
    assert st["state"] == "上升" and st["prior_state"] == "下降"
    assert st["transition"] == "由下降转"
    assert [st[k]["price"] for k in ("L1", "L2", "L3")] == [10.0, 9.0, 9.5]
    monkeypatch.setattr(tm, "macd_platform_segments", lambda df: segs[2:])
    assert tm.macd_platform_structure(df)["available"] is False  # 只剩两组
