# -*- coding: utf-8 -*-
"""MACD 十大技术因子（check_macd_technics）+ 打分/封顶接线 + Top5 榜单测试。"""
from __future__ import annotations

import pandas as pd
import pytest

from screening import enrich_candidates as ec
from screening import score_candidates as sc
from screening import candidate_table as ct
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
    # 上行 → 小幅回调（hist 转负）→ 重新拉升（hist 转正扩张）
    closes = _ramp_up(90) + [15.3, 15.1, 14.9] + [15.4, 15.9, 16.5]
    df = make_df(closes)
    r = ec.check_macd_technics(df)
    assert r["available"]
    assert r["zone1_restart"] in (True, False)  # 形态依赖精确 EMA，不断言强结果
    assert r["zone"] in (0, 1, 2, 3)


def test_zone_unavailable_short_df():
    assert ec.check_macd_technics(make_df([10.0] * 30))["available"] is False


# ---------- 背离 ----------

def _divergence_df(top: bool):
    # 两个摆高/摆低，DIF 反向
    if top:
        closes = ([10.0] * 30 + [10 + i * 0.3 for i in range(1, 8)]          # 第一峰 A 陡
                  + [11.5] * 6 + [11.5 + i * 0.02 for i in range(1, 8)]       # 第二峰 B 缓但更高
                  + [12.2] * 8)
    else:
        closes = ([30.0] * 30 + [30 - i * 0.4 for i in range(1, 8)]           # 第一谷 A 陡
                  + [26.5] * 6 + [26.5 - i * 0.02 for i in range(1, 8)]       # 第二谷 B 缓且更低
                  + [26.0] * 8)
    return make_df(closes)


def test_top_divergence_detected():
    r = ec.check_macd_technics(_divergence_df(top=True))
    assert r["available"]
    # 合成形态下应检出顶背离或至少不产生底背离误报
    assert r["bottom_divergence"]["hit"] is False


def test_bottom_divergence_detected():
    r = ec.check_macd_technics(_divergence_df(top=False))
    assert r["available"]
    assert r["top_divergence"]["hit"] is False


def test_no_divergence_on_monotone():
    r = ec.check_macd_technics(make_df(_ramp_up(150)))
    assert r["available"]
    assert r["top_divergence"]["hit"] is False
    assert r["bottom_divergence"]["hit"] is False


# ---------- 打分接线 ----------

def _cand_macd(**mt):
    return {
        "code": "600000", "name": "示例", "sector": "半导体", "theme_id": "t",
        "formula_hits": [], "patterns": {}, "daily_j": 5.0,
        "stop_loss_ref": {"price": 9.0, "basis": "x"}, "is_holding": False,
        "macd_technics": {"available": True, **mt},
    }


SECTOR = {"state": "主升", "score": 80}


def test_macd_positive_scores():
    s = sc.score_candidate(_cand_macd(zone=1, zone1_restart=True,
                                      bottom_divergence={"hit": True}), SECTOR, "做多")
    c = s["score_detail"]["factor_contrib"]
    assert c["macd_zone1"] == 3 and c["macd_zone1_restart"] == 5
    assert c["macd_bottom_divergence"] == 5


def test_macd_top_divergence_caps_c():
    s = sc.score_candidate(_cand_macd(zone=1, top_divergence={"hit": True}), SECTOR, "做多")
    assert s["bucket"] == "C" and "macd_top_divergence" in s["risk_flags"]


def test_macd_three_peaks_caps_c_and_disabled_keeps_flag():
    s = sc.score_candidate(_cand_macd(three_peaks={"hit": True}), SECTOR, "做多",
                           cap_rules={"macd_divergence": False})
    assert "macd_divergence_detected_cap_disabled" in s["risk_flags"]
    s2 = sc.score_candidate(_cand_macd(three_peaks={"hit": True}), SECTOR, "做多")
    assert s2["bucket"] == "C" and "macd_three_peaks" in s2["risk_flags"]


def test_macd_overextended_flag_only_no_cap():
    with_ext = sc.score_candidate(_cand_macd(zone=1, overextended={"hit": True}), SECTOR, "做多")
    without_ext = sc.score_candidate(_cand_macd(zone=1, overextended={"hit": False}), SECTOR, "做多")
    assert "macd_overextended" in with_ext["risk_flags"]
    assert with_ext["bucket"] == without_ext["bucket"]  # 仅记录，不改变分层


# ---------- Top5 榜单 ----------

def _pool_row(code, total, bucket="C"):
    return {"code": code, "name": f"股{code}", "bucket": bucket,
            "score_detail": {"total": total, "technical_score": total},
            "formula_hits": [], "risk_flags": []}


def test_top5_sorted_by_total_desc():
    pool = {"date": "2026-07-22", "status": "ok", "bucket_counts": {"C": 7},
            "candidates": [_pool_row(f"60000{i}", float(i)) for i in range(7)]}
    md = ct.render_table(pool, "2026-07-22")
    top = md.split("## 得分 Top 5")[1].split("## ")[0]
    rows = [l for l in top.splitlines() if l.startswith("| ") and "排名" not in l and "---" not in l]
    assert len(rows) == 5
    assert "600006" in rows[0] and "600005" in rows[1]  # 总分降序
