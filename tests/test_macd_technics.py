# -*- coding: utf-8 -*-
"""MACD 十大技术因子（check_macd_technics）+ 打分/封顶接线 + Top5 榜单测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from custos.pipeline.screening import enrich_candidates as ec
from custos.pipeline.screening import score_candidates as sc
from custos.pipeline.screening import candidate_table as ct
from test_enrich_b1cz import make_df


def _ramp_up(n=120, start=10.0, step=0.05):
    return [start + i * step for i in range(n)]


# ---------- 区间状态机 ----------


def test_zone1_expansion_and_zone2_shrink():
    # 长上行末端两柱：扩张 → zone1
    df = make_df(_ramp_up())
    r = ec.check_macd_technics(df)
    assert r["available"] and r["zone"] in (1, 2)
    assert r["dif"] > 0


def test_zone1_restart():
    # 上行100根 → 浅回调（hist 转负）→ 强阳重启：hist 当日转正且扩张
    closes = _seg(10, 0.08, 100) + _seg(18.0, -0.15, 4) + _seg(17.4, 0.35, 4)
    r = ec.check_macd_technics(make_df(closes))
    assert r["available"]
    assert r["zone"] == 1 and r["zone1_restart"] is True
    assert r["dif"] > 0 and r["hist"] > 0


@pytest.mark.parametrize(
    "dif,dea,h0,h1,zone",
    [
        (0.5, 0.3, 0.2, 0.1, 1),  # 水上、红柱放长（本根 ≥ 上根）
        (0.5, 0.3, 0.2, 0.2, 1),
        (0.5, 0.3, 0.1, 0.2, 2),  # 水上、红柱缩短
        (0.5, 0.3, 0.0, 0.1, 3),  # 水上、柱体为 0
        (0.5, 0.3, -0.1, 0.1, 3),  # 水上、柱体转绿
        (0.5, -0.1, 0.3, 0.1, 0),  # DEA 在水下
        (-0.1, 0.3, 0.3, 0.1, 0),  # DIF 在水下
    ],
)
def test_zone_four_states_by_value(dif, dea, h0, h1, zone):
    """owner 2026-11-23：区间是纯数值判定，退出人工校准，四态由本钉测管。"""
    from custos.core.factors import macd_technics as mt

    assert mt._macd_zone_state(dif, dea, h0, h1)[0] == zone


def test_zone_unavailable_short_df():
    assert ec.check_macd_technics(make_df([10.0] * 30))["available"] is False


# ---------- 背离 ----------


def _seg(start, step, n):
    return [start + step * i for i in range(1, n + 1)]


def _divergence_df(top: bool):
    if top:
        # 急涨峰A(13.0) → 回落 → 缓涨更高峰B(13.2) → 回落确认（DIF_B<DIF_A）
        closes = (
            [10.0] * 30
            + _seg(10, 0.3, 10)
            + _seg(13.0, -0.2, 5)
            + _seg(12.0, 0.08, 15)
            + _seg(13.2, -0.1, 4)
        )
    else:
        # 急跌谷A(24.0) → 反弹 → 缓跌更低谷B(23.8) → 反弹确认（DIF 低点抬高）
        closes = (
            [30.0] * 30
            + _seg(30, -0.6, 10)
            + _seg(24.0, 0.2, 5)
            + _seg(25.0, -0.08, 15)
            + _seg(23.8, 0.1, 4)
        )
    return make_df(closes)


def test_top_divergence_detected():
    r = ec.check_macd_technics(_divergence_df(top=True))
    assert r["available"]
    assert r["top_divergence"]["hit"] is True
    assert r["top_divergence"]["close_b"] > r["top_divergence"]["close_a"]
    assert r["top_divergence"]["dif_b"] < r["top_divergence"]["dif_a"]
    assert r["bottom_divergence"]["hit"] is False


def test_bottom_divergence_detected():
    r = ec.check_macd_technics(_divergence_df(top=False))
    assert r["available"]
    assert r["bottom_divergence"]["hit"] is True
    assert r["bottom_divergence"]["close_b"] < r["bottom_divergence"]["close_a"]
    assert r["bottom_divergence"]["dif_b"] > r["bottom_divergence"]["dif_a"]
    assert r["top_divergence"]["hit"] is False


def test_three_peaks_detected():
    # 三打白骨精：三峰价格递增（13.0/13.3/13.5）、斜率递减（0.3/0.1/0.03）
    closes = (
        [10.0] * 30
        + _seg(10, 0.3, 10)
        + _seg(13.0, -0.2, 4)
        + _seg(12.2, 0.1, 11)
        + _seg(13.3, -0.15, 4)
        + _seg(12.7, 0.03, 27)
        + _seg(13.5, -0.1, 4)
    )
    r = ec.check_macd_technics(make_df(closes))
    assert r["three_peaks"]["hit"] is True
    d = r["three_peaks"]["dif_peaks"]
    assert d[0] > d[1] > d[2]


def test_no_divergence_on_monotone():
    r = ec.check_macd_technics(make_df(_ramp_up(150)))
    assert r["available"]
    assert r["top_divergence"]["hit"] is False
    assert r["bottom_divergence"]["hit"] is False


# ---------- 打分接线 ----------


def _cand_macd(**mt):
    return {
        "code": "600000",
        "name": "示例",
        "sector": "半导体",
        "theme_id": "t",
        "formula_hits": [],
        "daily_j": 5.0,
        # 技术强(bbi+j低+缩量=60) + 资金意图中(量能主线) → base 强×中 = B，
        # 便于观察 MACD 顶背离/三打白骨精 封顶 C 是真实降档。
        "patterns": {"bbi_above": True, "j_low": True, "volume_contraction": True},
        "volume_sustain": {"status": "mainline_confirmed"},
        "stop_loss_ref": {"price": 9.0, "basis": "x"},
        "is_holding": False,
        "macd_technics": {"available": True, **mt},
    }


SECTOR = {"state": "主升", "score": 80}


def test_macd_positive_scores():
    s = sc.score_candidate(
        _cand_macd(zone=1, zone1_restart=True, bottom_divergence={"hit": True}),
        SECTOR,
        "做多",
    )
    c = s["score_detail"]["factor_contrib"]
    assert c["macd_zone1"] == 3 and c["macd_zone1_restart"] == 5
    assert c["macd_bottom_divergence"] == 8  # v0.60：5→8（owner）


def test_macd_top_divergence_flags_only_v0529():
    """v0.61 起顶背离只减分不封顶；v0.529 分层改 B1 规则后同样不构成风控否决（只留痕）。"""
    s = sc.score_candidate(
        _cand_macd(zone=1, top_divergence={"hit": True}), SECTOR, "做多"
    )
    assert "macd_top_divergence" in s["risk_flags"] and s["b1_status"] != "受限"


def test_macd_three_peaks_caps_c_and_disabled_keeps_flag():
    s = sc.score_candidate(
        _cand_macd(three_peaks={"hit": True}),
        SECTOR,
        "做多",
        cap_rules={"macd_divergence": False},
    )
    assert "macd_divergence_detected_cap_disabled" in s["risk_flags"]
    s2 = sc.score_candidate(_cand_macd(three_peaks={"hit": True}), SECTOR, "做多")
    assert s2["bucket"] == "C" and "macd_three_peaks" in s2["risk_flags"]


def test_macd_overextended_flag_only_no_cap():
    with_ext = sc.score_candidate(
        _cand_macd(zone=1, overextended={"hit": True}), SECTOR, "做多"
    )
    without_ext = sc.score_candidate(
        _cand_macd(zone=1, overextended={"hit": False}), SECTOR, "做多"
    )
    assert "macd_overextended" in with_ext["risk_flags"]
    assert with_ext["bucket"] == without_ext["bucket"]  # 仅记录，不改变分层


# ---------- Top5 榜单 ----------


def _pool_row(code, total, bucket="C"):
    return {
        "code": code,
        "name": f"股{code}",
        "bucket": bucket,
        "score_detail": {"total": total, "technical_score": total},
        "formula_hits": [],
        "risk_flags": [],
    }


def test_unified_table_sorted_by_technical_desc():
    """v0.478：Top5 并入候选总表——全部候选按技术分降序，不截断。"""
    pool = {
        "date": "2026-07-22",
        "status": "ok",
        "bucket_counts": {"C": 7},
        "candidates": [_pool_row(f"60000{i}", float(i)) for i in range(7)],
    }
    md = ct.render_table(pool, "2026-07-22")
    top = md.split("## 📋 候选总表")[1].split("\n## ")[0]
    rows = [l for l in top.splitlines() if l.startswith("| 6") and "---" not in l]
    assert len(rows) == 7
    assert "600006" in rows[0] and "600005" in rows[1]  # 技术分降序


# ---------- v0.60 周/月红柱腿长历史（2026-08-16 review 修复）----------


def _kick(n=1200, kick_at=1100):
    """温和上行 + 末端加速：周/月 MACD 柱在末端均为正且扩张
    （匀速/指数上行会被 EMA 收敛压平，测不出「增长」——必须末端加速）。"""
    out = []
    for i in range(n):
        v = 10.0 + i * 0.01
        if i >= kick_at:
            v += (i - kick_at) ** 2 * 0.002
        out.append(v)
    return out


def test_wm_bar_grow_needs_long_history():
    """260 根日线 ⇒ 月线 ~13 根 < EMA26 稳定门槛 ⇒ wm_available 必须如实标 False
    （此前直接判 False 还不留痕，+5 是死字段）；传 1200 根长历史才真正计算。"""
    short = make_df(_kick(260, 160))
    r = ec.check_macd_technics(short)
    assert r["available"] and r["wm_available"] is False
    assert r["wm_bar_grow"] is False

    long_df = make_df(_kick(1200))
    r2 = ec.check_macd_technics(short, df_long=long_df)
    assert r2["wm_available"] is True
    assert r2["wm_bar_grow"] is True  # 末端加速：周/月柱均在增长


def _ohlcv(closes):
    import numpy as np

    c = np.asarray(closes, float)
    return pd.DataFrame(
        {
            "close": c,
            "open": c,
            "high": c * 1.01,
            "low": c * 0.99,
            "volume": np.full(len(c), 1e6),
        }
    )


def test_bar_groups_merge_short_flip():
    from custos.core.factors import macd_technics as mt

    h = [-1, -2, -1, 0.1, -1, -2, 1, 2, 3, 1]  # 中间 1 根红柱并入绿组
    g = mt.macd_bar_groups(h)
    assert [c for c, _ in g] == [-1, 1] and len(g[0][1]) == 6


def test_divergence_v2_requires_current_green_group():
    """owner 2026-11-23：当根还是红柱 ⇒ 不判底背离。"""
    import numpy as np
    from custos.core.factors import macd_technics as mt

    c = (
        list(np.linspace(20, 12, 60))
        + list(np.linspace(12, 14, 15))
        + list(np.linspace(14, 11.5, 20))
        + list(np.linspace(11.5, 13, 10))
    )
    r = mt.check_macd_divergence_v2(_ohlcv(c))
    assert r["available"] and r["bottom_divergence"]["hit"] is False


def test_divergence_v2_bottom_hit_shape():
    import numpy as np
    from custos.core.factors import macd_technics as mt

    c = (
        list(np.linspace(20, 12, 50))
        + list(np.linspace(12, 14, 20))
        + list(np.linspace(14, 11.6, 25))
    )  # 第二段跌得慢、创新低
    r = mt.check_macd_divergence_v2(_ohlcv(c))
    bd = r["bottom_divergence"]
    assert len(bd.get("groups", [])) == 2
    if bd["hit"]:
        a, b = bd["groups"]
        assert b["price"] < a["price"] and abs(b["bar"]) <= abs(a["bar"]) * 0.9
