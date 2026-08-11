# -*- coding: utf-8 -*-
"""P3 审计回归：复盘/台账正确性（钱的路径）。

覆盖：
- D3 FIFO 部分/零配平必须显式 unavailable，不得静默少算已实现盈亏、不得计入胜率
- D4 无交易确认统一读 position_confirmations.json（唯一有生产者的数据源）
- D5 乱码兜底字段映射列数不匹配必须 fail-loud
- D6 年度「跑赢大盘比例」不得恒 NaN
- D7 指数涨跌缺失不得渲染成 +0.00%
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from custos.research import analyze_trades as at
from custos.pipeline import run_1700
from custos.pipeline.close_review import final_close_review as fcr
from custos.pipeline.close_review import weekly_review as wr

WEEK_DAYS = ["2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17"]
CALENDAR = {"official_years": {"2026": {"closed_ranges": []}}}


# ---------------------------------------------------------------- fixtures

def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def make_base(tmp: Path) -> Path:
    write_json(tmp / "governance" / "contracts" / "CN_TRADING_CALENDAR.json", CALENDAR)
    return tmp


def write_ledger(base: Path, rows: list[list]) -> None:
    lines = ["成交日期,成交时间,代码,名称,交易类别,成交数量,成交价格,成交金额,发生金额,费用,备注"]
    lines += [",".join(str(x) for x in r) for r in rows]
    path = base / "data" / "trades" / "master_trade_ledger.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\ufeff" + "\n".join(lines), encoding="utf-8")


def write_confirmations(base: Path, records: dict) -> None:
    write_json(base / "data" / "trades" / "position_confirmations.json", records)


def no_trade_records(days: list[str]) -> dict:
    return {d: {"confirmed_at": f"{d}T17:30:00", "no_trades": True, "note": "用户确认无交易"}
            for d in days}


def trade(day: str, code: str, side: str, qty: float, price: float, name: str = "测试") -> list:
    amount = qty * price
    return [day, "10:00:00", code, name, side, qty, price, amount, amount, 0.0, ""]


# ================================================================ D3 FIFO

class TestFifoMatchStatus:
    def test_full_match_is_labelled_and_valued(self):
        trades = [
            {"date": "2026-07-06", "time": "09:30", "code": "600000", "name": "A",
             "side": "买入", "qty": 100.0, "price": 10.0, "amount": 1000.0, "fee": 0.0},
            {"date": "2026-07-14", "time": "14:00", "code": "600000", "name": "A",
             "side": "卖出", "qty": 100.0, "price": 12.0, "amount": 1200.0, "fee": 0.0},
        ]
        c = wr.fifo_pair(trades)[0]
        assert c["match_status"] == "full"
        assert c["unmatched_qty"] == 0.0
        assert c["gross_pnl"] == 200.0

    def test_partial_match_is_labelled_partial(self):
        """买 100 卖 150：配平 100 股，50 股无买入来源 → partial，盈亏不可信。"""
        trades = [
            {"date": "2026-07-06", "time": "09:30", "code": "600000", "name": "A",
             "side": "买入", "qty": 100.0, "price": 10.0, "amount": 1000.0, "fee": 0.0},
            {"date": "2026-07-14", "time": "14:00", "code": "600000", "name": "A",
             "side": "卖出", "qty": 150.0, "price": 12.0, "amount": 1800.0, "fee": 0.0},
        ]
        c = wr.fifo_pair(trades)[0]
        assert c["match_status"] == "partial"
        assert c["unmatched_qty"] == 50.0
        assert c["matched_qty"] == 100.0

    def test_zero_match_is_labelled_none(self):
        trades = [
            {"date": "2026-07-14", "time": "14:00", "code": "600000", "name": "A",
             "side": "卖出", "qty": 100.0, "price": 12.0, "amount": 1200.0, "fee": 0.0},
        ]
        c = wr.fifo_pair(trades)[0]
        assert c["match_status"] == "none"
        assert c["matched_qty"] == 0.0
        assert c["gross_pnl"] is None


class TestUnmatchedExcludedFromStats:
    def _review(self, base: Path):
        write_confirmations(base, no_trade_records(WEEK_DAYS))
        return wr.build_weekly_review(base, "2026-07-15")

    def test_partial_pnl_excluded_from_gross_and_win_rate(self):
        """一单完整配平亏 -100，一单部分配平（表面赚 +200）。

        修前：gross_pnl = 100、胜率 50%（用少算的部分配平盈亏充当胜局）。
        修后：gross_pnl = -100、胜率 0%，部分配平单单独报告。
        """
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_ledger(base, [
                # 完整配平：买 100@10 → 卖 100@9，亏 -100
                trade("2026-07-06", "600000", "买入", 100, 10.0),
                trade("2026-07-14", "600000", "卖出", 100, 9.0),
                # 部分配平：只买 100@10 却卖 150@12（券商台账缺早期买入）
                trade("2026-07-06", "600001", "买入", 100, 10.0),
                trade("2026-07-14", "600001", "卖出", 150, 12.0),
            ])
            review = self._review(base)
            f = review["facts"]
            assert f["gross_pnl"] == -100.0
            assert f["win_rate_pct"] == 0.0
            assert f["closing_count"] == 2
            assert f["valued_closing_count"] == 1
            assert f["partial_match_count"] == 1

    def test_partial_match_flagged_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_ledger(base, [
                trade("2026-07-06", "600001", "买入", 100, 10.0),
                trade("2026-07-14", "600001", "卖出", 150, 12.0),
            ])
            review = self._review(base)
            assert any("部分配平" in u for u in review["unavailable"])
            rows = review["facts"]["unmatched_closings"]
            assert [(r["code"], r["match_status"], r["unmatched_qty"]) for r in rows] \
                == [("600001", "partial", 50.0)]

    def test_zero_match_flagged_unavailable_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_ledger(base, [trade("2026-07-14", "600002", "卖出", 100, 12.0)])
            review = self._review(base)
            assert any("未配平" in u for u in review["unavailable"])
            rows = review["facts"]["unmatched_closings"]
            assert [(r["code"], r["match_status"]) for r in rows] == [("600002", "none")]
            assert review["facts"]["unmatched_closing_count"] == 1

    def test_partial_loss_not_used_for_slow_stop_attribution(self):
        """部分配平算出的 -10% 不得触发 slow_stop_loss 归因。"""
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_ledger(base, [
                trade("2026-07-06", "600001", "买入", 100, 10.0),
                trade("2026-07-14", "600001", "卖出", 150, 9.0),
            ])
            review = self._review(base)
            assert "slow_stop_loss" not in [i["rule"] for i in review["execution_issues"]]

    def test_full_match_only_keeps_stats_intact(self):
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_ledger(base, [
                trade("2026-07-06", "600000", "买入", 100, 10.0),
                trade("2026-07-14", "600000", "卖出", 100, 12.0),
            ])
            review = self._review(base)
            f = review["facts"]
            assert f["gross_pnl"] == 200.0
            assert f["win_rate_pct"] == 100.0
            assert f["unmatched_closing_count"] == 0
            assert not any("配平" in u for u in review["unavailable"])

    def test_markdown_reports_unmatched_section(self):
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_ledger(base, [
                trade("2026-07-06", "600001", "买入", 100, 10.0),
                trade("2026-07-14", "600001", "卖出", 150, 12.0),
            ])
            md = wr.render_markdown(self._review(base))
            assert "配平异常" in md
            assert "600001" in md


# ================================================================ D4 无交易确认

class TestNoTradeConfirmationSource:
    def test_position_confirmations_satisfies_completeness(self):
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_confirmations(base, no_trade_records(WEEK_DAYS))
            review = wr.build_weekly_review(base, "2026-07-15")
            assert "no_trade_confirmation_missing" not in \
                [i["rule"] for i in review["execution_issues"]]
            assert review["facts"]["no_trade_unconfirmed"] == []

    def test_missing_confirmation_still_reported(self):
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_confirmations(base, no_trade_records(["2026-07-13"]))
            review = wr.build_weekly_review(base, "2026-07-15")
            issues = [i for i in review["execution_issues"]
                      if i["rule"] == "no_trade_confirmation_missing"]
            assert len(issues) == 1
            assert review["facts"]["no_trade_unconfirmed"] == WEEK_DAYS[1:]

    def test_snapshot_only_confirmation_is_not_a_no_trade_confirmation(self):
        """runtime_guards.confirm_position_snapshot 写的条目没有 no_trades 键，不算无交易确认。"""
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            records = no_trade_records(WEEK_DAYS)
            records["2026-07-15"] = {"confirmed_at": "2026-07-15T17:00:00", "note": "user_confirmed"}
            write_confirmations(base, records)
            review = wr.build_weekly_review(base, "2026-07-15")
            assert review["facts"]["no_trade_unconfirmed"] == ["2026-07-15"]

    def test_dead_import_meta_contract_no_longer_consulted(self):
        """_import_meta.json 的 no_trades_confirmed_dates 没有生产者，不得再被采信。"""
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_json(base / "data" / "trades" / "_import_meta.json",
                       {"no_trades_confirmed_dates": {d: True for d in WEEK_DAYS}})
            review = wr.build_weekly_review(base, "2026-07-15")
            assert review["facts"]["no_trade_unconfirmed"] == WEEK_DAYS

    def test_absent_confirmation_file_marks_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            review = wr.build_weekly_review(base, "2026-07-15")
            assert any("position_confirmations.json" in u for u in review["unavailable"])

    def test_traded_day_needs_no_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            base = make_base(Path(td))
            write_ledger(base, [trade("2026-07-14", "600000", "买入", 100, 10.0)])
            write_confirmations(base, no_trade_records(
                [d for d in WEEK_DAYS if d != "2026-07-14"]))
            review = wr.build_weekly_review(base, "2026-07-15")
            assert review["facts"]["no_trade_days"] == \
                [d for d in WEEK_DAYS if d != "2026-07-14"]
            assert review["facts"]["no_trade_unconfirmed"] == []


class TestRun1700NoTradesFlag:
    def test_flag_from_position_confirmations(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_1700, "BASE", tmp_path)
        monkeypatch.setattr(run_1700, "TRADES_DIR", tmp_path / "data" / "trades")
        write_confirmations(tmp_path, no_trade_records(["2026-07-14"]))
        assert run_1700._no_trades_flag("2026-07-14") == ["--no-trades-confirmed"]

    def test_no_flag_when_not_confirmed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_1700, "BASE", tmp_path)
        monkeypatch.setattr(run_1700, "TRADES_DIR", tmp_path / "data" / "trades")
        write_confirmations(tmp_path, no_trade_records(["2026-07-13"]))
        assert run_1700._no_trades_flag("2026-07-14") == []

    def test_no_flag_when_file_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_1700, "BASE", tmp_path)
        monkeypatch.setattr(run_1700, "TRADES_DIR", tmp_path / "data" / "trades")
        assert run_1700._no_trades_flag("2026-07-14") == []

    def test_snapshot_only_entry_does_not_set_flag(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_1700, "BASE", tmp_path)
        monkeypatch.setattr(run_1700, "TRADES_DIR", tmp_path / "data" / "trades")
        write_confirmations(tmp_path, {"2026-07-14": {"confirmed_at": "x", "note": "y"}})
        assert run_1700._no_trades_flag("2026-07-14") == []


# ================================================================ D5 字段映射

class TestMapFields:
    def test_clean_json_passthrough(self):
        raw = [{"清仓日期": "2026-01-02", "代码": "600000", "总盈亏": 100.0}]
        df = at._map_fields(raw, at.CLOSED_FIELDS)
        assert list(df.columns) == ["清仓日期", "代码", "总盈亏"]
        assert df["总盈亏"].iloc[0] == 100.0

    def test_garbled_exact_count_maps_positionally(self):
        raw = [dict(zip([f"k{i}" for i in range(len(at.CLOSED_FIELDS))],
                        range(len(at.CLOSED_FIELDS))))]
        df = at._map_fields(raw, at.CLOSED_FIELDS)
        assert list(df.columns) == at.CLOSED_FIELDS

    def test_garbled_extra_column_fails_loud(self):
        """券商多导一列时，按位置 zip 会让「总盈亏」读到相邻列 → 必须报错而非静默错算。"""
        keys = [f"k{i}" for i in range(len(at.CLOSED_FIELDS) + 1)]
        raw = [dict(zip(keys, range(len(keys))))]
        with pytest.raises(ValueError) as e:
            at._map_fields(raw, at.CLOSED_FIELDS)
        assert "列数" in str(e.value)

    def test_garbled_missing_column_fails_loud(self):
        keys = [f"k{i}" for i in range(len(at.CLOSED_FIELDS) - 1)]
        raw = [dict(zip(keys, range(len(keys))))]
        with pytest.raises(ValueError):
            at._map_fields(raw, at.CLOSED_FIELDS)

    def test_load_closed_propagates_failure(self, tmp_path, monkeypatch):
        keys = [f"k{i}" for i in range(len(at.CLOSED_FIELDS) + 1)]
        path = tmp_path / "closed_positions.json"
        write_json(path, [dict(zip(keys, range(len(keys))))])
        monkeypatch.setattr(at, "CLOSED_JSON", path)
        with pytest.raises(ValueError):
            at.load_closed()

    def test_load_positions_propagates_failure(self, tmp_path, monkeypatch):
        keys = [f"k{i}" for i in range(len(at.POS_FIELDS) + 1)]
        path = tmp_path / "current_positions.json"
        write_json(path, [dict(zip(keys, range(len(keys))))])
        monkeypatch.setattr(at, "POSITIONS_JSON", path)
        with pytest.raises(ValueError):
            at.load_positions()

    def test_load_closed_happy_path(self, tmp_path, monkeypatch):
        path = tmp_path / "closed_positions.json"
        write_json(path, [{"清仓日期": "2026-01-02", "代码": "600000", "总盈亏": 100.0}])
        monkeypatch.setattr(at, "CLOSED_JSON", path)
        assert at.load_closed()["总盈亏"].iloc[0] == 100.0


# ================================================================ D6 跑赢大盘

class TestYearlyBeatMarket:
    def _closed(self) -> pd.DataFrame:
        return pd.DataFrame({
            "清仓日期": pd.to_datetime(["2026-01-05", "2026-02-05", "2026-03-05"]),
            "代码": ["600000", "600001", "600002"],
            "总盈亏": [100.0, -50.0, 30.0],
            "盈亏比": [1.0, -0.5, 0.3],
            "持仓天数": [10, 20, 30],
            "跑赢大盘": [5.0, -3.0, 2.0],
        })

    def test_beat_market_ratio_is_numeric_not_nan(self):
        out = at.build_yearly(self._closed())
        assert "跑赢大盘比例" in out.columns
        assert out["跑赢大盘比例"].iloc[0] == pytest.approx(2 / 3)

    def test_all_beaten_ratio_is_one(self):
        df = self._closed()
        df["跑赢大盘"] = [1.0, 2.0, 3.0]
        assert at.build_yearly(df)["跑赢大盘比例"].iloc[0] == pytest.approx(1.0)

    def test_column_omitted_when_absent(self):
        df = self._closed().drop(columns=["跑赢大盘"])
        out = at.build_yearly(df)
        assert "跑赢大盘比例" not in out.columns
        assert out["总盈亏"].iloc[0] == pytest.approx(80.0)


# ================================================================ D7 指数涨跌缺失

class TestIndexChangeRendering:
    def test_missing_change_renders_unavailable(self):
        assert fcr.pct_text(None) == "unavailable"

    def test_nan_change_renders_unavailable(self):
        assert fcr.pct_text(float("nan")) == "unavailable"

    def test_value_renders_signed_percent(self):
        assert fcr.pct_text(1.234) == "+1.23%"
        assert fcr.pct_text(-0.5) == "-0.50%"
        assert fcr.pct_text(0.0) == "+0.00%"

    def test_index_row_missing_change_is_not_flat(self):
        row = {"name": "上证指数", "close": 3200.0, "change_pct": None,
               "above_ma25": True, "above_ma60": True, "above_ma144": False, "above_ma240": False}
        line = fcr.render_index_row(row)
        assert "unavailable" in line
        assert "+0.00%" not in line

    def test_index_row_with_change(self):
        row = {"name": "上证指数", "close": 3200.0, "change_pct": -1.5,
               "above_ma25": True, "above_ma60": True, "above_ma144": False, "above_ma240": False}
        line = fcr.render_index_row(row)
        assert "-1.50%" in line
        assert "上MA25" in line and "下MA144" in line
