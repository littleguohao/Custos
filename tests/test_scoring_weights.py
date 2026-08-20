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
