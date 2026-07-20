# -*- coding: utf-8 -*-
"""backtest_0amv_bear_regime 单测：区间划分（含边界当日归属）、FIFO 重放、
跳过买单后的撮合、反弹日判定、非买卖类别处理。"""
from __future__ import annotations

import unittest

import backtest_0amv_bear_regime as bt


def _amv(pairs):
    """pairs: [(date, change_pct)] → 0AMV 记录列表。"""
    return [{"date": d, "change_pct": cp} for d, cp in pairs]


def _trade(date, code, category, qty, price, fee=0.0, cash=None):
    amount = qty * price
    if cash is None:
        cash = -(amount + fee) if category == "买入" else (amount - fee)
    return {"date": date, "time": "10:00:00", "code": code, "name": code,
            "category": category, "qty": float(qty), "price": float(price),
            "amount": amount, "cash": cash, "fee": fee}


def _prices(code, close_by_date):
    dates = sorted(close_by_date)
    return {code: {"dates": dates, "close_by_date": {d: float(close_by_date[d]) for d in dates}}}


class RegimeMapTests(unittest.TestCase):
    def test_boundary_days_belong_to_new_regime(self):
        # -2.3 当日即空头；+4 当日即多头；+4 前一日是空头最后一天
        records = _amv([
            ("2024-01-01", 1.0),
            ("2024-01-02", -2.3),   # 触发空头（含当日）
            ("2024-01-03", -1.0),
            ("2024-01-04", 4.0),    # 触发多头（含当日）
            ("2024-01-05", 0.5),
        ])
        rm = bt.build_regime_map(records)
        self.assertEqual(rm["2024-01-01"], "neutral")
        self.assertEqual(rm["2024-01-02"], "bear")
        self.assertEqual(rm["2024-01-03"], "bear")
        self.assertEqual(rm["2024-01-04"], "bull")
        self.assertEqual(rm["2024-01-05"], "bull")

    def test_exact_thresholds_and_neutral_before_first_trigger(self):
        records = _amv([
            ("2024-01-01", -2.29),  # 未达 -2.3，仍 neutral
            ("2024-01-02", -2.30),
            ("2024-01-03", 3.99),   # 未达 +4，延续空头
            ("2024-01-04", 4.01),
        ])
        rm = bt.build_regime_map(records)
        self.assertEqual(rm["2024-01-01"], "neutral")
        self.assertEqual(rm["2024-01-02"], "bear")
        self.assertEqual(rm["2024-01-03"], "bear")
        self.assertEqual(rm["2024-01-04"], "bull")

    def test_segments(self):
        records = _amv([
            ("2024-01-01", 0.1),
            ("2024-01-02", -3.0),
            ("2024-01-03", 0.0),
            ("2024-01-04", 5.0),
            ("2024-01-05", -4.0),   # 再次进入空头直到末尾
        ])
        rm = bt.build_regime_map(records)
        segs = bt.regime_segments(rm, "bear")
        self.assertEqual(segs, [
            {"start": "2024-01-02", "end": "2024-01-03", "days": 2},
            {"start": "2024-01-05", "end": "2024-01-05", "days": 1},
        ])


class FifoBookTests(unittest.TestCase):
    def test_fifo_consume_order(self):
        b = bt.FifoBook()
        b.add(100, 10.0)
        b.add(100, 12.0)
        consumed = b.consume(150)
        self.assertEqual(consumed, [(100, 10.0, "other"), (50, 12.0, "other")])
        self.assertEqual(b.qty, 50)
        self.assertAlmostEqual(b.cost, 600.0)

    def test_consume_caps_at_available(self):
        b = bt.FifoBook()
        b.add(100, 10.0)
        consumed = b.consume(300)
        self.assertEqual(sum(q for q, _, _ in consumed), 100)
        self.assertEqual(b.qty, 0)


class ScenarioReplayTests(unittest.TestCase):
    def setUp(self):
        # 2024-01-02 起空头，2024-01-05 起多头
        self.records = _amv([
            ("2024-01-01", 0.5),
            ("2024-01-02", -3.0),
            ("2024-01-03", 0.2),
            ("2024-01-04", -0.4),
            ("2024-01-05", 5.0),
        ])
        self.rm = bt.build_regime_map(self.records)
        self.days = [r["date"] for r in self.records]
        self.prices = _prices("600000", {
            "2024-01-01": 10.0, "2024-01-02": 10.0, "2024-01-03": 10.5,
            "2024-01-04": 10.2, "2024-01-05": 11.0,
        })

    def test_actual_fifo_realized(self):
        trades = [
            _trade("2024-01-01", "600000", "买入", 100, 10.0, fee=1.0),
            _trade("2024-01-02", "600000", "买入", 100, 9.0, fee=1.0),  # 空头区买入
            _trade("2024-01-05", "600000", "卖出", 150, 11.0, fee=1.0),
        ]
        res = bt.run_scenario(trades, self.rm, self.days, self.prices,
                              "actual", 0.001, "2024-01-01", "2024-01-05")
        # FIFO：先卖 100@成本10.01，再卖 50@成本9.01
        # proceeds = 1650-1 = 1649；成本 = 1001 + 450.5 = 1451.5；realized = 197.5
        self.assertAlmostEqual(res["realized_pnl"], 197.5, places=2)
        # 剩 50 股成本 9.01，期末价 11 → 浮盈 99.5
        self.assertAlmostEqual(res["unrealized_pnl"], 99.5, places=2)

    def test_no_bear_buys_skips_and_caps_sell(self):
        trades = [
            _trade("2024-01-01", "600000", "买入", 100, 10.0),
            _trade("2024-01-02", "600000", "买入", 100, 9.0),   # 空头区 → 跳过
            _trade("2024-01-05", "600000", "卖出", 150, 11.0),  # 只能卖 100
        ]
        res = bt.run_scenario(trades, self.rm, self.days, self.prices,
                              "no_bear_buys", 0.001, "2024-01-01", "2024-01-05")
        self.assertEqual(len(res["skipped_buys"]), 1)
        self.assertEqual(res["skipped_buys"][0]["qty"], 100)
        self.assertEqual(len(res["shortfalls"]), 1)
        self.assertEqual(res["shortfalls"][0]["available"], 100)
        # 只卖出 100 股，proceeds 按比例折算 1650*100/150=1100，成本 1000
        self.assertAlmostEqual(res["realized_pnl"], 100.0, places=2)
        self.assertEqual(res["positions_end"].get("600000", {}).get("qty", 0), 0)

    def test_bull_regime_buys_not_skipped(self):
        trades = [
            _trade("2024-01-05", "600000", "买入", 100, 11.0),  # 多头区，不跳过
        ]
        res = bt.run_scenario(trades, self.rm, self.days, self.prices,
                              "no_bear_buys", 0.001, "2024-01-01", "2024-01-05")
        self.assertEqual(len(res["skipped_buys"]), 0)
        self.assertEqual(res["positions_end"]["600000"]["qty"], 100)


class ReboundTests(unittest.TestCase):
    def test_is_rebound_day(self):
        entry = _prices("600000", {"2024-01-01": 10.0, "2024-01-02": 10.5,
                                   "2024-01-03": 10.5, "2024-01-04": 10.2})["600000"]
        self.assertTrue(bt.is_rebound_day(entry, "2024-01-02"))    # 10.5 > 10.0
        self.assertFalse(bt.is_rebound_day(entry, "2024-01-03"))   # 持平不算
        self.assertFalse(bt.is_rebound_day(entry, "2024-01-04"))   # 下跌
        self.assertFalse(bt.is_rebound_day(entry, "2024-01-01"))   # 无前收盘

    def test_rebound_reduce_sells_20pct_on_rebound_days(self):
        records = _amv([
            ("2024-01-01", 0.5),    # neutral：买入日
            ("2024-01-02", -3.0),   # 进入空头，当日跌
            ("2024-01-03", 0.1),    # 空头区，个股反弹 → 减 20%
            ("2024-01-04", 0.1),    # 空头区，个股再反弹 → 再减 20%
        ])
        rm = bt.build_regime_map(records)
        days = [r["date"] for r in records]
        prices = _prices("600000", {
            "2024-01-01": 10.0, "2024-01-02": 9.5,
            "2024-01-03": 9.8, "2024-01-04": 10.1,
        })
        trades = [_trade("2024-01-01", "600000", "买入", 1000, 10.0)]
        res = bt.run_scenario(trades, rm, days, prices,
                              "rebound_reduce", 0.001, "2024-01-01", "2024-01-04")
        sells = res["rebound_sells"]
        self.assertEqual(len(sells), 2)
        self.assertAlmostEqual(sells[0]["qty"], 200.0)   # 1000*20%
        self.assertAlmostEqual(sells[0]["price"], 9.8)
        self.assertAlmostEqual(sells[1]["qty"], 160.0)   # 800*20%
        self.assertAlmostEqual(sells[1]["price"], 10.1)
        self.assertAlmostEqual(res["positions_end"]["600000"]["qty"], 640.0)
        # 反弹减仓费：9.8*200*0.001 + 10.1*160*0.001 = 1.96 + 1.616
        expected_realized = (9.8 * 200 - 1.96 - 200 * 10.0) + (10.1 * 160 - 1.616 - 160 * 10.0)
        self.assertAlmostEqual(res["realized_pnl"], expected_realized, places=2)

    def test_rebound_not_applied_outside_bear(self):
        records = _amv([("2024-01-01", 0.5), ("2024-01-02", 0.6)])  # 全程 neutral
        rm = bt.build_regime_map(records)
        days = [r["date"] for r in records]
        prices = _prices("600000", {"2024-01-01": 10.0, "2024-01-02": 10.5})
        trades = [_trade("2024-01-01", "600000", "买入", 1000, 10.0)]
        res = bt.run_scenario(trades, rm, days, prices,
                              "rebound_reduce", 0.001, "2024-01-01", "2024-01-02")
        self.assertEqual(len(res["rebound_sells"]), 0)


class SpecialCategoryTests(unittest.TestCase):
    def setUp(self):
        self.records = _amv([("2024-01-01", 0.5), ("2024-01-02", 0.6)])
        self.rm = bt.build_regime_map(self.records)
        self.days = [r["date"] for r in self.records]
        self.prices = _prices("600000", {"2024-01-01": 10.0, "2024-01-02": 10.0})

    def test_dividend_cash_only(self):
        trades = [
            _trade("2024-01-01", "600000", "买入", 100, 10.0),
            {"date": "2024-01-02", "time": "", "code": "600000", "name": "x",
             "category": "除权除息", "qty": 0.0, "price": 0.0,
             "amount": 0.0, "cash": 288.0, "fee": 0.0},
        ]
        res = bt.run_scenario(trades, self.rm, self.days, self.prices,
                              "actual", 0.001, "2024-01-01", "2024-01-02")
        self.assertEqual(res["other_cashflow"], 288.0)
        self.assertEqual(res["positions_end"]["600000"]["qty"], 100)  # 数量不变

    def test_split_adds_zero_cost_lot(self):
        trades = [
            _trade("2024-01-01", "600000", "买入", 3000, 10.0),
            {"date": "2024-01-02", "time": "", "code": "600000", "name": "x",
             "category": "拆股", "qty": 600.0, "price": 0.0,
             "amount": 0.0, "cash": 0.0, "fee": 0.0},
        ]
        res = bt.run_scenario(trades, self.rm, self.days, self.prices,
                              "actual", 0.001, "2024-01-01", "2024-01-02")
        self.assertEqual(res["positions_end"]["600000"]["qty"], 3600)

    def test_drawdown(self):
        dd = bt.compute_drawdown([10, 100, 40, 120, 30],
                                 ["d1", "d2", "d3", "d4", "d5"])
        self.assertEqual(dd["max_dd_yuan"], 90)      # 120 → 30
        self.assertEqual(dd["peak_date"], "d4")
        self.assertEqual(dd["trough_date"], "d5")
        self.assertAlmostEqual(dd["max_dd_pct"], 75.0)

    def test_drawdown_pct_suppressed_when_equity_nonpositive(self):
        # 权益曾跌破 0（台账无入金、从 0 起步），相对百分比无意义
        dd = bt.compute_drawdown([0, 100, -50], ["d1", "d2", "d3"])
        self.assertEqual(dd["max_dd_yuan"], 150)
        self.assertIsNone(dd["max_dd_pct"])
        self.assertIsNotNone(dd["note"])


if __name__ == "__main__":
    unittest.main()
