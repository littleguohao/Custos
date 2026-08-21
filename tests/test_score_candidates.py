# -*- coding: utf-8 -*-
"""Tests for screening.score_candidates.

2026-07-23 重构后：分层 = 个股(技术结构 × 资金意图)矩阵，板块不封顶（只进
score/共振/trade_style）。风控/回避（无止损/空头/sprint/派发/MACD顶背离/cz回避）仍硬封。
"""

import pytest

from custos.pipeline.screening import score_candidates as sc
import sys


def _mk(patterns=None, capital="weak", stop_price=10.0, code="600000", **extra):
    """构造候选：patterns 定技术结构，capital∈{strong,mid,weak} 定资金意图。

    capital=strong: b1_ignition + 量能持续主线 → 资金意图 强(≥5)
    capital=mid:    量能持续主线 → 资金意图 中(=2)
    capital=weak:   无 → 资金意图 弱(0)
    （量能持续主线只加资金意图分、不加技术分，便于把两轴解耦。）
    """
    is_strong = patterns is TECH_STRONG  # v0.58：注入非 patterns 加分凑够技术强
    patterns = dict(patterns or {})
    cand = {
        "code": code,
        "name": "示例",
        "sector": "半导体/芯片/存储/封测",
        "theme_id": "semiconductor_chip_memory_packaging",
        "formula_hits": ["KDJ_J_LOW"],
        "patterns": patterns,
        "daily_j": 10.0
        if (patterns.get("j_low") or patterns.get("reversal_k_candidate"))
        else 55.0,
        "stop_loss_ref": {"price": stop_price, "basis": "近10日最低价"}
        if stop_price
        else None,
        "is_holding": False,
    }
    if capital == "strong":
        cand["b1_ignition"] = {"hit": True}  # 资金意图 +3, 技术 +8
        cand["volume_sustain"] = {
            "status": "mainline_confirmed"
        }  # 资金意图 +2, 技术 +0
    elif capital == "mid":
        cand["volume_sustain"] = {"status": "mainline_confirmed"}  # 资金意图 +2
    if is_strong:
        cand.update(TECH_STRONG_EXTRA)
    cand.update(extra)
    return cand


# 技术结构层级（纯技术、不污染资金意图轴）：
# ⚠️ v0.58 权重下 patterns 全中也只有 59 分（bbi5+反转K4+j_low20+缩量15+强RS15）
# ——技术强（≥60）必须再叠非 patterns 因子，这是调权的直接后果。TECH_STRONG 由
# _mk 注入 TECH_STRONG_EXTRA 凑够 60+；EXTRA 全部选**资金意图轴不认**的项
# （bottom_volume/强RS 会往资金轴 +2，污染 capital=weak 的网格行）。
TECH_STRONG = {"bbi_above": True, "j_low": True, "volume_contraction": True}  # 40
TECH_STRONG_EXTRA = {
    "five_day_entry": {"hit": True},  # +8
    "weekly_j_low": True,  # +5
    "adx": 61.0,  # +5
    "macd_technics": {"available": True, "zone1_restart": True},  # +5
    "non_one_wave": {"status": "confirmed"},  # +5
}  # 40 + 28 = 68 → 强
TECH_MID = {"j_low": True, "volume_contraction": True}  # 35 → 中
TECH_WEAK: dict = {}  # 0  → 弱

SECTOR_STRONG = {"state": "主升", "score": 80, "sector": "半导体/芯片/存储/封测"}
SECTOR_MID = {"state": "震荡", "score": 50, "sector": "半导体/芯片/存储/封测"}
SECTOR_WEAK = {"state": "退潮", "score": 30, "sector": "半导体/芯片/存储/封测"}


# 个股共振矩阵：(技术结构, 资金意图) → bucket（与板块无关）
GRID = [
    (TECH_STRONG, "strong", "A"),
    (TECH_STRONG, "mid", "B"),
    (TECH_STRONG, "weak", "C"),
    (TECH_MID, "strong", "B"),
    (TECH_MID, "mid", "C"),
    (TECH_MID, "weak", "D"),
    (TECH_WEAK, "strong", "C"),
    (TECH_WEAK, "mid", "D"),
    (TECH_WEAK, "weak", "D"),
]


@pytest.mark.parametrize("patterns,capital,expected", GRID)
def test_individual_grid(patterns, capital, expected):
    scored = sc.score_candidate(_mk(patterns, capital=capital), SECTOR_STRONG, "做多")
    assert scored["bucket"] == expected


def test_sector_does_not_cap_bucket_only_sets_trade_style():
    """同一强势个股在强/中/弱/无板块中 bucket 不变，只有 trade_style 变。

    v0.50（#37 阶段 A）：板块分**移出总分**——score 现在就是技术分，
    强弱板块下相等（板块信息仍落盘展示：sector_heat_filter / sector_score）。
    """
    cand = _mk(TECH_STRONG, capital="strong")
    a_strong = sc.score_candidate(cand, SECTOR_STRONG, "做多")
    a_weak = sc.score_candidate(cand, SECTOR_WEAK, "做多")
    a_none = sc.score_candidate(cand, None, "做多")
    assert a_strong["bucket"] == a_weak["bucket"] == a_none["bucket"] == "A"
    assert a_strong["trade_style"] == "波段"
    assert a_weak["trade_style"] == "短线(交易性)"
    assert a_none["trade_style"] == "短线(交易性)"
    assert a_strong["score"] == a_weak["score"], "v0.50：总分=技术分，板块不进总分"
    # 板块信息仍以展示列落盘（可读、可复盘），只是不驱动分层/总分
    assert a_strong["sector_heat_filter"]["sector_score"] is not None


def test_bear_market_caps_pool_at_b_and_observe():
    scored = sc.score_candidate(
        _mk(TECH_STRONG, capital="strong"), SECTOR_STRONG, "空头"
    )
    assert scored["bucket"] == "B"
    assert scored["next_step"] == "observe_price"
    assert scored["resonance"]["market_permission"] == "观察"


def test_no_stop_loss_ref_cannot_enter_a():
    scored = sc.score_candidate(
        _mk(TECH_STRONG, capital="strong", stop_price=None), SECTOR_STRONG, "做多"
    )
    assert scored["bucket"] == "B"
    assert "no_stop_loss_ref" in scored["risk_flags"]


def test_contract_required_fields():
    scored = sc.score_candidate(_mk(TECH_MID, capital="mid"), SECTOR_STRONG, "做多")
    for key in [
        "code",
        "name",
        "sector",
        "sector_heat_filter",
        "resonance",
        "stock_role",
        "relative_strength",
        "score",
        "bucket",
        "entry_reason",
        "risk_flags",
        "next_step",
        "trade_style",
        "capital_intent",
    ]:
        assert key in scored, f"缺契约字段 {key}"
    res = scored["resonance"]
    for key in [
        "technical_level",
        "capital_intent_level",
        "sector_heat_level",
        "market_permission",
        "resonance_level",
    ]:
        assert key in res
    assert scored["trade_style"] in ("波段", "波段(谨慎)", "短线(交易性)")
    assert scored["capital_intent"]["level"] in ("强", "中", "弱")
    assert scored["bucket"] in ("A", "B", "C", "D")


def test_change_pct_passthrough():
    """v0.94 修复：change_pct 透传进落盘字典——门内提醒（candidate_table，
    v0.89）的涨跌幅列读 candidates；score_candidate 是显式白名单，不透传
    则实盘该列恒为「-」（旧观察区由 enrich 侧显式携带，无此问题）。"""
    scored = sc.score_candidate(
        _mk(TECH_MID, capital="mid", change_pct=3.25), SECTOR_STRONG, "做多"
    )
    assert scored["change_pct"] == 3.25


def test_bucket_next_step_mapping():
    scored = sc.score_candidate(
        _mk(TECH_STRONG, capital="strong"), SECTOR_STRONG, "做多"
    )
    assert scored["bucket"] == "A"
    # v0.50（#37 阶段 A）：原 "generate_buy_plan" 是虚假承诺（BuyPlan 契约已删、
    # 无组件生成买入计划），改为如实的 "buy_review"
    assert scored["next_step"] == "buy_review"


def test_score_all_missing_sector_state_partial():
    enriched = {"status": "ok", "candidates": [_mk(TECH_STRONG, capital="strong")]}
    result = sc.score_all(
        "2026-07-21", enriched=enriched, sector_states=[], amv_state="做多"
    )
    assert result["status"] == "partial"
    assert "sector_state_missing" in result["degraded_reason"]
    # 板块缺失不再影响分层：强个股仍进 A
    assert result["bucket_counts"]["A"] == 1


def test_score_all_enriched_unavailable_passthrough():
    enriched = {
        "status": "unavailable",
        "degraded_reason": "formula_hits_unavailable:tdxw_not_running",
    }
    result = sc.score_all(
        "2026-07-21", enriched=enriched, sector_states=[SECTOR_STRONG], amv_state="做多"
    )
    assert result["status"] == "unavailable"
    assert "tdxw_not_running" in result["degraded_reason"]
    assert result["candidates"] == []


def test_score_all_bucket_counts_and_sort():
    enriched = {
        "status": "ok",
        "candidates": [
            _mk(TECH_MID, capital="mid", code="000001"),  # (中,中) → C
            _mk(TECH_STRONG, capital="strong", code="600000"),  # (强,强) → A
        ],
    }
    states = [{**SECTOR_STRONG, "theme_id": "semiconductor_chip_memory_packaging"}]
    result = sc.score_all(
        "2026-07-21", enriched=enriched, sector_states=states, amv_state="做多"
    )
    assert result["bucket_counts"] == {"A": 1, "B": 0, "C": 1, "D": 0}
    assert result["candidates"][0]["bucket"] == "A"  # 按 bucket 优先排序


# ---------- 资金意图强度 & trade_style 单元 ----------


def test_capital_intent_strength_grades():
    strong = _mk(TECH_WEAK, capital="strong")  # b1_ignition(3)+mainline(2)=5
    lvl, sc_, _ = sc.capital_intent_strength(strong)
    assert lvl == "强" and sc_ >= 5
    mid = _mk(TECH_WEAK, capital="mid")  # mainline(2)=2
    assert sc.capital_intent_strength(mid)[0] == "中"
    weak = _mk(TECH_WEAK, capital="weak")  # 0
    assert sc.capital_intent_strength(weak)[0] == "弱"


def test_capital_intent_ignores_distribution_negatives():
    # 派发是风控 cap 的职责，不在资金意图轴重复扣减（正向轴只看资金在进）
    c = _mk(
        TECH_WEAK,
        capital="strong",
        distribution={
            "available": True,
            "hits": ["top_huge_vol_bear"],
            "risk_level": "high",
        },
    )
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


_FIN_YOU = {
    "available": True,
    "dixi_proxy": {
        "real_earnings_cashflow": True,
        "roe_positive": True,
        "net_profit_positive": True,
        "op_cashflow_positive": True,
    },
}


def test_fundamental_quality_tiers():
    assert sc.fundamental_quality(None)["tier"] == "未知"
    assert sc.fundamental_quality({"available": False})["available"] is False
    assert (
        sc.fundamental_quality(_FIN_YOU)["tier"] == "优"
        and sc.fundamental_quality(_FIN_YOU)["sanwu"] is False
    )
    zhong = {
        "available": True,
        "dixi_proxy": {
            "net_profit_positive": True,
            "op_cashflow_positive": None,
            "real_earnings_cashflow": False,
            "roe_positive": False,
        },
    }
    assert (
        sc.fundamental_quality(zhong)["tier"] == "中"
        and sc.fundamental_quality(zhong)["sanwu"] is False
    )
    sanwu = {
        "available": True,
        "dixi_proxy": {
            "net_profit_positive": False,
            "op_cashflow_positive": False,
            "real_earnings_cashflow": False,
            "roe_positive": False,
        },
    }
    r = sc.fundamental_quality(sanwu)
    assert r["tier"] == "差" and r["sanwu"] is True  # 净利非正+现金流确认负 → 三无


def test_resonance_four_leg():
    cand = _mk(
        TECH_STRONG,
        capital="strong",
        financials=_FIN_YOU,
        sector_phase={"favorable": True, "available": True},
    )
    e = sc.score_candidate(cand, SECTOR_STRONG, "做多")
    r = e["resonance_4leg"]
    assert r["market"] and r["sector"] and r["fundamental"] and r["technical"]
    assert (
        r["aligned"] == 4 and r["label"] == "四面共振" and r["bull_candidate"] is True
    )
    assert e["fundamental_quality"]["tier"] == "优"
    # 空头 → market 腿灭 → 非四面共振、非牛股候选(hint 不改分层由既有测试覆盖)
    e2 = sc.score_candidate(cand, SECTOR_STRONG, "空头")
    assert (
        e2["resonance_4leg"]["market"] is False
        and e2["resonance_4leg"]["bull_candidate"] is False
    )


def test_stock_pool_json_carries_audit_block(tmp_path, monkeypatch):
    """可审计块（原待办 #29，已实现）：stock_pool.json 落盘前注入 audit 四件，
    且过 stock_pool 契约（audit 为可选字段，出现时四件必须齐）。"""
    import json
    import sys

    monkeypatch.setattr(sc, "SCREENING_DIR", tmp_path / "screening")
    monkeypatch.setattr(sc, "SECTORS_DIR", tmp_path / "sectors")
    monkeypatch.setattr(sc, "MARKET_DIR", tmp_path / "market")
    monkeypatch.setattr(sc, "STOCK_POOL_DIR", tmp_path / "stock_pool")
    monkeypatch.setattr(sc, "REGISTRY_PATH", tmp_path / "registry.json")
    (tmp_path / "screening").mkdir()
    (tmp_path / "screening" / "2026-08-07_candidates_enriched.json").write_text(
        json.dumps({"status": "ok", "candidates": [], "excluded": []}), encoding="utf-8"
    )
    monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-08-07"])
    assert sc.main() == 0
    pool = json.loads(
        (tmp_path / "stock_pool" / "2026-08-07_stock_pool.json").read_text(
            encoding="utf-8"
        )
    )
    audit = pool["audit"]
    assert audit["report_id"].startswith("2026-08-07_screening_")
    assert audit["strategy_version"] and audit["data_as_of"] and audit["inputs"]
    from custos.core.contracts import check

    result = check("stock_pool", pool)
    assert result["valid"], result["errors"]
