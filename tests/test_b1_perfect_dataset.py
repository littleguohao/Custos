# -*- coding: utf-8 -*-
"""b1_perfect_dataset 钉测（R36 Phase 0 数据接入）。

⚠️ **hermetic 纪律（2026-09-16）**：本文件**全部用合成 CSV 夹具**（tmp_path 自造
日线文件），不依赖本机绝对路径（/home/gh/agent/ZGNB/B1_DATA 只存在于 dev 机，
CI/生产机没有——模块级 load 真实目录会让收集期直接报错）。真实 10 点清单的
钉住在权威 JSON 上（TestCanonicalMarksJson），材料事实归 R36 文档。
铁律：正例点是发现级材料（L1），断言只钉**数据完好性与口径**。
"""

import json

import pandas as pd
import pytest

from custos.research.b1_perfect_dataset import (
    PerfectB1Case,
    audit_cases,
    load_cases,
    load_marks_json,
    resolve_bars,
)

REPO_MARKS_JSON = (
    __file__
    and __import__("pathlib").Path(__file__).resolve().parents[1]
    / "governance/research/R36_perfect_b1_marks.json"
)


def _write_case_csv(dir_, code_full, closes, vols=None, start="2025-01-06"):
    """合成案例 CSV：BOM + 大写列名 + {code}-{start}-{end}.csv 文件名口径。"""
    n = len(closes)
    vols = vols or [1000.0] * n
    dates = pd.date_range(start, periods=n, freq="B")
    lines = ["Date,Code,Amount,Close,ForwardFactor,High,Low,Volume,Open"]
    for d, c, v in zip(dates, closes, vols):
        lines.append(
            f"{d.date()},{code_full},{c * v:.2f},{c},0.0,{c * 1.01:.4f},"
            f"{c * 0.99:.4f},{v},{c * 0.998:.4f}"
        )
    end = dates[-1].strftime("%Y%m%d")
    p = dir_ / f"{code_full}-{start.replace('-', '')}-{end}.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return p


def _three_cases(tmp_path):
    """三个合成案例：600000.SH 上升 10→13.9（买点 +0.72%、量比 0.5）、
    000001.SZ 平走后小阳、300001.SZ 下行且买点收跌。"""
    _write_case_csv(
        tmp_path,
        "600000.SH",
        [10.0 + 0.1 * i for i in range(40)],
        vols=[1000.0] * 39 + [500.0],
    )
    _write_case_csv(tmp_path, "000001.SZ", [20.0] * 29 + [20.6])
    _write_case_csv(
        tmp_path, "300001.SZ", [30.0 - 0.075 * i for i in range(24)] + [27.27, 27.0]
    )
    return load_cases(tmp_path)


class TestLoadCases:
    def test_shape_columns_and_identity(self, tmp_path):
        cases = _three_cases(tmp_path)
        assert len(cases) == 3
        by_code = {c.code: c for c in cases}
        assert set(by_code) == {"600000", "000001", "300001"}
        c = by_code["600000"]
        assert isinstance(c, PerfectB1Case)
        assert c.code_full == "600000.SH"
        assert list(c.bars.columns) == [
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
        ]
        assert c.n_bars == 40 == len(c.bars)
        assert pd.api.types.is_datetime64_any_dtype(c.bars["date"])

    def test_buy_date_is_last_bar(self, tmp_path):
        for c in _three_cases(tmp_path):
            assert c.buy_date == str(c.bars["date"].iloc[-1].date())
            assert c.buy_date == c.end  # 文件名末根 ∧ 数据末根一致（装载护栏）

    def test_sorted_and_numeric(self, tmp_path):
        for c in _three_cases(tmp_path):
            assert c.bars["date"].is_monotonic_increasing
            for col in ("open", "high", "low", "close", "volume", "amount"):
                assert pd.api.types.is_numeric_dtype(c.bars[col])

    def test_bars_compatible_with_expr_dsl(self, tmp_path):
        from custos.research.evolution import expr_dsl

        df = _three_cases(tmp_path)[0].bars
        s = expr_dsl.evaluate("close/MA(close,20)", df)
        assert len(s) == len(df)


class TestAudit:
    def test_per_case_fields_hand_computed(self, tmp_path):
        audit = audit_cases(_three_cases(tmp_path))
        by_code = {r["code"]: r for r in audit["per_case"]}
        c1 = by_code["600000"]
        assert c1["excerpt_gain_pct"] == pytest.approx(39.0, abs=0.05)  # 13.9/10-1
        assert c1["buy_day_change_pct"] == pytest.approx(0.72, abs=0.01)
        assert c1["buy_vol_ratio5"] == pytest.approx(0.5, abs=0.01)
        assert c1["buy_near_limit"] is False
        assert c1["price_limit"] in (5, 10, 20, 30)
        # 无 provider ⇒ 买点后涨幅三键全缺（None/0），不是假装算过
        assert c1["forward_peak_pct_20d"] is None and c1["forward_n"] == 0

    def test_aggregates(self, tmp_path):
        audit = audit_cases(_three_cases(tmp_path))
        assert audit["n_cases"] == 3
        # 300001 片段内 −10% ⇒ 不全正；计数口径钉住
        assert audit["all_excerpt_gain_positive"] is False
        assert audit["min_excerpt_gain_pct"] == pytest.approx(-10.0, abs=0.05)
        assert audit["max_excerpt_gain_pct"] == pytest.approx(39.0, abs=0.05)
        assert audit["buy_day_negative_count"] == 1  # 300001 买点 −1%
        assert audit["buy_near_limit_count"] == 0
        assert "事后标注" in audit["label_note"]
        assert "非买点验证" in audit["gain_note"]  # 片段涨幅口径声明必须在

    def test_forward_peaks_with_provider(self, tmp_path):
        cases = _three_cases(tmp_path)
        case = next(c for c in cases if c.code == "600000")
        buy_close = case.bars["close"].iloc[-1]  # 13.9
        after = [buy_close + 0.1 * (i + 1) for i in range(25)]  # 买点后递升
        full = pd.concat(
            [
                case.bars,
                pd.DataFrame(
                    {
                        "date": pd.date_range(
                            case.bars["date"].iloc[-1], periods=26, freq="B"
                        )[1:],
                        "open": after,
                        "high": after,
                        "low": after,
                        "close": after,
                        "volume": 1000.0,
                        "amount": 1.0,
                    }
                ),
            ]
        )
        audit = audit_cases(cases, bars_provider=lambda code: full)
        row = next(r for r in audit["per_case"] if r["code"] == "600000")
        assert row["forward_n"] == 25
        assert row["forward_peak_pct_60d"] == pytest.approx(
            (16.4 / 13.9 - 1) * 100, abs=0.05
        )
        assert row["forward_peak_pct_20d"] == pytest.approx(
            (15.9 / 13.9 - 1) * 100, abs=0.05
        )

    def test_bars_none_case_rejected_in_audit(self):
        case = load_marks_json(REPO_MARKS_JSON)[0]
        assert case.bars is None
        with pytest.raises(ValueError, match="无材料片段"):
            audit_cases([case])

    def test_marks_only_case_with_provider_audits_forward(self):
        """点对模式 + provider：片段字段全 None，买点后涨幅照算（生产机回填路径）。"""
        case = load_marks_json(REPO_MARKS_JSON)[0]  # 002074 @ 2025-08-01
        dates = pd.date_range("2025-07-28", periods=10, freq="B")  # 含 08-01
        closes = [10.0, 10.1, 10.2, 10.3, 10.4, 10.0, 11.0, 11.5, 11.2, 11.8]
        full = pd.DataFrame(
            {
                "date": dates,
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": 1.0,
                "amount": 1.0,
            }
        )
        rep = audit_cases([case], bars_provider=lambda code: full)
        r = rep["per_case"][0]
        assert r["excerpt_gain_pct"] is None and r["buy_day_change_pct"] is None
        assert rep["all_excerpt_gain_positive"] is None  # 无片段读数不假装
        # 买点 2025-08-01 收盘 10.4（dates 第 5 根）；之后 5 根峰值 11.8 ⇒ +13.5%
        assert r["forward_peak_pct_20d"] == pytest.approx(13.5, abs=0.06)
        assert r["forward_n"] == 5

    def test_empty_cases_rejected(self):
        with pytest.raises(ValueError, match="0 案例"):
            audit_cases([])


class TestLoadMarksJson:
    def test_valid_roundtrip(self, tmp_path):
        p = tmp_path / "marks.json"
        p.write_text(
            json.dumps(
                {
                    "marks": [
                        {"code": "600000", "buy_date": "2025-03-14"},
                        {"code": "000001", "buy_date": "2025-04-01"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        cases = load_marks_json(p)
        assert [(c.code, c.code_full, c.buy_date) for c in cases] == [
            ("600000", "600000.SH", "2025-03-14"),  # 后缀经 code_utils.suffix 推导
            ("000001", "000001.SZ", "2025-04-01"),
        ]
        assert all(c.bars is None and c.n_bars == 0 for c in cases)

    def test_bare_list_form_accepted(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text(
            json.dumps([{"code": "600000", "buy_date": "2025-03-14"}]), encoding="utf-8"
        )
        assert len(load_marks_json(p)) == 1

    @pytest.mark.parametrize(
        "payload, match",
        [
            ('{"marks": [{"code": "600000"}]}', "缺 code/buy_date"),
            ('{"marks": [{"code": "abc", "buy_date": "2025-03-14"}]}', "code 非法"),
            (
                '{"marks": [{"code": "600000", "buy_date": "2025/03/14"}]}',
                "buy_date 非法",
            ),
            (
                '{"marks": [{"code": "600000", "buy_date": "2025-03-14"},'
                ' {"code": "600000", "buy_date": "2025-03-14"}]}',
                "重复",
            ),
            ('{"marks": []}', "为空"),
            ('{"nope": 1}', "形态非法"),
            ("not json{", "不可解析"),
        ],
    )
    def test_fail_closed(self, tmp_path, payload, match):
        p = tmp_path / "m.json"
        p.write_text(payload, encoding="utf-8")
        with pytest.raises(ValueError, match=match):
            load_marks_json(p)

    def test_missing_file_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="不存在"):
            load_marks_json(tmp_path / "nope.json")


class TestCanonicalMarksJson:
    """权威点对清单（库内 JSON）钉测：10 条，code/买点日期逐条在案。

    这钉的是**口径清单本身**（R36 文档清单表必须与它同步）；
    真实 CSV 材料只在 dev 机，不进测试（hermetic 纪律见文件头）。
    """

    def test_canonical_10_marks(self):
        payload = json.loads(REPO_MARKS_JSON.read_text(encoding="utf-8"))
        got = {(m["code"], m["buy_date"]) for m in payload["marks"]}
        assert got == {
            ("002074", "2025-08-01"),
            ("002812", "2025-09-23"),
            ("002940", "2025-07-11"),
            ("300689", "2025-07-18"),
            ("301076", "2025-08-01"),
            ("600184", "2025-07-10"),
            ("600366", "2025-08-06"),
            ("600601", "2025-07-23"),
            ("605378", "2025-07-31"),
            ("688321", "2025-06-20"),
        }


class TestResolveBars:
    def test_provider_truncates_at_buy_date(self, tmp_path):
        case = _three_cases(tmp_path)[0]
        extra = pd.DataFrame(
            {
                "date": pd.date_range("2026-01-01", periods=3, freq="B"),
                "open": [1, 1, 1],
                "high": [1, 1, 1],
                "low": [1, 1, 1],
                "close": [999.0] * 3,
                "volume": [1, 1, 1],
                "amount": [1, 1, 1],
            }
        )
        full = pd.concat([case.bars, extra])
        bars, src = resolve_bars(case, provider=lambda code: full)
        assert src == "provider"
        assert str(bars["date"].iloc[-1].date()) == case.buy_date  # 买点后不在场
        assert len(bars) == case.n_bars

    def test_excerpt_fallback_and_none_raise(self, tmp_path):
        case = _three_cases(tmp_path)[0]
        bars, src = resolve_bars(case)
        assert src == "excerpt" and len(bars) == case.n_bars
        no_bars = load_marks_json(REPO_MARKS_JSON)[0]
        with pytest.raises(ValueError, match="provider"):
            resolve_bars(no_bars)


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
