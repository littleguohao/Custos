# -*- coding: utf-8 -*-
"""`daily_report` —— 盘前日报（09:05 链，`daily_pipeline` 硬失败 stage）。

⚠️ 这是 owner **每天看的那份东西**，几行文案直接影响动作，所以测的是文案的
**层级关系**而不只是字符串存在：

    结构风控（N 型前低）  >  BBI 提醒  >  基础计划
    任何 BBI 派生建议都必须写「最终动作服从总控」—— `chief_decision` 是唯一输出层

BBI 在 B1 规则里是**预警而非权威**（`01_swing_rules.md` 原话），文案丢掉这层
限定，读者就会把它当买卖依据。
"""

from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

from custos.pipeline import daily_report as dr  # noqa: E402


class TestBbiHoldingReminder:
    def test_missing_bbi_says_unconfirmed_not_a_decision(self):
        """⚠️ 缺 BBI 数据时必须说「不据此调整持仓」——
        不能给出任何方向性建议，否则「算不出」变成了「可以拿住」。"""
        state, rem = dr.bbi_holding_reminder({})
        assert state == "BBI待确认"
        assert "不据此调整持仓" in rem

    def test_above_bbi_keeps_holding_but_defers_to_risk(self):
        state, rem = dr.bbi_holding_reminder(
            {"bbi": 10.0, "above_bbi": True, "bbi_distance_pct": 3.2}
        )
        assert "上方" in state
        assert "继续拿住" in rem
        assert "更高优先级风控仍有效" in rem, (
            "⚠️ BBI 是预警不是权威 —— 必须写明风控优先，否则读者会把它当买卖依据"
        )

    def test_two_days_below_escalates_to_liquidation_review(self):
        _, rem = dr.bbi_holding_reminder(
            {"bbi": 10.0, "above_bbi": False, "consecutive_closes_below_bbi": 2}
        )
        assert "清仓评估" in rem and "硬风险优先" in rem

    def test_first_day_below_waits_for_recovery(self):
        """⚠️ **首日跌破先看次日能否收回**，不直接清仓 ——
        持仓手册：忽略普通盘中冲高回落、检查尾盘/次日修复。"""
        _, rem = dr.bbi_holding_reminder(
            {"bbi": 10.0, "above_bbi": False, "consecutive_closes_below_bbi": 1}
        )
        assert "首日" in rem and "次日" in rem

    def test_garbage_below_days_is_treated_as_zero_not_crash(self):
        """`consecutive_closes_below_bbi` 来自上游 JSON，可能是 'N/A' 之类。"""
        _, rem = dr.bbi_holding_reminder(
            {"bbi": 10.0, "above_bbi": False, "consecutive_closes_below_bbi": "N/A"}
        )
        assert "首日" in rem

    def test_above_none_is_unconfirmed_not_below(self):
        """⚠️ `above_bbi=None` 走「待确认」而不是「下方」——
        把「算不出」显示成「跌破」方向偏空，读者会据此减仓。"""
        state, _ = dr.bbi_holding_reminder({"bbi": 10.0, "above_bbi": None})
        assert state == "BBI待确认"


class TestTechnicalRelation:
    def test_lists_above_and_below_separately(self):
        out = dr.technical_relation(
            {
                "above_ma25": True,
                "above_ma60": True,
                "above_ma144": False,
                "above_ma240": False,
            }
        )
        assert "站上MA25/60" in out and "低于MA144/240" in out

    def test_all_none_says_unconfirmed_not_all_below(self):
        """⚠️⚠️ 四均线全 None 时必须说「待确认」而**不是**「低于MA25/60/144/240」。

        这正是 `ma_flag(None)` 曾把「算不出」显示成「在均线下方」的那类失真 ——
        方向偏空而读者会据此减仓。判据必须是 `is True` / `is False`，不是真值判断。
        """
        assert dr.technical_relation({}) == "四均线待确认"
        assert (
            dr.technical_relation({f"above_ma{n}": None for n in (25, 60, 144, 240)})
            == "四均线待确认"
        )

    def test_partial_data_only_reports_what_is_known(self):
        out = dr.technical_relation({"above_ma25": True, "above_ma240": None})
        assert "站上MA25" in out and "240" not in out


# ---------------------------------------------------------------------------
# v0.57：三份报告角色对齐（盘前=信息处理+预案确认，盘后=复盘纠错+预案主产地）
# ---------------------------------------------------------------------------


class TestHoldingsPlanSection:
    """v0.100（owner）：盘前 §4 合并节「持仓与预案确认」——原 §4 持仓状态与
    §6 预案确认本是同一份「盘后计划 vs 盘前动作」对照，拆开必重复。"""

    _PRIOR = {
        "date": "2026-08-12",
        "next_day_plan": {
            "total_position_range": "40%-60%",
            "new_position_permission": "允许",
            "holding_plans": [
                {
                    "code": "600000.SH",
                    "name": "浦发银行",
                    "priority": "P1",
                    "direction": "减仓",
                    "trigger": "跌破BBI",
                    "invalidation": "收复",
                }
            ],
        },
    }

    _CHIEF = {
        "market_state": "防守",
        "risk_level": "普通",
        "total_position_range": "40%-60%",
        "new_position_permission": "允许",
        "allowed_actions": ["仅观察"],
        "tomorrow_validation": ["量能确认"],
        "holding_actions": [{"code": "600000.SH", "priority": "P1", "action": "减仓"}],
    }

    def _render(self, chief=None, prior=None):
        return "\n".join(
            dr.holdings_plan_section(
                chief if chief is not None else self._CHIEF,
                {},
                {},
                {},
                prior if prior is not None else self._PRIOR,
                "2026-08-12",
            )
        )

    def test_reads_prior_plan_and_confirms(self):
        text = self._render()
        assert "持仓与预案确认" in text
        assert "预案来源：**2026-08-12** 盘后复盘" in text
        assert "✅ 确认（一致）" in text  # 总仓位/新开仓与盘前值一致
        assert "600000 浦发银行" in text and "跌破BBI" in text
        assert "✅ 确认（盘前动作在列）" in text

    def test_divergence_marked(self):
        chief = {**self._CHIEF, "new_position_permission": "禁止"}
        text = self._render(chief=chief)
        assert "⚠️ 变化：盘前为 禁止" in text, "盘后计划与盘前刷新值不一致必须标出来"

    def test_missing_plan_says_so_not_fabricated(self):
        """盘后预案缺失 ⇒ 如实报「无预案可确认」（fail-closed：不编一份）。"""
        text = self._render(prior={})
        assert "预案**缺失**" in text and "无预案可确认" in text
        assert "盘前信息刷新" in text, "缺失时仍给刷新值"

    def test_holding_plan_without_today_action_flagged(self):
        chief = {**self._CHIEF, "holding_actions": []}
        text = self._render(chief=chief)
        assert "盘前无该票动作条目" in text

    def test_role_line_distinguishes_three_reports(self):
        """三份报告的角色一眼可辨（标题区角色行）。"""
        import inspect

        daily_src = inspect.getsource(dr.main)
        assert "盘前=信息处理 + 预案确认" in daily_src
        from custos.pipeline.close_review import final_close_review as fcr

        assert "盘后=复盘纠错 + 条件化预案主产地" in inspect.getsource(fcr.main)
        from custos.pipeline.close_review import review_core as rc

        assert "盘中14:45=按规则的交易提醒" in inspect.getsource(rc)

    def test_mainline_section_removed(self):
        """v0.100：§5「主线、机会与风险」整节下线（口径 TODO #26 待重设计，
        挂着「仅观察参考」不下决策的节是噪声）——main 里不得再渲染。"""
        import inspect

        src = inspect.getsource(dr.main)
        assert "主线、机会与风险" not in src
        assert "stock_pool_section" not in src, "公式选股备选池随 §5 一并下线"
