"""`review_core.classify` —— **14:45 尾盘持仓动作**的判定逻辑。

覆盖率清点（2026-08-07）：`review_core` 50%，`classify` 34 行里缺 19。

这是 `run_1445` 报告里那张持仓优先级表的来源，也就是**盘中要不要动手**的直接依据。
既有 `test_close_review.py` 覆盖了 `build_delivery_digest` / `json_safe` /
`validate_quote_snapshot` 等外围，这里补的是判定分支本身。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("07_tools", "07_tools/close_review"):
    sys.path.insert(0, str(ROOT / _p))

from close_review import review_core as rc  # noqa: E402

POS = {"单位成本": 10.0, "名称": "测试", "持有数量": 100}
DAY = "2026-08-07"


def _tech(**kw):
    base = {"trend_state": "横盘震荡", "box20_position": "箱体上半区", "latest_date": DAY}
    base.update(kw)
    return base


def _q(price=11.0, chg=1.0, **kw):
    base = {"price": price, "change_pct": chg, "date": DAY}
    base.update(kw)
    return base


class TestNoLivePriceIsHardStop:
    """⚠️ 最要紧的一条：**没有当日实时行情就不许生成尾盘动作**。"""

    @pytest.mark.parametrize("quote", [{}, {"price": None}, {"price": "n/a"},
                                       {"price": float("nan")}])
    def test_missing_price_returns_wait(self, quote):
        p, a, r = rc.classify(POS, _tech(), [], quote, False, None)
        assert p == "P1" and a == "等待当日行情/仅风险收缩"
        assert "禁止使用持仓快照旧价" in r

    def test_missing_price_wins_over_everything(self):
        """即使 B1 判 P0、又有高风险，缺价也先返回等待 ——
        因为后续每一条判定都要用到价格。"""
        p, a, _ = rc.classify(
            POS, _tech(trend_state="下跌", box20_position="破位"),
            [{"priority": "高", "reason": "止损"}], {"price": None}, True,
            {"final_priority": "P0", "final_action": "清仓", "final_reason": "x"})
        assert p == "P1" and a == "等待当日行情/仅风险收缩"


class TestStructureSignals:
    def test_structural_clear_is_p0(self):
        """N 型前低失守 ⇒ P0 清仓评估（结构破位是 B1 里最硬的卖出依据之一）。"""
        tech = _tech(n_structure={"available": True, "prior_low": 10.5,
                                  "prior_low_date": "2026-07-01"})
        p, a, _ = rc.classify(POS, tech, [], _q(price=9.0), False, None)
        assert p == "P0" and "N型前低" in a

    def test_price_above_prior_low_is_not_p0(self):
        tech = _tech(n_structure={"available": True, "prior_low": 8.0,
                                  "prior_low_date": "2026-07-01"})
        p, _a, _ = rc.classify(POS, tech, [], _q(price=11.0), False, None)
        assert p != "P0"


class TestRiskAndLoss:
    def test_high_risk_gives_p1_and_shows_reason(self):
        p, a, r = rc.classify(POS, _tech(), [{"priority": "高", "reason": "已触发止损线"}],
                              _q(), False, None)
        assert p == "P1" and a == "减仓/止损评估" and "已触发止损线" in r

    def test_box_break_gives_p1(self):
        p, a, _ = rc.classify(POS, _tech(box20_position="下沿/破位区"), [], _q(), False, None)
        assert p == "P1" and a == "减仓/止损评估"

    def test_loss_beyond_7pct_gives_p1(self):
        """浮亏用**实时价对成本**算，而不是读快照里的盈亏率。"""
        p, a, r = rc.classify(POS, _tech(), [], _q(price=9.2), False, None)
        assert p == "P1" and a == "减仓/止损评估" and "-8.0%" in r

    def test_mid_priority_risk_does_not_trigger_p1(self):
        """只有「高」优先风险才升级 —— 中/低不改写动作。"""
        p, _a, _ = rc.classify(POS, _tech(), [{"priority": "中", "reason": "x"}],
                               _q(), False, None)
        assert p != "P1"

    def test_falls_back_to_snapshot_pnl_when_cost_missing(self):
        """成本缺失时退回快照里的持有盈亏率 —— 否则除零。"""
        pos = {"名称": "x", "持有盈亏率": -0.09}
        p, a, _ = rc.classify(pos, _tech(), [], _q(), False, None)
        assert p == "P1" and a == "减仓/止损评估"


class TestB1Precedence:
    """⚠️ B1 状态的 P0/P1/P2 会**短路**后续全部判定。"""

    @pytest.mark.parametrize("pri", ["P0", "P1", "P2"])
    def test_b1_short_circuits(self, pri):
        p, a, r = rc.classify(POS, _tech(), [], _q(), False,
                              {"final_priority": pri, "final_action": "B1动作",
                               "final_reason": "B1理由"})
        assert (p, a) == (pri, "B1动作") and r.startswith("B1理由")

    def test_b1_p3_does_not_short_circuit(self):
        """P3 不短路 —— 它是「持有观察」，不该盖住本地的风险判定。"""
        p, a, _ = rc.classify(POS, _tech(box20_position="破位"), [], _q(), False,
                              {"final_priority": "P3", "final_action": "B1动作",
                               "final_reason": "x"})
        assert (p, a) == ("P1", "减仓/止损评估")

    def test_high_risk_reason_is_not_lost_when_b1_wins(self):
        """⚠️ 回归（2026-08-07 发现）：B1 短路时高优先风控理由**必须仍可见**。

        `risks` 在 `review_core` 里**只**经由 `classify` 影响输出（别处不渲染），
        所以修前那条理由在整份 14:45 报告里一个字都看不到 ——
        而「所有计划必须可复盘」。

        ⚠️ **优先级顺序本身未动**（B1 的 P2 仍压过风控的 P1）：B1 用 14:45 实时价、
        RiskDecision 可能来自前一日 17:00，谁该优先是决策问题，见待办 #50。
        """
        p, a, r = rc.classify(
            POS, _tech(trend_state="下跌", box20_position="下沿/破位区"),
            [{"priority": "高", "reason": "已触发止损线"}], _q(price=9.0), False,
            {"final_priority": "P2", "final_action": "尾盘跌破BBI待收盘确认",
             "final_reason": "首日跌破"})
        assert (p, a) == ("P2", "尾盘跌破BBI待收盘确认"), "优先级顺序不变"
        assert "已触发止损线" in r, "高优先风控依据必须仍然可见"
        assert "未消化的高优先风控依据" in r

    def test_no_annotation_when_no_high_risk(self):
        _p, _a, r = rc.classify(POS, _tech(), [{"priority": "中", "reason": "x"}], _q(),
                                False, {"final_priority": "P2", "final_action": "a",
                                        "final_reason": "b1理由"})
        assert r == "b1理由", "没有高风险时不该加注释"


class TestBearRegime:
    def test_rebound_in_bear_regime_is_reduce(self):
        """0AMV 空头里的**上涨**是减仓机会，不是持有理由。"""
        p, a, r = rc.classify(POS, _tech(), [], _q(chg=2.0), True, None)
        assert p == "P2" and a == "反弹减仓评估" and "0AMV空头区间" in r

    def test_big_rebound_escalates_to_p1(self):
        """反弹 ≥5% 升级 P1 —— 大反弹是更好的减仓窗口。"""
        p, a, _ = rc.classify(POS, _tech(), [], _q(chg=5.0), True, None)
        assert p == "P1" and a == "反弹减仓评估"

    def test_down_day_in_bear_regime_not_reduce(self):
        """下跌日不报「反弹减仓」（没有反弹可用）。"""
        _p, a, _ = rc.classify(POS, _tech(), [], _q(price=10.5, chg=-2.0), True, None)
        assert a != "反弹减仓评估"

    def test_missing_change_pct_does_not_fabricate_rebound(self):
        _p, a, _ = rc.classify(POS, _tech(), [], {"price": 11.0, "date": DAY}, True, None)
        assert a != "反弹减仓评估"


class TestDefaultBuckets:
    def test_downtrend_is_observe_no_add(self):
        p, a, _ = rc.classify(POS, _tech(trend_state="下跌", box20_position="箱体上半区"),
                              [], _q(), False, None)
        assert (p, a) == ("P2", "观察、不加仓")

    def test_small_loss_is_observe_no_add(self):
        p, a, _ = rc.classify(POS, _tech(), [], _q(price=9.8), False, None)
        assert (p, a) == ("P2", "观察、不加仓")

    def test_healthy_position_is_hold(self):
        p, a, r = rc.classify(POS, _tech(), [], _q(price=11.0), False, None)
        assert (p, a) == ("P3", "持有观察") and "+10.0%" in r


class TestMaps:
    """三个 map helper：把上游产物按代码索引，供 main 逐票取用。"""

    def test_quote_map_indexes_by_code(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rc, "MARKET", tmp_path)
        (tmp_path / f"{DAY}_holding_quotes.json").write_text(
            '{"quotes": [{"code": "600000", "price": 10}], "as_of": "x"}', encoding="utf-8")
        qm, snap = rc.quote_map(DAY)
        assert qm["600000"]["price"] == 10 and snap["as_of"] == "x"

    def test_quote_map_missing_file_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rc, "MARKET", tmp_path)
        assert rc.quote_map(DAY) == ({}, {})

    def test_technical_map_falls_back_to_latest(self, tmp_path, monkeypatch):
        """⚠️ 当日技术面缺失时回退**最近一份** —— 陈旧判定由
        `intraday_bbi_basis`/`n_structure_basis` 按 `latest_date` 各自处理，
        这里只负责能取到数据。"""
        monkeypatch.setattr(rc, "HOLDINGS", tmp_path)
        (tmp_path / "2026-08-05_holding_technical_summary.json").write_text(
            '[{"code": "600000", "trend_state": "下跌"}]', encoding="utf-8")
        tm = rc.technical_map(DAY)
        assert tm["600000"]["trend_state"] == "下跌"

    def test_technical_map_empty_when_nothing_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rc, "HOLDINGS", tmp_path)
        assert rc.technical_map(DAY) == {}

    def test_risk_map_groups_by_bare_code(self, tmp_path, monkeypatch):
        """带后缀与裸码要归到同一只票。"""
        monkeypatch.setattr(rc, "RISK", tmp_path)
        (tmp_path / f"{DAY}_risk_decision.json").write_text(
            '{"stock_risks": [{"code": "600000.SH", "priority": "高"},'
            ' {"code": "600000", "priority": "中"}]}', encoding="utf-8")
        rm = rc.risk_map(DAY)
        assert len(rm["600000"]) == 2

    def test_risk_map_skips_blank_code(self, tmp_path, monkeypatch):
        """空代码不建键 —— 否则会出现一个 key 为 "" 的伪持仓。"""
        monkeypatch.setattr(rc, "RISK", tmp_path)
        (tmp_path / f"{DAY}_risk_decision.json").write_text(
            '{"stock_risks": [{"code": "", "priority": "高"}, {"code": null}]}',
            encoding="utf-8")
        assert rc.risk_map(DAY) == {}

    def test_risk_map_no_file_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rc, "RISK", tmp_path)
        assert rc.risk_map(DAY) == {}

    def test_risk_source_date_flags_non_current(self, tmp_path, monkeypatch):
        """⚠️ 回退到旧 risk_decision 时**必须能看出来** ——
        否则读者分不清风控依据是今天的还是几天前的。"""
        monkeypatch.setattr(rc, "RISK", tmp_path)
        (tmp_path / "2026-08-05_risk_decision.json").write_text("{}", encoding="utf-8")
        _path, src = rc.risk_source_date(DAY)
        assert src == "2026-08-05" and src != DAY
