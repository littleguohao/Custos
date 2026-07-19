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


if __name__ == "__main__":
    unittest.main()
