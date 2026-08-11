"""`chief_decision_report` —— **最终交易计划输出层**，`daily_pipeline` 硬失败 stage。

覆盖率清点（2026-08-07）：19%、58 语句未覆盖。它是 README 里
「chief_decision 是最终交易计划输出层」那一层 —— **开仓权限、禁止动作、
持仓处理优先级都在这里定稿**。这条路径上的每个判定都直接对着钱。

模块里记着一次真实事故（审计 A3）：门控缺失时曾兜底成 `{}` ⇒
`status=None`、`allow_position_increase=None`，两个 `== 'blocked'` / `is False` 判定
全部落空，于是**没有门控的情况下照样输出「允许开新仓」的计划**。
现改为强制输入 + 未知按 blocked，本测试把这些语义逐条钉住。
"""

from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

from custos.pipeline.market_timing import chief_decision_report as cdr  # noqa: E402
import sys

MT_MD = (
    "状态：**进攻**\n择时评分：**78**\n建议总仓位：**40%-60%**\n"
    "今日是否允许开新仓：**允许**\n"
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for sub in ("risk", "quality", "holdings", "sectors", "decisions"):
        (tmp_path / sub).mkdir()
    plans = tmp_path / "plans"
    plans.mkdir()
    monkeypatch.setattr(cdr, "DATA", tmp_path)
    monkeypatch.setattr(cdr, "PLANS", plans)
    return tmp_path, plans


def _write(
    data,
    plans,
    day,
    *,
    risk=None,
    gate=None,
    holdings=None,
    b1=None,
    sectors=None,
    mt=MT_MD,
):
    if risk is not None:
        (data / "risk" / f"{day}_risk_decision.json").write_text(
            json.dumps(risk, ensure_ascii=False), encoding="utf-8"
        )
    if gate is not None:
        (data / "quality" / f"{day}_runtime_gate.json").write_text(
            json.dumps(gate, ensure_ascii=False), encoding="utf-8"
        )
    if holdings is not None:
        (data / "holdings" / f"{day}_holding_review.json").write_text(
            json.dumps(holdings, ensure_ascii=False), encoding="utf-8"
        )
    if b1 is not None:
        (data / "holdings" / f"{day}_b1_holding_state.json").write_text(
            json.dumps(b1, ensure_ascii=False), encoding="utf-8"
        )
    if sectors is not None:
        (data / "sectors" / f"{day}_sector_state.json").write_text(
            json.dumps(sectors, ensure_ascii=False), encoding="utf-8"
        )
    if mt is not None:
        (plans / f"{day}_market_timing_score.md").write_text(mt, encoding="utf-8")


def _run(data, plans, day, monkeypatch, **kw):
    _write(data, plans, day, **kw)
    monkeypatch.setattr(sys, "argv", ["x", "--date", day])
    cdr.main()
    return json.loads(
        (data / "decisions" / f"{day}_chief_decision.json").read_text(encoding="utf-8")
    )


OK_GATE = {
    "market_quality": {"status": "pass", "quality_score": 0.9},
    "position_gate": {"allow_position_increase": True},
    "technical_freshness": {"status": "confirmed"},
    "position_freshness": {"status": "confirmed", "reason": "ok"},
}


class TestMandatoryInputs:
    """强制输入缺失必须**中止**，不得兜底后继续出计划。"""

    def test_missing_risk_decision_aborts(self, env, monkeypatch):
        data, plans = env
        _write(data, plans, "2026-08-07", gate=OK_GATE)
        monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-08-07"])
        with pytest.raises(SystemExit) as e:
            cdr.main()
        assert "RiskDecision missing" in str(e.value)

    def test_missing_runtime_gate_aborts(self, env, monkeypatch):
        """⚠️ **回归（审计 A3）**：门控缺失曾兜底成 `{}` ⇒
        `status=None`、`allow_position_increase=None`，两个判定全部落空，
        于是**没有门控照样输出「允许开新仓」**。"""
        data, plans = env
        _write(data, plans, "2026-08-07", risk={"risk_level": "普通"})
        monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-08-07"])
        with pytest.raises(SystemExit) as e:
            cdr.main()
        assert "runtime_gate missing" in str(e.value)

    def test_empty_gate_json_aborts(self, env, monkeypatch):
        """空 JSON 与缺文件同等对待 —— `{}` 也读不出结论。"""
        data, plans = env
        _write(data, plans, "2026-08-07", risk={"risk_level": "普通"}, gate={})
        monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-08-07"])
        with pytest.raises(SystemExit):
            cdr.main()


class TestPermissionGating:
    """开仓权限 —— 未知一律按阻断（风控优先于买入）。"""

    def test_happy_path_keeps_permission(self, env, monkeypatch):
        data, plans = env
        d = _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk={"risk_level": "普通"},
            gate=OK_GATE,
            holdings=[],
            b1=[],
            sectors=[],
        )
        assert d["new_position_permission"] == "允许"
        assert d["risk_level"] == "普通"

    @pytest.mark.parametrize("status", [None, "blocked", "typo_status", ""])
    def test_unknown_or_blocked_quality_forbids(self, env, monkeypatch, status):
        """⚠️ `market_quality.status` 只要不在 {pass, degraded} 白名单里（含 **None / 拼错**）
        就必须禁止开仓并升到强风控 —— 这是 fail-closed。"""
        data, plans = env
        gate = {**OK_GATE, "market_quality": {"status": status}}
        d = _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk={"risk_level": "普通"},
            gate=gate,
            holdings=[],
            b1=[],
            sectors=[],
        )
        assert d["new_position_permission"] == "禁止"
        assert d["risk_level"] == "强风控"
        assert any("市场数据质量" in x for x in d["forbidden_actions"])

    def test_strong_risk_level_forbids(self, env, monkeypatch):
        data, plans = env
        d = _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk={"risk_level": "强风控"},
            gate=OK_GATE,
            holdings=[],
            b1=[],
            sectors=[],
        )
        assert d["new_position_permission"] == "禁止"

    def test_degraded_upgrades_normal_to_elevated(self, env, monkeypatch):
        """质量 degraded 时「普通」风控升「提高」—— 数据不全就不该按常规风控走。"""
        data, plans = env
        gate = {**OK_GATE, "market_quality": {"status": "degraded"}}
        d = _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk={"risk_level": "普通"},
            gate=gate,
            holdings=[],
            b1=[],
            sectors=[],
        )
        assert d["risk_level"] == "提高"

    @pytest.mark.parametrize("val", [None, False])
    def test_no_increase_authorization_downgrades_permission(
        self, env, monkeypatch, val
    ):
        """⚠️ `allow_position_increase` 为 **None**（字段缺失/门控未算出）必须与
        False 同等对待 —— **未获授权 ≠ 已获授权**。"""
        data, plans = env
        gate = {**OK_GATE, "position_gate": {"allow_position_increase": val}}
        d = _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk={"risk_level": "普通"},
            gate=gate,
            holdings=[],
            b1=[],
            sectors=[],
        )
        assert d["new_position_permission"] == "仅观察，不得加仓"
        assert any("加仓或输出精确交易数量" in x for x in d["forbidden_actions"])

    def test_forbid_stays_forbid_not_downgraded_to_observe(self, env, monkeypatch):
        """已经是「禁止」的不能被改写成「仅观察」—— 那是**放松**。"""
        data, plans = env
        gate = {
            "market_quality": {"status": "blocked"},
            "position_gate": {"allow_position_increase": False},
            "technical_freshness": {"status": "confirmed"},
        }
        d = _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk={"risk_level": "普通"},
            gate=gate,
            holdings=[],
            b1=[],
            sectors=[],
        )
        assert d["new_position_permission"] == "禁止"


class TestHoldingActions:
    HOLD = [
        {
            "code": "600000.SH",
            "name": "浦发",
            "action": "持有",
            "priority": "P3",
            "reason": ["结构完好"],
        }
    ]
    B1 = [
        {
            "code": "600000",
            "final_action": "持有",
            "final_priority": "P3",
            "final_reason": "B1 结构完好",
        }
    ]

    def test_b1_action_used_when_no_high_risk(self, env, monkeypatch):
        data, plans = env
        d = _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk={"risk_level": "普通"},
            gate=OK_GATE,
            holdings=self.HOLD,
            b1=self.B1,
            sectors=[],
        )
        h = d["holding_actions"][0]
        assert h["action"] == "持有" and h["priority"] == "P3"
        assert h["b1_reference_action"] == "持有"
        assert h["execution_status"] == "current"

    @pytest.mark.parametrize(
        "risk_action,want",
        [("清仓", "清仓"), ("止损", "止损"), ("减仓", "减仓"), ("观察", "禁止加仓")],
    )
    def test_high_risk_overrides_b1_by_severity(
        self, env, monkeypatch, risk_action, want
    ):
        """高优先风险**覆盖** B1 动作，且按严重度取最重的：清仓>止损>减仓>禁止加仓。

        「观察」这类非处置动作在高风险下也必须至少是「禁止加仓」——
        高优先风险不能落到比禁止加仓更松的结论。
        """
        data, plans = env
        risk = {
            "risk_level": "普通",
            "stock_risks": [
                {
                    "code": "600000",
                    "priority": "高",
                    "action": risk_action,
                    "reason": "破位",
                }
            ],
        }
        d = _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk=risk,
            gate=OK_GATE,
            holdings=self.HOLD,
            b1=self.B1,
            sectors=[],
        )
        h = d["holding_actions"][0]
        assert h["action"] == want and h["priority"] == "P1"
        assert "破位" in h["reasons"]

    def test_low_priority_risk_does_not_override(self, env, monkeypatch):
        """只有 priority=='高' 才覆盖 —— 中/低优先风险不该改写动作。"""
        data, plans = env
        risk = {
            "risk_level": "普通",
            "stock_risks": [
                {"code": "600000", "priority": "中", "action": "清仓", "reason": "x"}
            ],
        }
        d = _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk=risk,
            gate=OK_GATE,
            holdings=self.HOLD,
            b1=self.B1,
            sectors=[],
        )
        assert d["holding_actions"][0]["action"] == "持有"

    def test_stale_technical_forces_wait(self, env, monkeypatch):
        """⚠️ 目标日技术行情未确认 ⇒ **等待行情更新**，不沿用旧技术动作。

        沿用旧动作等于拿昨天的技术面下今天的单。
        """
        data, plans = env
        gate = {**OK_GATE, "technical_freshness": {"status": "stale"}}
        d = _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk={"risk_level": "普通"},
            gate=gate,
            holdings=self.HOLD,
            b1=self.B1,
            sectors=[],
        )
        h = d["holding_actions"][0]
        assert h["action"] == "等待行情更新"
        assert h["reasons"] == ["目标日持仓技术行情未确认，不沿用旧技术动作"]
        assert h["execution_status"] == "waiting_for_current_technical"

    def test_high_risk_beats_stale_technical(self, env, monkeypatch):
        """技术行情陈旧也不能压住高优先风险 —— 风险处置优先于等数据。"""
        data, plans = env
        gate = {**OK_GATE, "technical_freshness": {"status": "stale"}}
        risk = {
            "risk_level": "普通",
            "stock_risks": [
                {"code": "600000", "priority": "高", "action": "止损", "reason": "破位"}
            ],
        }
        d = _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk=risk,
            gate=gate,
            holdings=self.HOLD,
            b1=self.B1,
            sectors=[],
        )
        assert d["holding_actions"][0]["action"] == "止损"

    def test_code_suffix_stripped_for_matching(self, env, monkeypatch):
        """持仓带 `.SH` 后缀、风险/`b1` 用裸码 —— 必须能对上。"""
        data, plans = env
        risk = {
            "risk_level": "普通",
            "stock_risks": [
                {"code": "600000.SH", "priority": "高", "action": "清仓", "reason": "x"}
            ],
        }
        d = _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk=risk,
            gate=OK_GATE,
            holdings=self.HOLD,
            b1=self.B1,
            sectors=[],
        )
        assert d["holding_actions"][0]["action"] == "清仓"

    def test_sorted_by_priority_then_code(self, env, monkeypatch):
        data, plans = env
        holds = [
            {"code": "600001", "name": "b", "action": "持有", "priority": "P3"},
            {"code": "600000", "name": "a", "action": "持有", "priority": "P3"},
        ]
        risk = {
            "risk_level": "普通",
            "stock_risks": [
                {"code": "600001", "priority": "高", "action": "减仓", "reason": "x"}
            ],
        }
        d = _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk=risk,
            gate=OK_GATE,
            holdings=holds,
            b1=[],
            sectors=[],
        )
        assert [x["code"] for x in d["holding_actions"]] == ["600001", "600000"]


class TestWatchlistAndForbidden:
    def test_watchlist_only_permitted_sectors_capped_at_3(self, env, monkeypatch):
        """观察方向只取 `trade_permission=='支持'` 的板块，最多 3 个。"""
        data, plans = env
        sectors = [{"sector": f"s{i}", "trade_permission": "支持"} for i in range(5)]
        sectors.append({"sector": "no", "trade_permission": "不支持"})
        d = _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk={"risk_level": "普通"},
            gate=OK_GATE,
            holdings=[],
            b1=[],
            sectors=sectors,
        )
        assert d["watchlist"] == ["s0", "s1", "s2"]
        assert "no" not in d["watchlist"]

    def test_forbidden_always_has_the_three_hard_rules(self, env, monkeypatch):
        """三条硬禁令必须恒在，且与 RiskDecision 的禁令合并去重。"""
        data, plans = env
        d = _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk={
                "risk_level": "普通",
                "forbidden_actions": ["无计划追高", "自定义禁令"],
            },
            gate=OK_GATE,
            holdings=[],
            b1=[],
            sectors=[],
        )
        f = d["forbidden_actions"]
        for x in (
            "无计划追高",
            "因J值低直接补仓",
            "绕过risk_control开仓",
            "自定义禁令",
        ):
            assert x in f
        assert len(f) == len(set(f)), "禁令未去重"

    def test_missing_market_timing_md_degrades(self, env, monkeypatch):
        """择时报告缺失 ⇒ 状态未知、权限落到「原则不允许」，**不能崩**。"""
        data, plans = env
        d = _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk={"risk_level": "普通"},
            gate=OK_GATE,
            holdings=[],
            b1=[],
            sectors=[],
            mt=None,
        )
        assert d["market_state"] == "未知"
        assert d["new_position_permission"] in (
            "原则不允许",
            "仅观察，不得加仓",
            "禁止",
        )

    def test_markdown_and_json_both_written(self, env, monkeypatch):
        data, plans = env
        _run(
            data,
            plans,
            "2026-08-07",
            monkeypatch,
            risk={"risk_level": "普通"},
            gate=OK_GATE,
            holdings=[],
            b1=[],
            sectors=[],
        )
        md = (plans / "2026-08-07_chief_decision.md").read_text(encoding="utf-8")
        assert "# chief_decision 每日总控交易计划" in md
        assert "| - | 暂无 | - | - | - |" in md, "无买入计划时应有占位行"
        assert "RiskDecision为强制输入" not in md


class TestHelpers:
    def test_bare_strips_suffix(self):
        assert cdr.bare("600000.SH") == "600000" and cdr.bare(None) == ""

    def test_dedupe_preserves_order_and_drops_falsy(self):
        assert cdr.dedupe(["a", "b", "a", "", None, "c"]) == ["a", "b", "c"]

    def test_extract_returns_default_when_absent(self):
        assert cdr.extract(r"状态：\*\*(.*?)\*\*", "", "未知") == "未知"
