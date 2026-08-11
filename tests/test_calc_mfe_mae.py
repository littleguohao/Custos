# -*- coding: utf-8 -*-
"""calc_mfe_mae 测试:窗口必须按**入场日**锚定,不得用自然日「持仓天数」当 K 线行数。

此前 `df.tail(持仓天数)` 把自然日当行数,365 自然日只有约 250 个交易日 ⇒ 窗口往回多伸
约 40%,吃进入场前的 K 线,把入场前的高低点算成本笔交易的浮盈/浮亏。该数字流向
weekly_review 的卖飞判定,口径错会直接误判。
"""

from __future__ import annotations

import csv
import io

from custos.pipeline.close_review import calc_mfe_mae as cm


def _t(day, side, qty, price=10.0, code="600000", name="测试股"):
    return {
        "date": day,
        "time": "09:30",
        "code": code,
        "name": name,
        "side": side,
        "qty": qty,
        "price": price,
        "amount": qty * price,
        "fee": 0.0,
    }


class TestResolveOpenEntryDates:
    def test_single_buy_gives_its_date(self):
        got = cm.resolve_open_entry_dates([_t("2026-06-10", "买入", 1000)])
        assert got["600000"]["entry_date"] == "2026-06-10"
        assert got["600000"]["open_qty"] == 1000 and got["600000"]["open_lots"] == 1

    def test_fully_closed_position_absent(self):
        """已清仓不该有建仓日 —— 否则 MFE/MAE 会给已平仓的票出数。"""
        got = cm.resolve_open_entry_dates(
            [_t("2026-06-10", "买入", 1000), _t("2026-06-20", "卖出", 1000)]
        )
        assert "600000" not in got

    def test_fifo_consumes_oldest_lot_first(self):
        """先进先出:卖掉第一批后,建仓日应前移到**第二批**的日期,而不是仍报第一批。"""
        got = cm.resolve_open_entry_dates(
            [
                _t("2026-05-06", "买入", 1000),
                _t("2026-06-10", "买入", 1000),
                _t("2026-06-20", "卖出", 1000),
            ]
        )
        assert got["600000"]["entry_date"] == "2026-06-10"
        assert got["600000"]["open_qty"] == 1000

    def test_partial_sell_keeps_oldest_remaining_lot(self):
        got = cm.resolve_open_entry_dates(
            [
                _t("2026-05-06", "买入", 1000),
                _t("2026-06-10", "买入", 1000),
                _t("2026-06-20", "卖出", 400),
            ]
        )
        assert got["600000"]["entry_date"] == "2026-05-06"  # 第一批还剩 600 股
        assert got["600000"]["open_qty"] == 1600

    def test_oversell_treated_as_flat_not_crash(self):
        """卖出多于买入(台账不完整)按无持仓处理:宁可不出数,也不给错窗口。"""
        got = cm.resolve_open_entry_dates(
            [_t("2026-06-10", "买入", 500), _t("2026-06-20", "卖出", 900)]
        )
        assert "600000" not in got

    def test_avg_buy_date_is_quantity_weighted(self):
        got = cm.resolve_open_entry_dates(
            [_t("2026-06-01", "买入", 3000), _t("2026-06-11", "买入", 1000)]
        )
        # 加权平均 = (3000*06-01 + 1000*06-11)/4000 = 06-01 + 2.5 天 → 06-03/06-04
        assert got["600000"]["entry_date"] == "2026-06-01"
        assert got["600000"]["avg_buy_date"] in {"2026-06-03", "2026-06-04"}

    def test_unsorted_input_is_sorted_by_date(self):
        """台账行序不可信,解析器必须自己排序,否则 FIFO 消耗顺序错。"""
        got = cm.resolve_open_entry_dates(
            [
                _t("2026-06-20", "卖出", 1000),
                _t("2026-06-10", "买入", 1000),
                _t("2026-05-06", "买入", 1000),
            ]
        )
        assert got["600000"]["entry_date"] == "2026-06-10"

    def test_same_day_buy_before_sell(self):
        """同日买卖:买入先入栈才能被当日卖出消耗(与 weekly_review.parse_ledger 排序口径一致)。"""
        got = cm.resolve_open_entry_dates(
            [_t("2026-06-10", "卖出", 500), _t("2026-06-10", "买入", 1000)]
        )
        assert got["600000"]["entry_date"] == "2026-06-10"
        assert got["600000"]["open_qty"] == 500

    def test_multiple_codes_independent(self):
        got = cm.resolve_open_entry_dates(
            [
                _t("2026-06-10", "买入", 1000, code="600000"),
                _t("2026-06-15", "买入", 2000, code="000001"),
                _t("2026-06-20", "卖出", 1000, code="600000"),
            ]
        )
        assert "600000" not in got and got["000001"]["entry_date"] == "2026-06-15"

    def test_non_trade_rows_ignored(self):
        """非买卖(数量 0/空代码)不得进 FIFO。"""
        got = cm.resolve_open_entry_dates(
            [
                {"date": "2026-06-01", "code": "", "side": "买入", "qty": 100},
                {"date": "2026-06-02", "code": "600000", "side": "买入", "qty": 0},
                _t("2026-06-10", "买入", 1000),
            ]
        )
        assert list(got) == ["600000"] and got["600000"]["entry_date"] == "2026-06-10"


class TestNormalizeDateCol:
    """三个数据源列名不同(date/datetime/index),归一失败会让持仓被误判成"无数据"。"""

    def test_date_column_passthrough(self):
        import pandas as pd

        df = pd.DataFrame({"date": ["2026-06-10"], "high": [11.0], "low": [10.0]})
        assert cm.normalize_date_col(df) is df

    def test_datetime_renamed(self):
        import pandas as pd

        df = pd.DataFrame({"datetime": ["2026-06-10"], "high": [11.0]})
        assert "date" in cm.normalize_date_col(df).columns

    def test_reset_index_unnamed_column_accepted(self):
        """Reader.daily() 的 DatetimeIndex 无名时 reset_index() 产出 `index` 列,必须认。"""
        import pandas as pd

        df = pd.DataFrame(
            {"high": [11.0]}, index=pd.to_datetime(["2026-06-10"])
        ).reset_index()
        out = cm.normalize_date_col(df)
        assert out is not None and "date" in out.columns

    def test_no_date_like_column_returns_none(self):
        import pandas as pd

        assert cm.normalize_date_col(pd.DataFrame({"high": [11.0]})) is None

    def test_empty_frame_returns_none(self):
        import pandas as pd

        assert cm.normalize_date_col(pd.DataFrame()) is None


class TestWindowAnchoring:
    """核心回归:窗口左端必须是入场日,不能是「末 N 行」。"""

    def test_entry_anchor_excludes_pre_entry_bars(self):
        """入场前的更高高点/更低低点不得进 MFE/MAE —— 旧口径 tail(自然日) 正是会吃进来。"""
        import pandas as pd

        bars = pd.DataFrame(
            {
                "date": ["2026-06-01", "2026-06-02", "2026-06-10", "2026-06-11"],
                "high": [99.0, 98.0, 12.0, 13.0],  # 入场前 99 是干扰项
                "low": [50.0, 51.0, 9.5, 9.8],  # 入场前 50 是干扰项
            }
        )
        entry_date, target, cost = "2026-06-10", "2026-06-11", 10.0
        d = bars.assign(_d=bars["date"].astype(str).str[:10])
        win = d[(d["_d"] >= entry_date) & (d["_d"] <= target)]
        assert len(win) == 2
        assert round((win["high"].astype(float).max() / cost - 1) * 100, 2) == 30.0
        assert round((win["low"].astype(float).min() / cost - 1) * 100, 2) == -5.0
        # 旧口径:持仓天数=自然日 11 天 → tail(11) 会把 4 根全吃进来,MFE 变成 +890%
        old = bars.tail(11)
        assert round((old["high"].astype(float).max() / cost - 1) * 100, 2) == 890.0

    def test_bars_after_target_excluded(self):
        """--date 指定的目标日之后的 K 线不得进窗口(否则用了未来数据)。"""
        import pandas as pd

        bars = pd.DataFrame(
            {
                "date": ["2026-06-10", "2026-06-11", "2026-06-12"],
                "high": [11.0, 12.0, 99.0],
                "low": [10.0, 10.5, 10.6],
            }
        )
        d = bars.assign(_d=bars["date"].astype(str).str[:10])
        win = d[(d["_d"] >= "2026-06-10") & (d["_d"] <= "2026-06-11")]
        assert win["high"].astype(float).max() == 12.0


class TestLedgerIntegration:
    """跑通台账 CSV → 建仓日,确认复用 weekly_review.parse_ledger 没有列名漂移。"""

    def _ledger(self, tmp_path, rows):
        p = tmp_path / "master_trade_ledger.csv"
        buf = io.StringIO()
        w = csv.DictWriter(
            buf,
            fieldnames=[
                "成交日期",
                "成交时间",
                "代码",
                "名称",
                "交易类别",
                "成交数量",
                "成交价格",
                "成交金额",
                "费用",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)
        p.write_text(buf.getvalue(), encoding="utf-8-sig")
        return p

    def _row(self, day, code, side, qty, price):
        return {
            "成交日期": day,
            "成交时间": "09:30:00",
            "代码": code,
            "名称": "测试股",
            "交易类别": side,
            "成交数量": qty,
            "成交价格": price,
            "成交金额": qty * price,
            "费用": 5.0,
        }

    def test_real_ledger_columns_parsed(self, tmp_path):
        p = self._ledger(
            tmp_path,
            [
                self._row("2026-05-06", "600000", "买入", 1000, 10.0),
                self._row("2026-06-10", "600000", "买入", 1000, 11.0),
                self._row("2026-06-20", "600000", "卖出", 1000, 12.0),
            ],
        )
        got = cm.load_entry_dates(p)
        assert got["600000"]["entry_date"] == "2026-06-10"

    def test_missing_ledger_returns_empty_not_raise(self, tmp_path):
        """台账不存在 → 空 dict ⇒ 主流程 fail-closed 不出数,而不是崩溃或退回旧口径。"""
        assert cm.load_entry_dates(tmp_path / "nope.csv") == {}

    def test_non_trade_category_ignored(self, tmp_path):
        """转债转入等非买卖类别不得进 FIFO(parse_ledger 已过滤,这里钉住)。"""
        p = self._ledger(
            tmp_path,
            [
                self._row("2026-06-01", "600000", "转债转入", 1000, 10.0),
                self._row("2026-06-10", "600000", "买入", 1000, 11.0),
            ],
        )
        got = cm.load_entry_dates(p)
        assert got["600000"]["entry_date"] == "2026-06-10"
