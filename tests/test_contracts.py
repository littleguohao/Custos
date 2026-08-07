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
    # ⚠️ `evidence_date` 是 2026-08-07 新增：09:05 盘前也产 risk_decision，
    # 那时当日 K 线不存在 ⇒ 依据是前一交易日收盘。缺它下游只能按文件名判「当日」。
    "date": "2026-08-07", "evidence_date": "2026-08-06", "market_regime": "空头",
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


class TestEvidenceDate:
    """⚠️ `risk_decision.evidence_date` —— **证据日 ≠ 运行日**。

    09:05 盘前也会跑 `generate_risk_and_sectors`（`daily_pipeline` 里它不受
    session_type 限制），那时当日 K 线还不存在，所以盘前产出的 risk_decision
    打着当日日期、依据却是**前一交易日收盘**。

    不把这件事写进产物，14:45 报告只能按文件名判「当日」，
    读者会以为风控依据是今天的 —— 同「把缺数渲染成读数」那一类失真。
    """

    def test_required(self):
        obj = {k: v for k, v in VALID_RISK.items() if k != "evidence_date"}
        r = C.check("risk_decision", obj)
        assert not r["valid"] and any("evidence_date" in e for e in r["errors"])

    def test_empty_allowed(self):
        """技术面全缺时取不到证据日 —— 允许空串（比编一个日期好）。"""
        assert C.check("risk_decision", {**VALID_RISK, "evidence_date": ""})["valid"]

    def test_null_rejected(self):
        assert not C.check("risk_decision", {**VALID_RISK, "evidence_date": None})["valid"]


VALID_MTI = {
    "date": "2026-08-07", "collector_version": "v1",
    "amv_0": {"amv_zone": "", "amv_change_pct": None, "as_of": None},
    "overseas_market": {}, "a_share_indices": {}, "market_breadth": {},
    "sentiment": {}, "turnover": {}, "theme": {}, "macro_policy": {},
    "data_quality": {},
}


class TestProgressiveFillArtifact:
    """⚠️ `market_timing_input` 是**渐进填充产物**，契约只管**结构**。

    它是全项目扇出最大的产物：**19 个消费者**，12 个读 `amv_0`。
    4 个 stage 依次改写它（collector → sync_compass_amv 填 amv_0day →
    merge 置 quality/effective_state → amv_state 切 regime），
    要求「值已填」会让第一个写者就失败。

    字段集与空值形态**核对过真实 collector 产出**（跑了带/不带 `--amv` 两种）：
    `amv_change_pct: null`、`amv_zone: ""`（空串）、`as_of: null`。
    """

    def test_collector_baseline_valid(self):
        assert C.check("market_timing_input", VALID_MTI)["valid"]

    def test_amv_zone_empty_string_allowed(self):
        """0AMV 未填时 `amv_zone` 就是空串（实测），不得因此判畸形。"""
        assert C.check("market_timing_input",
                       {**VALID_MTI, "amv_0": {**VALID_MTI["amv_0"], "amv_zone": ""}})["valid"]

    def test_as_of_null_allowed_deliberately(self):
        """⚠️ `as_of` **刻意留 None**（collector 源码原话）：08:50 手工读数属哪个
        数据日无法自证，「编一个 as_of 等于给门控一个假的新鲜度」。"""
        assert C.check("market_timing_input", VALID_MTI)["valid"]

    def test_amv_change_pct_nan_still_rejected(self):
        """允许 null 不等于允许 NaN —— NaN 会让下游阈值判定静默为 False。"""
        bad = {**VALID_MTI, "amv_0": {**VALID_MTI["amv_0"], "amv_change_pct": float("nan")}}
        assert not C.check("market_timing_input", bad)["valid"]

    def test_missing_section_rejected(self):
        """11 个顶层节缺任一即畸形 —— 消费端 19 处 `.get()` 会静默拿到 None。"""
        bad = {k: v for k, v in VALID_MTI.items() if k != "turnover"}
        r = C.check("market_timing_input", bad)
        assert not r["valid"] and any("turnover" in e for e in r["errors"])

    def test_quality_and_effective_state_optional_but_domained(self):
        """⚠️ 这两个由 **merge** 写、collector 刻意不置 ⇒ 非必填；
        但**出现时必须在域内** —— 正是审计 B1 的所在。"""
        assert C.check("market_timing_input", VALID_MTI)["valid"], "缺它们要放行"
        bad = {**VALID_MTI, "amv_0": {**VALID_MTI["amv_0"], "effective_state": "空头触发"}}
        assert not C.check("market_timing_input", bad)["valid"], "未归一的词表必须拒收"


class TestOnlyScoping:
    """⚠️ `only` 是**责任边界**，不是「放松校验」的出口。

    实测两次撞在这上面（都在 `merge_incremental_market` 上）：
      · 它用 `setdefault`，只为**有增量数据的节**建节 ⇒ 只给 breadth 增量时，
        sentiment/turnover 本就不该存在
      · 它的第一处落盘**根本不碰 amv_0** ⇒ 要求 amv_0 存在是替 collector 背责
    ⇒ `only` 一律去掉 `required`：部分写者只保证「我写的字段格式正确」，
       存在性由文档创建者（collector 的完整 require）保证。
    """

    OWN = ("amv_0.quality", "amv_0.effective_state", "amv_0.as_of")

    def test_catches_unnormalized_regime(self):
        """审计 B1：`effective_state` 写成「空头触发」会让下游精确等值比较落空、
        `allow_add=False` 漏置。契约在 merge 那一步就该拦住。"""
        r = C.check("market_timing_input",
                    {"amv_0": {"effective_state": "空头触发", "quality": "confirmed"}},
                    only=self.OWN)
        assert not r["valid"] and "空头触发" in r["errors"][0]

    def test_accepts_normalized(self):
        assert C.check("market_timing_input",
                       {"amv_0": {"effective_state": "空头", "quality": "confirmed"}},
                       only=self.OWN)["valid"]

    def test_catches_misspelled_quality(self):
        r = C.check("market_timing_input", {"amv_0": {"quality": "comfirmed"}},
                    only=self.OWN)
        assert not r["valid"]

    def test_absent_section_allowed(self):
        """整块不存在是第一处落盘的常态 —— 不得判畸形。"""
        assert C.check("market_timing_input", {}, only=self.OWN)["valid"]

    def test_unlisted_sibling_not_checked(self):
        """⚠️ `amv_zone` 不在 only 里就**不该被检查** —— 它是 collector 派生的。

        （第一版的 `_narrow` 把裁剪结果合并回完整集，等于没裁，
        `amv_0.amv_zone: 缺失` 照样报出来。）
        """
        assert C.check("market_timing_input", {"amv_0": {"quality": "confirmed"}},
                       only=self.OWN)["valid"]

    def test_typo_in_only_path_warns(self):
        """`only` 里的路径拼错会让校验**变成空操作** —— 必须告警。"""
        r = C.check("market_timing_input", {}, only=("amv_0.qualtiy",))
        assert any("only" in w for w in r["warnings"])


class TestSectorStateContract:
    VALID = [{"date": "2026-08-07", "sector": "半导体", "state": "主升", "trend": "上涨",
              "trade_permission": "支持", "score": 80.0, "risk_flags": []}]

    def test_baseline(self):
        assert C.check("sector_state", self.VALID)["valid"]

    def test_nan_score_rejected(self):
        """⚠️ 这条对应实际 bug：`score` 为 NaN 时 `nan >= 60` 恒为 False
        ⇒ 板块**静默降级**成「观察」且无告警。生产者已过 fnum，契约在此兜底。"""
        bad = [{**self.VALID[0], "score": float("nan")}]
        assert not C.check("sector_state", bad)["valid"]

    def test_null_score_allowed(self):
        """板块技术面没给分是正常的 ⇒ null 放行（与 NaN 区别对待）。"""
        assert C.check("sector_state", [{**self.VALID[0], "score": None}])["valid"]

    @pytest.mark.parametrize("field,bad", [
        ("state", "暴涨"), ("trend", "横盘"), ("trade_permission", "买入")])
    def test_enum_domains(self, field, bad):
        assert not C.check("sector_state", [{**self.VALID[0], field: bad}])["valid"]

    def test_empty_list_valid(self):
        assert C.check("sector_state", [])["valid"]


class TestHoldingTechnicalContract:
    """⚠️ **分支型产物**：`technical_available=False` 时后面那堆技术字段**全不存在**
    （生产者直接 `return {**it, 'technical_available': False, 'technical_error': ...}`）。
    所以契约只要求 `code` + `technical_available` —— 要求技术字段会让
    「取不到技术面」这个正常状态被判成畸形产物。
    """

    def test_both_branches_valid(self):
        rows = [{"code": "600000", "technical_available": True,
                 "latest_date": "2026-08-07", "trend_state": "上涨"},
                {"code": "600001", "technical_available": False,
                 "technical_error": "vipdoc 缺失"}]
        assert C.check("holding_technical_summary", rows)["valid"]

    def test_missing_code_rejected(self):
        assert not C.check("holding_technical_summary", [{"technical_available": True}])["valid"]

    def test_available_must_be_bool(self):
        assert not C.check("holding_technical_summary",
                           [{"code": "600000", "technical_available": "yes"}])["valid"]

    def test_empty_list_valid(self):
        assert C.check("holding_technical_summary", [])["valid"]
