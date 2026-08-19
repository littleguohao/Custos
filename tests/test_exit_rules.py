# -*- coding: utf-8 -*-
"""止盈/止损规则集中化（v0.81）钉测：

1. 默认值 == 现网口径（−0.10 / −0.07 / 减仓幅度表 / 各方案默认开关）；
2. live 三处判定点与 `core/exit_rules` 同源（仿
   `test_enrich_b1cz.py::TestReversalKThresholdSingleSource` 的思路）；
3. `governance/contracts/EXIT_RULES.json` 与代码默认值一致；
4. 覆盖模式：未知方案/未知参数键忽略，默认表兜底。
"""

from __future__ import annotations

import json

from custos.core import b1_thresholds, exit_rules
from custos.core.paths import EXIT_RULES_FILE


def _strip_notes(obj):
    """递归剥掉 ``_note`` 注释键（EXIT_RULES.json 的说明性字段，不参与比对）。"""
    if isinstance(obj, dict):
        return {k: _strip_notes(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_notes(v) for v in obj]
    return obj


class TestDefaultsMatchLive:
    """默认值必须 == 现网口径 —— 这是「行为零变化」的锚。"""

    def test_stop_thresholds(self):
        assert exit_rules.HARD_LOSS_PCT == -0.10
        assert exit_rules.LOSS_REDUCTION_PCT == -0.07

    def test_default_enabled_flags(self):
        # 现网在跑的三个方案默认开
        assert exit_rules.DEFAULT_STOP_RULES["hard_loss"]["enabled"] is True
        assert exit_rules.DEFAULT_STOP_RULES["loss_reduction"]["enabled"] is True
        assert (
            exit_rules.DEFAULT_TAKE_PROFIT_RULES["scale_out_two_bull"]["enabled"]
            is True
        )
        # 仅研究侧、live 本就不跑的四个方案默认关
        for rule_id in ("breakeven_stop", "trailing_stop", "time_stop"):
            assert exit_rules.DEFAULT_STOP_RULES[rule_id]["enabled"] is False
        assert (
            exit_rules.DEFAULT_TAKE_PROFIT_RULES["cost_zone_flat"]["enabled"] is False
        )

    def test_reduction_table(self):
        assert exit_rules.REDUCTION_PCT_OF_HOLDING == {
            "P0": [100, 100],
            "P1": [10, 25],
            "P2": [10, 20],
        }

    def test_every_rule_has_id_enabled_params(self):
        for rules in (
            exit_rules.DEFAULT_STOP_RULES,
            exit_rules.DEFAULT_TAKE_PROFIT_RULES,
        ):
            for rule_id, rule in rules.items():
                assert rule["rule_id"] == rule_id
                assert isinstance(rule["enabled"], bool)
                assert isinstance(rule["params"], dict) and rule["params"]


class TestExitRulesSingleSource:
    """⚠️ −10%/−7% 阈值的**唯一来源**边界：live 三处判定点必须读同一模块。

    分散史见 `core/exit_rules.py` docstring —— 三份 L3 硬编码拷贝
    （b1_holding_state / review_core / portfolio_review_report）。
    """

    def test_three_live_sites_share_one_source(self):
        from custos.pipeline.close_review import review_core
        from custos.pipeline.holdings import b1_holding_state
        from custos.pipeline.holdings import portfolio_review_report

        assert b1_holding_state.HARD_LOSS_PCT == exit_rules.HARD_LOSS_PCT
        assert b1_holding_state.LOSS_REDUCTION_PCT == exit_rules.LOSS_REDUCTION_PCT
        assert review_core.LOSS_REDUCTION_PCT == exit_rules.LOSS_REDUCTION_PCT
        assert portfolio_review_report.HARD_LOSS_PCT == exit_rules.HARD_LOSS_PCT
        assert portfolio_review_report.LOSS_REDUCTION_PCT == (
            exit_rules.LOSS_REDUCTION_PCT
        )

    def test_reduction_table_shared(self):
        from custos.pipeline.holdings import b1_holding_state

        assert (
            b1_holding_state.REDUCTION_PCT_OF_HOLDING
            == exit_rules.REDUCTION_PCT_OF_HOLDING
        )

    def test_j_threshold_unified(self):
        """portfolio_review_report 的 J 低位阈值必须与全仓同源（v0.81 修正 12→13）。"""
        from custos.pipeline.holdings import portfolio_review_report

        assert portfolio_review_report.J_LOW_THRESHOLD == b1_thresholds.J_LOW_THRESHOLD


class TestExitRulesJsonConsistency:
    """EXIT_RULES.json 必须与代码默认值一致 —— 否则「默认行为不变」就是假话。"""

    def test_json_matches_code_defaults(self):
        data = _strip_notes(json.loads(EXIT_RULES_FILE.read_text(encoding="utf-8")))
        assert set(data["stop_rules"]) == set(exit_rules.DEFAULT_STOP_RULES)
        assert set(data["take_profit_rules"]) == set(
            exit_rules.DEFAULT_TAKE_PROFIT_RULES
        )
        for section, defaults in (
            ("stop_rules", exit_rules.DEFAULT_STOP_RULES),
            ("take_profit_rules", exit_rules.DEFAULT_TAKE_PROFIT_RULES),
        ):
            for rule_id, rule in defaults.items():
                got = data[section][rule_id]
                assert got["rule_id"] == rule["rule_id"]
                assert got["enabled"] == rule["enabled"]
                assert got["params"] == rule["params"]
        assert data["reduction_pct_of_holding"] == (
            exit_rules.DEFAULT_REDUCTION_PCT_OF_HOLDING
        )

    def test_effective_values_match_defaults(self):
        """当前配置下模块级生效值必须等于默认表（行为零变化断言）。"""
        rules = exit_rules.resolve_exit_rules(exit_rules.load_exit_rule_overrides())
        assert rules["stop_rules"] == exit_rules.DEFAULT_STOP_RULES
        assert rules["take_profit_rules"] == exit_rules.DEFAULT_TAKE_PROFIT_RULES
        assert rules["reduction_pct_of_holding"] == (
            exit_rules.DEFAULT_REDUCTION_PCT_OF_HOLDING
        )


class TestResolveExitRules:
    """覆盖模式（仿 score_candidates.resolve_cap_rules）：未知键忽略，默认表兜底。"""

    def test_unknown_rule_and_param_keys_ignored(self):
        rules = exit_rules.resolve_exit_rules(
            {
                "stop_rules": {
                    "no_such_rule": {"enabled": True, "params": {"pnl_pct": -0.5}},
                    "hard_loss": {"params": {"no_such_param": 1.0}},
                },
                "take_profit_rules": {"bogus": {"enabled": True}},
                "reduction_pct_of_holding": {"P9": [1, 2]},
            }
        )
        assert rules["stop_rules"] == exit_rules.DEFAULT_STOP_RULES
        assert rules["take_profit_rules"] == exit_rules.DEFAULT_TAKE_PROFIT_RULES
        assert rules["reduction_pct_of_holding"] == (
            exit_rules.DEFAULT_REDUCTION_PCT_OF_HOLDING
        )

    def test_known_override_applies(self):
        rules = exit_rules.resolve_exit_rules(
            {
                "stop_rules": {
                    "hard_loss": {"enabled": False, "params": {"pnl_pct": -0.12}}
                },
                "reduction_pct_of_holding": {"P1": [5, 15]},
            }
        )
        assert rules["stop_rules"]["hard_loss"]["enabled"] is False
        assert rules["stop_rules"]["hard_loss"]["params"]["pnl_pct"] == -0.12
        assert rules["reduction_pct_of_holding"]["P1"] == [5, 15]
        # 未被覆盖的方案保持默认
        assert rules["stop_rules"]["loss_reduction"]["params"]["pnl_pct"] == -0.07

    def test_garbage_input_falls_back_to_defaults(self):
        for bad in (None, "x", [1], 0):
            rules = exit_rules.resolve_exit_rules(bad)
            assert rules["stop_rules"] == exit_rules.DEFAULT_STOP_RULES

    def test_missing_or_corrupt_file_falls_back(self, tmp_path):
        assert exit_rules.load_exit_rule_overrides(tmp_path / "missing.json") == {}
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert exit_rules.load_exit_rule_overrides(bad) == {}

    def test_defaults_not_mutated_by_overrides(self):
        """覆盖必须工作在深拷贝上 —— 改默认表会污染同进程的其他调用方。"""
        exit_rules.resolve_exit_rules(
            {"stop_rules": {"hard_loss": {"params": {"pnl_pct": -0.99}}}}
        )
        assert exit_rules.DEFAULT_STOP_RULES["hard_loss"]["params"]["pnl_pct"] == -0.10
