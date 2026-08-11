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
sys.path.insert(0, str(ROOT / "src"))

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
        """未定义契约的产物**放行 + 告警**，不阻断 —— 铺量是渐进的，
        不能因为某个产物还没写 spec 就让消费端拿不到结论。

        ⚠️ 这条**必须用明确虚构的名字**。原本用 `holding_quotes`，第三批把它纳入后
        测试挂了；改用 `mfe_mae`，第四批又把它纳入、又挂一次。
        拿真实产物名当「未定义」的例子，等于断言「它**永远**不会有契约」——
        而铺契约正是在推翻这个断言。用虚构名才测的是 fallback 行为本身。
        """
        r = C.check("__not_a_real_artifact__", {"anything": 1})
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
    # ⚠️ 按**真实 collector 骨架**写全（2026-08-10 核对 `market_timing_collector`
    #    的字面量：6 个 change_pct + as_of + overseas_summary）。
    #    此前这里是 `{}` —— 而下面 docstring 却声称「字段集核对过真实产出」，
    #    这一节没核对到。空夹具让 `overseas_market.as_of` 的契约缺失一直没被发现
    #    （同形状的事 08-10 在 `holding_quotes.indices` 上刚踩过：
    #     spec 写错 + 夹具按错的形状写 ⇒ 两者相互印证，真实产出才是判据）。
    "overseas_market": {"nasdaq_change_pct": None, "sp500_change_pct": None,
                        "sox_change_pct": None, "nikkei_change_pct": None,
                        "kospi_change_pct": None, "hstech_change_pct": None,
                        "as_of": None, "overseas_summary": ""},
    "a_share_indices": {}, "market_breadth": {},
    "sentiment": {}, "turnover": {}, "theme": {}, "macro_policy": {},
    "data_quality": {},
}


class TestProgressiveFillArtifact:
    """⚠️ `market_timing_input` 是**渐进填充产物**，契约只管**结构**。

    它是全项目扇出最大的产物：**19 个消费者**，12 个读 `amv_0`。
    4 个 stage 依次改写它（collector → sync_compass_amv 填 amv_0day →
    merge 置 quality/effective_state → amv_state 切 regime），
    要求「值已填」会让第一个写者就失败。

    `amv_0` 的字段集与空值形态**核对过真实 collector 产出**（跑了带/不带 `--amv` 两种）：
    `amv_change_pct: null`、`amv_zone: ""`（空串）、`as_of: null`。

    ⚠️ 但 `overseas_market` 一节**当初没核对**，夹具写的是 `{}` ——
    于是 `overseas_market.as_of` 的契约缺失一直没被发现（2026-08-10 补，TODO #52）。
    「核对过真实产出」这句话当时只对 `amv_0` 成立，现已按 collector 字面量补全。
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

    def test_only_on_array_spec_warns_not_silently_ignored(self):
        """⚠️ array 类契约按**条目**校验，`only`（顶层字段裁剪）对它无意义。
        静默忽略会让调用方误以为只校验了部分字段、实际查了整份数组 ——
        与 unknown path 一样发 warning（不升级为 error）。"""
        r = C.check("sector_state", [], only=("score",))
        assert r["valid"], "warning 不得升级为 error"
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


class TestHoldingQuotesContract:
    """⚠️ **分支型**：取不到数的票只有 `{code, name, market, available, reason}`。

    5 个消费者、⛔硬失败链。`price` 由落盘时归一补上
    （`q["price"] = q.get("close")`）—— 9 个 quote 变体里有 5 个原本只有 `close`，
    而 **5 个消费者读 `price`**，所以它是契约的一部分而非实现细节；
    但取不到数的票没有它 ⇒ 不设 required。
    """

    # indices 形状按生产实况（collect_holding_quotes._collect_indices）：list，
    # 成功项无 available 键、失败项才有 available=False。曾误记为 dict，
    # 导致 1445 链契约校验必败（2026-08-08 重跑抓到）。
    VALID = {"as_of_date": "2026-08-07", "captured_at": "2026-08-07T17:00:00+08:00",
             "source": "mootdx", "breadth": {},
             "indices": [{"code": "000001", "name": "上证指数", "close": 3500.0,
                          "price": 3500.0, "change_pct": 0.5, "source": "mootdx_online_index"},
                         {"code": "399006", "name": "创业板指",
                          "available": False, "reason": "no data"}],
             "quotes": [{"code": "600000", "name": "甲", "market": "SH", "available": True,
                         "date": "2026-08-07", "date_verified": False,
                         "close": 10.5, "price": 10.5, "change_pct": 1.2},
                        {"code": "920808", "name": "乙", "market": "BJ",
                         "available": False, "reason": "no data"}]}

    def test_both_branches(self):
        assert C.check("holding_quotes", self.VALID)["valid"]

    def test_missing_code_rejected(self):
        bad = {**self.VALID, "quotes": [{"name": "缺code", "available": True}]}
        assert not C.check("holding_quotes", bad)["valid"]

    def test_empty_as_of_date_rejected(self):
        """`as_of_date` 是门控判「行情是否当日」的依据，空串等于没有。"""
        assert not C.check("holding_quotes", {**self.VALID, "as_of_date": ""})["valid"]

    def test_empty_quotes_valid(self):
        """零持仓时 quotes 为空是正常的。"""
        assert C.check("holding_quotes", {**self.VALID, "quotes": []})["valid"]


class TestSectorTechnicalSummaryContract:
    """⚠️ **分支型**：`available=False` 的板块技术字段全不存在。

    消费端有 **96 处 `.get("available")`** —— 那是全项目最常被读的分支键，
    所以契约要保证它是**真布尔**（`"no"` 这种字符串会让所有分支判定翻转）。
    字段集来自 `theme_tracker_report.py:133`(unavailable) / `:148`(available) 的 AST 提取。
    """

    UN = {"theme_id": "chip", "theme_name": "半导体", "priority": 1, "available": False,
          "reason": "无成分股行情", "representative_stocks": [], "semantic_tags": []}
    AV = {**UN, "available": True, "latest_date": "2026-08-07", "trend_state": "上涨",
          "close": 1234.5, "stage": "主升/加速", "score": 80, "action_bias": "可关注核心股"}

    @pytest.mark.parametrize("rows", [[AV, UN], [UN], [AV], []])
    def test_valid_shapes(self, rows):
        assert C.check("sector_technical_summary", rows)["valid"]

    def test_available_must_be_real_bool(self):
        """⚠️ 字符串 `"no"` 是**真值** ⇒ 96 处分支判定会全部翻转。"""
        assert not C.check("sector_technical_summary",
                           [{**self.UN, "available": "no"}])["valid"]

    def test_missing_theme_id_rejected(self):
        bad = [{k: v for k, v in self.UN.items() if k != "theme_id"}]
        assert not C.check("sector_technical_summary", bad)["valid"]


class TestReviewStepContracts:
    EXEC = {"date": "2026-08-07", "status": "ok", "recorded_trade_count": 2,
            "no_trades_confirmed": False, "premarket_snapshot_available": True,
            "rows": [], "behavior_checks": {}, "missing": [], "sources": []}
    ENRICH = {"date": "2026-08-07", "theme_lifecycles": [], "holding_diagnoses": [],
              "next_day_plan": {"holding_plans": []}, "rule_review": {},
              "unavailable": [], "permission_rule": "enrichment cannot override "
              "RiskDecision or ChiefDecision"}

    def test_execution_review_baseline(self):
        assert C.check("execution_review", self.EXEC)["valid"]

    def test_behavior_checks_and_missing_both_required(self):
        """⚠️ `behavior_checks`（纪律结论）与 `missing`（数据缺口）**都要在** ——
        缺一个会让「缺文件」与「违纪」分不开（weekly_review 同类教训）。"""
        for k in ("behavior_checks", "missing"):
            bad = {kk: vv for kk, vv in self.EXEC.items() if kk != k}
            assert not C.check("execution_review", bad)["valid"], k

    def test_trade_count_must_be_finite_number(self):
        assert not C.check("execution_review",
                           {**self.EXEC, "recorded_trade_count": float("nan")})["valid"]
        assert not C.check("execution_review",
                           {**self.EXEC, "recorded_trade_count": True})["valid"]

    def test_review_enrichment_baseline(self):
        assert C.check("review_enrichment", self.ENRICH)["valid"]

    def test_exact_quantity_must_stay_none(self):
        """⚠️ 次日计划**不得给精确数量**：那另需当日行情授权
        （`runtime_gate.position_gate.allow_precise_quantity`），复盘层无权给出。

        契约允许 null（正是它应有的值），但**给了数字也会通过类型检查** ——
        所以这里同时断言「生产者写的是 None」这条由
        `test_review_enrichment.py::test_exact_quantity_always_none` 端到端保证。
        """
        plan = {"code": "600000", "priority": "P0", "direction": "清仓",
                "exact_quantity": None}
        ok = {**self.ENRICH, "next_day_plan": {"holding_plans": [plan]}}
        assert C.check("review_enrichment", ok)["valid"]
        # 缺这个键则拒收 —— 不能靠「没写」来表达「不给数量」
        bad = {**self.ENRICH, "next_day_plan": {"holding_plans": [
            {k: v for k, v in plan.items() if k != "exact_quantity"}]}}
        assert not C.check("review_enrichment", bad)["valid"]

    def test_plan_priority_domain(self):
        bad = {**self.ENRICH, "next_day_plan": {"holding_plans": [
            {"code": "600000", "priority": "高", "direction": "清仓",
             "exact_quantity": None}]}}
        assert not C.check("review_enrichment", bad)["valid"], "计划用 P0-P3，不是高/中/低"

    def test_permission_rule_required(self):
        """⚠️ 复盘层是**解释**不是裁决 —— 这句必须在产物里。"""
        bad = {k: v for k, v in self.ENRICH.items() if k != "permission_rule"}
        assert not C.check("review_enrichment", bad)["valid"]


class TestFourthBatch:
    """第四批：硬失败链之外的产物。这批的价值不是防链路挂掉（它们本来就不阻断），
    而是「消费端读到的东西是不是它以为的东西」。
    """

    def test_stock_pool_bucket_domain(self):
        base = {"date": "2026-08-07", "status": "ok", "bucket_counts": {},
                "candidates": [{"code": "600000", "bucket": "A",
                                "next_step": "generate_buy_plan",
                                "risk_flags": [], "entry_reason": []}]}
        assert C.check("stock_pool", base)["valid"]
        bad = {**base, "candidates": [{**base["candidates"][0], "bucket": "S"}]}
        assert not C.check("stock_pool", bad)["valid"], "分层只有 A/B/C/D"

    def test_final_review_quality_domain(self):
        base = {"date": "2026-08-07", "report_quality": "degraded", "unavailable": [],
                "revalued_positions": [], "next_day_plan": {},
                "precise_quantity_allowed": False, "quotes_current": True,
                "technical_current": False}
        assert C.check("final_review", base)["valid"]
        assert not C.check("final_review", {**base, "report_quality": "ok"})["valid"]

    def test_precise_quantity_allowed_must_be_bool(self):
        """⚠️ 这个布尔来自门控，决定次日计划能不能给精确数量。
        `1` 会通过真值判定但不是布尔 —— 类型收紧是为了让「未授权」不能伪装成已授权。"""
        base = {"date": "2026-08-07", "report_quality": "complete", "unavailable": [],
                "revalued_positions": [], "next_day_plan": {}, "quotes_current": True,
                "technical_current": True, "precise_quantity_allowed": 1}
        assert not C.check("final_review", base)["valid"]

    def test_holding_review_priority_domain(self):
        """⚠️ `holding_review` 是 RiskDecision 的**直接上游**：
        `action` ∈ {止损,清仓} ⇒ 高优先风险。`priority` 用 P0-P3。"""
        ok = [{"code": "600000", "action": "止损", "priority": "P1", "reason": ["破位"]}]
        assert C.check("holding_review", ok)["valid"]
        assert not C.check("holding_review",
                           [{**ok[0], "priority": "高"}])["valid"]

    def test_mfe_mae_requires_coverage(self):
        """⚠️ 必须报 coverage —— 「卖飞 N 笔」不说分母会被读成「没卖飞」。"""
        assert C.check("mfe_mae", {"date": "2026-08-07", "coverage": {},
                                   "holdings": []})["valid"]
        assert not C.check("mfe_mae", {"date": "2026-08-07", "holdings": []})["valid"]

    def test_fund_flow_status_domain_and_per_type_status(self):
        """⚠️ `sector_rank_status` 要单独留痕：industry 成功而 concept 失败时，
        顶层 `status=partial` 说不出是哪个坏了。"""
        base = {"date": "2026-08-07", "status": "partial", "stock_rank": [],
                "sector_rank": {}, "sector_rank_status": {"industry": {"status": "ok"},
                                                          "concept": {"status": "failed"}},
                "source": "eastmoney_direct_api"}
        assert C.check("fund_flow_rank", base)["valid"]
        assert not C.check("fund_flow_rank", {**base, "status": "degraded"})["valid"]
        assert not C.check("fund_flow_rank",
                           {k: v for k, v in base.items()
                            if k != "sector_rank_status"})["valid"]

    def test_formula_hits_fields_come_from_result_not_summary(self):
        """⚠️ 字段取自**落盘的 `result`**，不是只被 print 的 `summary`。

        第一版按 summary 提字段（它有 `date`/`hit_total`，result 没有），
        接生产者时才发现 —— 契约要对着**真正写进文件的那个对象**建。
        """
        ok = {"status": "ok", "universe_size": 5000, "universe_source": "tq_local",
              "st_filter": "ok", "formulas": []}
        assert C.check("formula_hits", ok)["valid"]
        # summary 才有的键不该被要求
        assert C.check("formula_hits", {k: v for k, v in ok.items()})["valid"]

    def test_candidates_enriched_records_signal_date_contract(self):
        """⚠️ 公式命中来自 TQ 在线（按最新交易日报出），而充实段用本地日线
        `last_date==date` 校验 —— 两者口径不同，这句话必须写进产物。"""
        ok = {"status": "ok", "candidates": [], "excluded": [], "st_filter": "ok",
              "signal_date_contract": "公式命中按最新交易日报出；本段以 last_date==date 为准"}
        assert C.check("candidates_enriched", ok)["valid"]
        assert not C.check("candidates_enriched",
                           {k: v for k, v in ok.items()
                            if k != "signal_date_contract"})["valid"]

    def test_rss_evidence_three_trust_fields(self):
        """⚠️ `quality` / `confirmed` / `transport_verified` 三个一起决定
        「这条能不能当既成事实」。契约查各自的域 ——
        **跨字段矛盾**（未校验却 confirmed）查不出来，
        由 `test_rss_collector.py::TestTierQuality` 覆盖。"""
        ok = [{"item_id": "abc", "source_id": "gov", "source_tier": "S", "title": "t",
               "quality": "candidate", "confirmed": False, "transport_verified": False}]
        assert C.check("rss_evidence", ok)["valid"]
        assert not C.check("rss_evidence",
                           [{**ok[0], "quality": "verified"}])["valid"]

    def test_rss_candidates_requires_item_id(self):
        """`item_id` 是去重与可追溯的键，`rss_collector` 一定会写。"""
        ok = [{"item_id": "abc", "source_id": "gov", "relevance_score": 80,
               "matched_themes": [], "matched_holdings_or_pool": {"names": [], "codes": []},
               "filter_session": "premarket"}]
        assert C.check("rss_candidates", ok)["valid"]
        assert not C.check("rss_candidates",
                           [{k: v for k, v in ok[0].items() if k != "item_id"}])["valid"]

    def test_postclose_digest_permission_rule(self):
        """⚠️ 新闻只能加验证条件或收紧风险，**不能直接放宽交易权限**。"""
        ok = {"date": "2026-08-07", "status": "ok", "sections": {}, "missing": [],
              "permission_rule": "news may add validation or tighten risk; "
                                 "it cannot directly increase trading permissions"}
        assert C.check("postclose_news_digest", ok)["valid"]
        assert not C.check("postclose_news_digest",
                           {k: v for k, v in ok.items()
                            if k != "permission_rule"})["valid"]
