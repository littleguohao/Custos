# -*- coding: utf-8 -*-
"""持仓计划影子判定钉测（v0.83，「因子×止盈×止损」架构 Phase C）。

影子期铁律：plan 信号只落盘与展示，**不得影响 final_priority / bucket / 权限 /
任何既有字段**。这里钉住：同输入带/不带 plan 时 final 完全一致；破位产生影子
信号；无计划（存量持仓常态，早于机制落地）如实标 plan_missing 不炸；渲染层
「⚠️影子不一致」标注与「无计划」呈现。
"""

from __future__ import annotations

from custos.pipeline.holdings.b1_holding_state import evaluate, shadow_compare_line
from custos.pipeline.close_review import review_core as rc


def _row(**over):
    row = {
        "code": "600000",
        "close": 9.0,
        "trend_state": "上涨",
        "box20_position": "箱体上半区",
        "above_bbi": True,
        "latest_date": "2026-08-20",
        "holding_pnl_pct": -0.08,
    }
    row.update(over)
    return row


def _plan(stop_price=None, with_two_bull=False):
    return {
        "entry_date": "2026-08-18",
        "entry_price": 10.0,
        "stop": {
            "rule_id": "stock_pool_stop_ref",
            "price": stop_price,
            "basis": "候选池止损参考",
        },
        "take_profit": (
            {"scale_out_two_bull": {"params": {"require_above_bbi": True}}}
            if with_two_bull
            else {}
        ),
        "source": "candidate:2026-08-17",
    }


class TestShadowDoesNotAffectFinal:
    def test_final_identical_with_and_without_plan(self):
        """同输入带/不带 plan，final_priority/action/reason 与 signals 完全一致。"""
        row = _row()
        without = evaluate(row, "做多", price=9.0, price_date="2026-08-20")
        with_plan = evaluate(
            row, "做多", price=9.0, price_date="2026-08-20", plan=_plan(stop_price=9.5)
        )
        for key in (
            "final_priority",
            "final_action",
            "final_reason",
            "signals",
            "permissions",
            "action_plan",
        ):
            assert with_plan[key] == without[key], key

    def test_shadow_signals_never_enter_signal_order(self):
        """影子信号名不在 SIGNAL_ORDER 里——它们不参与现行裁决排序。"""
        from custos.pipeline.holdings.b1_holding_state import SIGNAL_ORDER

        assert "plan_stop_breach" not in SIGNAL_ORDER
        assert "plan_tp_scale_out" not in SIGNAL_ORDER


class TestShadowSignals:
    def test_stop_breach_produces_p0_shadow(self):
        """现价 ≤ 计划止损价 ⇒ plan_stop_breach（P0 级影子）。"""
        state = evaluate(
            _row(),
            "做多",
            price=9.0,
            price_date="2026-08-20",
            plan=_plan(stop_price=9.5),
        )
        shadow = state["shadow"]
        assert shadow["reason"] == "ok"
        assert shadow["plan_based_priority"] == "P0"
        assert shadow["signals"][0]["signal"] == "plan_stop_breach"
        assert "9.50" in shadow["signals"][0]["reason"]
        # 铁律：现行判定仍是现行判定（-8% ⇒ loss_reduction P1，不被影子抬成 P0）
        assert state["final_priority"] == "P1"

    def test_stop_not_breached_no_signal(self):
        state = evaluate(
            _row(),
            "做多",
            price=9.0,
            price_date="2026-08-20",
            plan=_plan(stop_price=8.0),
        )
        assert state["shadow"]["signals"] == []
        assert state["shadow"]["plan_based_priority"] == "P3"

    def test_take_profit_two_bull_hit(self):
        """计划里 enabled 的 scale_out_two_bull 命中（BBI 上方连续两根中大阳）。"""
        row = _row(
            holding_pnl_pct=0.15,
            price_volume={"available": True, "two_medium_large_bull": True},
        )
        state = evaluate(
            row,
            "做多",
            price=11.0,
            price_date="2026-08-20",
            plan=_plan(stop_price=8.0, with_two_bull=True),
        )
        kinds = [s["signal"] for s in state["shadow"]["signals"]]
        assert "plan_tp_scale_out" in kinds
        assert state["shadow"]["plan_based_priority"] == "P2"

    def test_take_profit_requires_current_price_volume(self):
        """量价证据非当日（陈旧）⇒ 影子止盈不发——与现行 two_bull 同一门槛。"""
        row = _row(
            holding_pnl_pct=0.15,
            price_volume={"available": True, "two_medium_large_bull": True},
        )
        state = evaluate(
            row,
            "做多",
            price=11.0,
            price_date="2026-08-21",  # 与 latest_date 不同日 ⇒ 量价非当日
            plan=_plan(stop_price=8.0, with_two_bull=True),
        )
        assert state["shadow"]["signals"] == []


class TestPlanMissing:
    def test_no_plan_marks_plan_missing_not_crash(self):
        """无计划（存量持仓常态，早于机制落地）如实标 plan_missing，不报错。"""
        state = evaluate(_row(), "做多", price=9.0, price_date="2026-08-20")
        shadow = state["shadow"]
        assert shadow["reason"] == "plan_missing"
        assert shadow["signals"] == []
        assert shadow["plan_based_priority"] is None

    def test_malformed_plan_treated_as_missing(self):
        """非 dict 的 plan 一律按无计划处理，不炸。"""
        for junk in (None, "x", []):
            state = evaluate(
                _row(), "做多", price=9.0, price_date="2026-08-20", plan=junk
            )
            assert state["shadow"]["reason"] == "plan_missing"

    def test_plan_with_malformed_stop_no_crash(self):
        """plan 是 dict 但 stop 形状不对：如实按「有计划、无有效止损价」处理，不炸。"""
        state = evaluate(
            _row(),
            "做多",
            price=9.0,
            price_date="2026-08-20",
            plan={"stop": "not-a-dict"},
        )
        assert state["shadow"]["reason"] == "ok"
        assert state["shadow"]["signals"] == []


class TestShadowCompareLine:
    def test_divergence_marked(self):
        line = shadow_compare_line(
            "600000",
            "浦发",
            "P1",
            "P1 减仓评估",
            {
                "reason": "ok",
                "plan_based_priority": "P0",
                "plan_based_action": "计划止损位清仓评估",
            },
        )
        assert "⚠️影子不一致" in line
        assert "P0 计划止损位清仓评估" in line and "P1 减仓评估" in line

    def test_agreement_not_marked(self):
        line = shadow_compare_line(
            "600000",
            "浦发",
            "P1",
            "P1 减仓评估",
            {
                "reason": "ok",
                "plan_based_priority": "P1",
                "plan_based_action": "计划止损位清仓评估",
            },
        )
        assert "⚠️影子不一致" not in line

    def test_missing_plan_label(self):
        line = shadow_compare_line("600000", "浦发", "P3", "P3 条件持有", {})
        assert "无计划（早于机制落地）" in line


class TestReviewCoreShadowRender:
    def test_section_renders_missing_and_divergence(self):
        actions = [
            {
                "priority": "P1",
                "code": "600000",
                "name": "浦发",
                "action": "减仓评估",
                "b1_holding_state": {
                    "shadow": {
                        "reason": "ok",
                        "plan_based_priority": "P0",
                        "plan_based_action": "计划止损位清仓评估",
                    }
                },
            },
            {
                "priority": "P3",
                "code": "601398",
                "name": "工行",
                "action": "持有观察",
                "b1_holding_state": {"shadow": {"reason": "plan_missing"}},
            },
        ]
        lines: list[str] = []
        rc.render_shadow_comparison(lines, actions)
        text = "\n".join(lines)
        assert "持仓计划影子对比" in text
        assert "⚠️影子不一致" in text
        assert "无计划（早于机制落地）" in text
