# -*- coding: utf-8 -*-
"""v0.84（Phase D 因子化 + 权重外置）钉测。

三组约束：
① 因子化是**搬迁不是复制**——score_candidates/financials 里的名字与因子模块
   是同一函数对象（`is`），全项目唯一一份；
② registry `scoring.weights` 与代码默认表**逐键一致**（默认值==现值是
   「缺省输出逐字节不变」的前提）；
③ 覆盖语义仿 resolve_cap_rules：未知键忽略、默认兜底；覆盖真实生效（改
   j_low 分值 → 技术分/分层变化）。
"""

from __future__ import annotations

import json
import pathlib

from custos.core.factors import capital_intent as ci
from custos.core.factors import fundamentals as fund
from custos.pipeline.screening import financials as fin
from custos.pipeline.screening import score_candidates as sc

ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "governance" / "contracts" / "SCREEN_FORMULA_REGISTRY.json"


class TestFactorizationIsMoveNotCopy:
    def test_capital_intent_is_same_object(self):
        assert sc.capital_intent_strength is ci.capital_intent_strength
        assert sc.resolve_capital_weights is ci.resolve_capital_weights

    def test_fundamentals_is_same_object(self):
        assert fin.financial_factor is fund.financial_factor
        assert fin.REPORT_MAX_AGE_DAYS == fund.REPORT_MAX_AGE_DAYS
        assert sc.fundamental_quality is fund.fundamental_quality

    def test_factors_registered(self):
        from custos.core import factors

        reg = factors.registry()
        assert "capital_intent" in reg and "fundamentals" in reg


class TestWeightsDefaultsMatchRegistry:
    def test_tech_weights_match(self):
        reg_w = json.loads(REGISTRY.read_text(encoding="utf-8"))["scoring"]["weights"]
        for k, v in sc.DEFAULT_TECH_WEIGHTS.items():
            assert reg_w.get(k) == v, (
                f"registry weights[{k}]={reg_w.get(k)} != 默认 {v}"
            )

    def test_capital_weights_match(self):
        reg_w = json.loads(REGISTRY.read_text(encoding="utf-8"))["scoring"]["weights"]
        for k, v in ci.DEFAULT_EVIDENCE_WEIGHTS.items():
            assert reg_w.get(k) == v, (
                f"registry weights[{k}]={reg_w.get(k)} != 默认 {v}"
            )


class TestWeightsOverrideSemantics:
    def test_unknown_keys_ignored(self):
        w = sc.resolve_tech_weights({"j_low": 1, "unknown_key": 999})
        assert w["j_low"] == 1 and "unknown_key" not in w
        w2 = ci.resolve_capital_weights({"cap_strong": 9, "bogus": 1})
        assert w2["cap_strong"] == 9 and "bogus" not in w2

    def test_none_and_garbage_fall_back_to_defaults(self):
        assert sc.resolve_tech_weights(None) == sc.DEFAULT_TECH_WEIGHTS
        assert sc.resolve_tech_weights("junk") == sc.DEFAULT_TECH_WEIGHTS
        assert ci.resolve_capital_weights(None) == ci.DEFAULT_EVIDENCE_WEIGHTS

    def test_override_actually_changes_scoring(self):
        """证明 weights 真的接线：j_low 24→0 后技术分降 24，资金档位阈值可调。"""
        cand = {"patterns": {"j_low": True}}
        base, _, _ = sc.technical_score(cand)
        zeroed, _, _ = sc.technical_score(cand, {"j_low": 0})
        assert base - zeroed == sc.DEFAULT_TECH_WEIGHTS["j_low"]
        # 资金意图：默认阈值 5/2；cap_mid 调到 1 后 1 分证据即「中」
        c2 = {"patterns": {"reversal_k_candidate": True}}
        assert ci.capital_intent_strength(c2)[0] == "弱"
        assert ci.capital_intent_strength(c2, {"cap_mid": 1})[0] == "中"


class TestWeightValueTypeValidation:
    """code review（v0.84）修复：registry 误写非数值覆盖值不得生效——

    字符串/None/bool 忽略并用默认兜底 + stderr [WARN] 一次（绝不 raise：
    误写配置不能在 `score += w[...]` 抛 TypeError 炸掉整池打分）；
    float/int 覆盖正常生效。bool 是 int 子类，显式排除。
    """

    def test_tech_bad_values_ignored_and_warned_once(self, capsys):
        sc._WARNED_BAD_WEIGHT_KEYS.clear()
        w = sc.resolve_tech_weights({"j_low": "24", "five_day_entry": None})
        assert w["j_low"] == sc.DEFAULT_TECH_WEIGHTS["j_low"]
        assert w["five_day_entry"] == sc.DEFAULT_TECH_WEIGHTS["five_day_entry"]
        # 同键再 resolve 不再重复 WARN（整池逐票调用，不去重会刷屏）
        sc.resolve_tech_weights({"j_low": "24", "five_day_entry": None})
        assert capsys.readouterr().err.count("[WARN]") == 2

    def test_tech_bool_value_ignored(self, capsys):
        sc._WARNED_BAD_WEIGHT_KEYS.clear()
        w = sc.resolve_tech_weights({"leader_volume": True})
        assert w["leader_volume"] == sc.DEFAULT_TECH_WEIGHTS["leader_volume"]
        assert "[WARN]" in capsys.readouterr().err

    def test_tech_float_and_int_values_apply(self, capsys):
        w = sc.resolve_tech_weights({"j_low": 12.5, "adx_gt_60": 9})
        assert w["j_low"] == 12.5 and w["adx_gt_60"] == 9
        assert "[WARN]" not in capsys.readouterr().err

    def test_tech_bad_value_does_not_raise_in_scoring(self, capsys):
        """误写字符串时 technical_score 仍出分（默认兜底），不抛 TypeError。"""
        sc._WARNED_BAD_WEIGHT_KEYS.clear()
        cand = {"patterns": {"j_low": True}}
        got, _, _ = sc.technical_score(cand, {"j_low": "junk"})
        base, _, _ = sc.technical_score(cand)
        assert got == base

    def test_capital_bad_values_ignored_and_warned_once(self, capsys):
        ci._WARNED_BAD_WEIGHT_KEYS.clear()
        w = ci.resolve_capital_weights(
            {"ci_b1_ignition": "3", "cap_strong": None, "ci_ignition": False}
        )
        assert w["ci_b1_ignition"] == ci.DEFAULT_EVIDENCE_WEIGHTS["ci_b1_ignition"]
        assert w["cap_strong"] == ci.DEFAULT_EVIDENCE_WEIGHTS["cap_strong"]
        assert w["ci_ignition"] == ci.DEFAULT_EVIDENCE_WEIGHTS["ci_ignition"]
        ci.resolve_capital_weights({"ci_b1_ignition": "3"})
        assert capsys.readouterr().err.count("[WARN]") == 3

    def test_capital_float_and_int_values_apply(self, capsys):
        w = ci.resolve_capital_weights({"ci_b1_ignition": 2.5, "cap_mid": 1})
        assert w["ci_b1_ignition"] == 2.5 and w["cap_mid"] == 1
        assert "[WARN]" not in capsys.readouterr().err


class TestEffectiveWeightsPersisted:
    """code review（v0.84）修复：生效权重随 stock_pool 结果壳落盘（审计缺口）。"""

    def test_score_all_result_carries_effective_weights(self):
        result = sc.score_all(
            "2026-07-21",
            enriched={"status": "ok", "candidates": []},
            sector_states=[],
            amv_state="做多",
            weights={"j_low": 12.5},
        )
        w = result["weights"]
        # 覆盖生效 + 未覆盖键默认兜底；技术分与资金意图两组键同表可查
        assert w["j_low"] == 12.5
        assert w["adx_gt_60"] == sc.DEFAULT_TECH_WEIGHTS["adx_gt_60"]
        assert w["ci_b1_ignition"] == ci.DEFAULT_EVIDENCE_WEIGHTS["ci_b1_ignition"]
        assert w["cap_strong"] == ci.DEFAULT_EVIDENCE_WEIGHTS["cap_strong"]

    def test_weights_key_defaults_to_registry_values(self):
        result = sc.score_all(
            "2026-07-21",
            enriched={"status": "ok", "candidates": []},
            sector_states=[],
            amv_state="做多",
        )
        for k, v in sc.DEFAULT_TECH_WEIGHTS.items():
            assert result["weights"][k] == v
