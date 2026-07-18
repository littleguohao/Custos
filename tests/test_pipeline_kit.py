# -*- coding: utf-8 -*-
"""Semantic-lock tests for 07_tools/pipeline_kit.py and 07_tools/code_utils.py."""
from __future__ import annotations

import math

import pytest

import code_utils
import pipeline_kit


# ---------------------------------------------------------------------------
# code_utils.clean_code — ledger semantics (incremental_ledger baseline)
# ---------------------------------------------------------------------------

class TestCleanCode:
    def test_five_digit_padded(self):
        assert code_utils.clean_code("12345") == "012345"

    def test_six_digit_unchanged(self):
        assert code_utils.clean_code("600519") == "600519"

    def test_trailing_dot_zero_stripped(self):
        # float-like codes from xlsx/csv imports
        assert code_utils.clean_code("600519.0") == "600519"
        assert code_utils.clean_code(600519.0) == "600519"

    def test_int_input(self):
        assert code_utils.clean_code(12345) == "012345"

    def test_empty_and_none(self):
        # incremental_ledger semantics: falsy -> "" (standardize_trades would give "None")
        assert code_utils.clean_code(None) == ""
        assert code_utils.clean_code("") == ""

    def test_global_dot_zero_replace(self):
        # incremental_ledger does a global .replace('.0',''), so "10.05" -> "105".
        # standardize_trades (trailing-only strip) would leave "10.05" untouched.
        assert code_utils.clean_code("10.05") == "000105"

    def test_decimal_head_segment(self):
        assert code_utils.clean_code("12345.67") == "012345"

    def test_non_digit_passthrough(self):
        assert code_utils.clean_code("ABC") == "ABC"

    def test_bj_code(self):
        assert code_utils.clean_code("920001") == "920001"
        assert code_utils.clean_code("430047") == "430047"


# ---------------------------------------------------------------------------
# code_utils.norm_code — market semantics (technical_monitor version)
# ---------------------------------------------------------------------------

class TestNormCode:
    def test_already_suffixed(self):
        assert code_utils.norm_code("600519.SH") == "600519.SH"
        assert code_utils.norm_code("000001.sz") == "000001.SZ"

    def test_sh(self):
        assert code_utils.norm_code("600519") == "600519.SH"
        assert code_utils.norm_code("510300") == "510300.SH"

    def test_sz(self):
        assert code_utils.norm_code("000001") == "000001.SZ"
        assert code_utils.norm_code("300750") == "300750.SZ"

    def test_bj_920(self):
        assert code_utils.norm_code("920001") == "920001.BJ"

    def test_bj_4_and_8(self):
        assert code_utils.norm_code("430047") == "430047.BJ"
        assert code_utils.norm_code("830799") == "830799.BJ"

    def test_unknown_prefix_passthrough(self):
        assert code_utils.norm_code("700000") == "700000"


# ---------------------------------------------------------------------------
# code_utils.split_code / suffix / finite
# ---------------------------------------------------------------------------

class TestSplitCode:
    def test_sh(self):
        assert code_utils.split_code("600519") == ("sh", "600519")

    def test_sz(self):
        assert code_utils.split_code("000001.SZ") == ("sz", "000001")

    def test_bj(self):
        assert code_utils.split_code("920001") == ("bj", "920001")


class TestSuffix:
    def test_bj(self):
        assert code_utils.suffix("920001") == ".BJ"
        assert code_utils.suffix("830799") == ".BJ"
        assert code_utils.suffix("430047") == ".BJ"

    def test_sh(self):
        assert code_utils.suffix("600519") == ".SH"
        assert code_utils.suffix("510300") == ".SH"

    def test_sz(self):
        assert code_utils.suffix("000001") == ".SZ"
        assert code_utils.suffix("300750") == ".SZ"

    def test_unknown(self):
        assert code_utils.suffix("999999") == ""


class TestFinite:
    def test_normal(self):
        assert code_utils.finite("3.14") == 3.14
        assert code_utils.finite(2) == 2.0

    def test_nan_returns_default(self):
        assert code_utils.finite(float("nan")) == 0.0
        assert code_utils.finite(float("nan"), d=-1.0) == -1.0

    def test_invalid_returns_default(self):
        assert code_utils.finite(None) == 0.0
        assert code_utils.finite("abc", d=7.5) == 7.5


# ---------------------------------------------------------------------------
# pipeline_kit.md_to_digest
# ---------------------------------------------------------------------------

class TestMdToDigest:
    def test_headers_converted(self):
        md = "# 标题一\n内容行\n"
        digest = pipeline_kit.md_to_digest(md)
        assert "标题一" in digest
        assert "─" * min(len("标题一") * 2, 40) in digest
        assert "#" not in digest

    def test_table_rows_converted(self):
        md = "# 表\n| 代码 | 名称 |\n| --- | --- |\n| 600519 | 贵州茅台 |\n"
        digest = pipeline_kit.md_to_digest(md)
        assert "600519 | 贵州茅台" in digest
        # separator row dropped
        assert "---" not in digest

    def test_bullets_kept(self):
        md = "# 要点\n- 第一条\n• 第二条\n"
        digest = pipeline_kit.md_to_digest(md)
        assert "- 第一条" in digest
        assert "• 第二条" in digest

    def test_text_before_first_header_dropped(self):
        md = "前言废话\n# 正文\n保留我\n"
        digest = pipeline_kit.md_to_digest(md)
        assert "前言废话" not in digest
        assert "保留我" in digest

    def test_leading_empty_lines_skipped(self):
        md = "\n\n# 标题\n内容\n"
        digest = pipeline_kit.md_to_digest(md)
        assert digest.startswith("标题")

    def test_truncation_default_note(self):
        md = "# 长\n" + "字" * 5000 + "\n"
        digest = pipeline_kit.md_to_digest(md)
        assert len(digest) <= 3500
        assert digest.endswith("...(完整报告见文件)")

    def test_truncation_review_note(self):
        md = "# 长\n" + "字" * 5000 + "\n"
        digest = pipeline_kit.md_to_digest(md, truncate_note="...(完整复盘见文件)")
        assert digest.endswith("...(完整复盘见文件)")

    def test_truncation_custom_limit(self):
        md = "# 长\n" + "字" * 5000 + "\n"
        digest = pipeline_kit.md_to_digest(md, limit=1000)
        assert digest.endswith("...(完整报告见文件)")


# ---------------------------------------------------------------------------
# pipeline_kit._extract_json — pure JSON-line extraction
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_single_json_line(self):
        text = '{"is_trading_day": true, "date": "2026-07-17"}\n'
        assert pipeline_kit._extract_json(text) == {"is_trading_day": True, "date": "2026-07-17"}

    def test_mixed_stderr_noise(self):
        text = "[WARN] something\nnot json at all\n{\"is_trading_day\": false}\ntrailing noise\n"
        assert pipeline_kit._extract_json(text) == {"is_trading_day": False}

    def test_first_dict_wins(self):
        text = '{"a": 1}\n{"b": 2}\n'
        assert pipeline_kit._extract_json(text) == {"a": 1}

    def test_non_dict_json_skipped(self):
        text = '[1, 2, 3]\n42\n"str"\n{"ok": true}\n'
        assert pipeline_kit._extract_json(text) == {"ok": True}

    def test_no_json_returns_empty(self):
        assert pipeline_kit._extract_json("no json here\n") == {}
        assert pipeline_kit._extract_json("") == {}

    def test_pretty_printed_multiline_json(self):
        # trading_calendar.py prints indent=2 JSON; line-by-line parsing
        # fails on this shape (regression: runners misread trading days).
        text = '{\n  "date": "2026-07-18",\n  "is_trading_day": false,\n  "reason": "周末休市"\n}\n'
        assert pipeline_kit._extract_json(text) == {
            "date": "2026-07-18", "is_trading_day": False, "reason": "周末休市",
        }

    def test_pretty_json_with_noise_around(self):
        text = '[RUN] trading_calendar\n{\n  "is_trading_day": true\n}\n[DONE]\n'
        assert pipeline_kit._extract_json(text) == {"is_trading_day": True}
