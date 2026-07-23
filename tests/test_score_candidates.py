# -*- coding: utf-8 -*-
"""Tests for screening.score_candidates.

2026-07-23 重构后：分层 = 个股(技术结构 × 资金意图)矩阵，板块不封顶（只进
score/共振/trade_style）。风控/回避（无止损/空头/sprint/派发/MACD顶背离/cz回避）仍硬封。
"""
import pytest

from screening import score_candidates as sc


def _mk(patterns=None, capital="weak", stop_price=10.0, code="600000", **extra):
    """构造候选：patterns 定技术结构，capital∈{strong,mid,weak} 定资金意图。

    capital=strong: b1_ignition + 量能持续主线 → 资金意图 强(≥5)
    capital=mid:    量能持续主线 → 资金意图 中(=2)
    capital=weak:   无 → 资金意图 弱(0)
    （量能持续主线只加资金意图分、不加技术分，便于把两轴解耦。）
    """
    patterns = dict(patterns or {})
    cand = {
        "code": code, "name": "示例",
        "sector": "半导体/芯片/存储/封测",
        "theme_id": "semiconductor_chip_memory_packaging",
        "formula_hits": ["KDJ_J_LOW"],
        "patterns": patterns,
        "daily_j": 10.0 if (patterns.get("j_low") or patterns.get("reversal_k_candidate")) else 55.0,
        "stop_loss_ref": {"price": stop_price, "basis": "近10日最低价"} if stop_price else None,
        "is_holding": False,
    }
    if capital == "strong":
        cand["b1_ignition"] = {"hit": True}                      # 资金意图 +3, 技术 +8
        cand["volume_sustain"] = {"status": "mainline_confirmed"}  # 资金意图 +2, 技术 +0
    elif capital == "mid":
        cand["volume_sustain"] = {"status": "mainline_confirmed"}  # 资金意图 +2
    cand.update(extra)
    return cand


# 技术结构层级（纯技术、不污染资金意图轴）：
TECH_STRONG = {"bbi_above": True, "j_low": True, "volume_contraction": True}   # 60 → 强
TECH_MID = {"bbi_above": True, "j_low": True}                                   # 45 → 中
TECH_WEAK: dict = {}                                                            # 0  → 弱

SECTOR_STRONG = {"state": "主升", "score": 80, "sector": "半导体/芯片/存储/封测"}
SECTOR_MID = {"state": "震荡", "score": 50, "sector": "半导体/芯片/存储/封测"}
SECTOR_WEAK = {"state": "退潮", "score": 30, "sector": "半导体/芯片/存储/封测"}


# 个股共振矩阵：(技术结构, 资金意图) → bucket（与板块无关）
GRID = [
    (TECH_STRONG, "strong", "A"), (TECH_STRONG, "mid", "B"), (TECH_STRONG, "weak", "C"),
    (TECH_MID, "strong", "B"), (TECH_MID, "mid", "C"), (TECH_MID, "weak", "D"),
    (TECH_WEAK, "strong", "C"), (TECH_WEAK, "mid", "D"), (TECH_WEAK, "weak", "D"),
]


@pytest.mark.parametrize("patterns,capital,expected", GRID)
def test_individual_grid(patterns, capital, expected):
    scored = sc.score_candidate(_mk(patterns, capital=capital), SECTOR_STRONG, "做多")
    assert scored["bucket"] == expected


def test_sector_does_not_cap_bucket_only_sets_trade_style():
    """同一强势个股在强/中/弱/无板块中 bucket 不变，只有 trade_style 变。"""
    cand = _mk(TECH_STRONG, capital="strong")
    a_strong = sc.score_candidate(cand, SECTOR_STRONG, "做多")
    a_weak = sc.score_candidate(cand, SECTOR_WEAK, "做多")
    a_none = sc.score_candidate(cand, None, "做多")
    assert a_strong["bucket"] == a_weak["bucket"] == a_none["bucket"] == "A"
    assert a_strong["trade_style"] == "波段"
    assert a_weak["trade_style"] == "短线(交易性)"
    assert a_none["trade_style"] == "短线(交易性)"
    # 板块仍进 score：强板块总分应高于弱板块
    assert a_strong["score"] > a_weak["score"]


def test_bear_market_caps_pool_at_b_and_observe():
    scored = sc.score_candidate(_mk(TECH_STRONG, capital="strong"), SECTOR_STRONG, "空头")
    assert scored["bucket"] == "B"
    assert scored["next_step"] == "observe_price"
    assert scored["resonance"]["market_permission"] == "观察"


def test_no_stop_loss_ref_cannot_enter_a():
    scored = sc.score_candidate(_mk(TECH_STRONG, capital="strong", stop_price=None),
                                SECTOR_STRONG, "做多")
    assert scored["bucket"] == "B"
    assert "no_stop_loss_ref" in scored["risk_flags"]


def test_contract_required_fields():
    scored = sc.score_candidate(_mk(TECH_MID, capital="mid"), SECTOR_STRONG, "做多")
    for key in ["code", "name", "sector", "sector_heat_filter", "resonance",
                "stock_role", "relative_strength", "score", "bucket",
                "entry_reason", "risk_flags", "next_step", "trade_style", "capital_intent"]:
        assert key in scored, f"缺契约字段 {key}"
    res = scored["resonance"]
    for key in ["technical_level", "capital_intent_level", "sector_heat_level",
                "market_permission", "resonance_level"]:
        assert key in res
    assert scored["trade_style"] in ("波段", "波段(谨慎)", "短线(交易性)")
    assert scored["capital_intent"]["level"] in ("强", "中", "弱")
    assert scored["bucket"] in ("A", "B", "C", "D")


def test_bucket_next_step_mapping():
    scored = sc.score_candidate(_mk(TECH_STRONG, capital="strong"), SECTOR_STRONG, "做多")
    assert scored["bucket"] == "A"
    assert scored["next_step"] == "generate_buy_plan"


def test_score_all_missing_sector_state_partial():
    enriched = {"status": "ok", "candidates": [_mk(TECH_STRONG, capital="strong")]}
    result = sc.score_all("2026-07-21", enriched=enriched, sector_states=[], amv_state="做多")
    assert result["status"] == "partial"
    assert "sector_state_missing" in result["degraded_reason"]
    # 板块缺失不再影响分层：强个股仍进 A
    assert result["bucket_counts"]["A"] == 1


def test_score_all_enriched_unavailable_passthrough():
    enriched = {"status": "unavailable", "degraded_reason": "formula_hits_unavailable:tdxw_not_running"}
    result = sc.score_all("2026-07-21", enriched=enriched,
                          sector_states=[SECTOR_STRONG], amv_state="做多")
    assert result["status"] == "unavailable"
    assert "tdxw_not_running" in result["degraded_reason"]
    assert result["candidates"] == []


def test_score_all_bucket_counts_and_sort():
    enriched = {"status": "ok", "candidates": [
        _mk(TECH_MID, capital="mid", code="000001"),      # (中,中) → C
        _mk(TECH_STRONG, capital="strong", code="600000"),  # (强,强) → A
    ]}
    states = [{**SECTOR_STRONG, "theme_id": "semiconductor_chip_memory_packaging"}]
    result = sc.score_all("2026-07-21", enriched=enriched, sector_states=states, amv_state="做多")
    assert result["bucket_counts"] == {"A": 1, "B": 0, "C": 1, "D": 0}
    assert result["candidates"][0]["bucket"] == "A"  # 按 bucket 优先排序


# ---------- 资金意图强度 & trade_style 单元 ----------

def test_capital_intent_strength_grades():
    strong = _mk(TECH_WEAK, capital="strong")  # b1_ignition(3)+mainline(2)=5
    lvl, sc_, _ = sc.capital_intent_strength(strong)
    assert lvl == "强" and sc_ >= 5
    mid = _mk(TECH_WEAK, capital="mid")         # mainline(2)=2
    assert sc.capital_intent_strength(mid)[0] == "中"
    weak = _mk(TECH_WEAK, capital="weak")       # 0
    assert sc.capital_intent_strength(weak)[0] == "弱"


def test_capital_intent_ignores_distribution_negatives():
    # 派发是风控 cap 的职责，不在资金意图轴重复扣减（正向轴只看资金在进）
    c = _mk(TECH_WEAK, capital="strong",
            distribution={"available": True, "hits": ["top_huge_vol_bear"], "risk_level": "high"})
    assert sc.capital_intent_strength(c)[0] == "强"


def test_trade_style_of_mapping():
    assert sc.trade_style_of("强") == "波段"
    assert sc.trade_style_of("中") == "波段(谨慎)"
    assert sc.trade_style_of("弱") == "短线(交易性)"
    assert sc.trade_style_of("未知") == "短线(交易性)"


def test_strong_stock_in_weak_sector_reaches_a_short_term():
    """用户核心诉求：走势好的强势个股在弱板块不被打到 D，仍进 A，只是提示短线。"""
    scored = sc.score_candidate(_mk(TECH_STRONG, capital="strong"), SECTOR_WEAK, "做多")
    assert scored["bucket"] == "A"
    assert scored["trade_style"] == "短线(交易性)"
