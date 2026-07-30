# -*- coding: utf-8 -*-
"""trades/incremental_ledger 首批测试——台账是钱的路径，此前 0% 覆盖。

覆盖:①norm 归一与非法输入拒绝;②fingerprint 稳定性/敏感性;
③apply_positions 的买入加权成本(含费)、卖出减仓、清零删除、超卖拒绝。
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from trades import incremental_ledger as il


def _df(rows):
    return pd.DataFrame(rows)


class TestNorm:
    def test_fills_missing_columns_and_normalizes(self):
        out = il.norm(_df([{"成交日期": "2026/07/20", "代码": "000001.SZ", "名称": "平安",
                            "交易类别": "买入", "成交数量": "100", "成交价格": "10.5",
                            "成交时间": "093000.0"}]))
        assert list(out.columns) == il.FIELDS
        assert out.loc[0, "成交日期"] == "2026-07-20"
        assert out.loc[0, "代码"] == "000001"
        assert out.loc[0, "成交时间"] == "093000"
        assert out.loc[0, "成交数量"] == 100 and out.loc[0, "成交价格"] == 10.5

    def test_invalid_date_rejected(self):
        with pytest.raises(ValueError):
            il.norm(_df([{"成交日期": "not-a-date", "代码": "000001"}]))

    def test_empty_code_rejected(self):
        with pytest.raises(ValueError):
            il.norm(_df([{"成交日期": "2026-07-20", "代码": ""}]))


class TestFingerprint:
    def _row(self, **kw):
        base = {k: "" for k in il.KEY}
        base.update({"成交日期": "2026-07-20", "代码": "000001", "交易类别": "买入",
                     "成交数量": 100, "成交价格": 10.5})
        base.update(kw)
        return base

    def test_stable_and_short(self):
        fp = il.fingerprint(self._row())
        assert fp == il.fingerprint(self._row()) and len(fp) == 20

    def test_sensitive_to_every_key_field(self):
        base = il.fingerprint(self._row())
        assert il.fingerprint(self._row(成交数量=200)) != base
        assert il.fingerprint(self._row(成交价格=10.6)) != base
        assert il.fingerprint(self._row(交易类别="卖出")) != base

    def test_nan_treated_as_empty(self):
        assert il.fingerprint(self._row(费用=float("nan"))) == il.fingerprint(self._row(费用=""))


class TestApplyPositions:
    @pytest.fixture(autouse=True)
    def _tmp_pos(self, tmp_path, monkeypatch):
        self.pos = tmp_path / "current_positions.json"
        monkeypatch.setattr(il, "POS", self.pos)

    def _read(self):
        return {x["代码"]: x for x in json.loads(self.pos.read_text(encoding="utf-8"))}

    def _trades(self, rows):
        return il.norm(_df(rows))

    def test_buy_creates_position_with_fee_in_cost(self):
        il.apply_positions(self._trades([{"成交日期": "2026-07-20", "代码": "000001", "名称": "平安",
                                          "交易类别": "买入", "成交数量": 100, "成交价格": 10.0,
                                          "费用": 5.0}]))
        p = self._read()["000001"]
        assert p["持有数量"] == 100
        assert p["单位成本"] == pytest.approx((100 * 10.0 + 5.0) / 100)   # 费用摊入成本
        assert p["snapshot_status"] == "pending_close_revaluation"

    def test_second_buy_averages_cost(self):
        rows = [{"成交日期": "2026-07-20", "代码": "000001", "名称": "平安", "交易类别": "买入",
                 "成交数量": 100, "成交价格": 10.0, "费用": 0.0},
                {"成交日期": "2026-07-20", "代码": "000001", "名称": "平安", "交易类别": "买入",
                 "成交数量": 100, "成交价格": 12.0, "费用": 0.0}]
        il.apply_positions(self._trades(rows))
        p = self._read()["000001"]
        assert p["持有数量"] == 200 and p["单位成本"] == pytest.approx(11.0)

    def test_sell_reduces_quantity_and_keeps_cost(self):
        self.pos.write_text(json.dumps([{"代码": "000001", "名称": "平安", "持有数量": 200.0,
                                         "单位成本": 11.0}]), encoding="utf-8")
        il.apply_positions(self._trades([{"成交日期": "2026-07-20", "代码": "000001", "名称": "平安",
                                          "交易类别": "卖出", "成交数量": 50, "成交价格": 13.0,
                                          "费用": 1.0}]))
        p = self._read()["000001"]
        assert p["持有数量"] == 150 and p["单位成本"] == 11.0

    def test_full_sell_removes_position(self):
        self.pos.write_text(json.dumps([{"代码": "000001", "名称": "平安", "持有数量": 100.0,
                                         "单位成本": 11.0}]), encoding="utf-8")
        il.apply_positions(self._trades([{"成交日期": "2026-07-20", "代码": "000001", "名称": "平安",
                                          "交易类别": "卖出", "成交数量": 100, "成交价格": 13.0}]))
        assert self._read() == {}

    def test_oversell_is_rejected(self):
        """卖出超过台账持仓必须报错,不能写出负持仓。"""
        self.pos.write_text(json.dumps([{"代码": "000001", "名称": "平安", "持有数量": 100.0,
                                         "单位成本": 11.0}]), encoding="utf-8")
        with pytest.raises(ValueError, match="超过台账持仓"):
            il.apply_positions(self._trades([{"成交日期": "2026-07-20", "代码": "000001",
                                              "名称": "平安", "交易类别": "卖出",
                                              "成交数量": 500, "成交价格": 13.0}]))

    def test_sell_without_position_is_rejected(self):
        self.pos.write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError):
            il.apply_positions(self._trades([{"成交日期": "2026-07-20", "代码": "600000",
                                              "名称": "浦发", "交易类别": "卖出",
                                              "成交数量": 100, "成交价格": 8.0}]))

    def test_non_trade_categories_ignored(self):
        self.pos.write_text("[]", encoding="utf-8")
        il.apply_positions(self._trades([{"成交日期": "2026-07-20", "代码": "000001", "名称": "平安",
                                          "交易类别": "银行转证券", "成交数量": 0,
                                          "成交价格": 0}]))
        assert self._read() == {}
