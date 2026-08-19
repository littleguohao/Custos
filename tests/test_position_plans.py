# -*- coding: utf-8 -*-
"""core/trades/position_plans（v0.82）钉测。

覆盖：①新建仓生成（candidate 来源止损价）②候选缺失兜底（entry×(1−7%)）
③补仓不覆盖止损价、entry_price 随摊薄成本更新 ④清仓归档（带 closed_at）
⑤转债转入/拆股等入账类不触发 ⑥坏 JSON 文件兜底不炸
⑦incremental_ledger.apply_positions 集成挂钩。
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from custos.core import positions_history
from custos.core.trades import incremental_ledger as il
from custos.core.trades import position_plans as pp


def _trades(rows):
    return pd.DataFrame(rows)


def _buy(code="000001", day="2026-08-19", price=10.0, qty=100):
    return {
        "成交日期": day,
        "代码": code,
        "名称": "测试",
        "交易类别": "买入",
        "成交数量": qty,
        "成交价格": price,
        "费用": 0.0,
    }


def _pos(code="000001", cost=10.0, qty=100):
    return {"代码": code, "名称": "测试", "持有数量": qty, "单位成本": cost}


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    self_plans = tmp_path / "position_plans.json"
    self_pool = tmp_path / "stock_pool"
    self_pool.mkdir()
    monkeypatch.setattr(pp, "PLANS_FILE", self_plans)
    monkeypatch.setattr(pp, "POOL_DIR", self_pool)
    return type("Ctx", (), {"plans": self_plans, "pool": self_pool})()


class TestNewPosition:
    def test_candidate_stop_from_latest_pool(self, _tmp):
        """新建仓：止损价取 ≤ 买入日最近一份 stock_pool 的 stop_loss_ref。"""
        (_tmp.pool / "2026-08-18_stock_pool.json").write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "code": "000001",
                            "stop_loss_ref": {"price": 9.5, "basis": "破10日最低价"},
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        plans = pp.sync_plans(_trades([_buy()]), [], [_pos()])
        plan = plans["positions"]["000001"]
        assert plan["source"] == "candidate:2026-08-18"
        assert plan["stop"] == {
            "rule_id": "stock_pool_stop_ref",
            "price": 9.5,
            "basis": "破10日最低价",
        }
        assert plan["entry_date"] == "2026-08-19"
        assert plan["entry_price"] == 10.0
        # enabled 止盈方案快照（exit_rules 默认 scale_out_two_bull 在跑）
        assert "scale_out_two_bull" in plan["take_profit"]
        assert plan["rules_version"]

    def test_default_fallback_when_no_candidate(self, _tmp):
        """候选记录找不到：兜底 entry×(1+loss_reduction −7%)，source=default。"""
        plans = pp.sync_plans(_trades([_buy()]), [], [_pos()])
        plan = plans["positions"]["000001"]
        assert plan["source"] == "default"
        assert plan["stop"]["rule_id"] == "loss_reduction"
        assert plan["stop"]["price"] == pytest.approx(10.0 * 0.93)
        assert "兜底" in plan["stop"]["basis"]


class TestAddAndClose:
    def test_add_buy_keeps_stop_updates_entry_price(self, _tmp):
        """补仓：不重建计划、止损价不动；entry_price 随摊薄成本更新并标 averaged。"""
        pp.sync_plans(_trades([_buy()]), [], [_pos()])
        plans = pp.sync_plans(
            _trades([_buy(day="2026-08-20", price=12.0)]),
            [_pos(cost=10.0)],
            [_pos(cost=11.0, qty=200)],
        )
        plan = plans["positions"]["000001"]
        assert plan["entry_price"] == 11.0  # 摊薄后单位成本（与 live pnl 口径同源）
        assert plan["averaged"] is True
        assert plan["stop"]["price"] == pytest.approx(10.0 * 0.93)  # 止损价不被覆盖
        assert plan["entry_date"] == "2026-08-19"  # 首笔买入日不变

    def test_close_moves_plan_to_archive(self, _tmp):
        """清仓：条目从 positions 移入 archive，带 closed_at。"""
        pp.sync_plans(_trades([_buy()]), [], [_pos()])
        sell = _buy(qty=100)
        sell["交易类别"] = "卖出"
        plans = pp.sync_plans(_trades([sell]), [_pos()], [])
        assert "000001" not in plans["positions"]
        archived = plans["archive"]["000001"]
        assert len(archived) == 1 and archived[0]["closed_at"]
        assert archived[0]["stop"]["price"] == pytest.approx(10.0 * 0.93)


class TestShareCreditAndCorrupt:
    def test_share_credit_does_not_create_plan(self, _tmp):
        """转债转入/拆股建的头寸不触发计划生成（无买入决策 ⇒ 无选股止损参考）。"""
        credit = _buy()
        credit["交易类别"] = "转债转入"
        plans = pp.sync_plans(_trades([credit]), [], [_pos()])
        assert plans["positions"] == {}

    def test_corrupt_plans_file_falls_back(self, _tmp):
        """计划文件损坏：回落空结构照常生成，不炸导入链。"""
        _tmp.plans.write_text("{ not json", encoding="utf-8")
        plans = pp.sync_plans(_trades([_buy()]), [], [_pos()])
        assert plans["positions"]["000001"]["source"] == "default"


class TestLedgerIntegration:
    def test_apply_positions_syncs_plans(self, tmp_path, monkeypatch):
        """写入点挂钩：apply_positions（直接调用方路径）同步生成计划。"""
        monkeypatch.setattr(il, "POS", tmp_path / "current_positions.json")
        monkeypatch.setattr(positions_history, "HISTORY_DIR", tmp_path / "hist")
        monkeypatch.setattr(pp, "PLANS_FILE", tmp_path / "position_plans.json")
        monkeypatch.setattr(pp, "POOL_DIR", tmp_path / "stock_pool")
        il.apply_positions(il.norm(_trades([_buy()])))
        plans = json.loads((tmp_path / "position_plans.json").read_text("utf-8"))
        assert plans["positions"]["000001"]["entry_price"] == pytest.approx(10.0)
