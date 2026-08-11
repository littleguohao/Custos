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


class TestXlsxInputPath:
    """`.xlsx` 输入分支 —— **日常导入路径**（券商导出就是 xlsx）。

    ⚠️ 2026-08-07 覆盖率清点查出：`openpyxl` **根本不在项目依赖里**，
    而四处代码需要它（本模块的 `.xlsx` 分支、`standardize_trades` 的三处
    `read_excel`、`holding_sector_mapper`、`analyze_trades` 写 xlsx）。
    也就是说**给一份 xlsx 就会 ImportError** —— 而这正是券商导出的格式。

    没人发现是因为这些分支覆盖率是 0%（`standardize_trades`）或只测过 csv/json 分支。
    ⇒ 本测试同时是**依赖守卫**：openpyxl 若再被移除，这里会立刻红。
    """

    COLS = ["成交日期", "成交时间", "代码", "名称", "交易类别",
            "成交数量", "成交价格", "成交金额", "发生金额", "费用", "备注"]

    def _xlsx(self, tmp_path, rows):
        import pandas as pd
        p = tmp_path / "broker.xlsx"
        pd.DataFrame(rows, columns=self.COLS).to_excel(p, index=False)
        return p

    def test_reads_xlsx(self, tmp_path):
        import incremental_ledger as il
        p = self._xlsx(tmp_path, [
            ["2026-08-03", "09:31:00", "600000", "浦发", "买入", 1000, 10.0, 10000, -10005, 5.0, ""]])
        df = il.read_input(p)
        assert len(df) == 1 and str(df["代码"].iloc[0]) == "600000"

    def test_xls_suffix_also_accepted(self, tmp_path):
        """`.xls` 也在白名单里；至少不能因为后缀被拒（老券商导出仍有 .xls）。"""
        import incremental_ledger as il
        import inspect
        src = inspect.getsource(il.read_input)
        assert "'.xls'" in src or '".xls"' in src

    def test_unknown_suffix_rejected_loudly(self, tmp_path):
        """未知后缀必须**明确报错**，不能静默返回空表 ——
        空表会让 select_new_rows 选出 0 行、审计写 appended_rows=0，
        看起来像「本来就没有新成交」。"""
        import incremental_ledger as il
        p = tmp_path / "x.txt"
        p.write_text("noop", encoding="utf-8")
        with pytest.raises(ValueError):
            il.read_input(p)


class TestConfirmNoTradesNeedsNoInputFile:
    """⚠️ `--confirm-no-trades` 不得再要求 `--input`。

    此前 `--input` 是 `required=True`，而该模式又**要求输入为空**
    （`if a.confirm_no_trades and len(incoming): raise`）⇒ 操作者必须造一个
    只含 `{}` 的文件纯粹为了满足参数。那些文件留在 CWD 里被目标机的自动提交
    扫进仓库：`07_tools/trades/_no_trades_2026080{5,6,7}.json`（2026-08-10 清理）。

    **是 CLI 设计逼出来的垃圾，不是操作者不小心** —— 所以修的是 CLI。
    """

    def test_confirm_without_input_writes_confirmation(self, tmp_path, monkeypatch):
        import json

        import incremental_ledger as il

        # ⚠️ patch **全部** Path 常量，不能只挑想到的那几个 ——
        #    第一版漏了 `AUDIT`，测试把审计记录写进了真实
        #    `data/trades/ledger_append_audit.jsonl`（被 repo-hygiene 守卫抓到）。
        #    同一教训在 2026-08-07 给 runner 写 harness 时踩过：漏了 `REVIEWS`。
        import pathlib as _pl
        for attr in dir(il):
            v = getattr(il, attr, None)
            if not (attr.isupper() and isinstance(v, _pl.Path)):
                continue
            # ⚠️ 保留**原文件名**（含后缀）—— 用属性名当文件名会把
            #    `CONFIRM` 变成无后缀的 `tmp/confirm`，按 `*.json` 找不到产物。
            monkeypatch.setattr(il, attr,
                                tmp_path if v.suffix == "" else tmp_path / v.name,
                                raising=False)

        # ⚠️ `main()` 返回**审计记录 dict**，不是退出码（第一版断言 `rc in (0, None)` 就挂了）
        rec = il.main(["--confirm-no-trades", "--date", "2026-08-10"])
        assert isinstance(rec, dict) and rec.get("no_trades_confirmed") is True, rec
        assert rec["source"] == "(无输入文件：--confirm-no-trades)", \
            f"src 为 None 时不得落成字符串 \"None\"，实际 {rec['source']!r}"
        # 确认文件落盘（路径常量名可能不同，扫 tmp_path 下的 json）
        found = [p for p in tmp_path.rglob("*.json")]
        assert found, "应写出无交易确认"
        data = json.loads(found[0].read_text(encoding="utf-8"))
        assert "2026-08-10" in data
        assert data["2026-08-10"].get("no_trades") is True

    def test_missing_input_without_confirm_still_errors(self, tmp_path, monkeypatch):
        """普通导入模式缺 `--input` 必须报错 —— 别把校验一起放松了。"""
        import pytest

        import incremental_ledger as il

        monkeypatch.setattr(il, "LEDGER", tmp_path / "l.csv", raising=False)
        with pytest.raises(SystemExit):
            il.main([])

    def test_read_input_none_is_empty_frame(self):
        import incremental_ledger as il

        assert len(il.read_input(None)) == 0
