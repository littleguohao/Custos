# -*- coding: utf-8 -*-
r"""Unified stock-code normalization helpers for strategy_team.

Consolidates the four divergent code-normalization implementations found in
07_tools/trades/incremental_ledger.py, 07_tools/trades/standardize_trades.py,
07_tools/market_timing/technical_monitor.py and
07_tools/market_timing/holding_sector_mapper.py.

Semantics are locked by tests/test_pipeline_kit.py.
"""
from __future__ import annotations

import math


def clean_code(v):
    """Ledger semantics: normalize a trade code to a 6-digit zero-padded string.

    Baseline: incremental_ledger.clean_code (verbatim). Known behavior
    differences vs the standardize_trades.clean_code variant:

    - ".0" handling: this version strips every ".0" occurrence globally
      (``str.replace('.0','')``), so e.g. "10.05" -> "105" -> "000105";
      standardize_trades only strips a single trailing ".0"
      ("10.05" stays "10.05").
    - zfill condition: this version applies zfill(6) unconditionally when the
      head segment is all digits; standardize_trades only pads when
      ``len(s) < 6`` (identical result for len >= 6, since zfill is a no-op).
    - empty input: ``None``/falsy values become "" here; standardize_trades
      would stringify None to "None".
    """
    s = str(v or '').strip().replace('.0', '')
    return s.split('.')[0].zfill(6) if s.split('.')[0].isdigit() else s.split('.')[0]


def market_of(code: str) -> str:
    """Single source of truth for exchange classification: "SH" | "SZ" | "BJ" | "".

    Rules (applied in order):

    - Explicit suffix wins: "xxx.BJ" -> "BJ", "xxx.SH"/"xxx.SZ" likewise, even
      when the bare code's prefix heuristic would disagree (e.g. 880005.SH is an
      SH statistics index, not a BJ stock).
    - Suffix-less digit codes are zero-padded to 6 digits, then:
      - "880" -> "SH" (通达信沪市统计指数系列，必须排在 "8" 前缀之前)
      - "920" / "8" / "4" -> "BJ" (北交所)
      - "6" / "5" / "9" -> "SH"
      - "0" / "1" / "2" / "3" -> "SZ"
    - Anything else -> "".
    """
    s = str(code).strip().upper()
    if "." in s:
        suf = s.rsplit(".", 1)[1]
        return suf if suf in {"SH", "SZ", "BJ"} else ""
    if not s.isdigit():
        return ""
    s = s.zfill(6)
    if s.startswith("880"):
        return "SH"
    if s.startswith(("920", "8", "4")):
        return "BJ"
    if s.startswith(("6", "5", "9")):
        return "SH"
    if s.startswith(("0", "1", "2", "3")):
        return "SZ"
    return ""


def norm_code(code: str) -> str:
    """Market-data semantics: ensure a .SH/.SZ/.BJ suffix (technical_monitor version).

    Note: holding_sector_mapper.norm_code has different semantics (6-digit
    zero-padding, no exchange suffix) and intentionally stays in its original
    file; it is NOT merged here.
    """
    s = str(code).strip().upper()
    if s.endswith((".SH", ".SZ", ".BJ")):
        return s
    market = market_of(s)
    return f"{s}.{market}" if market else s


def split_code(tdx_code: str):
    """Split a tdx code into (lowercase exchange prefix, bare code).

    Verbatim from technical_monitor.split_code; relies on norm_code.
    """
    s = norm_code(tdx_code)
    code, suf = s.split(".")
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(suf, "")
    return prefix, code


def suffix(code: str) -> str:
    """Exchange suffix for a bare 6-digit code; delegates to market_of."""
    market = market_of(code)
    return f".{market}" if market else ""


def finite(v, d=0.0):
    """Coerce v to float; return d on failure or NaN (incremental_ledger version, verbatim)."""
    try:
        x = float(v); return d if math.isnan(x) else x
    except: return d
