"""`daily_pipeline` 里**硬失败（required=True）且此前 0% 覆盖**的报告 stage。

⚠️ 这是覆盖率清点（2026-08-07）里风险最高的组合：

    portfolio_review_report   0%   48 语句   ⛔ 硬失败
    theme_tracker_report      0%  225 语句   ⛔ 硬失败
    execution_review          0%   63 语句   ⛔ 硬失败
    chief_decision_report    19%   58 语句   ⛔ 硬失败

硬失败 = 它一挂，**整条 17:00 盘后链失败**（`daily_pipeline` 不传 `required=False`）。
而 0% 覆盖 = 没有任何测试会提前发现。

**写这批测试当场抓到一个真 bug**（见 `TestPortfolioReviewStateShadowing`）：
`portfolio_review_report` 的 `state` 变量被循环内赋值覆盖，
报告里「market_timing：**…**」一直在打印**最后一只票的 b1 状态字典**。
每份日报都错，而没有测试会发现 —— 这正是补覆盖率的价值。
"""

from __future__ import annotations

import json
import pathlib

import pytest
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ─────────────────────────── portfolio_review_report ───────────────────────────


@pytest.fixture()
def prr_env(tmp_path, monkeypatch):
    from custos.pipeline.holdings import portfolio_review_report as prr

    (tmp_path / "holdings").mkdir()
    monkeypatch.setattr(prr, "DATA", tmp_path)
    return prr, tmp_path


def _write_prr_inputs(tmp_path, day, tech, b1):
    (tmp_path / "holdings" / f"{day}_holding_technical_summary.json").write_text(
        json.dumps(tech, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "holdings" / f"{day}_b1_holding_state.json").write_text(
        json.dumps(b1, ensure_ascii=False), encoding="utf-8"
    )


class TestPortfolioReviewStateShadowing:
    """历史回归：b1 final_* 必须优先于 classify 回退落进 JSON。

    原 `state` 变量遮蔽 bug 随 v0.162 md 停产消失（md 渲染已整段删除）——
    钉测改钉结构化产物 holding_review.json 的内容。
    """

    def test_b1_final_action_lands_in_json(self, prr_env, monkeypatch):
        prr, tmp = prr_env
        day = "2026-08-07"
        _write_prr_inputs(
            tmp,
            day,
            [
                {
                    "code": "600000",
                    "name": "浦发",
                    "trend_state": "横盘震荡",
                    "box20_position": "上半区",
                }
            ],
            [
                {
                    "code": "600000",
                    "final_priority": "P3",
                    "final_action": "持有",
                    "final_reason": "结构完好",
                    "signals": [],
                }
            ],
        )
        monkeypatch.setattr(sys, "argv", ["x", "--date", day])
        prr.main()
        rows = json.loads(
            (tmp / "holdings" / f"{day}_holding_review.json").read_text(
                encoding="utf-8"
            )
        )
        assert rows[0]["action"] == "持有" and rows[0]["priority"] == "P3"
        assert "结构完好" in rows[0]["reason"]

    def test_empty_inputs_produce_empty_json(self, prr_env, monkeypatch):
        prr, tmp = prr_env
        day = "2026-08-07"
        _write_prr_inputs(tmp, day, [], [])
        monkeypatch.setattr(sys, "argv", ["x", "--date", day])
        prr.main()
        assert (
            json.loads(
                (tmp / "holdings" / f"{day}_holding_review.json").read_text(
                    encoding="utf-8"
                )
            )
            == []
        )


class TestPortfolioReviewClassify:
    """`classify` 是无 b1 状态时的**回退风控判据** —— 它决定「止损/减仓/持有」。"""

    def _c(self, **kw):
        from custos.pipeline.holdings import portfolio_review_report as prr

        return prr.classify(kw)

    def test_downtrend_and_breakdown_is_stop_loss(self):
        pri, act, why = self._c(trend_state="下跌", box20_position="破位区")
        assert (pri, act) == ("P1", "止损") and "破位" in why[0]

    def test_deep_loss_forces_stop_loss(self):
        """浮亏 ≤ −10% 是**强制风控阈值**，与趋势无关。"""
        pri, act, _ = self._c(holding_pnl_pct=-0.10, trend_state="上涨")
        assert (pri, act) == ("P1", "止损")

    def test_moderate_loss_reduces(self):
        pri, act, _ = self._c(holding_pnl_pct=-0.07)
        assert (pri, act) == ("P2", "减仓")

    def test_low_j_is_never_a_reason_to_add(self):
        """⚠️ J 低只作观察，**不构成加仓理由** —— 这条是 B1 的核心纪律。"""
        _, act, why = self._c(
            daily_j=8.0, trend_state="横盘震荡", box20_position="上半区"
        )
        assert act == "持有"
        assert any("不构成加仓理由" in r for r in why)

    def test_no_signal_still_gives_a_reason(self):
        """理由列表不许为空 —— 空理由的研判无法复盘。"""
        _, _, why = self._c()
        assert why == ["暂无强触发信号"]

    def test_missing_market_timing_md_still_writes_json(self, prr_env, monkeypatch):
        """择时报告缺失也不再被读取（md 停产）——硬失败 stage 仍照常产 JSON，不能崩。"""
        prr, tmp = prr_env
        day = "2026-08-07"
        _write_prr_inputs(tmp, day, [], [])
        monkeypatch.setattr(sys, "argv", ["x", "--date", day])
        prr.main()
        assert (tmp / "holdings" / f"{day}_holding_review.json").exists()

    def test_empty_holdings_produces_valid_json_no_md(self, prr_env, monkeypatch):
        """空持仓产空数组 JSON；v0.162 起不再写 portfolio_review.md。"""
        prr, tmp = prr_env
        day = "2026-08-07"
        _write_prr_inputs(tmp, day, [], [])
        monkeypatch.setattr(sys, "argv", ["x", "--date", day])
        prr.main()
        assert (
            json.loads(
                (tmp / "holdings" / f"{day}_holding_review.json").read_text(
                    encoding="utf-8"
                )
            )
            == []
        )
        assert not list(tmp.rglob("*_portfolio_review.md")), "md 应已停产"


# ─────────────────────────── execution_review ───────────────────────────


class TestExecutionReviewStatuses:
    """执行复盘的四种状态 —— 它决定「有没有违纪」，措辞必须严谨。

    ⚠️ 最要紧的一条：**尾盘是评估/观察类建议且当日无成交，不得自动判违纪**
    （真实未执行原因未记录时，无从判断）。
    """

    @pytest.fixture(autouse=True)
    def env(self, tmp_path, monkeypatch):
        from custos.pipeline.close_review import execution_review as er

        (tmp_path / "decisions").mkdir()
        (tmp_path / "trades").mkdir()
        log = tmp_path / "logs"
        log.mkdir()
        monkeypatch.setattr(er, "DATA", tmp_path)
        monkeypatch.setattr(er, "LOG", log)
        self.er, self.data, self.log = er, tmp_path, log

    def _run(self, day, chief=None, tail=None, trades=None, monkeypatch=None):
        if chief is not None:
            (self.data / "decisions" / f"{day}_chief_decision.json").write_text(
                json.dumps(chief, ensure_ascii=False), encoding="utf-8"
            )
        if tail is not None:
            (self.log / f"{day}_1445_review.json").write_text(
                json.dumps(tail, ensure_ascii=False), encoding="utf-8"
            )
        (self.data / "trades" / "trades_stock.json").write_text(
            json.dumps(trades or [], ensure_ascii=False), encoding="utf-8"
        )
        import sys as _s

        old = _s.argv
        _s.argv = ["x", "--date", day]
        try:
            self.er.main()
        finally:
            _s.argv = old
        out = self.data / "review_steps" / f"{day}_execution_review.json"
        return json.loads(out.read_text(encoding="utf-8"))

    def test_bare_strips_suffix(self):
        assert self.er.bare("600000.SH") == "600000"
        assert self.er.bare(None) == ""

    def test_load_returns_default_when_missing(self, tmp_path):
        assert self.er.load(tmp_path / "nope.json", {"d": 1}) == {"d": 1}

    def test_executed_when_trade_exists(self, monkeypatch):
        r = self._run(
            "2026-08-07",
            chief={"holding_actions": [{"code": "600000", "action": "减仓"}]},
            tail={
                "actions": [{"code": "600000", "action": "减仓一半", "priority": "P1"}]
            },
            trades=[
                {
                    "成交日期": "2026-08-07",
                    "代码": "600000.SH",
                    "名称": "浦发",
                    "交易类别": "卖出",
                }
            ],
        )
        row = next(x for x in r["rows"] if x["code"] == "600000")
        assert row["execution_status"] == "executed"
        assert row["discipline_status"] == "no_breach_detected"

    def test_evaluative_tail_without_trade_is_not_a_breach(self):
        """⚠️ **最要紧的一条**：尾盘是「评估/观察」类建议且当日无成交，
        **不得自动判违纪** —— 真实未执行原因未记录时无从判断。
        """
        r = self._run(
            "2026-08-07",
            chief={},
            tail={
                "actions": [{"code": "600000", "action": "减仓评估", "priority": "P2"}]
            },
            trades=[],
        )
        row = r["rows"][0]
        assert row["execution_status"] == "not_executed_reason_unavailable"
        assert row["discipline_status"] == "unavailable", "评估类无成交被判成了违纪"
        assert "不能自动判定违纪" in row["execution_reason"]

    def test_explicit_tail_action_without_trade_requires_review(self):
        """尾盘有**明确动作**却无成交 → 需要人补原因，但仍不自动判违纪。"""
        r = self._run(
            "2026-08-07",
            chief={},
            tail={"actions": [{"code": "600000", "action": "清仓", "priority": "P1"}]},
            trades=[],
        )
        row = r["rows"][0]
        assert row["execution_status"] == "not_executed_requires_review"
        assert row["discipline_status"] == "unavailable"
        assert "user_execution_reason" in r["missing"]

    def test_no_action_no_trade(self):
        r = self._run(
            "2026-08-07",
            chief={"holding_actions": [{"code": "600000", "action": "持有"}]},
            tail={},
            trades=[],
        )
        assert r["rows"][0]["execution_status"] == "no_action_no_trade"

    def test_status_degraded_without_trades_or_confirmation(self):
        """无成交且**未确认「今日无交易」** ⇒ status=degraded。

        这条区分很重要：「确认了今天不交易」和「不知道有没有交易」是两回事，
        后者不能当成正常完成。
        """
        r = self._run("2026-08-07", chief={}, tail={}, trades=[])
        assert r["status"] == "degraded"

    def test_status_complete_when_no_trades_confirmed(self):
        r = self._run(
            "2026-08-07",
            chief={"position_freshness": {"confirmation": {"no_trades": True}}},
            tail={},
            trades=[],
        )
        assert r["status"] == "complete" and r["no_trades_confirmed"] is True

    def test_trades_of_other_days_excluded(self):
        """台账是全量的，必须只取当日 —— 否则历史成交会被当成今天执行了。"""
        r = self._run(
            "2026-08-07",
            chief={},
            tail={"actions": [{"code": "600000", "action": "清仓", "priority": "P1"}]},
            trades=[
                {"成交日期": "2026-08-06", "代码": "600000.SH", "交易类别": "卖出"}
            ],
        )
        assert r["recorded_trade_count"] == 0
        assert r["rows"][0]["execution_status"] == "not_executed_requires_review"

    def test_premarket_snapshot_missing_is_recorded(self):
        """盘前快照缺失要进 `missing` —— 没有它就无法对照「计划 vs 实际」。"""
        r = self._run("2026-08-07", chief={}, tail={}, trades=[])
        assert r["premarket_snapshot_available"] is False
        assert "premarket_chief_decision_snapshot" in r["missing"]

    def test_no_nan_in_output(self):
        """`allow_nan=False`：NaN 会让下游 json.loads 拿到非法值。"""
        r = self._run("2026-08-07", chief={}, tail={}, trades=[])
        assert "NaN" not in json.dumps(r)
