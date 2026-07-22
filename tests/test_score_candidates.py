# -*- coding: utf-8 -*-
"""Tests for screening.score_candidates: resonance grid, caps, StockPool contract."""
import pytest

from screening import score_candidates as sc


def _cand(patterns, stop_price=10.0, code="600000"):
    return {
        "code": code,
        "name": "示例",
        "sector": "半导体/芯片/存储/封测",
        "theme_id": "semiconductor_chip_memory_packaging",
        "formula_hits": ["UPN_3"],
        "patterns": patterns,
        "daily_j": 10.0 if patterns.get("j_low") else 55.0,
        "stop_loss_ref": {"price": stop_price, "basis": "近10日最低价"} if stop_price else None,
        "is_holding": False,
    }


# 技术面层级构造（反转K为复合信号，取代 j_low+volume_contraction 子项分）：
# 强=bbi+反转K+强RS(65分)；中=bbi+j_low(45分)；弱=无标签(0分)
TECH_STRONG = {"bbi_above": True, "j_low": True, "volume_contraction": True,
               "reversal_k_candidate": True, "relative_strength_strong": True}
TECH_MID = {"bbi_above": True, "j_low": True, "volume_contraction": False,
            "reversal_k_candidate": False, "relative_strength_strong": False}
TECH_WEAK = {"bbi_above": False, "j_low": False, "volume_contraction": False,
             "reversal_k_candidate": False, "relative_strength_strong": False}

SECTOR_STRONG = {"state": "主升", "score": 80, "sector": "半导体/芯片/存储/封测"}
SECTOR_MID = {"state": "震荡", "score": 50, "sector": "半导体/芯片/存储/封测"}
SECTOR_WEAK = {"state": "退潮", "score": 30, "sector": "半导体/芯片/存储/封测"}

GRID = [
    (TECH_STRONG, SECTOR_STRONG, "A"),
    (TECH_STRONG, SECTOR_MID, "B"),
    (TECH_STRONG, SECTOR_WEAK, "C"),
    (TECH_STRONG, None, "C"),           # 板块未知：矩阵 C，且不进 A
    (TECH_MID, SECTOR_STRONG, "B"),
    (TECH_MID, SECTOR_MID, "C"),
    (TECH_MID, SECTOR_WEAK, "D"),
    (TECH_MID, None, "D"),
    (TECH_WEAK, SECTOR_STRONG, "C"),
    (TECH_WEAK, SECTOR_MID, "D"),
    (TECH_WEAK, SECTOR_WEAK, "D"),
    (TECH_WEAK, None, "D"),
]


@pytest.mark.parametrize("patterns,sector,expected", GRID)
def test_resonance_grid(patterns, sector, expected):
    scored = sc.score_candidate(_cand(patterns), sector, "做多")
    assert scored["bucket"] == expected


def test_bear_market_caps_pool_at_b_and_observe():
    scored = sc.score_candidate(_cand(TECH_STRONG), SECTOR_STRONG, "空头")
    assert scored["bucket"] == "B"
    assert scored["next_step"] == "observe_price"
    assert scored["resonance"]["market_permission"] == "观察"


def test_no_stop_loss_ref_cannot_enter_a():
    scored = sc.score_candidate(_cand(TECH_STRONG, stop_price=None), SECTOR_STRONG, "做多")
    assert scored["bucket"] == "B"
    assert "no_stop_loss_ref" in scored["risk_flags"]


def test_contract_required_fields():
    scored = sc.score_candidate(_cand(TECH_MID), SECTOR_STRONG, "做多")
    for key in ["code", "name", "sector", "sector_heat_filter", "resonance",
                "stock_role", "relative_strength", "score", "bucket",
                "entry_reason", "risk_flags", "next_step"]:
        assert key in scored, f"缺契约字段 {key}"
    shf = scored["sector_heat_filter"]
    for key in ["sector_state", "sector_score", "heat_level", "pass_level", "reason"]:
        assert key in shf
    res = scored["resonance"]
    for key in ["technical_level", "sector_heat_level", "market_permission", "resonance_level"]:
        assert key in res
    assert scored["bucket"] in ("A", "B", "C", "D")
    assert scored["next_step"] in ("generate_buy_plan", "observe_price",
                                   "long_term_track", "avoid")
    assert isinstance(scored["score_detail"]["technical_score"], int)


def test_bucket_next_step_mapping():
    scored = sc.score_candidate(_cand(TECH_STRONG), SECTOR_STRONG, "做多")
    assert scored["bucket"] == "A"
    assert scored["next_step"] == "generate_buy_plan"


def test_score_all_missing_sector_state_partial():
    enriched = {"status": "ok", "candidates": [_cand(TECH_STRONG)]}
    result = sc.score_all("2026-07-21", enriched=enriched, sector_states=[], amv_state="做多")
    assert result["status"] == "partial"
    assert result["degraded_reason"] == "sector_state_missing"
    assert result["bucket_counts"]["C"] == 1  # 强×未知 → C


def test_score_all_enriched_unavailable_passthrough():
    enriched = {"status": "unavailable", "degraded_reason": "formula_hits_unavailable:tdxw_not_running"}
    result = sc.score_all("2026-07-21", enriched=enriched,
                          sector_states=[SECTOR_STRONG], amv_state="做多")
    assert result["status"] == "unavailable"
    assert "tdxw_not_running" in result["degraded_reason"]
    assert result["candidates"] == []


def test_score_all_bucket_counts_and_sort():
    enriched = {"status": "ok", "candidates": [
        _cand(TECH_MID, code="000001"),
        _cand(TECH_STRONG, code="600000"),
    ]}
    states = [{**SECTOR_STRONG, "theme_id": "semiconductor_chip_memory_packaging"}]
    result = sc.score_all("2026-07-21", enriched=enriched, sector_states=states, amv_state="做多")
    assert result["bucket_counts"] == {"A": 1, "B": 1, "C": 0, "D": 0}
    assert result["candidates"][0]["bucket"] == "A"  # 按分数降序
