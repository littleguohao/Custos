"""`contracts.py` —— 产物 schema 的**可执行**唯一来源。

2026-08-07 架构审查的产物：19 种 stage 产物里文档覆盖 6 个、代码级校验只有 1 个，
消费端 2391 处 `.get()` 不带默认值。本模块把「钱的路径」4 个产物的 schema
变成参与执行的代码。

⚠️ 这里的每一条校验规则都对应一个**今天实际查到的 bug**，不是想象出来的防御。
测试里逐条标出对应关系 —— 这样将来有人想放宽某条规则时，能看到放宽的代价。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "07_tools"))

import contracts as C  # noqa: E402

VALID_GATE = {
    "date": "2026-08-07",
    "calendar": {"is_trading_day": True},
    "position_freshness": {"status": "confirmed"},
    "technical_freshness": {"status": "confirmed"},
    "position_gate": {"status": "pass", "allow_position_increase": True,
                      "allow_position_reduction": True, "allow_precise_quantity": True,
                      "market_regime": "中性", "limitations": []},
    "market_quality": {"status": "pass", "quality_score": 0.9, "checks": [],
                       "limitations": []},
    "generated_at": "2026-08-07T17:00:00+08:00",
}
VALID_RISK = {
    "date": "2026-08-07", "market_regime": "空头",
    "regime_directive": {"reduce_top_priority": True},
    "risk_level": "强风控", "forbidden_actions": ["止损"],
    "stock_risks": [{"code": "600000", "risk_type": "亏损扩大", "action": "止损",
                     "priority": "高", "reason": "浮亏达阈值"}],
}
VALID_B1 = [{
    "version": "B1-holding-v1", "code": "600000", "market_regime": "中性",
    "final_priority": "P0", "final_action": "止损/清仓评估", "final_reason": "跌破L1",
    "signals": [{"signal": "hard_loss", "priority": "P0", "action": "止损/清仓评估",
                 "reason": "达到-10%硬风控阈值"}],
    "unavailable": [], "facts": {},
}]


class TestBaselineValid:
    """基线：真实产出形状必须通过。**这是最重要的一组** ——
    schema 比生产者严会打断 live 链。"""

    def test_gate(self):
        assert C.check("runtime_gate", VALID_GATE)["valid"]

    def test_risk(self):
        assert C.check("risk_decision", VALID_RISK)["valid"]

    def test_b1(self):
        assert C.check("b1_holding_state", VALID_B1)["valid"]

    def test_empty_collections_are_valid(self):
        """空数组是合法的（没有风险 / 没有信号是正常状态），不得当成缺数。"""
        assert C.check("risk_decision", {**VALID_RISK, "stock_risks": [],
                                        "forbidden_actions": [],
                                        "risk_level": "普通"})["valid"]
        assert C.check("b1_holding_state", [])["valid"]


class TestNullVsMissing:
    """⚠️ 对应今天查到的 bug：`.get(k, 默认值)` 在 **key 存在而值为 None** 时
    返回 **None 而不是默认值**。

    实例：`risk_map` 里 `str(x.get("code", ""))` 遇到 `{"code": null}` 得到
    字符串 `"None"`（真值）⇒ 建出一个叫 "None" 的幽灵持仓键。
    """

    def test_null_value_is_an_error_not_missing(self):
        r = C.check("runtime_gate", {**VALID_GATE, "date": None})
        assert not r["valid"] and "值为 null" in r["errors"][0]

    def test_missing_key_reports_missing(self):
        g = {k: v for k, v in VALID_GATE.items() if k != "date"}
        r = C.check("runtime_gate", g)
        assert not r["valid"] and any("date: 缺失" in e for e in r["errors"])

    def test_nested_null_detected(self):
        g = {**VALID_GATE, "position_gate": {**VALID_GATE["position_gate"],
                                            "allow_position_increase": None}}
        r = C.check("runtime_gate", g)
        assert not r["valid"] and any("allow_position_increase" in e for e in r["errors"])


class TestEnumWhitelist:
    """⚠️ 对应审计 A3：门控缺失兜底成 `{}` ⇒ `status=None` ⇒ 两个
    `== "blocked"` 判定落空 ⇒ **没有门控照样输出「允许开新仓」**。

    白名单式校验（而不是黑名单「不等于 blocked」）是 fail-closed 的前提。
    """

    @pytest.mark.parametrize("bad", [None, "blocked_", "PASS", "", "ok"])
    def test_unknown_gate_status_rejected(self, bad):
        g = {**VALID_GATE, "market_quality": {**VALID_GATE["market_quality"], "status": bad}}
        assert not C.check("runtime_gate", g)["valid"]

    def test_regime_must_be_normalized(self):
        """⚠️ 对应审计 B1：`market_regime` 曾只读 `effective_state` 且精确等值，
        「空头触发」这套词表会让 `allow_add=False` 漏置。
        契约要求它已经过 `normalize_regime` 归一。"""
        assert not C.check("risk_decision", {**VALID_RISK, "market_regime": "空头触发"})["valid"]
        assert C.check("risk_decision", {**VALID_RISK, "market_regime": "空头"})["valid"]

    def test_b1_priority_domain(self):
        bad = [{**VALID_B1[0], "final_priority": "P4"}]
        assert not C.check("b1_holding_state", bad)["valid"]

    def test_risk_priority_domain(self):
        bad = {**VALID_RISK, "stock_risks": [{**VALID_RISK["stock_risks"][0],
                                             "priority": "紧急"}]}
        assert not C.check("risk_decision", bad)["valid"]

    def test_signal_priority_domain(self):
        bad = [{**VALID_B1[0], "signals": [{**VALID_B1[0]["signals"][0], "priority": "高"}]}]
        assert not C.check("b1_holding_state", bad)["valid"], "信号用 P0-P3，不是高/中/低"


class TestFiniteness:
    """⚠️ 对应 `sector_state.score` 为 NaN 的那条：`nan >= 60` 恒为 False
    ⇒ 板块**静默降级**成「观察」，且没有任何告警。"""

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_number_rejected(self, bad):
        g = {**VALID_GATE, "market_quality": {**VALID_GATE["market_quality"],
                                              "quality_score": bad}}
        r = C.check("runtime_gate", g)
        assert not r["valid"] and any("非有限值" in e for e in r["errors"])

    def test_zero_is_valid(self):
        """`0` 是合法读数，不得因为「假值」被拒。"""
        g = {**VALID_GATE, "market_quality": {**VALID_GATE["market_quality"],
                                              "quality_score": 0}}
        assert C.check("runtime_gate", g)["valid"]


class TestTypes:
    def test_bool_is_not_a_number(self):
        """`True` 是 `int` 的子类 —— 数字字段收到布尔必须报错，
        否则 `quality_score=True` 会被当成 1.0 通过。"""
        g = {**VALID_GATE, "market_quality": {**VALID_GATE["market_quality"],
                                              "quality_score": True}}
        assert not C.check("runtime_gate", g)["valid"]

    def test_number_is_not_a_bool(self):
        g = {**VALID_GATE, "position_gate": {**VALID_GATE["position_gate"],
                                            "allow_position_increase": 1}}
        assert not C.check("runtime_gate", g)["valid"], "权限字段必须是真布尔"

    def test_empty_string_rejected_where_non_empty_required(self):
        assert not C.check("risk_decision", {**VALID_RISK, "date": "  "})["valid"]

    def test_array_artifact_rejects_object(self):
        assert not C.check("b1_holding_state", VALID_B1[0])["valid"], \
            "b1_holding_state 落盘是数组"


class TestWarningsNotErrors:
    def test_unknown_permission_is_warning_only(self):
        """⚠️ `new_position_permission` 是**从 markdown 报告正则抽出来的**
        （`chief_decision_report:39`），上游改一个字就会出现新值。
        所以只作已知值告警、**不阻断** —— 强枚举会让报告措辞变动打断交易计划。
        """
        obj = {"date": "2026-08-07", "market_state": "中性",
               "total_position_range": "20%-40%",
               "new_position_permission": "视情况而定",  # 未登记的措辞
               "risk_level": "普通", "position_gate": {}, "market_quality": {},
               "allowed_actions": [], "forbidden_actions": [], "holding_actions": [],
               "buy_actions": [], "risk_notice": "x",
               "sources": {"risk_decision": "a", "runtime_gate": "b"}}
        r = C.check("chief_decision", obj)
        assert r["valid"], "未知措辞不得阻断"
        assert any("上游措辞可能变了" in w for w in r["warnings"])

    def test_undefined_artifact_warns_not_fails(self):
        r = C.check("holding_quotes", {"anything": 1})
        assert r["valid"] and "尚未定义契约" in r["warnings"][0]


class TestRequireVsCheck:
    """⚠️ **写严、读松**：生产者硬失败，消费者只拿结论。

    为什么不对消费者也强制：README 记着 2026-07-30 的事故 ——
    「悄悄收紧硬闸 + 收紧 stale 判定叠加，导致 17:00 盘后复盘直接失败」。
    消费端的降级策略是校准过的，不能因为新增校验就改变它。
    """

    def test_require_raises_with_all_errors(self):
        with pytest.raises(SystemExit) as e:
            C.require("runtime_gate", {})
        msg = str(e.value)
        assert "产物契约校验失败" in msg and "runtime_gate" in msg
        assert msg.count("缺失") >= 5, "应一次报出全部缺失字段，而不是只报第一个"

    def test_require_returns_object_when_valid(self):
        assert C.require("runtime_gate", VALID_GATE) is VALID_GATE

    def test_check_never_raises(self):
        for bad in [None, [], "x", 0, {"a": float("nan")}]:
            C.check("runtime_gate", bad)   # 不得抛异常
