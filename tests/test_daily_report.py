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
            {"bbi": 10.0, "above_bbi": True, "bbi_distance_pct": 3.2})
        assert "上方" in state
        assert "继续拿住" in rem
        assert "更高优先级风控仍有效" in rem, \
            "⚠️ BBI 是预警不是权威 —— 必须写明风控优先，否则读者会把它当买卖依据"

    def test_two_days_below_escalates_to_liquidation_review(self):
        _, rem = dr.bbi_holding_reminder(
            {"bbi": 10.0, "above_bbi": False, "consecutive_closes_below_bbi": 2})
        assert "清仓评估" in rem and "硬风险优先" in rem

    def test_first_day_below_waits_for_recovery(self):
        """⚠️ **首日跌破先看次日能否收回**，不直接清仓 ——
        持仓手册：忽略普通盘中冲高回落、检查尾盘/次日修复。"""
        _, rem = dr.bbi_holding_reminder(
            {"bbi": 10.0, "above_bbi": False, "consecutive_closes_below_bbi": 1})
        assert "首日" in rem and "次日" in rem

    def test_garbage_below_days_is_treated_as_zero_not_crash(self):
        """`consecutive_closes_below_bbi` 来自上游 JSON，可能是 'N/A' 之类。"""
        _, rem = dr.bbi_holding_reminder(
            {"bbi": 10.0, "above_bbi": False, "consecutive_closes_below_bbi": "N/A"})
        assert "首日" in rem

    def test_above_none_is_unconfirmed_not_below(self):
        """⚠️ `above_bbi=None` 走「待确认」而不是「下方」——
        把「算不出」显示成「跌破」方向偏空，读者会据此减仓。"""
        state, _ = dr.bbi_holding_reminder({"bbi": 10.0, "above_bbi": None})
        assert state == "BBI待确认"


class TestAdjustmentWithBbi:
    """⚠️ **结构风控优先于 BBI** —— N 型前低失守时不看 BBI 说什么。"""

    def test_structural_clear_outranks_bbi(self, monkeypatch):
        monkeypatch.setattr(dr, "n_structure_basis",
                            lambda row, close: {"signal": "structural_clear"})
        out = dr.adjustment_with_bbi({"above_bbi": True}, None)
        assert "结构失效" in out and "优先级高于BBI" in out, \
            "N 型前低是硬结构位，BBI 只是动态趋势预警（v0.17/v0.18 的定案）"

    def test_pullback_failure_is_not_structural_clear(self, monkeypatch):
        """L2（更高回踩低点）失守 ≠ L1（主结构前低）失守 ——
        v0.18 明确区分：前者进减仓/清仓评估，后者才是结构失效。"""
        monkeypatch.setattr(dr, "n_structure_basis",
                            lambda row, close: {"signal": "pullback_failure"})
        out = dr.adjustment_with_bbi({}, None)
        assert "主结构前低未破" in out

    def test_two_days_below_bbi_tightens(self, monkeypatch):
        monkeypatch.setattr(dr, "n_structure_basis", lambda row, close: {"signal": ""})
        out = dr.adjustment_with_bbi(
            {"above_bbi": False, "consecutive_closes_below_bbi": 3}, None)
        assert "清仓评估" in out

    def test_bbi_derived_advice_always_defers_to_chief(self, monkeypatch):
        """⚠️ 任何 BBI 派生建议都要写「最终动作服从总控」——
        `chief_decision` 是唯一输出层，日报是证据层。"""
        monkeypatch.setattr(dr, "n_structure_basis", lambda row, close: {"signal": ""})
        for row in ({"above_bbi": False, "consecutive_closes_below_bbi": 2},
                    {"above_bbi": False}):
            assert "服从总控" in dr.adjustment_with_bbi(row, None)

    def test_above_bbi_falls_back_to_base_plan(self, monkeypatch):
        """站上 BBI 且结构无恙时不额外加戏，回落到基础计划。"""
        monkeypatch.setattr(dr, "n_structure_basis", lambda row, close: {"signal": ""})
        out = dr.adjustment_with_bbi({"above_bbi": True}, None)
        assert "跌破BBI" not in out


class TestTechnicalRelation:
    def test_lists_above_and_below_separately(self):
        out = dr.technical_relation({"above_ma25": True, "above_ma60": True,
                                     "above_ma144": False, "above_ma240": False})
        assert "站上MA25/60" in out and "低于MA144/240" in out

    def test_all_none_says_unconfirmed_not_all_below(self):
        """⚠️⚠️ 四均线全 None 时必须说「待确认」而**不是**「低于MA25/60/144/240」。

        这正是 `ma_flag(None)` 曾把「算不出」显示成「在均线下方」的那类失真 ——
        方向偏空而读者会据此减仓。判据必须是 `is True` / `is False`，不是真值判断。
        """
        assert dr.technical_relation({}) == "四均线待确认"
        assert dr.technical_relation(
            {f"above_ma{n}": None for n in (25, 60, 144, 240)}) == "四均线待确认"

    def test_partial_data_only_reports_what_is_known(self):
        out = dr.technical_relation({"above_ma25": True, "above_ma240": None})
        assert "站上MA25" in out and "240" not in out
