"""`review_enrichment` —— 盘后复盘的**主题生命周期 + 持仓诊断 + 次日计划**，硬失败 stage。

覆盖率清点（2026-08-07）：32%、45 语句未覆盖。

它读 `chief_decision` / `sector_state` / 持仓技术面 / `execution_review` / 盘后新闻，
产出复盘用的结构化诊断。硬失败 = 它一挂整条 17:00 链失败。

⚠️ 最要紧的一条语义写在返回值里：
`"permission_rule": "theme lifecycle is a filter, not a direct trade authorization"`
—— **主题生命周期只是过滤器，不是交易授权**。这与 R2「板块族+密度是归因工具，
『跟随主流』机械规则不成立」一致，测试把它钉住。
"""
from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

from custos.pipeline.close_review import review_enrichment as re_mod  # noqa: E402


class TestLifecyclePhase:
    """阶段判定优先级：**退潮优先于一切** —— 它是唯一会禁止加仓的档。"""

    def test_ebb_from_raw_stage(self):
        assert re_mod.lifecycle({"raw_stage": "退潮/下跌"}, 0)["phase"] == "退潮"

    def test_ebb_from_trend_even_if_stage_says_rally(self):
        """⚠️ 趋势下跌 ⇒ 退潮，**即使阶段字段写着主升**。

        两个字段来自不同上游，冲突时取更保守的那个。
        """
        assert re_mod.lifecycle({"raw_stage": "主升/加速", "trend": "下跌"}, 0)["phase"] == "退潮"

    @pytest.mark.parametrize("raw,want", [
        ("主升/加速", "主升"), ("修复", "修复"), ("分歧/弱震荡", "分歧"),
        ("震荡", "震荡/待确认"), ("", "震荡/待确认")])
    def test_other_phases(self, raw, want):
        assert re_mod.lifecycle({"raw_stage": raw}, 0)["phase"] == want

    def test_missing_stage_falls_back_to_state_then_default(self):
        assert re_mod.lifecycle({"state": "主升"}, 0)["technical_stage"] == "主升"
        assert re_mod.lifecycle({}, 0)["technical_stage"] == "数据不足"

    def test_ebb_marks_continuity_weak(self):
        """退潮时延续性标 weak；其余标 unavailable（**不假装知道**）。"""
        assert re_mod.lifecycle({"raw_stage": "退潮"}, 0)["continuity"] == "weak"
        assert re_mod.lifecycle({"raw_stage": "主升"}, 0)["continuity"] == "unavailable"

    def test_lifecycle_is_a_filter_not_authorization(self):
        """⚠️ 生命周期**不是交易授权** —— 这条必须在输出里写明。

        与 R2 的结论一致：板块族/主线是归因工具，「跟随主流」机械规则不成立。
        """
        r = re_mod.lifecycle({"raw_stage": "主升/加速", "score": 95}, 10)
        assert r["permission_rule"] == "theme lifecycle is a filter, not a direct trade authorization"

    def test_unknown_evidence_marked_unavailable_not_zero(self):
        """资金流/龙头结构取不到时标 `unavailable`，**不能填 0 或空串** ——
        那会让「没数据」看起来像「测过且为零」。"""
        r = re_mod.lifecycle({"raw_stage": "主升"}, 0)
        assert r["fund_flow_evidence"] == "unavailable"
        assert r["leader_structure"] == "unavailable"

    def test_event_count_passed_through(self):
        assert re_mod.lifecycle({"raw_stage": "主升"}, 7)["event_evidence_count"] == 7


class TestHelpers:
    def test_bare(self):
        assert re_mod.bare("600000.SH") == "600000" and re_mod.bare(None) == ""

    def test_load_default(self, tmp_path):
        assert re_mod.load(tmp_path / "x.json", {"d": 1}) == {"d": 1}


class TestMainPipeline:
    @pytest.fixture(autouse=True)
    def env(self, tmp_path, monkeypatch):
        for sub in ("decisions", "sectors", "holdings", "review_steps"):
            (tmp_path / sub).mkdir()
        monkeypatch.setattr(re_mod, "DATA", tmp_path)
        self.data = tmp_path

    def _w(self, rel, obj):
        p = self.data / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def _run(self, day="2026-08-07", monkeypatch=None):
        import sys as _s
        old = _s.argv
        _s.argv = ["x", "--date", day]
        try:
            re_mod.main()
        finally:
            _s.argv = old
        out = self.data / "review_steps" / f"{day}_review_enrichment.json"
        return json.loads(out.read_text(encoding="utf-8")) if out.exists() else None

    def test_all_inputs_missing_does_not_crash(self):
        """全部输入缺失时**不能崩** —— 硬失败 stage 不该因为上游没产出就拖垮整链。"""
        r = self._run()
        assert r is not None
        assert r["theme_lifecycles"] == [] and r["holding_diagnoses"] == []

    def test_news_events_counted_per_theme(self):
        """盘后新闻按主题计数，供生命周期的 `event_evidence_count` 用。"""
        self._w("sectors/2026-08-07_sector_state.json",
                [{"theme_id": "chip", "sector": "半导体", "raw_stage": "主升/加速"}])
        self._w("news/postclose/2026-08-07_postclose_news_digest.json",
                {"sections": {"policy": [{"matched_themes": ["半导体"]},
                                         {"matched_themes": ["半导体", "机器人"]}]}})
        r = self._run()
        assert r["theme_lifecycles"][0]["event_evidence_count"] == 2

    def test_sector_name_with_slash_uses_head_segment(self):
        """板块名含 `/` 时按**首段**匹配新闻主题 —— 命名口径不一致时仍能对上。"""
        self._w("sectors/2026-08-07_sector_state.json",
                [{"sector": "半导体/芯片", "raw_stage": "主升"}])
        self._w("news/postclose/2026-08-07_postclose_news_digest.json",
                {"sections": {"x": [{"matched_themes": ["半导体"]}]}})
        assert self._run()["theme_lifecycles"][0]["event_evidence_count"] == 1

    def test_holding_diagnosis_prefers_chief_b1_state(self):
        """持仓诊断优先取 `chief_decision` 里的 b1 状态（那是定稿后的），
        技术面表里的只作兜底 —— 否则会用未经风控裁决的中间态复盘。"""
        self._w("holdings/2026-08-07_holding_technical_summary.json",
                [{"code": "600000", "name": "浦发", "trend_state": "兜底趋势"}])
        self._w("decisions/2026-08-07_chief_decision.json", {"holding_actions": [
            {"code": "600000", "b1_holding_state": {"facts": {"trend_state": "定稿趋势"}}}]})
        r = self._run()
        assert r["holding_diagnoses"][0]["trend"] == "定稿趋势"

    def test_diagnosis_falls_back_to_tech_row(self):
        self._w("holdings/2026-08-07_holding_technical_summary.json",
                [{"code": "600000", "trend_state": "兜底趋势"}])
        r = self._run()
        assert r["holding_diagnoses"][0]["trend"] == "兜底趋势"

    def test_code_suffix_matching_across_sources(self):
        """技术面用裸码、chief 用带后缀 —— 必须能对上。"""
        self._w("holdings/2026-08-07_holding_technical_summary.json", [{"code": "600000"}])
        self._w("decisions/2026-08-07_chief_decision.json", {"holding_actions": [
            {"code": "600000.SH", "b1_holding_state": {"facts": {"trend_state": "定稿"}}}]})
        assert self._run()["holding_diagnoses"][0]["trend"] == "定稿"

    def test_next_day_plan_inherits_chief_permissions(self):
        """次日计划的仓位/权限**继承总控**，不自行放宽。"""
        self._w("decisions/2026-08-07_chief_decision.json", {
            "total_position_range": "20%-40%", "new_position_permission": "禁止",
            "forbidden_actions": ["无计划追高"], "tomorrow_validation": ["v1"]})
        r = self._run()["next_day_plan"]
        assert r["total_position_range"] == "20%-40%"
        assert r["new_position_permission"] == "禁止"
        assert r["forbidden_actions"] == ["无计划追高"]
        assert r["global_validation"] == ["v1"]

    def test_enrichment_cannot_override_upstream(self):
        """⚠️ 输出必须写明**不得覆盖 RiskDecision / ChiefDecision** ——
        复盘层是解释，不是裁决。"""
        assert self._run()["permission_rule"] == \
            "enrichment cannot override RiskDecision or ChiefDecision"

    def test_exact_quantity_always_none(self):
        """⚠️ 次日计划**不给精确数量** —— 精确减仓量另需当日行情授权
        （`runtime_guards.position_gate`），复盘层无权给出。"""
        self._w("holdings/2026-08-07_holding_technical_summary.json", [{"code": "600000"}])
        assert self._run()["next_day_plan"]["holding_plans"][0]["exact_quantity"] is None

    def test_plan_defaults_when_no_b1_state(self):
        """无 b1 状态时方向默认「观察」、优先级 P3、触发条件写「等待目标日技术确认」——
        不得空着（空计划无法执行也无法复盘）。"""
        self._w("holdings/2026-08-07_holding_technical_summary.json", [{"code": "600000"}])
        p = self._run()["next_day_plan"]["holding_plans"][0]
        assert p["direction"] == "观察" and p["priority"] == "P3"
        assert p["trigger"] == "等待目标日技术确认"
        assert "不得由单一低位指标放宽权限" in p["invalidation"]

    def test_risk_flags_deduped_from_signals(self):
        self._w("holdings/2026-08-07_holding_technical_summary.json", [{"code": "600000"}])
        self._w("decisions/2026-08-07_chief_decision.json", {"holding_actions": [
            {"code": "600000", "b1_holding_state": {"signals": [
                {"signal": "hard_loss"}, {"signal": "hard_loss"}, {"signal": "downtrend"}]}}]})
        assert self._run()["holding_diagnoses"][0]["risk_flags"] == ["hard_loss", "downtrend"]

    def test_trade_feedback_reflects_actual_trades(self):
        self._w("holdings/2026-08-07_holding_technical_summary.json", [{"code": "600000"}])
        self._w("review_steps/2026-08-07_execution_review.json", {"rows": [
            {"code": "600000", "execution_status": "executed", "actual_trades": [{"x": 1}]}]})
        d = self._run()["holding_diagnoses"][0]
        assert d["trade_feedback"] == "recorded" and d["execution_status"] == "executed"

    def test_no_trades_marks_feedback_unavailable(self):
        """没有成交时标 `unavailable` 而非 `none` —— 区分「没交易」与「不知道」。"""
        self._w("holdings/2026-08-07_holding_technical_summary.json", [{"code": "600000"}])
        self._w("review_steps/2026-08-07_execution_review.json", {"rows": [
            {"code": "600000", "execution_status": "no_action_no_trade", "actual_trades": []}]})
        assert self._run()["holding_diagnoses"][0]["trade_feedback"] == "unavailable"

    def test_unavailable_list_includes_market_quality_gaps(self):
        """总控的质量检查里 missing/candidate/stale 的块要进 `unavailable` ——
        复盘时才知道哪些结论建立在缺数之上。"""
        self._w("decisions/2026-08-07_chief_decision.json", {"market_quality": {"checks": [
            {"field": "zero_amv", "quality": "missing"},
            {"field": "breadth", "quality": "confirmed"},
            {"field": "turnover", "quality": "stale"}]}})
        u = self._run()["unavailable"]
        assert "zero_amv" in u and "turnover" in u and "breadth" not in u

    def test_output_has_no_nan(self):
        """`allow_nan=False`：NaN 会让下游 json.loads 拿到非法值。"""
        assert "NaN" not in json.dumps(self._run())
