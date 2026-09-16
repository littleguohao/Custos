# -*- coding: utf-8 -*-
"""b1_perfect_dataset 钉测（R36 Phase 0 数据接入）。

真目录（/home/gh/agent/ZGNB/B1_DATA，本机有）做接入对拍；坏文件/缺列/
空目录的 fail-closed 用 tmp_path 合成。铁律：这 10 个点是发现级材料（L1），
任何断言只钉**数据完好性与口径**，不钉「它们有什么特征」（那是发现段的事）。
"""

import pandas as pd
import pytest

from custos.research.b1_perfect_dataset import (
    DEFAULT_DATA_DIR,
    PerfectB1Case,
    audit_cases,
    load_cases,
)

CASES = load_cases()  # 模块级一次加载（10 个 CSV 是静态材料，重复读无意义）
AUDIT = audit_cases(CASES)


class TestLoadCases:
    def test_all_10_loaded(self):
        assert len(CASES) == 10
        codes = sorted(c.code for c in CASES)
        assert codes == [
            "002074",
            "002812",
            "002940",
            "300689",
            "301076",
            "600184",
            "600366",
            "600601",
            "605378",
            "688321",
        ]

    def test_case_shape_and_columns(self):
        for c in CASES:
            assert (
                isinstance(c, PerfectB1Case) and c.code.isdigit() and len(c.code) == 6
            )
            assert c.code_full.endswith((".SZ", ".SH")) and c.code_full.startswith(
                c.code
            )
            assert list(c.bars.columns) == [
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
            ]
            assert c.n_bars == len(c.bars) and 60 <= c.n_bars <= 80  # ~3 个月日线
            assert pd.api.types.is_datetime64_any_dtype(c.bars["date"])

    def test_buy_date_is_last_bar(self):
        for c in CASES:
            assert c.buy_date == str(c.bars["date"].iloc[-1].date())
            assert c.buy_date == c.end  # 文件名窗口末根与数据末根一致（装载护栏已钉）

    def test_bars_sorted_and_numeric(self):
        for c in CASES:
            assert c.bars["date"].is_monotonic_increasing
            for col in ("open", "high", "low", "close", "volume", "amount"):
                assert pd.api.types.is_numeric_dtype(c.bars[col])

    def test_bars_compatible_with_expr_dsl(self):
        """列名对齐 evolution 包约定：bars 可直接喂 expr_dsl.evaluate / ic_eval。"""
        from custos.research.evolution import expr_dsl

        df = CASES[0].bars
        s = expr_dsl.evaluate("close/MA(close,20)", df)  # 不 raise = 列约定兼容
        assert len(s) == len(df)


class TestAudit:
    def test_report_shape(self):
        assert AUDIT["n_cases"] == 10
        assert AUDIT["all_gain_positive"] is True
        assert AUDIT["min_gain_pct"] > 10  # 涨幅均为正且显著（最小 11.2%）
        assert AUDIT["max_gain_pct"] > 200  # 002940 +207.9%
        assert "单一近期 regime" in AUDIT["regime_note"]
        assert "事后标注" in AUDIT["label_note"]  # 后视标注声明必须在报告里

    def test_per_case_fields(self):
        by_code = {r["code"]: r for r in AUDIT["per_case"]}
        assert by_code["002940"]["gain_pct"] == pytest.approx(207.9, abs=0.2)
        assert by_code["688321"]["gain_pct"] == pytest.approx(11.2, abs=0.2)
        # 买点当日多为缩量小阴（B1 回调末端形态的实据）：7 只当日收跌、0 只涨停附近
        assert AUDIT["buy_day_negative_count"] == 7
        assert AUDIT["buy_near_limit_count"] == 0
        for r in AUDIT["per_case"]:
            assert r["buy_vol_ratio5"] > 0
            assert r["price_limit"] in (5, 10, 20, 30)

    def test_empty_cases_rejected(self):
        with pytest.raises(ValueError, match="0 案例"):
            audit_cases([])


class TestFailClosed:
    def _write(self, tmp_path, name, text):
        p = tmp_path / name
        p.write_text(text, encoding="utf-8-sig")
        return p

    def test_missing_dir_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="案例目录不存在"):
            load_cases(tmp_path / "nope")

    def test_empty_dir_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="0 个 CSV"):
            load_cases(tmp_path)

    def test_bad_filename_rejected(self, tmp_path):
        self._write(tmp_path, "random.csv", "Date,Close\n2025-01-01,1\n")
        with pytest.raises(ValueError, match="文件名形态非法"):
            load_cases(tmp_path)

    def test_missing_column_rejected(self, tmp_path):
        self._write(
            tmp_path,
            "600000.SH-20250101-20250102.csv",
            "Date,Code,Close\n2025-01-01,600000.SH,1\n2025-01-02,600000.SH,2\n",
        )
        with pytest.raises(ValueError, match="缺列"):
            load_cases(tmp_path)

    def test_code_mismatch_rejected(self, tmp_path):
        self._write(
            tmp_path,
            "600000.SH-20250101-20250102.csv",
            "Date,Code,Amount,Close,ForwardFactor,High,Low,Volume,Open\n"
            "2025-01-01,600001.SH,1,1,0,1,1,1,1\n"
            "2025-01-02,600001.SH,1,2,0,2,1,1,1\n",
        )
        with pytest.raises(ValueError, match="不一致"):
            load_cases(tmp_path)

    def test_end_date_mismatch_rejected(self, tmp_path):
        self._write(
            tmp_path,
            "600000.SH-20250101-20250103.csv",
            "Date,Code,Amount,Close,ForwardFactor,High,Low,Volume,Open\n"
            "2025-01-01,600000.SH,1,1,0,1,1,1,1\n"
            "2025-01-02,600000.SH,1,2,0,2,1,1,1\n",
        )
        with pytest.raises(ValueError, match="末根交易日"):
            load_cases(tmp_path)

    def test_duplicate_date_rejected(self, tmp_path):
        self._write(
            tmp_path,
            "600000.SH-20250101-20250102.csv",
            "Date,Code,Amount,Close,ForwardFactor,High,Low,Volume,Open\n"
            "2025-01-01,600000.SH,1,1,0,1,1,1,1\n"
            "2025-01-01,600000.SH,1,2,0,2,1,1,1\n"
            "2025-01-02,600000.SH,1,3,0,3,1,1,1\n",
        )
        with pytest.raises(ValueError, match="重复交易日"):
            load_cases(tmp_path)
