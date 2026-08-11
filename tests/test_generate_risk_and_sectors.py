"""`generate_risk_and_sectors` —— 产出 **RiskDecision + SectorState**，硬失败 stage。

覆盖率清点（2026-08-07）：43%、42 语句未覆盖。

它是 `chief_decision_report` 的**强制上游**（RiskDecision 缺失 ⇒ 总控直接中止），
也是 `sector_state` 的唯一来源（`trade_permission` 决定观察方向）。
它同时取代了旧的 `build_skill_contracts.py` + `skill_adapters.py`
（那也是 `DATA_FLOW_CONTRACT` 里 `SkillEvidence` 实体被判无生产者的原因）。

模块里记着一次真实事故（审计 B1）：`market_regime` 此前只读 `effective_state`
且做精确等值比较 ⇒ **「空头触发」这套词表会让 `allow_add=False` 漏置**。
现已 `amv_zone` 兜底 + `normalize_regime` 归一，本测试钉住它。
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from custos.pipeline import generate_risk_and_sectors as g  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for sub in ("holdings", "sectors", "market", "risk"):
        (tmp_path / sub).mkdir()
    monkeypatch.setattr(g, "DATA", tmp_path)
    return tmp_path


def _w(data, rel, obj):
    p = data / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


class TestNormalizeStage:
    def test_default_is_range(self):
        assert g.normalize_stage("", "") == "震荡"

    @pytest.mark.parametrize("raw,trend", [("退潮/下跌", ""), ("", "下跌")])
    def test_ebb_detected_from_either_field(self, raw, trend):
        """阶段与趋势**任一**命中即可 —— 两个字段来自不同上游，不能只看一个。"""
        assert g.normalize_stage(raw, trend) == "退潮"


class TestSectorState:
    def test_unavailable_sectors_dropped(self, env):
        """取不到数据的板块不进 SectorState —— 否则下游会拿一个空板块当可交易方向。"""
        _w(env, "sectors/2026-08-07_sector_technical_summary.json",
           [{"theme_name": "x", "available": False}])
        assert g.build_sector_state("2026-08-07") == []

    def test_ebb_forces_avoid(self, env):
        _w(env, "sectors/2026-08-07_sector_technical_summary.json",
           [{"theme_name": "退潮板块", "available": True, "stage": "退潮/下跌",
             "trend_state": "下跌", "score": 95, "action_bias": "回避/禁止加仓"}])
        r = g.build_sector_state("2026-08-07")
        assert r[0]["trade_permission"] == "回避", "退潮板块高分也不得放行"

    def test_action_bias_avoid_also_forces_avoid(self, env):
        """即使阶段不是退潮，`action_bias` 含「回避/禁止」也必须回避 ——
        两条判据是**或**关系（任一说不行就不行）。"""
        _w(env, "sectors/2026-08-07_sector_technical_summary.json",
           [{"theme_name": "x", "available": True, "stage": "主升/加速",
             "trend_state": "上涨", "score": 90, "action_bias": "回避/禁止加仓"}])
        assert g.build_sector_state("2026-08-07")[0]["trade_permission"] == "回避"

    def test_support_needs_stage_and_score(self, env):
        """「支持」要求阶段 ∈ {主升, 修复} **且** 分数 ≥60。"""
        _w(env, "sectors/2026-08-07_sector_technical_summary.json", [
            {"theme_name": "强", "available": True, "stage": "主升/加速",
             "trend_state": "上涨", "score": 60, "action_bias": "可关注核心股"},
            {"theme_name": "分不够", "available": True, "stage": "主升/加速",
             "trend_state": "上涨", "score": 59, "action_bias": "观察"}])
        r = {x["sector"]: x["trade_permission"] for x in g.build_sector_state("2026-08-07")}
        assert r["强"] == "支持" and r["分不够"] == "观察"

    def test_missing_score_still_can_support(self, env):
        """`score` 缺失时不因「取不到分」而降级 —— 阶段已足够（代码显式允许 None）。"""
        _w(env, "sectors/2026-08-07_sector_technical_summary.json",
           [{"theme_name": "x", "available": True, "stage": "修复", "trend_state": "上涨",
             "action_bias": "观察"}])
        assert g.build_sector_state("2026-08-07")[0]["trade_permission"] == "支持"


class TestRiskDecision:
    def test_no_holdings_is_normal_level(self, env):
        _w(env, "holdings/2026-08-07_holding_review.json", [])
        r = g.build_risk_decision("2026-08-07")
        assert r["risk_level"] == "普通" and r["stock_risks"] == []

    @pytest.mark.parametrize("action,priority", [("止损", "高"), ("清仓", "高"), ("减仓", "中")])
    def test_action_drives_priority(self, env, action, priority):
        """止损/清仓是**高**优先，减仓是中 —— 这决定 `chief_decision` 是否覆盖 B1 动作。"""
        _w(env, "holdings/2026-08-07_holding_review.json",
           [{"code": "600000", "name": "x", "action": action, "reason": ["r"]}])
        r = g.build_risk_decision("2026-08-07")
        assert r["stock_risks"][0]["priority"] == priority

    def test_b1_p0_becomes_liquidate_high(self, env):
        """B1 判 P0（最紧急）⇒ 归一为「清仓」+ 高优先，即使 review 侧只写了「观察」。"""
        _w(env, "holdings/2026-08-07_holding_review.json",
           [{"code": "600000", "action": "观察",
             "b1_holding_state": {"final_priority": "P0", "signals": [{"signal": "s1"}]}}])
        x = g.build_risk_decision("2026-08-07")["stock_risks"][0]
        assert x["action"] == "清仓" and x["priority"] == "高"
        assert x["risk_type"] == "B1持仓结构" and x["b1_signal_refs"] == ["s1"]

    def test_b1_p1_becomes_reduce_mid(self, env):
        _w(env, "holdings/2026-08-07_holding_review.json",
           [{"code": "600000", "action": "观察", "b1_holding_state": {"final_priority": "P1"}}])
        x = g.build_risk_decision("2026-08-07")["stock_risks"][0]
        assert x["action"] == "减仓" and x["priority"] == "中"

    def test_observe_without_b1_priority_is_not_a_risk(self, env):
        """只是「观察」且 B1 无 P0/P1 ⇒ 不进风险清单（不制造假风险）。"""
        _w(env, "holdings/2026-08-07_holding_review.json",
           [{"code": "600000", "action": "观察"}])
        assert g.build_risk_decision("2026-08-07")["stock_risks"] == []

    def test_risk_level_escalates_to_strong_on_any_high(self, env):
        _w(env, "holdings/2026-08-07_holding_review.json",
           [{"code": "600000", "action": "减仓"}, {"code": "600001", "action": "止损"}])
        assert g.build_risk_decision("2026-08-07")["risk_level"] == "强风控"

    def test_risk_level_elevated_when_only_mid(self, env):
        _w(env, "holdings/2026-08-07_holding_review.json",
           [{"code": "600000", "action": "减仓"}])
        assert g.build_risk_decision("2026-08-07")["risk_level"] == "提高"

    def test_dedupe_by_code_type_reason(self, env):
        """同一 (代码, 类型, 理由) 只留一条 —— 重复风险会让优先级清单虚长。"""
        _w(env, "holdings/2026-08-07_holding_review.json", [
            {"code": "600000.SH", "action": "止损", "reason": ["破位"]},
            {"code": "600000", "action": "止损", "reason": ["破位"]}])
        r = g.build_risk_decision("2026-08-07")
        assert len(r["stock_risks"]) == 1
        assert r["stock_risks"][0]["code"] == "600000", "去重后应存裸码"

    def test_sorted_high_first(self, env):
        _w(env, "holdings/2026-08-07_holding_review.json", [
            {"code": "600001", "action": "减仓"}, {"code": "600000", "action": "清仓"}])
        r = g.build_risk_decision("2026-08-07")
        assert [x["code"] for x in r["stock_risks"]] == ["600000", "600001"]

    def test_forbidden_actions_only_hard_ones(self, env):
        _w(env, "holdings/2026-08-07_holding_review.json", [
            {"code": "600000", "action": "止损"}, {"code": "600001", "action": "减仓"}])
        f = g.build_risk_decision("2026-08-07")["forbidden_actions"]
        assert "止损" in f and "减仓" not in f

    def test_default_reason_when_absent(self, env):
        """理由缺失时给默认值 —— 空理由的风险条目无法复盘。"""
        _w(env, "holdings/2026-08-07_holding_review.json",
           [{"code": "600000", "action": "止损"}])
        assert g.build_risk_decision("2026-08-07")["stock_risks"][0]["reason"] \
            == "portfolio_review触发风控"


class TestRegimeDirective:
    """⚠️ 回归（审计 B1）：`market_regime` 必须归一三套词表。

    此前只读 `effective_state` 且精确等值 ⇒ 「空头触发」这套写法会让
    `allow_add=False` **漏置**，等于 0AMV 空头期照样允许加仓。
    """

    @pytest.mark.parametrize("amv", [
        {"effective_state": "空头"},
        {"amv_zone": "空头触发"},                      # ← 曾漏掉的这套
        {"effective_state": None, "amv_zone": "空头触发"},
    ])
    def test_bear_sets_allow_add_false(self, env, amv):
        _w(env, "holdings/2026-08-07_holding_review.json", [])
        _w(env, "market/2026-08-07_market_timing_input.json", {"amv_0": amv})
        r = g.build_risk_decision("2026-08-07")
        assert r["market_regime"] == "空头"
        assert r["regime_directive"]["allow_add"] is False
        assert r["regime_directive"]["reduce_top_priority"] is True
        assert "任何反弹都是卖出机会" in r["regime_directive"]["note"]

    @pytest.mark.parametrize("amv,want", [
        ({"effective_state": "做多"}, "做多"),
        ({"amv_zone": "阈值内"}, "中性"),
        ({}, "未知"),
    ])
    def test_non_bear_regimes(self, env, amv, want):
        _w(env, "holdings/2026-08-07_holding_review.json", [])
        _w(env, "market/2026-08-07_market_timing_input.json", {"amv_0": amv})
        r = g.build_risk_decision("2026-08-07")
        assert r["market_regime"] == want
        assert r["regime_directive"]["reduce_top_priority"] is False
        assert "allow_add" not in r["regime_directive"], \
            "非空头不该显式给 allow_add —— 缺省由下游门控裁决"


class TestMainWritesBothArtifacts:
    def test_main(self, env, monkeypatch):
        _w(env, "holdings/2026-08-07_holding_review.json",
           [{"code": "600000", "action": "止损", "reason": ["破位"]}])
        _w(env, "sectors/2026-08-07_sector_technical_summary.json",
           [{"theme_name": "半导体", "available": True, "stage": "主升/加速",
             "trend_state": "上涨", "score": 80, "action_bias": "可关注核心股"}])
        _w(env, "market/2026-08-07_market_timing_input.json", {"amv_0": {"amv_zone": "空头触发"}})
        monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-08-07"])
        g.main()
        risk = json.loads((env / "risk" / "2026-08-07_risk_decision.json").read_text(encoding="utf-8"))
        sec = json.loads((env / "sectors" / "2026-08-07_sector_state.json").read_text(encoding="utf-8"))
        assert risk["risk_level"] == "强风控" and risk["regime_directive"]["allow_add"] is False
        assert sec[0]["trade_permission"] == "支持"


class TestNanScoreHandling:
    """⚠️ 板块 `score` 为 NaN 时，判定与落盘**刻意走两条路**。

    NaN 会从技术面上游漏进来（pandas 的缺失值就是 NaN）。两侧要求相反：

        判定侧：读原值 ⇒ `float(nan) >= 60` 为 False ⇒ 落「观察」（保守）
                若改用 fnum，NaN→None 会命中「没打分不算减分项」⇒ **放宽成支持**
        落盘侧：写 fnum(score) ⇒ None
                NaN 不是合法 JSON，且 write_json 的 allow_nan=False 会当场崩，
                而这是硬失败 stage —— 不能因一个板块的脏分数拖垮整条 17:00 链
    """

    def test_nan_score_downgrades_to_observe(self, env):
        _w(env, "sectors/2026-08-07_sector_technical_summary.json",
           [{"theme_name": "x", "available": True, "stage": "主升/加速",
             "trend_state": "上涨", "score": float("nan"), "action_bias": "观察"}])
        r = g.build_sector_state("2026-08-07")
        assert r[0]["trade_permission"] == "观察", "NaN 分数不得放宽成支持"

    def test_nan_score_written_as_null(self, env):
        _w(env, "sectors/2026-08-07_sector_technical_summary.json",
           [{"theme_name": "x", "available": True, "stage": "主升/加速",
             "trend_state": "上涨", "score": float("nan"), "action_bias": "观察"}])
        assert g.build_sector_state("2026-08-07")[0]["score"] is None

    def test_main_survives_nan_score(self, env, monkeypatch):
        """端到端：脏分数不得让硬失败 stage 崩。"""
        _w(env, "holdings/2026-08-07_holding_review.json", [])
        _w(env, "sectors/2026-08-07_sector_technical_summary.json",
           [{"theme_name": "x", "available": True, "stage": "主升", "trend_state": "上涨",
             "score": float("inf"), "action_bias": "观察"}])
        monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-08-07"])
        g.main()
        txt = (env / "sectors" / "2026-08-07_sector_state.json").read_text(encoding="utf-8")
        assert "NaN" not in txt and "Infinity" not in txt
        assert json.loads(txt)[0]["score"] is None
