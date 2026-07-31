# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest.mock import patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime_guards


class RuntimeGuardTests(unittest.TestCase):
    def test_same_day_confirmation_is_confirmed(self):
        confirmations = {"2026-07-15": {"no_trades": True, "note": "无交易"}}
        with patch.object(runtime_guards, "position_freshness", return_value={"status": "stale", "confirmed": False}), patch.object(runtime_guards, "load_json", return_value=confirmations), patch.object(runtime_guards, "ledger_trades_on", return_value=[]):
            result = runtime_guards.position_freshness_with_confirmation("2026-07-15")
        self.assertEqual(result["status"], "confirmed")
        self.assertTrue(result["confirmed"])

    def test_previous_day_no_trade_confirmation_is_default_intraday_baseline(self):
        confirmations = {"2026-07-14": {"no_trades": True, "note": "无交易"}}
        with patch.object(runtime_guards, "position_freshness", return_value={"status": "stale", "confirmed": False}), patch.object(runtime_guards, "load_json", return_value=confirmations), patch.object(runtime_guards, "ledger_trades_on", return_value=[]):
            result = runtime_guards.position_freshness_with_confirmation("2026-07-15")
        self.assertEqual(result["status"], "confirmed")
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["inherited_from"], "2026-07-14")
        self.assertIn("B1", result["assumption"])

    def test_ledger_trades_override_inherited_no_trade_baseline(self):
        confirmations = {"2026-07-14": {"no_trades": True, "note": "无交易"}}
        trades = [{"交易类别": "买入", "名称": "中国船舶", "代码": "600150", "成交数量": "900.0", "成交价格": "32.92"}]
        with patch.object(runtime_guards, "position_freshness", return_value={"status": "stale", "confirmed": False}), patch.object(runtime_guards, "load_json", return_value=confirmations), patch.object(runtime_guards, "ledger_trades_on", return_value=trades):
            result = runtime_guards.position_freshness_with_confirmation("2026-07-15")
        self.assertEqual(result["status"], "confirmed")
        self.assertFalse(result["inherited"])
        self.assertNotIn("assumption", result)
        self.assertIn("买入中国船舶", result["reason"])


class MarketQualityAsOfTests(unittest.TestCase):
    """新鲜度必须按 as_of 判定:当日文件里装 T-1 数据同样是 stale。"""

    DAY = "2026-07-20"

    def _market(self, as_of, quality="auto"):
        return {
            "amv_0": {"amv_change_pct": 1.0, "quality": "confirmed", "as_of": self.DAY},
            "market_breadth": {"up_count": 3000, "down_count": 1000, "quality": quality,
                               "as_of": as_of},
            "sentiment": {"limit_up_count": 50, "quality": quality, "as_of": as_of},
            "turnover": {"turnover_change_pct": 5.0, "quality": quality, "as_of": as_of},
            "overseas_market": {"nasdaq_change_pct": 0.5, "as_of": self.DAY},
        }

    def _check(self, result, field):
        return next(x for x in result["checks"] if x["field"] == field)

    def test_current_day_as_of_passes(self):
        r = runtime_guards.market_quality_gate(self._market(self.DAY), self.DAY)
        self.assertEqual(self._check(r, "market_breadth")["quality"], "auto")
        self.assertFalse(self._check(r, "market_breadth")["stale_as_of"])
        self.assertEqual(r["status"], "pass")

    def test_prior_day_as_of_in_today_file_is_stale(self):
        r = runtime_guards.market_quality_gate(self._market("2026-07-17"), self.DAY)
        for field in ("market_breadth", "sentiment", "turnover"):
            chk = self._check(r, field)
            self.assertEqual(chk["quality"], "stale", field)
            self.assertTrue(chk["stale_as_of"], field)
        self.assertLess(r["quality_score"], 0.8)
        self.assertNotEqual(r["status"], "pass")

    def test_confirmed_quality_also_downgraded_when_as_of_is_old(self):
        r = runtime_guards.market_quality_gate(self._market("2026-07-17", quality="confirmed"),
                                               self.DAY)
        self.assertEqual(self._check(r, "market_breadth")["quality"], "stale")


class MarketQualityWeightingTests(unittest.TestCase):
    """关键性加权:0AMV 缺失不得再靠"其余齐全"凑到 0.8 判 pass。"""

    DAY = "2026-07-20"

    def _market(self, amv: dict | None):
        m = {
            "market_breadth": {"up_count": 3000, "quality": "confirmed", "as_of": self.DAY},
            "sentiment": {"limit_up_count": 50, "quality": "confirmed", "as_of": self.DAY},
            "turnover": {"turnover_change_pct": 5.0, "quality": "confirmed", "as_of": self.DAY},
            "overseas_market": {"nasdaq_change_pct": 0.5, "as_of": self.DAY},
        }
        m["amv_0"] = amv if amv is not None else {}
        return m

    def test_missing_amv_no_longer_passes_at_exactly_080(self):
        """回归本体:旧口径无权重平均 = 4/5 = 0.8 恰好 pass;加权后必须降级。"""
        r = runtime_guards.market_quality_gate(self._market(None), self.DAY,
                                               expected_day=self.DAY)
        self.assertLess(r["quality_score"], 0.8)
        self.assertEqual(r["status"], "degraded")
        self.assertFalse(r["amv_ok"])
        self.assertTrue(any("0AMV" in x for x in r["limitations"]))

    def test_amv_weight_exceeds_overseas(self):
        """0AMV 与海外行情不能同权 —— 前者定 regime,后者只是背景。"""
        r = runtime_guards.market_quality_gate(
            self._market({"amv_change_pct": 5.0, "quality": "confirmed", "as_of": self.DAY}),
            self.DAY, expected_day=self.DAY)
        self.assertGreater(r["weights"]["0AMV"], r["weights"]["overseas"])
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["limitations"], [])

    def test_stale_amv_also_blocks_pass(self):
        r = runtime_guards.market_quality_gate(
            self._market({"amv_change_pct": 5.0, "quality": "confirmed", "as_of": "2026-01-05"}),
            self.DAY, expected_day=self.DAY)
        self.assertEqual(r["status"], "degraded")
        self.assertFalse(r["amv_ok"])

    def test_blocked_only_when_all_core_sections_bad(self):
        """blocked 会真正中断链路(--require-quality/--require-gate),口径必须是"大面积缺数"。"""
        partial = runtime_guards.market_quality_gate(
            {"amv_0": {}, "market_breadth": {"up_count": 1, "quality": "confirmed",
                                             "as_of": self.DAY},
             "sentiment": {}, "turnover": {},
             "overseas_market": {"nasdaq_change_pct": 0.5, "as_of": self.DAY}},
            self.DAY, expected_day=self.DAY)
        self.assertEqual(partial["status"], "degraded")     # 还有一个核心块新鲜 → 不阻断
        allbad = runtime_guards.market_quality_gate(
            {"amv_0": {}, "market_breadth": {}, "sentiment": {}, "turnover": {},
             "overseas_market": {"nasdaq_change_pct": 0.5, "as_of": self.DAY}},
            self.DAY, expected_day=self.DAY)
        self.assertEqual(allbad["status"], "blocked")


class RegimeNormalizeTests(unittest.TestCase):
    """三套并行词表(effective_state / amv_zone / README 用词)都要归一,否则漏进未知分支。"""

    def test_canonical_values(self):
        self.assertEqual(runtime_guards.normalize_regime("做多"), "做多")
        self.assertEqual(runtime_guards.normalize_regime("空头"), "空头")
        self.assertEqual(runtime_guards.normalize_regime("中性"), "中性")

    def test_amv_zone_vocabulary(self):
        """merge_incremental_market 会用 amv_zone 兜底填 effective_state。"""
        self.assertEqual(runtime_guards.normalize_regime("做多触发"), "做多")
        self.assertEqual(runtime_guards.normalize_regime("空头触发"), "空头")
        self.assertEqual(runtime_guards.normalize_regime("阈值内"), "中性")

    def test_readme_wording_alias(self):
        self.assertEqual(runtime_guards.normalize_regime("多头"), "做多")

    def test_empty_and_unknown_are_not_tradeable(self):
        """核心:空串必须归成"未知"而不是落进"非空头"⇒ 可加仓。"""
        for raw in ("", None, "  ", "乱码"):
            self.assertEqual(runtime_guards.normalize_regime(raw), "未知")
        self.assertNotIn("未知", runtime_guards._REGIME_ALLOW_INCREASE)
        self.assertNotIn("空头", runtime_guards._REGIME_ALLOW_INCREASE)


class MarketQualitySessionAwareTests(unittest.TestCase):
    """M3:盘前/盘中期望数据日=T-1,不得用日历日误伤正常盘前(告警疲劳/误开硬闸)。"""

    DAY = "2026-07-20"
    PREV = "2026-07-17"

    def _market(self, as_of, quality="auto"):
        return {
            "amv_0": {"amv_change_pct": 1.0, "quality": "confirmed", "as_of": self.PREV},
            "market_breadth": {"up_count": 3000, "down_count": 1000, "quality": quality,
                               "as_of": as_of},
            "sentiment": {"limit_up_count": 50, "quality": quality, "as_of": as_of},
            "turnover": {"turnover_change_pct": 5.0, "quality": quality, "as_of": as_of},
            "overseas_market": {"nasdaq_change_pct": 0.5, "as_of": self.PREV},
        }

    def test_preclose_expected_t_minus_1_not_stale(self):
        r = runtime_guards.market_quality_gate(self._market(self.PREV), self.DAY,
                                               expected_day=self.PREV)
        chk = next(x for x in r["checks"] if x["field"] == "market_breadth")
        self.assertEqual(chk["quality"], "auto")
        self.assertFalse(chk["stale_as_of"])
        self.assertEqual(r["expected_day"], self.PREV)

    def test_preclose_older_than_expected_is_stale(self):
        r = runtime_guards.market_quality_gate(self._market("2026-07-15"), self.DAY,
                                               expected_day=self.PREV)
        chk = next(x for x in r["checks"] if x["field"] == "market_breadth")
        self.assertEqual(chk["quality"], "stale")


class PositionIncreaseDecisionTests(unittest.TestCase):
    """加仓授权是钱的路径:regime 未知一律不放行。"""

    OK_QUALITY = {"status": "pass", "amv_ok": True, "limitations": []}

    def _decide(self, regime_field=None, quality=None, **kw):
        market = {"amv_0": dict(regime_field or {})}
        args = {"reduction_ready": True, "technical_current": True, "quotes_current": True,
                "market_quality": quality or self.OK_QUALITY}
        args.update(kw)
        return runtime_guards.position_increase_decision(market, **args)

    def test_long_regime_authorized(self):
        d = self._decide({"effective_state": "做多"})
        self.assertTrue(d["allow"])
        self.assertTrue(d["regime_ok"])
        self.assertEqual(d["limitations"], [])

    def test_neutral_regime_authorized(self):
        self.assertTrue(self._decide({"effective_state": "中性"})["allow"])

    def test_bear_regime_denied(self):
        d = self._decide({"effective_state": "空头"})
        self.assertFalse(d["allow"])
        self.assertTrue(any("白名单" in x for x in d["limitations"]))

    def test_missing_amv_no_longer_slips_through_as_not_bear(self):
        """漏洞本体:0AMV 整段缺失 → regime 空串 → 旧逻辑 `!= "空头"` 为真 ⇒ 误授加仓权。"""
        d = self._decide({})                       # amv_0 = {}
        self.assertEqual(d["regime"], "未知")
        self.assertFalse(d["regime_ok"])
        self.assertFalse(d["allow"])

    def test_unknown_regime_string_denied(self):
        self.assertFalse(self._decide({"effective_state": "待确认"})["allow"])

    def test_amv_zone_bear_trigger_denied(self):
        """effective_state 缺失时会用 amv_zone 兜底,空头触发同样不得放行。"""
        d = self._decide({"amv_zone": "空头触发"})
        self.assertEqual(d["regime"], "空头")
        self.assertFalse(d["allow"])

    def test_stale_amv_denies_even_when_regime_is_long(self):
        """regime 写着做多但 0AMV 不新鲜(amv_ok=False) ⇒ 不放行:那是过期的方向。"""
        d = self._decide({"effective_state": "做多"},
                         quality={"status": "pass", "amv_ok": False,
                                  "limitations": ["0AMV=stale"]})
        self.assertFalse(d["allow"])

    def test_quality_degraded_denies(self):
        d = self._decide({"effective_state": "做多"},
                         quality={"status": "degraded", "amv_ok": True, "limitations": []})
        self.assertFalse(d["allow"])

    def test_stale_technical_denies_and_is_recorded(self):
        d = self._decide({"effective_state": "做多"}, technical_current=False)
        self.assertFalse(d["allow"])
        self.assertTrue(any("技术指标" in x for x in d["limitations"]))

    def test_no_reduction_baseline_denies(self):
        self.assertFalse(self._decide({"effective_state": "做多"},
                                      reduction_ready=False)["allow"])


if __name__ == "__main__":
    unittest.main()
