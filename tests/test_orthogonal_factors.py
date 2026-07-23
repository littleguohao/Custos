# -*- coding: utf-8 -*-
"""方向A 正交因子测试：流动性(成交额底线) + 资金流向(个股/板块主力净流入)。"""
import pandas as pd

from screening import enrich_candidates as ec
from screening import score_candidates as sc


def _df(amount, n=30):
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="B"),
        "open": [10.0] * n, "high": [10.1] * n, "low": [9.9] * n, "close": [10.0] * n,
        "volume": [1000.0] * n, "amount": [float(amount)] * n,
    })


# ---------- 流动性 ----------

def test_check_liquidity_value():
    r = ec.check_liquidity(_df(8e7))   # 8000万/日
    assert r["available"] and abs(r["avg_amount_yi"] - 0.8) < 1e-6


def test_check_liquidity_unavailable_no_amount():
    df = _df(1e8).drop(columns=["amount"])
    assert ec.check_liquidity(df)["available"] is False


# ---------- 资金流向 ----------

def test_fund_flow_of_in_rank_and_sector():
    ff = {"available": True,
          "by_code": {"600000": {"main_net_inflow": 1.2e8, "main_net_pct": 3.1}},
          "sectors": [{"name": "半导体", "main_net_inflow": 5e8, "main_net_pct": 2.0},
                      {"name": "白酒", "main_net_inflow": -3e8}]}
    r = ec.fund_flow_of("600000", "半导体/芯片/存储", ff)
    assert r["available"] and r["in_rank_positive"] and r["sector_inflow_positive"]
    assert r["sector_matched"] == "半导体"
    r2 = ec.fund_flow_of("000001", "白酒", ff)   # 不在榜 + 板块净流出
    assert r2["in_rank"] is False and r2["sector_inflow_positive"] is False


def test_fund_flow_missing_degrades():
    assert ec.fund_flow_of("600000", "半导体", {"available": False})["available"] is False
    assert ec.load_fund_flow("1999-01-01")["available"] is False   # 无文件


def test_capital_intent_includes_fund_flow():
    lvl, score, detail = sc.capital_intent_strength(
        {"patterns": {}, "fund_flow": {"available": True, "in_rank_positive": True,
                                       "sector_inflow_positive": False}})
    assert detail["fund_flow_inflow"]["hit"] is True and detail["fund_flow_inflow"]["points"] == 2


# ---------- 流动性底线 flag / 可配封顶 ----------

def _strong_cand(**extra):
    c = {"code": "600000", "name": "示例", "sector": "半导体",
         "patterns": {"bbi_above": True, "j_low": True, "volume_contraction": True,
                      "reversal_k_candidate": True, "relative_strength_strong": True},
         "daily_j": 10.0, "stop_loss_ref": {"price": 10.0},
         "b1_ignition": {"hit": True},
         "zhixing": {"available": True, "qsx_gt_dks": True, "close_above_qsx": True},
         "volume_sustain": {"status": "mainline_confirmed"}}
    c.update(extra)
    return c


SECTOR = {"state": "主升", "score": 80}


def test_low_liquidity_flag_default_no_cap():
    s = sc.score_candidate(_strong_cand(liquidity={"available": True, "avg_amount_yi": 0.1}), SECTOR, "做多")
    assert "low_liquidity" in s["risk_flags"]
    assert s["bucket"] == "A"   # 默认 liquidity_floor 关 → 仅flag、不降档


def test_low_liquidity_caps_when_enabled():
    s = sc.score_candidate(_strong_cand(liquidity={"available": True, "avg_amount_yi": 0.1}),
                           SECTOR, "做多", cap_rules={"liquidity_floor": True})
    assert "low_liquidity" in s["risk_flags"] and s["bucket"] == "C"


def test_ample_liquidity_no_flag():
    s = sc.score_candidate(_strong_cand(liquidity={"available": True, "avg_amount_yi": 3.0}), SECTOR, "做多")
    assert "low_liquidity" not in s["risk_flags"] and s["bucket"] == "A"
