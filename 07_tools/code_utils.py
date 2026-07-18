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


def norm_code(code: str) -> str:
    """Market-data semantics: ensure a .SH/.SZ/.BJ suffix (technical_monitor version).

    Note: holding_sector_mapper.norm_code has different semantics (6-digit
    zero-padding, no exchange suffix) and intentionally stays in its original
    file; it is NOT merged here.
    """
    s = str(code).strip().upper()
    if s.endswith((".SH", ".SZ", ".BJ")):
        return s
    # 北交所常见代码含 4/8 开头，也包含 920xxx。
    if s.startswith(("920", "8", "4")):
        return s + ".BJ"
    if s.startswith(("6", "5", "9")):
        return s + ".SH"
    if s.startswith(("0", "1", "2", "3")):
        return s + ".SZ"
    return s


def split_code(tdx_code: str):
    """Split a tdx code into (lowercase exchange prefix, bare code).

    Verbatim from technical_monitor.split_code; relies on norm_code.
    """
    s = norm_code(tdx_code)
    code, suf = s.split(".")
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(suf, "")
    return prefix, code


def suffix(code: str) -> str:
    """Exchange suffix for a bare 6-digit code (holding_sector_mapper version, verbatim)."""
    if code.startswith(("92", "8", "4")): return ".BJ"
    if code.startswith(("6", "5")): return ".SH"
    if code.startswith(("0", "1", "2", "3")): return ".SZ"
    return ""


def finite(v, d=0.0):
    """Coerce v to float; return d on failure or NaN (incremental_ledger version, verbatim)."""
    try:
        x = float(v); return d if math.isnan(x) else x
    except: return d
