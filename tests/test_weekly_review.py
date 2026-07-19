# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from close_review import weekly_review as wr

WEEK_DAYS = ["2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17"]
CALENDAR = {
    "official_years": {
        "2026": {
            "closed_ranges": [{"name": "测试假日", "start": "2026-10-01", "end": "2026-10-07"}],
        }
    }
}


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def make_base(tmp_path: Path) -> Path:
    write_json(tmp_path / "00_governance" / "CN_TRADING_CALENDAR.json", CALENDAR)
    return tmp_path


def write_ledger(base: Path, rows: list[list]) -> None:
    lines = ["成交日期,成交时间,代码,名称,交易类别,成交数量,成交价格,成交金额,发生金额,费用,备注"]
    for r in rows:
        lines.append(",".join(str(x) for x in r))
    path = base / "01_data" / "trades" / "master_trade_ledger.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\ufeff" + "\n".join(lines), encoding="utf-8")


def write_review(base: Path, day: str, plan_codes: list[str] | None = None) -> None:
    review = {"date": day, "next_day_plan": {"holding_plans": [{"code": c} for c in (plan_codes or [])]}}
    write_json(base / "04_reviews" / "daily" / f"{day}_final_review.json", review)


def write_mfe(base: Path, day: str, holdings: list[dict]) -> None:
    write_json(base / "01_data" / "holdings" / f"{day}_mfe_mae.json", {"date": day, "holdings": holdings})


def write_amv(base: Path, entries: list[tuple[str, float]]) -> None:
    path = base / "01_data" / "market" / "0amv_observations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps({"date": d, "amv_change_pct": p}) for d, p in entries),
        encoding="utf-8",
    )


def write_meta(base: Path, confirmed: dict) -> None:
    write_json(base / "01_data" / "trades" / "_import_meta.json", {"no_trades_confirmed_dates": confirmed})


class IsoWeekRangeTests(unittest.TestCase):
    def test_plain_week(self):
        w = wr.iso_week_range("2026-07-15")
        self.assertEqual((w["iso_year"], w["iso_week"]), (2026, 29))
        self.assertEqual((w["start"], w["end"]), ("2026-07-13", "2026-07-17"))

    def test_sunday_maps_to_same_week(self):
        w = wr.iso_week_range("2026-07-19")
        self.assertEqual((w["start"], w["end"]), ("2026-07-13", "2026-07-17"))

    def test_cross_month(self):
        w = wr.iso_week_range("2026-07-30")
        self.assertEqual((w["start"], w["end"]), ("2026-07-27", "2026-07-31"))

    def test_cross_year(self):
        # 2026-01-01 是周四，属于 ISO 2026-W01，周一落在 2025-12-29
        w = wr.iso_week_range("2026-01-01")
        self.assertEqual((w["iso_year"], w["iso_week"]), (2026, 1))
        self.assertEqual((w["start"], w["end"]), ("2025-12-29", "2026-01-02"))


class LedgerTests(unittest.TestCase):
    def test_parse_skips_non_trade_and_reads_bom(self):
        with self.subTest("fixture"):
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                base = make_base(Path(td))
                write_ledger(base, [
                    ["2026-07-13", "10:00:00", "600000", "测试A", "买入", 100, 10.0, 1000.0, -1001.0, 1.0, ""],
                    ["2026-07-13", "10:01:00", "600000", "测试A", "转债转入", 3, 0, 0, 0, 0, ""],
                ])
                trades = wr.parse_ledger(base / "01_data" / "trades" / "master_trade_ledger.csv")
                self.assertEqual(len(trades), 1)
                self.assertEqual(trades[0]["code"], "600000")
                self.assertEqual(trades[0]["qty"], 100.0)

    def test_fifo_partial_sell(self):
        trades = [
            {"date": "2026-07-01", "time": "09:30:00", "code": "600000", "name": "A", "side": "买入", "qty": 100.0, "price": 10.0, "amount": 1000.0, "fee": 1.0},
            {"date": "2026-07-02", "time": "09:30:00", "code": "600000", "name": "A", "side": "买入", "qty": 100.0, "price": 11.0, "amount": 1100.0, "fee": 1.0},
            {"date": "2026-07-14", "time": "14:00:00", "code": "600000", "name": "A", "side": "卖出", "qty": 150.0, "price": 12.0, "amount": 1800.0, "fee": 1.0},
        ]
        closings = wr.fifo_pair(trades)
        self.assertEqual(len(closings), 1)
        c = closings[0]
        # 100 股 @10 + 50 股 @11，卖出 150 @12 → 200 + 50 = 250
        self.assertEqual(c["gross_pnl"], 250.0)
        # 数量加权买入成本 = (100*10 + 50*11)/150
        self.assertAlmostEqual(c["avg_buy_cost"], 1550.0 / 150.0, places=4)
        # 加权买入日 = (07-01*100 + 07-02*50)/150 ≈ 07-01，持有约 13 天
        self.assertEqual(c["hold_days"], 13)


def traded_base(tmp_path: Path) -> Path:
    """构造一个有交易的 fixture：07-13 计划、07-14 卖 600000 亏 -9%、07-15 买 600001。"""
    base = make_base(tmp_path)
    write_ledger(base, [
        ["2026-07-06", "09:30:00", "600000", "测试A", "买入", 100, 100.0, 10000.0, -10001.0, 1.0, ""],
        ["2026-07-14", "14:00:00", "600000", "测试A", "卖出", 100, 91.0, 9100.0, 9099.0, 1.0, ""],
        ["2026-07-15", "09:30:00", "600001", "测试B", "买入", 100, 10.0, 1000.0, -1001.0, 1.0, ""],
    ])
    write_review(base, "2026-07-13", plan_codes=["600000"])  # 600001 不在计划 → 计划外
    for d in WEEK_DAYS[1:]:
        write_review(base, d, plan_codes=[])
    write_meta(base, {d: True for d in ["2026-07-13", "2026-07-16", "2026-07-17"]})
    return base


class ExecutionRuleTests(unittest.TestCase):
    def test_plan_match_hit_and_miss(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = traded_base(Path(td))
            review = wr.build_weekly_review(base, "2026-07-15")
            checks = {(p["code"], p["date"]): p["status"] for p in review["details"]["plan_checks"]}
            self.assertEqual(checks[("600000", "2026-07-14")], "planned")
            self.assertEqual(checks[("600001", "2026-07-15")], "unplanned")
            rules = [i["rule"] for i in review["execution_issues"]]
            self.assertIn("unplanned_trade", rules)

    def test_plan_unknown_when_no_prior_review(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_ledger(base, [
                ["2026-07-14", "09:30:00", "600000", "测试A", "买入", 100, 10.0, 1000.0, -1001.0, 1.0, ""],
            ])
            review = wr.build_weekly_review(base, "2026-07-15")
            checks = review["details"]["plan_checks"]
            self.assertEqual(checks[0]["status"], "unknown")
            self.assertTrue(any("计划归属无法判定" in u for u in review["unavailable"]))

    def test_slow_stop_loss_rule(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = traded_base(Path(td))
            review = wr.build_weekly_review(base, "2026-07-15")
            slow = [i for i in review["execution_issues"] if i["rule"] == "slow_stop_loss"]
            self.assertEqual(len(slow), 1)
            self.assertEqual(slow[0]["evidence"][0]["pnl_pct"], -9.0)

    def test_compliant_stop_not_flagged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_ledger(base, [
                ["2026-07-13", "09:30:00", "600000", "测试A", "买入", 100, 100.0, 10000.0, -10001.0, 1.0, ""],
                ["2026-07-14", "14:00:00", "600000", "测试A", "卖出", 100, 95.0, 9500.0, 9499.0, 1.0, ""],
            ])
            write_review(base, "2026-07-13", plan_codes=["600000"])
            write_meta(base, {})
            review = wr.build_weekly_review(base, "2026-07-15")
            self.assertNotIn("slow_stop_loss", [i["rule"] for i in review["execution_issues"]])

    def test_no_trade_confirmation_completeness(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_meta(base, {"2026-07-13": True})
            review = wr.build_weekly_review(base, "2026-07-15")
            issues = [i for i in review["execution_issues"] if i["rule"] == "no_trade_confirmation_missing"]
            self.assertEqual(len(issues), 1)
            self.assertEqual(review["facts"]["no_trade_unconfirmed"], WEEK_DAYS[1:])

    def test_no_trade_confirmation_all_present(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_meta(base, {d: True for d in WEEK_DAYS})
            review = wr.build_weekly_review(base, "2026-07-15")
            self.assertNotIn("no_trade_confirmation_missing", [i["rule"] for i in review["execution_issues"]])


class StrategyRuleTests(unittest.TestCase):
    def sell_fly_base(self, td: str, mfe_pct: float | None) -> Path:
        base = make_base(Path(td))
        write_ledger(base, [
            ["2026-07-13", "09:30:00", "600000", "测试A", "买入", 100, 10.0, 1000.0, -1001.0, 1.0, ""],
            ["2026-07-14", "14:00:00", "600000", "测试A", "卖出", 100, 10.5, 1050.0, 1049.0, 1.0, ""],
        ])
        write_review(base, "2026-07-13", plan_codes=["600000"])
        write_meta(base, {})
        if mfe_pct is not None:
            write_mfe(base, "2026-07-17", [{"code": "600000", "cost": 10.0, "mfe_pct": mfe_pct, "mfe_date": "2026-07-16"}])
        return base

    def test_sell_fly_hit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            review = wr.build_weekly_review(self.sell_fly_base(td, 20.0), "2026-07-15")
            hits = [i for i in review["strategy_issues"] if i["rule"] == "sell_fly"]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["evidence"][0]["implied_mfe_price"], 12.0)

    def test_sell_fly_miss(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            review = wr.build_weekly_review(self.sell_fly_base(td, 2.0), "2026-07-15")
            self.assertNotIn("sell_fly", [i["rule"] for i in review["strategy_issues"]])

    def test_sell_fly_unevaluated_without_mfe(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            review = wr.build_weekly_review(self.sell_fly_base(td, None), "2026-07-15")
            self.assertNotIn("sell_fly", [i["rule"] for i in review["strategy_issues"]])
            self.assertEqual(len(review["facts"]["sell_fly_unevaluated"]), 1)

    def test_short_hold_loss_profile(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            # 短持有亏损 -100（持有 7 天）+ 长持有亏损 -300（持有 40 天）
            write_ledger(base, [
                ["2026-07-06", "09:30:00", "600000", "短持", "买入", 100, 10.0, 1000.0, -1000.0, 0.0, ""],
                ["2026-07-13", "14:00:00", "600000", "短持", "卖出", 100, 9.0, 900.0, 900.0, 0.0, ""],
                ["2026-06-01", "09:30:00", "600001", "长持", "买入", 100, 10.0, 1000.0, -1000.0, 0.0, ""],
                ["2026-07-14", "14:00:00", "600001", "长持", "卖出", 100, 7.0, 700.0, 700.0, 0.0, ""],
            ])
            write_review(base, "2026-07-10", plan_codes=["600000", "600001"])
            write_meta(base, {})
            review = wr.build_weekly_review(base, "2026-07-15")
            issue = [i for i in review["strategy_issues"] if i["rule"] == "short_hold_loss_profile"][0]
            self.assertEqual(issue["evidence"]["short_hold_loss_share_pct"], 25.0)
            self.assertEqual(issue["evidence"]["short_hold_loss_count"], 1)

    def test_bear_regime_background(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_ledger(base, [
                ["2026-07-06", "09:30:00", "600000", "测试A", "买入", 100, 10.0, 1000.0, -1000.0, 0.0, ""],
                ["2026-07-13", "14:00:00", "600000", "测试A", "卖出", 100, 9.0, 900.0, 900.0, 0.0, ""],
            ])
            write_review(base, "2026-07-10", plan_codes=["600000"])
            write_meta(base, {})
            write_amv(base, [("2026-07-13", -5.0), ("2026-07-14", 5.0), ("2026-07-15", 0.0)])
            review = wr.build_weekly_review(base, "2026-07-15")
            f = review["facts"]
            self.assertEqual(f["bear_days"], ["2026-07-13"])
            self.assertAlmostEqual(f["bear_day_ratio_pct"], round(1 / 3 * 100, 2))
            self.assertEqual(f["bear_loss_share_pct"], 100.0)


class DegradationTests(unittest.TestCase):
    def test_empty_base_runs_and_marks_unavailable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)  # 连日历都没有
            review = wr.build_weekly_review(base, "2026-07-15")
            self.assertEqual(review["facts"]["trade_count"], 0)
            self.assertTrue(any("成交台账缺失" in u for u in review["unavailable"]))
            self.assertTrue(any("交易日历未覆盖" in u for u in review["unavailable"]))
            md = wr.render_markdown(review)
            self.assertIn("数据缺口声明", md)
            self.assertIn("本周无成交记录", md)

    def test_main_writes_json_and_md(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = traded_base(Path(td))
            with patch("sys.argv", ["weekly_review.py", "--date", "2026-07-19", "--base", str(base)]):
                wr.main()
            json_path = base / "04_reviews" / "weekly" / "2026W29_weekly_review.json"
            md_path = base / "04_reviews" / "weekly" / "2026W29_weekly_review.md"
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(data["range"], {"start": "2026-07-13", "end": "2026-07-17"})
            self.assertIn("执行纪律审计", md_path.read_text(encoding="utf-8"))

    def test_holiday_weekday_not_counted(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_meta(base, {})
            # 2026-10-01~07 休市；10-05~10-09 这周只有 10-05/10-06/10-07 是工作日且全部休市
            review = wr.build_weekly_review(base, "2026-10-07")
            self.assertEqual(review["trading_days"], ["2026-10-08", "2026-10-09"])


# ------------------------------------------------- 新板块 fixture 辅助

def write_full_review(base: Path, day: str, revalued: list[dict]) -> None:
    write_json(base / "04_reviews" / "daily" / f"{day}_final_review.json",
               {"date": day, "revalued_positions": revalued})


def revalued(code: str, name: str, close: float, weight: float, mv: float, pnl: float, cost: float) -> dict:
    return {"code": code, "name": name, "close": close, "position_pct": weight,
            "market_value": mv, "pnl_pct": pnl, "cost": cost, "quantity": 100}


def write_market_timing(base: Path, day: str, latest_date: str, close: float, chg: float | None) -> None:
    write_json(base / "01_data" / "market" / f"{day}_market_timing_input.json",
               {"a_share_indices": {"上证指数": {"latest_date": latest_date, "latest_close": close,
                                               "daily_change_pct": chg}}})


def write_chief(base: Path, day: str, state: str, permission: str) -> None:
    write_json(base / "01_data" / "decisions" / f"{day}_chief_decision.json",
               {"date": day, "market_state": state, "market_score": "40/100",
                "total_position_range": "20%-40%", "new_position_permission": permission})


def write_b1(base: Path, day: str, items: list[dict]) -> None:
    write_json(base / "01_data" / "holdings" / f"{day}_b1_holding_state.json", items)


class HoldingPerformanceTests(unittest.TestCase):
    def test_with_data(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_full_review(base, "2026-07-14", [revalued("600000", "测试A", 10.0, 0.20, 20000.0, 0.05, 9.5)])
            write_full_review(base, "2026-07-17", [revalued("600000", "测试A", 9.0, 0.19, 18000.0, -0.05, 9.5)])
            write_b1(base, "2026-07-16", [{"code": "600000", "final_priority": "P1", "final_action": "N型回踩失守评估"}])
            write_b1(base, "2026-07-17", [{"code": "600000", "final_priority": "P0", "final_action": "下降N型结构清仓评估"}])
            review = wr.build_weekly_review(base, "2026-07-15")
            hp = review["holding_performance"]
            self.assertEqual(len(hp["rows"]), 1)
            row = hp["rows"][0]
            self.assertEqual((row["first_date"], row["last_date"]), ("2026-07-14", "2026-07-17"))
            self.assertEqual(row["week_change_pct"], -10.0)
            self.assertEqual((row["first_weight_pct"], row["last_weight_pct"]), (20.0, 19.0))
            self.assertEqual(row["float_pnl_pct"], -5.0)
            self.assertAlmostEqual(row["contribution_pp"], -2.0)
            traj = hp["b1_trajectory"]["600000"]
            self.assertEqual([s["priority"] for s in traj], ["P1", "P0"])

    def test_holding_quotes_fallback_and_name_lookup(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            # 只有 holding_quotes，无 revalued：价格可取，权重 unavailable；名称由 current_positions 兜底
            write_json(base / "01_data" / "market" / "2026-07-14_holding_quotes.json",
                       {"quotes": [{"code": "600000", "close": 10.0}]})
            write_full_review(base, "2026-07-17", [revalued("600000", "测试A", 11.0, 0.2, 20000.0, 0.1, 10.0)])
            write_json(base / "01_data" / "trades" / "current_positions.json", [{"代码": "600000", "名称": "兜底名"}])
            review = wr.build_weekly_review(base, "2026-07-15")
            row = review["holding_performance"]["rows"][0]
            self.assertEqual(row["first_date"], "2026-07-14")
            self.assertEqual(row["week_change_pct"], 10.0)
            # 权重取首个有权重值的日期（07-17），而非价格首日
            self.assertEqual(row["first_weight_pct"], 20.0)
            self.assertEqual(row["first_weight_date"], "2026-07-17")
            self.assertEqual(row["name"], "测试A")

    def test_missing_data_degrades(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_meta(base, {d: True for d in WEEK_DAYS})
            review = wr.build_weekly_review(base, "2026-07-15")
            self.assertEqual(review["holding_performance"]["rows"], [])
            self.assertTrue(any("持仓周度表现" in u for u in review["unavailable"]))
            self.assertTrue(any("B1 持仓状态" in u for u in review["unavailable"]))


class PortfolioTrajectoryTests(unittest.TestCase):
    def write_portfolio_reviews(self, base: Path) -> None:
        write_full_review(base, "2026-07-15", [
            revalued("600000", "A", 10.0, 0.5, 60.0, 0.0, 10.0),
            revalued("600001", "B", 10.0, 0.3, 40.0, 0.0, 10.0)])
        write_full_review(base, "2026-07-16", [
            revalued("600000", "A", 9.0, 0.5, 54.0, -0.1, 10.0),
            revalued("600001", "B", 9.0, 0.3, 36.0, -0.1, 10.0)])
        write_full_review(base, "2026-07-17", [
            revalued("600000", "A", 9.5, 0.5, 57.0, -0.05, 10.0),
            revalued("600001", "B", 9.5, 0.3, 38.0, -0.05, 10.0)])

    def test_return_drawdown_benchmark(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            self.write_portfolio_reviews(base)
            write_meta(base, {d: True for d in WEEK_DAYS})
            for d, chg in zip(WEEK_DAYS, [-1.0, -2.0, 1.0, -3.0, 0.5]):
                write_market_timing(base, d, d.replace("-", ""), 100.0, chg)
            review = wr.build_weekly_review(base, "2026-07-15")
            pf = review["portfolio"]
            self.assertEqual(len(pf["daily"]), 3)
            self.assertEqual(pf["daily"][0]["total_position_pct"], 80.0)
            self.assertEqual(pf["week_return_pct"], -5.0)
            self.assertEqual(pf["max_drawdown_pct"], -10.0)
            expected = (0.99 * 0.98 * 1.01 * 0.97 * 1.005 - 1) * 100
            self.assertAlmostEqual(pf["benchmark_week_pct"], round(expected, 2))
            self.assertEqual(pf["benchmark_missing_days"], [])

    def test_insufficient_data_degrades(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_full_review(base, "2026-07-17", [revalued("600000", "A", 10.0, 0.5, 100.0, 0.0, 10.0)])
            write_meta(base, {d: True for d in WEEK_DAYS})
            review = wr.build_weekly_review(base, "2026-07-15")
            pf = review["portfolio"]
            self.assertIsNone(pf["week_return_pct"])
            self.assertIsNone(pf["max_drawdown_pct"])
            self.assertTrue(any("组合轨迹" in u for u in review["unavailable"]))
            self.assertTrue(any("基准对照" in u for u in review["unavailable"]))

    def test_benchmark_chg_fallback_by_close_ratio(self):
        # daily_change_pct 为 None 时用相邻 latest_close 比值
        sse_map = {"2026-07-13": {"close": 100.0, "chg": None},
                   "2026-07-14": {"close": 98.0, "chg": None}}
        self.assertEqual(wr.sse_change(sse_map, "2026-07-14"), -2.0)
        self.assertIsNone(wr.sse_change(sse_map, "2026-07-13"))
        self.assertIsNone(wr.sse_change(sse_map, "2026-07-15"))

    def test_partial_day_excluded_from_metrics(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_meta(base, {d: True for d in WEEK_DAYS})
            # 中间日一只持仓缺报价（market_value None），不得造成虚假回撤
            write_full_review(base, "2026-07-15", [
                revalued("600000", "A", 10.0, 0.5, 60.0, 0.0, 10.0),
                revalued("600001", "B", 10.0, 0.3, 40.0, 0.0, 10.0)])
            write_full_review(base, "2026-07-16", [
                revalued("600000", "A", 9.9, 0.5, 59.4, -0.01, 10.0),
                {"code": "600001", "name": "B", "close": None, "position_pct": None,
                 "market_value": None, "pnl_pct": None, "cost": 10.0}])
            write_full_review(base, "2026-07-17", [
                revalued("600000", "A", 9.5, 0.5, 57.0, -0.05, 10.0),
                revalued("600001", "B", 9.5, 0.3, 38.0, -0.05, 10.0)])
            review = wr.build_weekly_review(base, "2026-07-15")
            pf = review["portfolio"]
            self.assertTrue(pf["daily"][1]["partial"])
            self.assertEqual(pf["daily"][1]["unpriced_codes"], ["600001"])
            self.assertEqual(pf["week_return_pct"], -5.0)   # 100 → 95，不受中间日影响
            self.assertEqual(pf["max_drawdown_pct"], -5.0)
            self.assertEqual(len(pf["partial_notes"]), 1)


class AdviceReviewTests(unittest.TestCase):
    def build_advice_base(self, td: str, chg_0714: float) -> Path:
        """07-14 次日涨跌可配；其余固定：07-15 -2%、07-16 -3%、07-17 -3.05%。"""
        base = make_base(Path(td))
        write_meta(base, {d: True for d in WEEK_DAYS})
        write_chief(base, "2026-07-13", "防守", "禁止")
        write_chief(base, "2026-07-14", "防守", "仅观察，不得加仓")
        write_chief(base, "2026-07-15", "震荡偏弱", "仅观察，不得加仓")
        write_chief(base, "2026-07-16", "防守", "禁止")
        write_chief(base, "2026-07-17", "防守", "禁止")
        write_market_timing(base, "2026-07-14", "20260714", 100.0, chg_0714)
        write_market_timing(base, "2026-07-15", "20260715", 98.0, -2.0)
        write_market_timing(base, "2026-07-16", "20260716", 95.0, -3.0)
        write_market_timing(base, "2026-07-17", "20260717", 92.0, -3.05)
        # 组合周收益为负（两天可得，100 → 95）
        write_full_review(base, "2026-07-16", [revalued("600000", "A", 10.0, 0.5, 100.0, 0.0, 10.0)])
        write_full_review(base, "2026-07-17", [revalued("600000", "A", 9.5, 0.5, 95.0, -0.05, 10.0)])
        return base

    def test_verdicts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            review = wr.build_weekly_review(self.build_advice_base(td, 1.0), "2026-07-15")
            verdicts = {r["date"]: r["verdict"] for r in review["advice_review"]["rows"]}
            self.assertEqual(verdicts["2026-07-13"], "失误")      # 防守 + 次日 +1.0%
            self.assertEqual(verdicts["2026-07-14"], "正确")      # 防守 + 次日 -2.0%
            self.assertEqual(verdicts["2026-07-15"], "正确")      # 震荡偏弱(bearish) + 次日 -3.0%
            self.assertEqual(verdicts["2026-07-16"], "正确")      # 防守 + 次日 -3.05%
            self.assertEqual(verdicts["2026-07-17"], "待验证")    # 次日 07-20 无数据

    def test_wrong_advice_attribution(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            review = wr.build_weekly_review(self.build_advice_base(td, 1.0), "2026-07-15")
            self.assertIn("wrong_advice_direction", [i["rule"] for i in review["strategy_issues"]])
            self.assertEqual(review["environment_issues"], [])

    def test_environment_attribution_when_all_correct(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            review = wr.build_weekly_review(self.build_advice_base(td, -1.0), "2026-07-15")
            env = [i["rule"] for i in review["environment_issues"]]
            self.assertEqual(env, ["adverse_market_environment"])
            self.assertNotIn("wrong_advice_direction", [i["rule"] for i in review["strategy_issues"]])

    def test_missing_chief_degrades(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_meta(base, {d: True for d in WEEK_DAYS})
            review = wr.build_weekly_review(base, "2026-07-15")
            rows = review["advice_review"]["rows"]
            self.assertEqual(len(rows), 5)
            self.assertTrue(all(r["verdict"] == "unavailable" for r in rows))
            self.assertTrue(any("每日建议检验" in u for u in review["unavailable"]))

    def test_classify_advice(self):
        self.assertEqual(wr.classify_advice("防守", "禁止"), "bearish")
        self.assertEqual(wr.classify_advice("震荡偏弱", "仅观察"), "bearish")
        self.assertEqual(wr.classify_advice("进攻", "允许"), "bullish")
        self.assertEqual(wr.classify_advice("震荡", "仅观察"), "neutral")


if __name__ == "__main__":
    unittest.main()
